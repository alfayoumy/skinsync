"""Gemini review of canonical product identity, separate from the INCI Gemini queue.

The LLM is a candidate evaluator; it cannot merge canonical entities, change offers,
approve brand aliases, or write ingredient formulas. Auto-approval is guarded by
deterministic checks and fully audited.
"""
import json
import os
import urllib.request
import urllib.error
from collections import Counter

from pipeline.brand_registry import CURATED_BRANDS,brand_key,resolve_brand
from pipeline.canonical_identity_resolver import (
    CanonicalIdentityResolver,extract_identity,compare_attributes,identity_name
)
from pipeline.identity_gates import validate_identity_gates
from pipeline.identity_review import formula_fingerprint
from pipeline.db_manager import get_connection
from pipeline.task_tracker import update_task_progress,complete_task,fail_task

POLICY_VERSION='gemini_identity_v1'
AUTO_THRESHOLD=0.90
DEFAULT_BATCH=1
TRUSTED_INCI={'store_page_tab','historical_store_scrape','curated_v10','admin_override'}
GEMINI_VERDICTS={'same_product','different_product','uncertain'}


class GeminiIdentityRequestError(RuntimeError):
    """Provider failure with a credential-free diagnostic safe for task UI."""
    def __init__(self,reason):
        self.safe_reason=reason
        super().__init__(reason)


def _provider_failure_reason(error):
    detail=''
    if error.code==429:
        try:
            body=json.loads(error.read(20000))
            limits=[str(v.get('quotaId','')).casefold()
                for d in body.get('error',{}).get('details',[]) if isinstance(d,dict)
                for v in d.get('violations',[]) if isinstance(v,dict)]
            if any('perday' in q for q in limits):detail=' (daily provider quota)'
            elif any('perminute' in q for q in limits):detail=' (per-minute provider quota)'
        except (ValueError,TypeError,AttributeError):pass
    return f'Gemini returned HTTP {error.code}{detail}; unfinished pairs remain queued.'


def _brand_family(brand):
    key=brand_key(brand)
    for name,aliases in CURATED_BRANDS.items():
        if key in {brand_key(x) for x in [name]+aliases}:
            return brand_key(name)
    return None


def equivalent_brand(conn,left,right):
    a,b=resolve_brand(left,conn),resolve_brand(right,conn)
    if not a or not b:return False
    if a.get('id') and a['id']==b.get('id'):
        return True
    # An explicitly curated alias is acceptable when legacy exact-brand
    # records produced different internal IDs for the same known brand.
    family_a,family_b=_brand_family(left),_brand_family(right)
    return bool(family_a and family_a==family_b)



def recover_abandoned_cases(conn):
    """Reset cases owned by terminated workers, but never steal an active batch."""
    cursor=conn.execute("""UPDATE identity_gemini_queue SET status='queued',
        updated_at=CURRENT_TIMESTAMP WHERE status='processing'
        AND (task_id IS NULL OR NOT EXISTS(
            SELECT 1 FROM pipeline_tasks t
            WHERE t.task_id=identity_gemini_queue.task_id AND t.status='running'))""")
    return cursor.rowcount

