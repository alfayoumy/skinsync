"""Automated conservative revalidation of existing legacy product identities.

No new canonical entities, offer assignments, category writes or INCI publication.
Ambiguous clusters remain untouched for product review.
"""
from collections import Counter, defaultdict
import json
from pipeline.brand_registry import resolve_brand
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver, identity_name, extract_identity, compare_attributes
from pipeline.identity_gates import validate_identity_gates
from pipeline.identity_review import formula_fingerprint
from pipeline.waterfall_readiness import pending_identity_ids

CRITICAL=('spf','concentrations','timing','fragrance','tint','shade','formula_revision','vehicle','function')
TRUSTED_INGREDIENT_ORIGINS={'store_page_tab','historical_store_scrape','curated_v10','admin_override'}


def reconcile_legacy_exact(conn,dry_run=True,max_verified=500,queue_failures=True,max_review_cases=250):
    """Verify bounded safe clusters; give exact totals and persist actionable rejects only on apply.

    max_verified controls WRITES, not the number of records inspected. Repeated
    applications continue from the next unverified group, never the same first 500.
    """
    if not 0 <= int(max_verified) <= 5000:
        raise ValueError('Batch size must be between 0 and 5000.')
    if not 1 <= int(max_review_cases) <= 1000:
        raise ValueError('Review batch size must be between 1 and 1000.')
    pending=pending_identity_ids(conn)
    canonicals=[dict(x) for x in conn.execute("""SELECT id,canonical_title,brand,brand_id,identity_status,
        identity_attributes,ingredients_raw,is_skincare FROM canonical_products
        WHERE is_skincare=1 AND identity_status IN ('legacy','legacy_preserved_unverified') ORDER BY id""")]
    active=[dict(x) for x in conn.execute("""SELECT id,canonical_title,brand,brand_id FROM canonical_products
        WHERE is_skincare=1 AND identity_status NOT IN ('redirected','reversed_split')""")]
    profiles=defaultdict(set)
    for row in active:
        b=resolve_brand(row['brand'],conn)
        if b and b.get('id'):
            profiles[(b['id'],identity_name(row['canonical_title'],row['brand']))].add(row['id'])
    offers=defaultdict(list)
    for row in conn.execute("""SELECT id,store_id,store_product_id,canonical_product_id,
        COALESCE(original_title,title) title,COALESCE(original_brand,brand) brand,
        ingredients_raw,ingredient_source FROM products WHERE canonical_product_id IS NOT NULL
        ORDER BY canonical_product_id,id"""):
        offers[row['canonical_product_id']].append(dict(row))

    counts=Counter()
    qualified=[]
    failures=[]
    for can in canonicals:
        cid=can['id']
        group=offers.get(cid,[])
        reason=None
        extra={}
        if cid in pending:
            reason='pending_identity_review'
        elif not group:
            reason='no_retailer_listing'
        else:
            brand=resolve_brand(can['brand'],conn)
            if not brand or not brand.get('id') or (can['brand_id'] and can['brand_id']!=brand['id']):
                reason='brand_not_resolved'
            else:
                expected=identity_name(can['canonical_title'],can['brand'])
                if not expected:
                    reason='incomplete_identity'
                elif profiles[(brand['id'],expected)]!={cid}:
                    reason='duplicate_canonical_identity'
                    extra['canonical_ids']=sorted(profiles[(brand['id'],expected)])
                else:
                    reference=extract_identity(can['canonical_title'],can['brand'])
                    evidence={value for value in (formula_fingerprint(can),) if value}
                    for item in group:
                        b=resolve_brand(item['brand'],conn)
                        if not b or b.get('id')!=brand['id'] or identity_name(item['title'],item['brand'])!=expected:
                            reason='nonidentical_or_incomplete_variants'
                            extra['conflicting_offer_id']=item['id']
                            break
                        allowed,_=validate_identity_gates(item['title'],can['canonical_title'],item['brand'],can['brand'])
                        cmp=compare_attributes(extract_identity(item['title'],item['brand']),reference)
                        if not allowed or cmp['conflicts'] or any(k in CRITICAL for k in cmp['unknown']):
                            reason='nonidentical_or_incomplete_variants'
                            extra['conflicting_offer_id']=item['id']
                            break
                        if item.get('ingredient_source') in TRUSTED_INGREDIENT_ORIGINS:
                            hash_value=formula_fingerprint(item)
                            if hash_value:evidence.add(hash_value)
                    if not reason and len(evidence)>1:
                        reason='ingredient_formula_conflict'
                        extra['formula_fingerprints']=sorted(evidence)
                    if not reason:
                        qualified.append((can,group,brand))
                        counts['qualified']+=1
        if reason:
            counts[reason]+=1
            # Existing pending reviews are not redundantly re-enqueued.
            if reason in ('nonidentical_or_incomplete_variants','ingredient_formula_conflict',
                          'duplicate_canonical_identity','brand_not_resolved','incomplete_identity'):
                failures.append((reason,can,group,extra))

    applied=qualified[:max_verified] if not dry_run else []
    queued=0
    if not dry_run:
        resolver=CanonicalIdentityResolver(conn)
        for can,group,brand in applied:
            cid=can['id']
            for item in group:
                resolver.record_link(item['store_id'],str(item['store_product_id'] or item['id']),cid,'accepted',
                    'exact_legacy_identity_auto',{'policy':'exact_legacy_identity_v1','product_id':item['id'],
                    'verified_name':identity_name(item['title'],item['brand'])},reviewer='identity_policy_v1')
            cursor=conn.execute("""UPDATE canonical_products SET identity_status='verified',brand_id=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND identity_status IN ('legacy','legacy_preserved_unverified')""",
                (brand['id'],cid))
            if cursor.rowcount!=1:
                raise ValueError('Legacy identity changed during verification. Retry the preview.')
            conn.execute("""INSERT INTO canonical_identity_history(operation,canonical_product_id,target_id,
                before_state,after_state,reviewed_by,reason) VALUES (?,?,?,?,?,?,?)""",
                ('auto_verify',cid,None,json.dumps({'identity_status':can['identity_status'],'brand_id':can['brand_id']}),
                 json.dumps({'identity_status':'verified','brand_id':brand['id']}),'identity_policy_v1',
                 'Exact compatible legacy listing group with no competing canonical identity or INCI conflict'))
        if queue_failures:
            from pipeline.aggregation_service import queue_review
            for reason,can,group,extra in failures[:max_review_cases]:
                # A duplicate is a canonical-level decision; a variant discrepancy
                # references the actual source listing so the reviewer can inspect it.
                offender=next((o for o in group if o['id']==extra.get('conflicting_offer_id')),None)
                offender=offender or (group[0] if group else None)
                is_duplicate=reason=='duplicate_canonical_identity'
                kind='duplicate' if is_duplicate else 'identity'
                sid='canonical' if is_duplicate or not offender else offender['store_id']
                source_key=str(can['id']) if sid=='canonical' else str(offender['store_product_id'] or offender['id'])
                detail={
                    'origin':'legacy_identity_scan_v1','reason_code':reason,
                    'title':can['canonical_title'],'brand':can['brand'],
                    'canonical_ids':extra.get('canonical_ids',[can['id']]),
                    'canonical_id':can['id'],
                    'offers':[{'product_id':o['id'],'store_id':o['store_id'],'title':o['title'],
                               'brand':o['brand']} for o in group[:25]],
                    'product_id':offender['id'] if offender and sid!='canonical' else None,
                    **extra
                }
                existing=conn.execute("""SELECT status FROM product_review_queue
                    WHERE kind=? AND source_id=? AND source_product_id=? AND canonical_product_id=?""",
                    (kind,sid,source_key,can['id'])).fetchone()
                if existing and existing['status']=='confirmed':
                    continue
                queue_review(conn,kind,sid,source_key,can['id'],detail)
                queued+=1
    pending_total=conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE status='pending'").fetchone()[0]
    return {
        'dry_run':dry_run,
        'auto_verified':len(applied),
        'safe_exact_matches_found':len(qualified),
        'remaining_safe_matches':len(qualified)-len(applied),
        'legacy_products_scanned':len(canonicals),
        'reasons_not_auto_matched':{k:v for k,v in counts.items() if k!='qualified'},
        'sample_canonical_ids':[can['id'] for can,_,_ in qualified[:12]],
        'review_cases_prepared':queued,
        'review_candidates_found':len(failures),
        'pending_review_cases':pending_total,
        'batch_limit':max_verified,
        'note':('Preview only; no records or review cases were changed.' if dry_run
                else 'Verified '+str(len(applied))+' exact identities. Remaining safe matches: '+
                     str(len(qualified)-len(applied))+'. Review cases are available separately.')
    }
