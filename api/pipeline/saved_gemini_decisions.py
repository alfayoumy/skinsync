"""Apply persisted Gemini identity verdicts. NEVER calls Gemini.

Different-product verdicts only split canonicals when source listings can be
partitioned by explicit variant discriminators and the current database still
matches the reviewed evidence. All changes are audited and idempotent.
"""
import json
import re
from collections import Counter

from pipeline.brand_registry import resolve_brand
from pipeline.canonical_identity_resolver import identity_name
from pipeline.identity_gates import validate_identity_gates
from pipeline.identity_review import formula_fingerprint
from pipeline.product_identity_gemini import (
    can_auto_resolve, _all_current_offers, equivalent_brand, POLICY_VERSION,
)
from pipeline.review_service import split_canonical
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver
from pipeline.db_manager import get_connection
from pipeline.task_tracker import update_task_progress, complete_task, fail_task

MIN_CONFIDENCE=0.90
VERDICTS=('same_product','different_product')


def candidate_rows(conn,limit=5000,threshold=MIN_CONFIDENCE):
    """Only previously evaluated cases; do not requeue or rerun model."""
    return conn.execute("""SELECT r.*,q.evidence_hash queued_hash,q.verdict,q.confidence,
        q.explanation,q.model_name FROM product_review_queue r
        JOIN identity_gemini_queue q ON q.review_id=r.id
        WHERE r.status='pending' AND q.status='needs_manual'
          AND q.verdict IN ('same_product','different_product')
          AND q.confidence>=?
        ORDER BY r.id LIMIT ?""",(threshold,limit)).fetchall()


def _answer(row):
    # The historic worker appended the old 0.97 policy reason to the
    # original model explanation. Preserve the original verdict, don't
    # re-interpret the appended application message as Gemini evidence.
    reason=(row['explanation'] or '').split(' | ',1)[0]
    return {'verdict':row['verdict'],'confidence':row['confidence'],
            'reason':reason,'model_name':row['model_name']}


def _case(row):
    c=dict(row)
    try:c['details']=json.loads(c['details'])
    except (ValueError,TypeError):c['details']={}
    return c


def _variant_marker(title):
    """Only known explicit product/variant markers, not generic fuzzy tokens."""
    t=re.sub(r'[^a-z0-9+]+',' ',__import__('unicodedata').normalize('NFKD',title.lower())
             .encode('ascii','ignore').decode('ascii')).strip()
    labels=[]
    # Distinct branded lines: ACTI-CLEAR, ACTI-REPAIR, ACTI-WHITE.
    m=re.search(r'\bacti\s+(clear|repair|white)\b',t)
    if m:labels.append('acti_'+m[1])
    # Explicit NUXE perfume/colorway variants. "Or" denotes the gold edition
    # only in the context of Huile Prodigieuse, not the English conjunction.
    if 'prodigieuse' in t:
        if re.search(r'\bflorale\b',t):labels.append('prodigieuse_florale')
        elif re.search(r'\b(?:golden|or)\b',t):labels.append('prodigieuse_gold')
        else:labels.append('prodigieuse_original')
    m=re.search(r'\bspf\s*(\d{2,3})\b',t)
    if m:labels.append('spf_'+m[1])
    if re.search(r'\b(?:tinted|teinte)\b',t):labels.append('tinted')
    if re.search(r'\b(?:untinted|invisible)\b',t):labels.append('untinted')
    if re.search(r'\bfragrance free\b|\bunscented\b',t):labels.append('unscented')
    if re.search(r'\blavender\b|\blavande\b',t):labels.append('lavender')
    if re.search(r'\b(?:night|nuit)\b',t):labels.append('night')
    if re.search(r'\b(?:day|jour)\b',t):labels.append('day')
    m=re.search(r'\b(\d+(?:\.\d+)?)\s*%\b',t)
    if m:labels.append('active_'+m[1])
    return tuple(sorted(labels))