def _source_case_current(conn, case):
    """A saved-source prompt must still match its immutable observation and live candidates."""
    rid = case['review_id'] if 'review_id' in case.keys() else case['id']
    entry = conn.execute("""SELECT evidence_hash,payload_json
        FROM identity_gemini_source_cases WHERE review_id=?""", (rid,)).fetchone()
    if not entry or entry['evidence_hash'] != case['evidence_hash']:
        return None
    try:
        data = json.loads(entry['payload_json'])
        source = data['source']
        candidates = data['candidates']
        if (data.get('mode') != 'saved_source_identity_candidates_v1'
            or not isinstance(candidates, list) or not 1 <= len(candidates) <= 20):
            return None
        observed = conn.execute("""SELECT source_id,source_product_id,source_url,
            raw_title,raw_brand FROM source_product_observations WHERE id=?""",
            (data['observation_id'],)).fetchone()
        if (not observed or observed['source_id'] != source['source_id']
            or observed['source_product_id'] != source['source_product_id']
            or observed['source_url'] != source['url']
            or observed['raw_title'] != source['title']
            or observed['raw_brand'] != source['brand']):
            return None
        latest = conn.execute("""SELECT MAX(id) FROM source_product_observations
            WHERE source_id=? AND source_product_id=?""",
            (source['source_id'], source['source_product_id'])).fetchone()[0]
        if latest != data['observation_id']:
            return None
        for item in candidates:
            cid = item['canonical_product_id']
            row = conn.execute("""SELECT canonical_title,brand,identity_status,identity_attributes
                FROM canonical_products WHERE id=? AND NOT EXISTS(
                    SELECT 1 FROM canonical_redirects WHERE canonical_product_id=?)""",
                (cid,cid)).fetchone()
            if (not row or row['canonical_title'] != item['canonical_title']
                or row['brand'] != item['brand']
                or (row['identity_status'] != item['identity_status']
                    and not (row['identity_status']=='verified' and item['identity_status'] in ('provisional','legacy','legacy_preserved_unverified')))
                or json.loads(row['identity_attributes'] or '{}') != item['identity_attributes']):
                return None
            live_offers=[dict(x) for x in conn.execute("""SELECT source_id AS retailer,
                title FROM product_offers WHERE canonical_product_id=? ORDER BY id LIMIT 8""",(cid,))]
            live_legacy=[dict(x) for x in conn.execute("""SELECT store_id AS retailer,
                COALESCE(original_title,title) AS title FROM products
                WHERE canonical_product_id=? ORDER BY id LIMIT 8""",(cid,))]
            live_links=conn.execute("""SELECT COUNT(*) FROM product_identity_links
                WHERE canonical_product_id=? AND status='accepted'""",(cid,)).fetchone()[0]
            # Applying another decision may ADD compatible listings or promote
            # trust. That does not invalidate the evidence Gemini actually saw.
            # Changed/deleted reviewed listings still invalidate the snapshot.
            from collections import Counter as ListingCounter
            old_listings=ListingCounter((x['retailer'],x['title']) for x in item['retailer_listings'])
            all_listings=[dict(x) for x in conn.execute('SELECT source_id retailer,title FROM product_offers WHERE canonical_product_id=?',(cid,))]
            all_listings += [dict(x) for x in conn.execute('SELECT store_id retailer,COALESCE(original_title,title) title FROM products WHERE canonical_product_id=?',(cid,))]
            current_listings=ListingCounter((x['retailer'],x['title']) for x in all_listings)
            if (old_listings-current_listings or live_links < item['accepted_source_link_count']):
                return None
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return data


def _actionable_review(conn,case):
    """Gemini can compare only real, fully identified cross-retailer listings.

    Fresh discovery often creates review cases for a single unknown candidate
    with canonical_product_id=NULL. Such cases have no canonical identity or
    independent offer snapshot: a Gemini verdict would be a guess, not a match.
    """
    if case['kind']=='identity' and not case['canonical_product_id']:
        source=_source_case_current(conn,case)
        if source is None:return False
        from pipeline.identity_pairs import product_pairs
        try:return bool(product_pairs({**dict(case),'source_comparison':source}))
        except ValueError:return False
    if case['kind'] != 'identity' or not case['canonical_product_id']:
        return False
    try:
        details=json.loads(case['details'] or '{}')
    except (TypeError,ValueError):
        return False
    cid=case['canonical_product_id']
    if details.get('canonical_ids',[cid]) != [cid]:
        return False
    row=conn.execute("""SELECT canonical_title,brand FROM canonical_products WHERE id=?""",(cid,)).fetchone()
    if not row or details.get('title') != row['canonical_title'] or not details.get('brand'):
        return False
    offers=details.get('offers')
    if not isinstance(offers,list) or len(offers)<2:
        return False
    if len({x.get('store_id') for x in offers if isinstance(x,dict)})<2:
        return False
    if any(not isinstance(x,dict) or type(x.get('product_id')) is not int
           or not x.get('title') or not x.get('brand') or not x.get('store_id')
           for x in offers):
        return False
    return _all_current_offers(conn,cid,offers) is not None


