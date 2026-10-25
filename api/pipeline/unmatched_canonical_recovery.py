"""Replay only old *no-candidate* observation cases into new provisional canonicals.

A review without an existing comparison is not an identity conflict. Only cases
whose original resolver reported unknown brand/new identity and zero rival
candidates qualify. Existing true conflicts and past human decisions are never
auto-closed. Does not rescrape, infer INCI or approve product identity.
"""
import json
import re
import logging
from collections import Counter

logger = logging.getLogger(__name__)

from pipeline.aggregation_service import process_source_observation
from pipeline.catalog_selective_rebuild import _parse_saved
from pipeline.catalog_reset import _active_tasks, _active_workers, _tables, guard_catalog_writes

ELIGIBLE_METHODS={'unknown_brand','new_identity'}
REPAIR_ORIGIN='new_canonical_no_existing_candidates_v1'


def _recoverable(case):
    try:
        evidence=json.loads(case['details'] or '{}')
    except (ValueError,TypeError):
        return None
    identity=evidence.get('identity')
    if not isinstance(identity,dict):
        return None
    if identity.get('candidates') not in (None,[]):
        return None
    if identity.get('method') not in ELIGIBLE_METHODS:
        return None
    if identity.get('status') not in ('ambiguous','new'):
        return None
    oid=evidence.get('observation_id')
    if not isinstance(oid,int) or oid<1:
        return None
    return oid


def _cases(conn,limit=None):
    cases=conn.execute("""SELECT id,kind,source_id,source_product_id,details
        FROM product_review_queue WHERE status='pending'
        AND canonical_product_id IS NULL AND kind IN ('identity','category')
        ORDER BY id""").fetchall()
    by_observation={}
    for case in cases:
        oid=_recoverable(case)
        if oid is None:
            continue
        saved=conn.execute("""SELECT id,source_id,source_product_id,raw_brand
            FROM source_product_observations WHERE id=?""",(oid,)).fetchone()
        if not saved or not saved['raw_brand'] or not saved['raw_brand'].strip():
            continue
        if saved['source_id']!=case['source_id']:
            continue
        # Repair only the latest immutable snapshot for this source product.
        # A review from an older payload must never resurrect a superseded
        # title, identity or formulation variant.
        newest=conn.execute("""SELECT MAX(id) FROM source_product_observations
            WHERE source_id=? AND source_product_id=?""",
            (saved['source_id'],saved['source_product_id'])).fetchone()[0]
        if newest!=oid:
            continue
        by_observation.setdefault(oid,[]).append(dict(case))
    selected=list(by_observation.items())
    return selected if limit is None else selected[:limit]