def plan_distinct_split(conn,case,answer):
    """Propose only deterministic, explicit variant splits; no fuzzy regrouping."""
    if (case['kind']!='identity' or answer['verdict']!='different_product'
        or not case['canonical_product_id']
        or case['details'].get('canonical_ids',[case['canonical_product_id']]) != [case['canonical_product_id']]):
        return None,'Canonical duplicate cases require separate merger/variant evidence.'
    cid=case['canonical_product_id']
    canonical=conn.execute('SELECT * FROM canonical_products WHERE id=?',(cid,)).fetchone()
    if not canonical or canonical['identity_status'] not in ('legacy','legacy_preserved_unverified','verified'):
        return None,'Canonical record missing or identity state changed.'
    c=dict(canonical)
    if case['details'].get('title')!=c['canonical_title']:
        return None,'Canonical title changed since the stored Gemini decision.'
    offers=_all_current_offers(conn,cid,case['details'].get('offers') or [])
    if not offers:
        return None,'The current store listings no longer match the reviewed snapshot.'
    if not all(equivalent_brand(conn,c['brand'],o['brand']) for o in offers):
        return None,'Unverified brand aliases prevent automatic offer reassignment.'
    # Never silently split between product and independent variant-offer rows
    # that cannot be reassigned with a known legacy product relationship.
    if conn.execute('SELECT 1 FROM product_offers WHERE canonical_product_id=? AND legacy_product_id IS NULL LIMIT 1',
                    (cid,)).fetchone():
        return None,'Independent variant offers require source-level mapping.'
    markers=_variant_marker(c['canonical_title'])
    if not markers:
        return None,'Canonical product does not identify a clear original variant.'
    groups={}
    for offer in offers:
        key=_variant_marker(offer['title'])
        if not key:
            return None,'Retailer title lacks a reliable explicit variant marker.'
        groups.setdefault(key,[]).append(offer)
    if len(groups)<2 or markers not in groups:
        return None,'Distinct variants cannot be partitioned with a canonical anchor.'
    # A canonical with a full INCI cannot be retained if its authoritative
    # exact formulation conflicts with the retained anchor group's credible INCI.
    retained=groups[markers]
    hashes={x for x in (formula_fingerprint(c),) if x}
    for offer in retained:
        if offer.get('ingredient_source') in (
            'store_page_tab','historical_store_scrape','curated_v10','admin_override'
        ):
            f=formula_fingerprint(offer)
            if f:hashes.add(f)
    if len(hashes)>1:
        return None,'The retained product has incompatible full INCI declarations.'
    for key,items in groups.items():
        if key==markers:continue
        # Do not infer that unknown marketing terms imply different formulas:
        # explicit marker differences plus Gemini's different verdict are required.
        if key==markers:return None,'No distinct variant discriminator.'
        for item in items:
            if not item['title'].strip():
                return None,'Missing product title.'
    others=conn.execute("""SELECT 1 FROM product_review_queue WHERE status='pending'
        AND canonical_product_id=? AND kind='duplicate' AND id!=? LIMIT 1""",
        (cid,case['id'])).fetchone()
    if others:return None,'A competing canonical duplicate review is still unresolved.'
    return {'canonical_id':cid,'original_variant':markers,
            'keep_ids':[x['id'] for x in retained],
            'moves':[{'variant':key,'product_ids':[x['id'] for x in group],
                      'title':group[0]['title']}
                     for key,group in groups.items() if key!=markers]},'Explicit variant groups are distinct.'


def _close_decision(conn,case,answer,action,reason,extras=None):
    doc={'action':action,'policy':'gemini_saved_identity_v2',
         'model_verdict':answer['verdict'],'confidence':answer['confidence'],
         'model':answer.get('model_name'),'source':'stored_gemini_decision',**(extras or {})}
    conn.execute("""UPDATE product_review_queue SET status='confirmed',decision=?,
        reviewed_by='gemini_saved_identity_policy',review_notes=?,updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='pending'""",(json.dumps(doc),reason,case['id']))
    conn.execute("""UPDATE identity_gemini_queue SET status='auto_resolved',
        explanation=?,updated_at=CURRENT_TIMESTAMP WHERE review_id=?""",
        ((answer['reason']+' | '+reason)[:800],case['id']))