def retire_unresearchable_queue(conn):
    """Preserve prior queue records while preventing wasted Gemini requests."""
    rows=conn.execute("""SELECT q.review_id,r.kind,r.canonical_product_id,r.details,r.evidence_hash
        FROM identity_gemini_queue q JOIN product_review_queue r ON r.id=q.review_id
        WHERE q.status='queued' AND r.status='pending'""").fetchall()
    retired=0
    for row in rows:
        if _actionable_review(conn,row):continue
        updated=conn.execute("""UPDATE identity_gemini_queue SET status='needs_manual',
            explanation='Insufficient comparable retailer evidence; Gemini not called.',
            verdict=NULL,confidence=NULL,model_name=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE review_id=? AND status='queued'""",(row['review_id'],))
        retired+=updated.rowcount
    return retired


def queue_cases(conn,review_ids=None,limit=20000,queued_by='admin'):
    if limit<1 or limit>20000:raise ValueError('Limit must be between 1 and 20000 cases.')
    recover_abandoned_cases(conn)
    retired=retire_unresearchable_queue(conn)
    if review_ids is not None and (not review_ids or len(review_ids)>20000):
        raise ValueError('Provide 1–20000 review IDs.')
    query="""SELECT r.id,r.evidence_hash,r.kind,r.canonical_product_id,r.details FROM product_review_queue r
        LEFT JOIN identity_gemini_queue q ON q.review_id=r.id
        WHERE r.status='pending' AND r.kind IN ('identity','duplicate')
        AND (q.review_id IS NULL OR q.status='queued')"""
    args=[]
    if review_ids is not None:
        query+=' AND r.id IN ('+','.join('?' for _ in review_ids)+')'
        args.extend(review_ids)
    # Invalid review cases can vastly outnumber usable snapshots. Scan all
    # eligible records, then cap actual Gemini *candidates*, not SQL rows.
    query+=' ORDER BY r.id'
    candidates=conn.execute(query,args).fetchall()
    actionable=[row for row in candidates if _actionable_review(conn,row)]
    rows=actionable[:limit]
    for row in rows:
        conn.execute("""INSERT INTO identity_gemini_queue(review_id,evidence_hash,status,queued_by)
            VALUES (?,?,'queued',?) ON CONFLICT(review_id) DO UPDATE SET
            evidence_hash=excluded.evidence_hash,status='queued',
            queued_by=excluded.queued_by,updated_at=CURRENT_TIMESTAMP""",
            (row['id'],row['evidence_hash'],queued_by))
    return {'queued':len(rows),'review_ids':[row['id'] for row in rows[:20]],
            'skipped_unresearchable':len(candidates)-len(actionable),
            'retired_unresearchable_queued':retired}


def gemini_queue_readiness(conn):
    """Read-only count of usable versus impossible-to-ground queued cases."""
    rows=conn.execute("""SELECT r.id,r.evidence_hash,r.kind,r.canonical_product_id,r.details
        FROM identity_gemini_queue q JOIN product_review_queue r ON r.id=q.review_id
        WHERE q.status='queued' AND r.status='pending'""").fetchall()
    valid=sum(bool(_actionable_review(conn,row)) for row in rows)
    return {'queued_actionable':valid,
            'queued_without_comparable_evidence':len(rows)-valid,
            'note':'Gemini requires a current saved source/candidate comparison or a current canonical listing comparison. Cases without comparison evidence need collection or review.'}


def queue_counts(conn):
    return {row['status']:row['n'] for row in conn.execute(
        'SELECT status,COUNT(*) n FROM identity_gemini_queue GROUP BY status')}


def _batch_prompt(cases):
    from pipeline.identity_pairs import pair_prompt
    return pair_prompt(cases)


