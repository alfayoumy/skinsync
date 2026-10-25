"""Triage unsupported single-candidate identity suggestions without false matches.

Weak similarity and an unobserved vehicle are not evidence to link products,
nor a reason to create potentially duplicate canonicals or demand thousands of
human decisions. Defer *only* suggestions with demonstrably missing hard
identifiers, retain the source record, and allow evidence enrichment later.
No product/canonical/offer/INCI record is modified here.
"""
import hashlib
import json

CONFIRMATION='DEFER WEAK MATCH SUGGESTIONS'
MAX_BATCH=10000


def is_weak_identity_suggestion(details,kind='identity',canonical_id=None):
    if kind!='identity' or canonical_id is not None or not isinstance(details,dict):
        return False
    decision=details.get('identity')
    if not isinstance(decision,dict) or decision.get('status')!='ambiguous' or decision.get('method')!='review_required':
        return False
    if decision.get('canonical_product_id') is not None:
        return False
    candidates=decision.get('candidates')
    if not isinstance(candidates,list) or len(candidates)!=1:
        return False
    c=candidates[0]
    if not isinstance(c,dict) or type(c.get('canonical_product_id')) is not int:
        return False
    if c.get('state')!='unknown' or c.get('conflicts') not in ([],):
        return False
    if not c.get('unknown') or not isinstance(c['unknown'],list):
        return False
    if any(c.get(flag) is not False for flag in ('durable_link','exact_name',
                                                'reviewed_link','strong_identifier','verified_name')):
        return False
    try:
        score=float(c['similarity'])
    except (KeyError,TypeError,ValueError):
        return False
    # A high-similarity unresolved variant deserves targeted conflict review;
    # similarity alone is never a strong identity identifier.
    if not 0<=score<0.8:
        return False
    return (type(details.get('observation_id')) is int and
            bool(str(details.get('title') or '').strip()) and
            bool(str(details.get('raw_brand') or '').strip()) and
            bool(str(details.get('source_url') or '').startswith('https://')))


def _eligible_rows(conn,limit=MAX_BATCH):
    rows=conn.execute("""SELECT r.id,r.evidence_hash,r.kind,r.canonical_product_id,
        r.details,r.source_id,r.source_product_id,r.reviewed_by,r.decision,
        q.status queue_status,q.attempts queue_attempts,q.verdict queue_verdict,
        q.model_name queue_model
        FROM product_review_queue r LEFT JOIN identity_gemini_queue q ON q.review_id=r.id
        WHERE r.status='pending' AND r.kind='identity'
        AND r.canonical_product_id IS NULL ORDER BY r.id""").fetchall()
    result=[]
    for row in rows:
        d=dict(row)
        if d['reviewed_by'] or d['decision']:
            continue
        # Do not reclassify a case already evaluated or in flight with Gemini.
        if d['queue_status'] in ('processing','auto_resolved') or d['queue_verdict'] is not None or d['queue_model'] is not None or (d['queue_attempts'] or 0)>0:
            continue
        try:
            payload=json.loads(d['details'])
        except (ValueError,TypeError):
            continue
        if not is_weak_identity_suggestion(payload,d['kind'],d['canonical_product_id']):
            continue
        obs=conn.execute("""SELECT source_id,source_url,raw_title,raw_brand FROM source_product_observations
            WHERE id=?""",(payload['observation_id'],)).fetchone()
        if not obs or obs['source_id']!=d['source_id'] or obs['source_url']!=payload['source_url'] or obs['raw_title']!=payload['title']:
            continue
        if not conn.execute("SELECT id FROM canonical_products WHERE id=?",
            (payload['identity']['candidates'][0]['canonical_product_id'],)).fetchone():
            continue
        result.append(d)
        if len(result)>=limit:break
    return result


def preview_weak_suggestions(conn,limit=MAX_BATCH):
    rows=_eligible_rows(conn,limit)
    fingerprint=hashlib.sha256(json.dumps(
        [(r['id'],r['evidence_hash']) for r in rows],separators=(',',':')).encode()).hexdigest()
    total_pending=conn.execute("""SELECT COUNT(*) FROM product_review_queue
        WHERE status='pending'""").fetchone()[0]
    return {'eligible':len(rows),'total_pending':total_pending,
            'fingerprint':fingerprint,'limit':limit,
            'sample':[{'review_id':r['id'],'source':r['source_id'],
                       'source_product_id':r['source_product_id']}
                      for r in rows[:15]],
            'confirmation_phrase':CONFIRMATION,
            'note':'Moves only weak, unevaluated single-candidate identity suggestions out of the human queue. Does not create/merge/link products, change formula or category, or delete any observation. Cases can be retrieved in the Awaiting stronger evidence view.'}


def defer_weak_suggestions(conn,fingerprint,confirmation,limit=MAX_BATCH):
    if confirmation!=CONFIRMATION:
        raise ValueError('Type DEFER WEAK MATCH SUGGESTIONS to confirm.')
    conn.execute('SAVEPOINT defer_weak_identity')
    try:
        active=conn.execute("""SELECT task_id FROM pipeline_tasks
            WHERE status IN ('pending','running') AND task_type IN
            ('catalog_discovery','product_identity_gemini','apply_saved_gemini',
             'canonical_no_comparison_recovery')
            LIMIT 1""").fetchone()
        if active:
            raise ValueError('An identity/discovery worker is active; finish or cancel it before triaging.')
        rows=_eligible_rows(conn,limit)
        actual=hashlib.sha256(json.dumps(
            [(r['id'],r['evidence_hash']) for r in rows],separators=(',',':')).encode()).hexdigest()
        if actual!=fingerprint:
            raise ValueError('Pending review evidence changed since preview; refresh.')
        changed=0
        for item in rows:
            updated=conn.execute("""UPDATE product_review_queue SET status='deferred',
                review_notes='Automated classification: weak single-candidate suggestion without verified product identity; awaiting independent evidence.',
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'
                AND evidence_hash=? AND reviewed_by IS NULL AND decision IS NULL""",
                (item['id'],item['evidence_hash']))
            changed+=updated.rowcount
            if updated.rowcount and item['queue_status']=='queued':
                conn.execute("""UPDATE identity_gemini_queue SET status='needs_manual',
                    explanation='Deferred weak evidence suggestion; Gemini was not called.',
                    updated_at=CURRENT_TIMESTAMP WHERE review_id=? AND status='queued' AND attempts=0
                    AND verdict IS NULL AND model_name IS NULL""",(item['id'],))
        conn.execute('RELEASE SAVEPOINT defer_weak_identity')
        return {'deferred':changed,'products_modified':0,'formulations_modified':0,
                'observations_deleted':0,'model_calls':0}
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT defer_weak_identity')
        conn.execute('RELEASE SAVEPOINT defer_weak_identity')
        raise