def resolve_saved_case(conn,row,threshold=MIN_CONFIDENCE):
    """Apply one stored verdict. Returns a durable outcome, never a model call."""
    case=_case(row)
    answer=_answer(row)
    if (case['status']!='pending' or row['queued_hash']!=case['evidence_hash']
        or answer['verdict'] not in VERDICTS or answer['confidence']<threshold):
        return 'manual','Stored decision is missing or its evidence has changed.'
    if answer['verdict']=='same_product':
        ok,reason=can_auto_resolve(conn,case,answer,threshold=threshold)
        if not ok:return 'manual',reason
        cid=case['canonical_product_id']
        offers=_all_current_offers(conn,cid,case['details']['offers'])
        previous=conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(cid,)).fetchone()[0]
        resolver=CanonicalIdentityResolver(conn)
        for offer in offers:
            resolver.record_link(offer['store_id'],str(offer['store_product_id'] or offer['id']),cid,
                'accepted','gemini_saved_guarded_identity',
                {'policy':'gemini_saved_identity_v2','review_id':case['id'],
                 'product_id':offer['id'],'confidence':answer['confidence']},
                reviewer='gemini_saved_identity_policy')
        conn.execute("UPDATE canonical_products SET identity_status='verified',updated_at=CURRENT_TIMESTAMP WHERE id=?",(cid,))
        conn.execute("""INSERT INTO canonical_identity_history(operation,canonical_product_id,target_id,
            before_state,after_state,reviewed_by,reason) VALUES (?,?,?,?,?,?,?)""",
            ('gemini_saved_identity_verify',cid,None,
             json.dumps({'identity_status':previous,'review_id':case['id']}),
             json.dumps({'identity_status':'verified','confidence':answer['confidence']}),
             'gemini_saved_identity_policy',answer['reason'][:500]))
        _close_decision(conn,case,answer,'auto_verify',reason)
        return 'verified',reason

    plan,reason=plan_distinct_split(conn,case,answer)
    if not plan:return 'manual',reason
    created=[]
    for step in plan['moves']:
        result=split_canonical(conn,plan['canonical_id'],step['product_ids'],[],
            step['title'],{},'gemini_saved_identity_policy',
            'High-confidence stored Gemini different_product verdict; explicit variant '
            +str(step['variant'])+'; review #'+str(case['id']))
        created.append(result['new_canonical_id'])
    _close_decision(conn,case,answer,'auto_split',reason,
                    {'new_canonical_ids':created,'moved_variants':len(plan['moves'])})
    return 'split',reason


def apply_saved_decisions(conn,limit=5000,threshold=MIN_CONFIDENCE,dry_run=True,
                          after_case=None,cancelled=None):
    if not 1<=limit<=20000:raise ValueError('Limit must be between 1 and 20000.')
    if not 0.9<=threshold<=1.0:raise ValueError('Confidence threshold must be between 0.90 and 1.0.')
    rows=candidate_rows(conn,limit,threshold)
    stats=Counter()
    examples=[]
    for i,row in enumerate(rows,1):
        if cancelled and cancelled():break
        conn.execute('SAVEPOINT saved_gemini_case')
        try:
            result,reason=resolve_saved_case(conn,row,threshold)
            if dry_run:
                conn.execute('ROLLBACK TO SAVEPOINT saved_gemini_case')
            conn.execute('RELEASE SAVEPOINT saved_gemini_case')
            stats[result]+=1
            if len(examples)<25:
                examples.append({'review_id':row['id'],'verdict':row['verdict'],
                                 'confidence':row['confidence'],'action':result,'reason':reason})
        except (ValueError,KeyError,TypeError) as exc:
            conn.execute('ROLLBACK TO SAVEPOINT saved_gemini_case')
            conn.execute('RELEASE SAVEPOINT saved_gemini_case')
            stats['manual']+=1
            if len(examples)<25:
                examples.append({'review_id':row['id'],'action':'manual',
                                 'reason':'Automated checks could not safely apply this decision: '+str(exc)[:180]})
        if after_case and (i%25==0 or i==len(rows)):
            after_case(i,dict(stats))
    return {'dry_run':dry_run,'no_gemini_calls':True,'threshold':threshold,
            'stored_decisions_checked':sum(stats.values()),
            'automatically_verified':stats['verified'],
            'automatically_split':stats['split'],
            'still_needs_review':stats['manual'],
            'sample_results':examples,
            'message':'Saved decisions were replayed without any additional Gemini requests.'}


def background_apply_saved(task_id,limit=5000,threshold=MIN_CONFIDENCE):
    """Task worker; bounded batches committed with progress, no external LLM calls."""
    conn=get_connection()
    try:
        def cancelled():
            # Same connection sees task changes between commits.
            row=conn.execute("SELECT status FROM pipeline_tasks WHERE task_id=?",(task_id,)).fetchone()
            return bool(row and row[0]=='cancelled')
        def progress(n,stats):
            conn.commit()
            update_task_progress(task_id,processed_items=n,
                successful_items=stats.get('verified',0)+stats.get('split',0),
                skipped_items=stats.get('manual',0),current_stage='Applying saved Gemini verdicts',
                metadata={'no_gemini_calls':True,'stats':stats})
        result=apply_saved_decisions(conn,limit,threshold,dry_run=False,
                                     after_case=progress,cancelled=cancelled)
        conn.commit()
        if not cancelled():
            complete_task(task_id,metadata=result)
        return result
    except Exception:
        conn.rollback()
        fail_task(task_id,'Applying saved Gemini decisions failed. Committed checkpoints remain intact.')
        raise
    finally:
        conn.close()