def call_gemini_identity_batch(cases):
    """Exactly one pair (two products) per API request; legacy function name."""
    from pipeline.identity_pairs import pair_prompt
    prompt=pair_prompt(cases)
    from pipeline.inci_matcher import gemini_limiter,_load_env
    from pipeline.config import cfg
    _load_env()
    key=os.environ.get('GEMINI_API_KEY') or getattr(cfg,'GEMINI_API_KEY','')
    if not key:raise RuntimeError('GEMINI_API_KEY is not configured; queue remains pending.')
    if not gemini_limiter.acquire():raise RuntimeError('Gemini quota unavailable; queue remains pending.')
    model=os.environ.get('GEMINI_MODEL_NAME') or getattr(cfg,'GEMINI_MODEL_NAME','gemini-3.5-flash-lite')
    model=model.removeprefix('models/')
    # Avoid putting the credential in logged exceptions.
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}'
    payload={'contents':[{'parts':[{'text':prompt}]}],
             'generationConfig':{'temperature':0,'responseMimeType':'application/json'}}
    req=urllib.request.Request(url,data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=getattr(cfg,'GEMINI_TIMEOUT_SECONDS',35)) as resp:
            result=json.loads(resp.read().decode('utf-8'))
        text=result['candidates'][0]['content']['parts'][0]['text']
        parsed=json.loads(text)
        if isinstance(parsed,dict):parsed=parsed.get('results',[parsed])
        if not isinstance(parsed,list) or len(parsed)!=1:raise ValueError('Exactly one pair result is required.')
        ids={c['id'] for c in cases}
        output={}
        for entry in parsed:
            if not isinstance(entry,dict):continue
            cid=entry.get('review_id',cases[0]['id'])
            if type(cid) is not int or cid not in ids or cid in output:continue
            verdict=entry.get('verdict')
            confidence=entry.get('confidence')
            reason=entry.get('reason')
            if (verdict not in GEMINI_VERDICTS or type(confidence) not in (float,int)
                or not 0<=confidence<=1 or not isinstance(reason,str) or not reason.strip()):
                continue
            case=next(c for c in cases if c['id']==cid)
            if entry.get('pair_key') is not None and entry['pair_key']!=case['pair_key']:
                raise ValueError('Pair response belongs to a different comparison.')
            matched=case.get('pair_canonical_id') if verdict=='same_product' else None
            output[cid]={'verdict':verdict,'confidence':float(confidence),
                         'reason':reason.strip()[:800],'model_name':model,
                         'matched_canonical_id':matched}
        return output
    except urllib.error.HTTPError as exc:
        raise GeminiIdentityRequestError(_provider_failure_reason(exc)) from exc
    except (TimeoutError,urllib.error.URLError) as exc:
        raise GeminiIdentityRequestError('Gemini connection failed or timed out; unfinished pairs remain queued.') from exc
    except Exception as exc:
        raise GeminiIdentityRequestError('Gemini returned an invalid pair response; unfinished pairs remain queued.') from exc


def _all_current_offers(conn,cid,declared):
    actual=[dict(r) for r in conn.execute("""SELECT id,store_id,COALESCE(original_title,title) title,
        COALESCE(original_brand,brand) brand,store_product_id,ingredients_raw,ingredient_source
        FROM products WHERE canonical_product_id=? ORDER BY id""",(cid,))]
    if not actual:return None
    if len({o['store_id'] for o in actual}) < 2:return None
    declared_ids={x.get('product_id') for x in declared if isinstance(x,dict)}
    if {o['id'] for o in actual}!=declared_ids:
        return None
    for item in declared:
        row=next((x for x in actual if x['id']==item.get('product_id')),None)
        if not row or item.get('title')!=row['title'] or item.get('brand')!=row['brand']:
            return None
    return actual


