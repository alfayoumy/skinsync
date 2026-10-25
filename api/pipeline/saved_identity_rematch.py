"""Non-destructive re-match of stale, source-backed identity review cases.

Never trust a Gemini verdict made with empty product listings. Rebuild each
product identity from the *existing* immutable normalized Shopify observation,
compare it against the CURRENT canonical catalog, and archive only successfully
replaced review cases. No rescraping, Gemini calls, purges or INCI publication.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import logging

from pipeline.aggregation_service import process_source_observation
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver
from pipeline.source_adapters.contracts import SourceProductObservation, SourceVariant
from pipeline.source_registry import get_source

logger=logging.getLogger(__name__)
CONFIRMATION='REMATCH SAVED PRODUCTS'
BATCH_LIMIT=10000
WORKER_TYPES=('catalog_discovery','product_identity_gemini','apply_saved_gemini',
              'canonical_no_comparison_recovery','catalog_selective_rebuild')


def _details(row):
    try:
        d=json.loads(row['details'] or '{}')
        return d if isinstance(d,dict) else {}
    except (ValueError,TypeError):
        return {}


def _eligible(conn,limit=BATCH_LIMIT):
    """Returns latest observed products, never ungrounded or reviewed decisions."""
    rows=conn.execute("""SELECT r.id,r.kind,r.status,r.source_id,
        r.source_product_id,r.canonical_product_id,r.details,r.evidence_hash,
        r.reviewed_by,r.decision,q.status gemini_status
        FROM product_review_queue r LEFT JOIN identity_gemini_queue q
        ON q.review_id=r.id WHERE r.kind='identity'
        AND r.status IN ('pending','deferred')
        AND r.canonical_product_id IS NULL ORDER BY r.id""").fetchall()
    grouped={}
    excluded=Counter()
    for raw in rows:
        r=dict(raw)
        if r['reviewed_by'] or r['decision']:
            excluded['human_decisions_preserved']+=1
            continue
        if r['gemini_status'] in ('processing','auto_resolved'):
            excluded['in_flight_or_approved_gemini']+=1
            continue
        info=_details(r)
        oid=info.get('observation_id')
        if type(oid) is not int:
            excluded['no_saved_observation']+=1
            continue
        obs=conn.execute("""SELECT id,source_id,source_product_id,source_url,
            raw_title,raw_brand FROM source_product_observations WHERE id=?""",(oid,)).fetchone()
        if not obs or obs['source_id']!=r['source_id'] or not obs['raw_title'] or not obs['source_product_id']:
            excluded['missing_or_wrong_source']+=1
            continue
        if (info.get('source_url') and info['source_url']!=obs['source_url']) or (
            info.get('title') and info['title']!=obs['raw_title']):
            excluded['snapshot_mismatch']+=1
            continue
        if not (r['source_product_id']==obs['source_product_id'] or
                str(r['source_product_id']).startswith(str(obs['source_product_id'])+':')):
            excluded['source_variant_mismatch']+=1
            continue
        latest=conn.execute("""SELECT MAX(id) FROM source_product_observations
            WHERE source_id=? AND source_product_id=?""",
            (obs['source_id'],obs['source_product_id'])).fetchone()[0]
        if oid!=latest:
            excluded['superseded_observation']+=1
            continue
        if oid not in grouped and len(grouped)>=limit:
            continue
        grouped.setdefault(oid,[]).append(r)
    return grouped,dict(excluded)


def _fingerprint(grouped):
    signatures=[(oid,[(r['id'],r['evidence_hash'],r['status']) for r in rows])
                for oid,rows in grouped.items()]
    return hashlib.sha256(json.dumps(signatures,separators=(',',':')).encode()).hexdigest()


def preview_saved_rematch(conn,limit=BATCH_LIMIT):
    if not 1<=limit<=BATCH_LIMIT:raise ValueError('Limit must be 1–10000 products.')
    grouped,excluded=_eligible(conn,limit)
    active=[dict(r) for r in conn.execute("""SELECT task_id,task_type,status,current_stage
        FROM pipeline_tasks WHERE (status IN ('pending','running')
        OR (status='cancelled' AND COALESCE(current_stage,'')!='worker_stopped'))
        ORDER BY created_at DESC LIMIT 20""")]
    return {'eligible_products':len(grouped),
        'review_cases_replaced_on_success':sum(map(len,grouped.values())),
        'excluded_reasons':excluded,'active_jobs':active,
        'can_start':bool(grouped) and not active,
        'fingerprint':_fingerprint(grouped),'limit':limit,
        'sample_observation_ids':list(grouped)[:12],
        'confirmation_phrase':CONFIRMATION,
        'note':'Only saved, latest unreviewed source observations qualify. Re-runs current deterministic identity matching; creates provisional identities where no defensible match exists. Supersedes stale reviews/Gemini verdicts only after a successful replay. Never deletes retailer source evidence or republishes INCI.'}


def _from_frozen_observation(conn,oid,source_cache):
    row=conn.execute('SELECT * FROM source_product_observations WHERE id=?',(oid,)).fetchone()
    if not row:raise ValueError('Saved source observation not found.')
    sid=row['source_id']
    if sid not in source_cache:
        source_cache[sid]=get_source(conn,sid)
    source=source_cache[sid]
    variants=[]
    for entry in conn.execute("""SELECT attributes,source_variant_id FROM source_product_variants
        WHERE observation_id=? ORDER BY id""",(oid,)):
        payload=json.loads(entry['attributes'] or '{}')
        if not isinstance(payload,dict):
            raise ValueError('Invalid original source variant.')
        payload['source_variant_id']=str(entry['source_variant_id'])
        variants.append(SourceVariant(**payload))
    try:
        images=json.loads(row['images'] or '[]')
        raw=json.loads(row['raw_payload'] or '{}')
    except (ValueError,TypeError) as exc:
        raise ValueError('Invalid saved source JSON; original evidence preserved.') from exc
    if not isinstance(images,list) or not isinstance(raw,dict):
        raise ValueError('Invalid original source payload or images.')
    # ingredient_text intentionally excluded: rematching a title/variant MUST
    # NOT create apparently fresh, authoritative source INCI evidence.
    return SourceProductObservation(
        source_id=sid,source_type=source['source_type'],
        source_product_id=row['source_product_id'],source_url=row['source_url'],
        raw_title=row['raw_title'],raw_brand=row['raw_brand'] or '',
        raw_category=row['raw_category'],source_gtin=row['source_gtin'],
        manufacturer_sku=row['manufacturer_sku'],description=row['description'],
        variants=variants,images=images,ingredient_text=None,
        ingredient_section_type=None,ingredient_order_verified=False,
        market=row['market'],language=row['language'] or source['language'],
        observed_at=row['observed_at'],raw_payload=raw,
        identity_attributes=json.loads(row['identity_attributes'] or '{}'))


def _apply_one(conn,oid,reviews,source_cache):
    original=_from_frozen_observation(conn,oid,source_cache)
    result=process_source_observation(conn,original,existing_observation_id=oid,
                                      replay_saved_projection=True)
    outputs=result['results']
    canonicals={x.get('canonical_product_id') for x in outputs if x.get('canonical_product_id')}
    if not canonicals or any(x.get('status') not in ('provisional','matched') for x in outputs):
        raise ValueError('Current matching still found a supported unresolved identity conflict.')
    cid=next(iter(canonicals)) if len(canonicals)==1 else None
    linked=0
    preserved=0
    if cid:
        # The legacy table is a projection, not the source of identity truth.
        legacy=conn.execute("""SELECT id,canonical_product_id,product_url
            FROM products WHERE store_id=? AND store_product_id=?""",
            (original.source_id,original.source_product_id)).fetchone()
        if legacy and legacy['product_url']==original.source_url:
            old=legacy['canonical_product_id']
            reviewed=conn.execute("""SELECT 1 FROM product_identity_links
                WHERE source_id=? AND source_product_id=? AND reviewed_by IS NOT NULL
                AND status='accepted' LIMIT 1""",
                (original.source_id,original.source_product_id)).fetchone()
            state=conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',
                               (old,)).fetchone() if old else None
            # Never overwrite a verified or manually anchored projection.
            if old in (None,cid) or (not reviewed and state and state[0]!='verified'):
                conn.execute('UPDATE products SET canonical_product_id=? WHERE id=?',
                             (cid,legacy['id']))
                conn.execute("""UPDATE product_offers SET legacy_product_id=?
                    WHERE source_id=? AND source_product_id=?""",
                    (legacy['id'],original.source_id,original.source_product_id))
                linked=1
            else:
                preserved=1
    # An older taxonomy review with no canonical should follow its *new*
    # identity. Keep taxonomy pending rather than counting it as resolved.
    if cid:
        old_categories=conn.execute("""SELECT id,details FROM product_review_queue
            WHERE kind='category' AND status='pending'
            AND canonical_product_id IS NULL AND source_id=?""",(original.source_id,)).fetchall()
        for cat in old_categories:
            try:d=json.loads(cat['details'] or '{}')
            except (ValueError,TypeError):continue
            if d.get('observation_id')==oid:
                conn.execute("""UPDATE product_review_queue
                    SET canonical_product_id=?,review_notes=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'""",
                    (cid,'Saved-source rematch identified the product. Category remains pending.',
                     cat['id']))
    superseded=0
    for row in reviews:
        changed=conn.execute("""UPDATE product_review_queue SET status='confirmed',
            decision=?,reviewed_by='saved_source_rematch_v1',
            review_notes='Stale empty-comparison identity review replaced by matching against current source/canonical evidence.',
            updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status IN ('pending','deferred')
            AND evidence_hash=? AND reviewed_by IS NULL AND decision IS NULL""",
            (json.dumps({'action':'superseded_by_saved_rematch',
                         'observation_id':oid,'canonical_ids':sorted(canonicals),
                         'old_gemini_result_not_identity_evidence':True}),
             row['id'],row['evidence_hash']))
        if changed.rowcount!=1:
            raise ValueError('Review changed during rematch; retry.')
        superseded+=1
    # Clean only machine-suggested, unapproved pending links belonging to this
    # source product. Old reviewed/accepted/rejected decisions remain intact.
    conn.execute("""DELETE FROM product_identity_links
        WHERE source_id=? AND method='requires_review'
        AND status='pending' AND reviewed_by IS NULL
        AND (source_product_id=? OR source_product_id LIKE ?)""",
        (original.source_id,original.source_product_id,original.source_product_id+':%'))
    return {'canonical_ids':sorted(canonicals),'new':sum(x['status']=='provisional' for x in outputs),
            'matched':sum(x['status']=='matched' for x in outputs),
            'review_cases_superseded':superseded,
            'legacy_listings_relinked':linked,'legacy_links_preserved':preserved}


