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


def reconcile_legacy_exact(conn,dry_run=True,max_verified=500):
    if not 1 <= int(max_verified) <= 5000:
        raise ValueError('Batch size must be between 1 and 5000.')
    pending=pending_identity_ids(conn)
    canonicals=[dict(x) for x in conn.execute("""SELECT id,canonical_title,brand,brand_id,identity_status,
        identity_attributes,ingredients_raw,is_skincare FROM canonical_products
        WHERE is_skincare=1 AND identity_status IN ('legacy','legacy_preserved_unverified') ORDER BY id""")]
    # Same-brand duplicates are not safe to verify independently.
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
    for can in canonicals:
        cid=can['id']
        if cid in pending:
            counts['pending_identity_review']+=1;continue
        group=offers.get(cid,[])
        if not group:
            counts['no_retailer_listing']+=1;continue
        brand=resolve_brand(can['brand'],conn)
        if not brand or not brand.get('id') or (can['brand_id'] and can['brand_id']!=brand['id']):
            counts['brand_not_resolved']+=1;continue
        expected=identity_name(can['canonical_title'],can['brand'])
        if not expected:
            counts['incomplete_identity']+=1;continue
        if profiles[(brand['id'],expected)]!={cid}:
            counts['duplicate_canonical_identity']+=1;continue
        reference=extract_identity(can['canonical_title'],can['brand'])
        evidence={v for v in (formula_fingerprint(can),) if v}
        compatible=True
        for item in group:
            b=resolve_brand(item['brand'],conn)
            if not b or b.get('id')!=brand['id'] or identity_name(item['title'],item['brand'])!=expected:
                compatible=False;break
            allowed,_=validate_identity_gates(item['title'],can['canonical_title'],item['brand'],can['brand'])
            comparison=compare_attributes(extract_identity(item['title'],item['brand']),reference)
            if not allowed or comparison['conflicts'] or any(k in CRITICAL for k in comparison['unknown']):
                compatible=False;break
            if item.get('ingredient_source') in TRUSTED_INGREDIENT_ORIGINS:
                hash_value=formula_fingerprint(item)
                if hash_value:evidence.add(hash_value)
        if not compatible:
            counts['nonidentical_or_incomplete_variants']+=1;continue
        if len(evidence)>1:
            counts['ingredient_formula_conflict']+=1;continue
        qualified.append((can,group,brand))
        counts['qualified']+=1
        if len(qualified)>=max_verified:break
    if not dry_run:
        resolver=CanonicalIdentityResolver(conn)
        for can,group,brand in qualified:
            cid=can['id']
            for item in group:
                source_key=str(item['store_product_id'] or item['id'])
                resolver.record_link(item['store_id'],source_key,cid,'accepted',
                    'exact_legacy_identity_auto',
                    {'policy':'exact_legacy_identity_v1','product_id':item['id'],
                     'verified_name':identity_name(item['title'],item['brand'])},
                    reviewer='identity_policy_v1')
            conn.execute("""UPDATE canonical_products SET identity_status='verified',brand_id=?
                WHERE id=? AND identity_status IN ('legacy','legacy_preserved_unverified')""",(brand['id'],cid))
            conn.execute("""INSERT INTO canonical_identity_history(operation,canonical_product_id,target_id,
                before_state,after_state,reviewed_by,reason) VALUES (?,?,?,?,?,?,?)""",
                ('auto_verify',cid,None,
                 json.dumps({'identity_status':can['identity_status'],'brand_id':can['brand_id']}),
                 json.dumps({'identity_status':'verified','brand_id':brand['id']}),
                 'identity_policy_v1',
                 'Exact brand and formulation-defining identity across all legacy offers; no conflicting canonical or INCI'))
    return {
        'dry_run':dry_run,'auto_verified':len(qualified) if not dry_run else 0,
        'safe_exact_matches_found':len(qualified),'legacy_products_scanned':sum(counts.values()),
        'reasons_not_auto_matched':{k:v for k,v in counts.items() if k!='qualified'},
        'sample_canonical_ids':[can['id'] for can,_,_ in qualified[:12]],
        'note':'Automatic verification is restricted to unique, consistent legacy identities. Conflicts remain for review. No ingredient lists or prices were changed.'
    }