def can_auto_resolve(conn,case,answer,threshold=None):
    """A model 'yes' is necessary but never sufficient for automatic approval."""
    if (case['kind']!='identity' or answer['verdict']!='same_product'
        or answer['confidence']<(AUTO_THRESHOLD if threshold is None else threshold)):
        return False,'Model verdict is uncertain, negative, or below automatic threshold.'
    details=case['details']
    cid=case['canonical_product_id']
    if not cid or details.get('canonical_ids',[cid]) != [cid]:
        return False,'Multiple canonical products require reviewed merge/split.'
    c=conn.execute('SELECT * FROM canonical_products WHERE id=?',(cid,)).fetchone()
    if not c:return False,'Canonical record missing.'
    c=dict(c)
    if c['identity_status'] not in ('legacy','legacy_preserved_unverified','provisional','verified'):
        return False,'Identity state changed.'
    if details.get('title')!=c['canonical_title']:
        return False,'Canonical title changed since review case creation.'
    offers=_all_current_offers(conn,cid,details.get('offers') or [])
    if offers is None:return False,'Current retailer listings differ from the reviewed snapshot.'
    for x in offers:
        if not equivalent_brand(conn,c['brand'],x['brand']):
            return False,'Brand names are not verified aliases.'
        a=extract_identity(x['title'],x['brand'])
        b=extract_identity(c['canonical_title'],c['brand'])
        if compare_attributes(a,b)['conflicts']:
            return False,'Conflicting formulation attributes.'
        compatible,_=validate_identity_gates(x['title'],c['canonical_title'])
        if not compatible:return False,'Hard product-variant gate rejected a listing.'
    # If a canonical has multiple competing identity-review findings, a
    # single model response is insufficient to approve the entire cluster.
    others=conn.execute("""SELECT COUNT(*) FROM product_review_queue WHERE status='pending'
        AND canonical_product_id=? AND kind IN ('identity','duplicate') AND id!=?""",
        (cid,case['id'])).fetchone()[0]
    if others:return False,'Other unresolved identity or duplicate evidence exists.'
    identities=conn.execute("""SELECT id,canonical_title,brand FROM canonical_products
        WHERE id!=? AND is_skincare=1
        AND NOT EXISTS(SELECT 1 FROM canonical_redirects WHERE canonical_product_id=canonical_products.id)""",
        (cid,)).fetchall()
    normalized=identity_name(c['canonical_title'],c['brand'])
    for other in identities:
        if equivalent_brand(conn,c['brand'],other['brand']) and identity_name(other['canonical_title'],other['brand'])==normalized:
            return False,'Another canonical has the same marketed identity.'
    hashes={v for v in [formula_fingerprint(c)] if v}
    for o in offers:
        if o.get('ingredient_source') in TRUSTED_INCI:
            f=formula_fingerprint(o)
            if f:hashes.add(f)
    if len(hashes)>1:return False,'Credible full INCI declarations conflict.'
    return True,'Verified aliases, compatible identity attributes and consistent INCI evidence.'


def _load_cases(conn,limit,batch_size,review_id=None):
    rows=conn.execute("""SELECT r.*,q.evidence_hash queued_evidence FROM identity_gemini_queue q
        JOIN product_review_queue r ON r.id=q.review_id
        WHERE q.status='queued' AND r.status='pending' AND (? IS NULL OR r.id=?) ORDER BY r.id LIMIT ?""",
        (review_id,review_id,min(limit,batch_size))).fetchall()
    cases=[]
    for row in rows:
        case=dict(row,details=json.loads(row['details']))
        source_record=(conn.execute('SELECT 1 FROM identity_gemini_source_cases WHERE review_id=?',
                                    (case['id'],)).fetchone()
                       if case['canonical_product_id'] is None else None)
        source=_source_case_current(conn,case) if source_record else None
        if source_record and source is None:
            # Never fall back to the legacy empty-list prompt if the saved
            # source or candidate changed after the queue was prepared.
            conn.execute("""UPDATE identity_gemini_queue
                SET status='needs_manual',
                explanation='Saved-source comparison changed; regenerate evidence before Gemini.',
                updated_at=CURRENT_TIMESTAMP WHERE review_id=? AND status='queued'""",
                (case['id'],))
            continue
        if source is not None:
            case['source_comparison']=source
        cases.append(case)
    return cases