def rematch_saved_reviews(conn,limit=100,progress=None,dry_run=False,expected_fingerprint=None,
                          should_cancel=None):
    from pipeline.catalog_reset import guard_catalog_writes
    guard_catalog_writes(conn)
    grouped,excluded=_eligible(conn,limit)
    if expected_fingerprint and _fingerprint(grouped)!=expected_fingerprint:
        raise ValueError('Review evidence changed since preview. Refresh before applying.')
    counts=Counter()
    errors=Counter()
    samples=[]
    cache={}
    for oid,reviews in grouped.items():
        if should_cancel and should_cancel():break
        conn.execute('SAVEPOINT rematch_saved_source')
        try:
            output=_apply_one(conn,oid,reviews,cache)
            counts['recovered_products']+=1
            counts.update({k:int(v) for k,v in output.items() if k!='canonical_ids'})
            if dry_run:
                conn.execute('ROLLBACK TO SAVEPOINT rematch_saved_source')
            conn.execute('RELEASE SAVEPOINT rematch_saved_source')
        except Exception as exc:
            conn.execute('ROLLBACK TO SAVEPOINT rematch_saved_source')
            conn.execute('RELEASE SAVEPOINT rematch_saved_source')
            counts['failed_products']+=1
            reason=type(exc).__name__+': '+str(exc)[:180]
            errors[reason]+=1
            if len(samples)<12:samples.append({'observation_id':oid,'error_type':type(exc).__name__,
                                               'message':str(exc)[:180]})
            logger.warning('Saved identity rematch skipped observation %s: %s',oid,type(exc).__name__)
        counts['processed']+=1
        if progress:progress(dict(counts),oid)
    counts['eligible_products']=len(grouped)
    for key in ('recovered_products','failed_products','review_cases_superseded',
                'new','matched','processed','legacy_listings_relinked','legacy_links_preserved'):
        counts.setdefault(key,0)
    return {**counts,'dry_run':dry_run,'excluded_reasons':excluded,
            'error_groups':[{'reason':e,'count':n} for e,n in errors.most_common(10)],
            'error_samples':samples,'provider_calls':0,'source_observations_deleted':0,
            'published_inci':0}