def preview_unmatched_recovery(conn):
    tables=_tables(conn)
    cases=_cases(conn)
    active=_active_tasks(conn,tables)
    workers=_active_workers(conn,tables)
    locked=False
    if 'catalog_reset_control' in tables:
        row=conn.execute('SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone()
        locked=bool(row and row[0])
    counts=Counter(case['kind'] for _,cases_for_ob in cases for case in cases_for_ob)
    return {'eligible_observations':len(cases),
            'orphan_identity_reviews':counts.get('identity',0),
            'orphan_category_reviews':counts.get('category',0),
            'sample_observation_ids':[oid for oid,_ in cases[:15]],
            'active_tasks':active,'active_workers':workers,
            'maintenance_locked':locked,
            'can_start':bool(cases) and not active and not workers and not locked,
            'note':'Creates provisional canonical identities from existing source observations only; old no-candidate review cases are closed only after a successful exact-source replay. Real matching conflicts remain pending; no INCI is approved.'}


def _diagnostic(exc,stage):
    # Avoid leaking payload text, database values, tokens or product names to
    # UI; show only a concise, reproducible error class and stage. Common
    # deterministic SQLite errors are useful, but redact long raw values.
    message=str(exc).replace(chr(10),' ')
    message=re.sub(r'https?://\S+','[source-url]',message)
    message=re.sub(r'(?i)(key|token|secret|authorization|password)\s*[:=]\s*\S+',
                   r'\1=[redacted]',message)
    return {'stage':stage,'error_type':type(exc).__name__,
            'message':message[:220]}


def recover_unmatched_canonicals(conn,limit=200,progress=None,dry_run=False):
    if not 1<=limit<=500:
        raise ValueError('Recovery batch limit must be 1..500 saved source products.')
    guard_catalog_writes(conn)
    stats=Counter()
    errors=Counter()
    samples=[]
    selected=_cases(conn,limit)
    source_cache={}
    for oid,reviews in selected:
        conn.execute('SAVEPOINT recover_unmatched_observation')
        stage='parse_saved'
        try:
            observation_row=conn.execute('SELECT * FROM source_product_observations WHERE id=?',(oid,)).fetchone()
            observation=_parse_saved(conn,observation_row,source_cache)
            # Recalculate all matching, variants and categories from saved raw
            # Shopify evidence. Never blindly trust the stale review payload.
            stage='replay_observation'
            decision=process_source_observation(conn,observation,existing_observation_id=oid)
            stage='validate_identity_result'
            entries=decision['results']
            canonicals={r['canonical_product_id'] for r in entries if r.get('canonical_product_id')}
            if not canonicals or any(r['status']=='review' and r.get('kind')=='identity' for r in entries):
                raise ValueError('Re-evaluation still requires an actual identity comparison.')
            # Source link/variant offer binding is written by the ordinary
            # ingestion pipeline. Update the compatibility projection only
            # when the entire saved listing has exactly one canonical.
            stage='bind_legacy_listing'
            if len(canonicals)==1:
                cid=next(iter(canonicals))
                legacy=conn.execute("""SELECT id,canonical_product_id FROM products
                    WHERE store_id=? AND store_product_id=?""",
                    (observation.source_id,observation.source_product_id)).fetchone()
                if legacy:
                    if legacy['canonical_product_id'] not in (None,cid):
                        # A retailer product can expose several distinct
                        # formulation variants. The legacy one-canonical
                        # projection may already reference a different
                        # accepted variant. Never overwrite that assignment,
                        # and don't throw away the safe newly created variant.
                        stats['legacy_projection_preserved']+=1
                    else:
                        conn.execute("UPDATE products SET canonical_product_id=? WHERE id=?",(cid,legacy['id']))
                        conn.execute("""UPDATE product_offers SET legacy_product_id=?
                            WHERE source_id=? AND source_product_id=?""",
                            (legacy['id'],observation.source_id,observation.source_product_id))
            stage='reconcile_old_reviews'
            for row in reviews:
                if row['kind']=='category':
                    # A category question survives as an anchored review. The
                    # original saved case may have a variant-suffixed source
                    # product key, so *exactly* matching that key to the
                    # unsuffixed product link incorrectly rejected all items.
                    # Use source observation provenance and canonical ID.
                    newly_anchored=conn.execute("""SELECT 1 FROM product_review_queue
                        WHERE kind='category' AND status='pending'
                        AND canonical_product_id IN (SELECT value FROM json_each(?))
                        AND source_id=? AND id!=?
                        AND json_extract(details,'$.observation_id')=?
                        LIMIT 1""",(json.dumps(sorted(canonicals)),
                                     row['source_id'],row['id'],oid)).fetchone()
                    if newly_anchored:
                        conn.execute("""UPDATE product_review_queue
                            SET status='confirmed',decision=?,reviewed_by=?,
                                review_notes=?,updated_at=CURRENT_TIMESTAMP
                            WHERE id=? AND status='pending' AND canonical_product_id IS NULL""",
                            (json.dumps({'action':'replaced_by_anchored_category_review',
                                         'canonical_ids':sorted(canonicals),'observation_id':oid}),
                             REPAIR_ORIGIN,'Category review re-anchored during source replay.',
                             row['id']))
                        stats['closed_unanchored_reviews']+=1
                    elif len(canonicals)==1:
                        # Do not pretend category uncertainty is resolved.
                        # Attach the old review to the newly created canonical.
                        conn.execute("""UPDATE product_review_queue
                            SET canonical_product_id=?,review_notes=?,
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=? AND status='pending' AND canonical_product_id IS NULL""",
                            (next(iter(canonicals)),
                             'No-comparison recovery linked canonical; taxonomy remains pending.',
                             row['id']))
                        stats['anchored_category_reviews']+=1
                    else:
                        stats['preserved_category_reviews']+=1
                    continue
                conn.execute("""UPDATE product_review_queue
                    SET status='confirmed',decision=?,reviewed_by=?,
                        review_notes=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status='pending' AND canonical_product_id IS NULL""",
                    (json.dumps({'action':'recovered_unmatched_source_identity',
                                 'canonical_ids':sorted(canonicals),'observation_id':oid}),
                     REPAIR_ORIGIN,'No-existing-candidate review resolved by replaying saved source evidence.',
                     row['id']))
                stats['closed_unanchored_reviews']+=1
            stats['recovered_observations']+=1
            stats['linked_canonicals']+=len(canonicals)
            if dry_run:
                conn.execute('ROLLBACK TO SAVEPOINT recover_unmatched_observation')
            conn.execute('RELEASE SAVEPOINT recover_unmatched_observation')
        except Exception as exc:
            conn.execute('ROLLBACK TO SAVEPOINT recover_unmatched_observation')
            conn.execute('RELEASE SAVEPOINT recover_unmatched_observation')
            stats['still_unresolved']+=1
            diag=_diagnostic(exc,stage)
            key=diag['stage']+' / '+diag['error_type']+' / '+diag['message']
            errors[key]+=1
            if len(samples)<15:
                source=conn.execute('SELECT source_id FROM source_product_observations WHERE id=?',
                                    (oid,)).fetchone()
                samples.append({'observation_id':oid,
                    'source_id':source['source_id'] if source else None,**diag})
            logger.warning('Canonical replay rolled back for saved observation %s at %s: %s',
                           oid,stage,diag['error_type'])
        stats['processed']+=1
        if progress:
            progress(dict(stats),oid)
    stats['total_selected']=len(selected)
    stats['provider_calls']=0
    stats['saved_source_observations_deleted']=0
    report=dict(stats)
    for name in ('recovered_observations','linked_canonicals',
                 'closed_unanchored_reviews','anchored_category_reviews',
                 'preserved_category_reviews','legacy_projection_preserved',
                 'still_unresolved','processed')
        report.setdefault(name,0)
    report.update({'dry_run':dry_run,
        'failure_reasons':[{'reason':reason,'count':count}
                           for reason,count in errors.most_common(12)],
        'failure_samples':samples,
        'all_failed':bool(selected) and stats['recovered_observations']==0
                      and stats['still_unresolved']==len(selected)})
    return report