def run_identity_gemini_queue(task_id,limit=500,batch_size=DEFAULT_BATCH,provider=None,review_id=None):
    """One pair per request; limit bounds requests, and completed pairs resume."""
    if batch_size!=1:raise ValueError('Gemini identity matching supports one two-product pair per request only.')
    provider=provider or call_gemini_identity_batch
    counts=Counter()
    calls=0
    scanned=0
    while calls<limit and scanned<20000:
        conn=get_connection()
        try:
            recover_abandoned_cases(conn)
            task=conn.execute('SELECT status FROM pipeline_tasks WHERE task_id=?',(task_id,)).fetchone()
            if task and task[0]=='cancelled':return {'status':'cancelled',**dict(counts)}
            retire_unresearchable_queue(conn)
            cases=_load_cases(conn,1,1,review_id=review_id)
            if not cases:
                conn.commit()
                break
            for case in cases:
                conn.execute("UPDATE identity_gemini_queue SET status='processing',attempts=attempts+1,task_id=?,updated_at=CURRENT_TIMESTAMP WHERE review_id=?",
                             (task_id,case['id']))
            conn.commit()
        finally:conn.close()
        try:
            from pipeline.identity_pairs import compare_case_pairs
            def pair_cancelled():
                check=get_connection()
                try:
                    state=check.execute('SELECT status FROM pipeline_tasks WHERE task_id=?',(task_id,)).fetchone()
                    return bool(state and state[0]=='cancelled')
                finally:check.close()
            answer,pair_calls=compare_case_pairs(cases[0],provider,get_connection,limit-calls,pair_cancelled)
            calls+=pair_calls
            if answer is None:
                pending=get_connection()
                try:
                    pending.execute("UPDATE identity_gemini_queue SET status='queued' WHERE review_id=? AND status='processing'",(cases[0]['id'],))
                    pending.commit()
                finally:pending.close()
                stopped=pair_cancelled()
                result={'status':'cancelled' if stopped else 'completed','processed':scanned,
                        'api_calls':calls,'products_per_request':2,'pending_partial_case':cases[0]['id'],**dict(counts)}
                update_task_progress(task_id,processed_items=calls,
                    current_stage='Pair request limit reached; unfinished review remains queued.',metadata=result)
                if not stopped:complete_task(task_id,metadata=result)
                return result
            answers={cases[0]['id']:answer}
        except Exception as error:
            calls+=getattr(error,'identity_pair_calls',0)
            reason=getattr(error,'safe_reason','Pair comparison could not complete; unfinished pairs remain queued.')
            conn=get_connection()
            try:
                for case in cases:
                    conn.execute("""UPDATE identity_gemini_queue SET status='queued',
                        explanation=?,updated_at=CURRENT_TIMESTAMP WHERE review_id=?""",
                        (reason,case['id']))
                conn.commit()
            finally:conn.close()
            fail_task(task_id,reason+' Completed pair results and applied decisions were preserved.',
                      metadata={'stats':dict(counts),'api_calls':calls,'products_per_request':2})
            return {'status':'failed','error':reason,'api_calls':calls,'products_per_request':2,**dict(counts)}
        conn=get_connection()
        try:
            state=conn.execute('SELECT status FROM pipeline_tasks WHERE task_id=?',(task_id,)).fetchone()
            if state and state[0]=='cancelled':
                for case in cases:
                    conn.execute("UPDATE identity_gemini_queue SET status='queued' WHERE review_id=?",(case['id'],))
                conn.commit()
                return {'status':'cancelled',**dict(counts)}
            for case in cases:
                scanned+=1
                answer=answers.get(case['id'])
                live=conn.execute('SELECT status,evidence_hash,kind,canonical_product_id,details FROM product_review_queue WHERE id=?',(case['id'],)).fetchone()
                if not live or live['status']!='pending' or live['evidence_hash']!=case['evidence_hash']:
                    outcome='needs_manual';reason='Case changed during Gemini evaluation.'
                elif case.get('source_comparison') and not _source_case_current(conn,case):
                    outcome='needs_manual';reason='Current source/canonical identity evidence changed during evaluation.'
                elif not answer:
                    outcome='needs_manual';reason='Model omitted the case or returned invalid structured fields.'
                elif case.get('source_comparison'):
                    from pipeline.saved_gemini_decisions import apply_pair_negatives
                    counts['pair_rejections']+=apply_pair_negatives(conn,case,AUTO_THRESHOLD)
                    # Persist the answer first; the shared source policy below
                    # then applies supported semantic decisions atomically.
                    outcome='needs_manual'
                    reason=('Individual pair results do not establish a unique supported source match.'
                            if answer['verdict']=='uncertain' else
                            'Pair confidence is below the automatic application threshold.'
                            if answer['confidence']<AUTO_THRESHOLD else
                            'Pairwise verdict is ready for source identity application.')
                    suggested=answer.get('matched_canonical_id')
                    allowed={x['canonical_product_id'] for x in
                             case['source_comparison']['candidates']}
                    if (answer['verdict']!='same_product' or
                        type(suggested) is not int or suggested not in allowed):
                        suggested=None
                    conn.execute("""UPDATE identity_gemini_source_cases
                        SET suggested_canonical_id=?,updated_at=CURRENT_TIMESTAMP
                        WHERE review_id=?""",(suggested,case['id']))
                else:
                    ok,reason=can_auto_resolve(conn,case,answer)
                    outcome='auto_resolved' if ok else 'needs_manual'
                if outcome=='auto_resolved':
                    cid=case['canonical_product_id']
                    old=conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(cid,)).fetchone()[0]
                    resolver=CanonicalIdentityResolver(conn)
                    for offer in _all_current_offers(conn,cid,case['details']['offers']):
                        resolver.record_link(offer['store_id'],str(offer['store_product_id'] or offer['id']),cid,
                            'accepted','gemini_guarded_identity',{'policy':POLICY_VERSION,
                            'review_id':case['id'],'product_id':offer['id'],
                            'model_confidence':answer['confidence']},reviewer='gemini_identity_policy')
                    conn.execute("UPDATE canonical_products SET identity_status='verified',updated_at=CURRENT_TIMESTAMP WHERE id=?",(cid,))
                    reason=(answer['reason'][:400]+' | '+reason)[:800]
                    conn.execute("""INSERT INTO canonical_identity_history(operation,canonical_product_id,target_id,
                        before_state,after_state,reviewed_by,reason) VALUES (?,?,?,?,?,?,?)""",
                        ('gemini_identity_verify',cid,None,json.dumps({'identity_status':old,'review_id':case['id']}),
                         json.dumps({'identity_status':'verified','confidence':answer['confidence']}),
                         'gemini_identity_policy',reason))
                    conn.execute("""UPDATE product_review_queue SET status='confirmed',decision=?,reviewed_by='gemini_identity_policy',
                        review_notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (json.dumps({'action':'gemini_auto_verify','confidence':answer['confidence'],
                                     'policy':POLICY_VERSION,'model':answer.get('model_name')}),reason,case['id']))
                conn.execute("""UPDATE identity_gemini_queue SET status=?,verdict=?,confidence=?,
                    explanation=?,model_name=?,updated_at=CURRENT_TIMESTAMP WHERE review_id=?""",
                    (outcome,answer['verdict'] if answer else None,
                     answer['confidence'] if answer else None,
                     (answer['reason'][:400]+' | '+reason)[:800] if answer else reason,
                     answer.get('model_name') if answer else None,case['id']))
                if answer:
                    conn.execute("UPDATE identity_gemini_queue SET comparison_mode='pairwise' WHERE review_id=?",(case['id'],))
                if case.get('source_comparison') and answer and outcome=='needs_manual':
                    from pipeline.saved_gemini_decisions import candidate_rows,resolve_saved_case
                    saved=candidate_rows(conn,threshold=AUTO_THRESHOLD,review_id=case['id'])
                    if saved:
                        conn.execute('SAVEPOINT apply_live_source_verdict')
                        try:
                            action,application_reason=resolve_saved_case(conn,saved[0],AUTO_THRESHOLD)
                            if action in ('source_linked','source_rejected'):
                                outcome='auto_resolved'
                            else:
                                conn.execute('UPDATE identity_gemini_queue SET explanation=? WHERE review_id=?',
                                    ((answer['reason'][:400]+' | '+application_reason)[:800],case['id']))
                            conn.execute('RELEASE SAVEPOINT apply_live_source_verdict')
                        except (ValueError,KeyError,TypeError) as error:
                            conn.execute('ROLLBACK TO SAVEPOINT apply_live_source_verdict')
                            conn.execute('RELEASE SAVEPOINT apply_live_source_verdict')
                            conn.execute('UPDATE identity_gemini_queue SET explanation=? WHERE review_id=?',
                                ((answer['reason'][:400]+' | Application failed: '+str(error)[:250])[:800],case['id']))
                counts[outcome]+=1
            conn.commit()
        except Exception as exc:
            conn.rollback()
            fail_task(task_id,'Product identity batch could not be persisted.',
                      metadata={'stats':dict(counts),'api_calls':calls})
            raise
        finally:conn.close()
        update_task_progress(task_id,processed_items=calls,
            successful_items=counts['auto_resolved'],skipped_items=counts['needs_manual'],
            current_stage=f'Gemini pair comparisons: {calls}',metadata={'stats':dict(counts),'api_calls':calls,'products_per_request':2})
    complete_task(task_id,metadata={'stats':dict(counts),'api_calls':calls,
                                    'processed':scanned,'batch_size':1,'products_per_request':2})
    return {'status':'completed','processed':scanned,'api_calls':calls,'products_per_request':2,**dict(counts)}
