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


def _normalized_title(title):
    import unicodedata
    return re.sub(r'\s+',' ',re.sub(
        r'[^a-z0-9%+]+',' ',unicodedata.normalize('NFKD',str(title or '').lower())
        .encode('ascii','ignore').decode('ascii'))).strip()


def _variant_marker(title):
    """Formulation-defining groups only; pack size and bundles do NOT create products."""
    t=_normalized_title(title)
    labels=[]
    m=re.search(r'\bacti\s+(clear|repair|white)\b',t)
    if m:labels.append('acti_'+m[1])
    if re.search(r'\bprodigieuse\b',t):
        if re.search(r'\bflorale\b',t):labels.append('prodigieuse_florale')
        elif re.search(r'\b(?:golden|or)\b',t):labels.append('prodigieuse_gold')
        else:labels.append('prodigieuse_original')
    m=re.search(r'\bspf\s*(\d{2,3})\b',t)
    if m:labels.append('spf_'+m[1])
    if re.search(r'\b(?:untinted|invisible)\b',t):labels.append('untinted')
    elif re.search(r'\b(?:tinted|teinte)\b',t):labels.append('tinted')
    if re.search(r'\bfragrance free\b|\bunscented\b',t):labels.append('unscented')
    if re.search(r'\blavender\b|\blavande\b',t):labels.append('lavender')
    if re.search(r'\b(?:night|nuit)\b',t):labels.append('night')
    if re.search(r'\b(?:day|jour)\b',t):labels.append('day')
    m=re.search(r'\b(\d+(?:\.\d+)?)\s*%',t)
    if m:labels.append('active_'+m[1])
    # Delivery form wins over words like "lotion" in "lotion spray".
    # Different package sizes and 1+1 promotions remain retail offers.
    if re.search(r'\b(?:sunscreen|sunblock|sun screen|sun protection)\b',t):
        if re.search(r'\b(?:spray|spritz|mist)\b',t):labels.append('delivery_spray')
        elif re.search(r'\b(?:lotion)\b',t):labels.append('delivery_lotion')
    return tuple(sorted(labels))


def _product_family(title,brand):
    """Exact residual product line after removing *only* recognized variant + pack labels.

    Identity matching is intentionally conservative: unknown marketing variants
    are NOT deleted. Cross-family product listings cannot be merged just because
    Gemini returned a high-confidence mixed-cluster verdict.
    """
    t=_normalized_title(title)
    b=_normalized_title(brand)
    if b:
        t=re.sub(r'\b'+re.escape(b)+r'\b',' ',t)
    t=re.sub(r'\b(?:\d+\s*[x×]\s*)?\d+(?:\.\d+)?\s*(?:ml|g|gm|kg|l|oz)\b',' ',t)
    # Sales packs, not formula changes: 1+1, twin/duo pack, two-for-one.
    t=re.sub(r'\b\d+\s*\+\s*\d+\b',' ',t)
    t=re.sub(r'\b(?:twin|duo|value)\s*pack\b',' ',t)
    t=re.sub(r'\b(?:pack|bundle)\s+of\s+\d+\b',' ',t)
    if re.search(r'\bprodigieuse\b',t):
        t=re.sub(r'\b(?:florale|golden|or)\b',' ',t)
    if re.search(r'\bacti\s+(?:clear|repair|white)\b',t):
        t=re.sub(r'\bacti\s+(?:clear|repair|white)\b',' ',t)
    if re.search(r'\b(?:sunscreen|sunblock|sun screen|sun protection)\b',t):
        t=re.sub(r'\b(?:spray|spritz|mist|lotion)\b',' ',t)
    t=re.sub(r'\bspf\s*\d{2,3}\b',' ',t)
    t=re.sub(r'\b(?:untinted|tinted|teinte|invisible|unscented|fragrance free|lavender|lavande)\b',' ',t)
    t=re.sub(r'\b(?:night|nuit|day|jour)\b',' ',t)
    t=re.sub(r'\b\d+(?:\.\d+)?\s*%\b',' ',t)
    return ' '.join(sorted(t.split()))


def plan_distinct_split(conn,case,answer):
    """One canonical per evidenced formula group; NEVER one per retailer listing."""
    if (case['kind']!='identity' or answer['verdict']!='different_product'
        or not case['canonical_product_id']
        or case['details'].get('canonical_ids',[case['canonical_product_id']]) != [case['canonical_product_id']]):
        return None,'Canonical duplicate cases require separate identity evidence.'
    cid=case['canonical_product_id']
    canonical=conn.execute('SELECT * FROM canonical_products WHERE id=?',(cid,)).fetchone()
    if not canonical or canonical['identity_status'] not in ('legacy','legacy_preserved_unverified','verified'):
        return None,'Canonical record missing or identity state changed.'
    c=dict(canonical)
    if case['details'].get('title')!=c['canonical_title']:
        return None,'Canonical title changed since the stored Gemini decision.'
    offers=_all_current_offers(conn,cid,case['details'].get('offers') or [])
    if not offers:
        return None,'Current store listings no longer match the reviewed evidence or lack independent sources.'
    if not all(equivalent_brand(conn,c['brand'],o['brand']) for o in offers):
        return None,'Unverified brand aliases prevent automatic regrouping.'
    # Avoid partial reassignment of independently mapped variants.
    if conn.execute('SELECT 1 FROM product_offers WHERE canonical_product_id=? AND legacy_product_id IS NULL LIMIT 1',
                    (cid,)).fetchone():
        return None,'Independent variant offers require verified per-variant mapping.'
    anchor=_variant_marker(c['canonical_title'])
    family=_product_family(c['canonical_title'],c['brand'])
    if not anchor or not family:
        return None,'Canonical title lacks an explicit variant and identifiable product family.'
    groups={}
    for offer in offers:
        key=_variant_marker(offer['title'])
        current_family=_product_family(offer['title'],offer['brand'])
        if not key or current_family!=family:
            return None,'A retailer listing has an unknown variant or different product family; cannot auto-partition.'
        groups.setdefault(key,[]).append(offer)
    if len(groups)<2 or anchor not in groups:
        return None,'Multiple distinct explicit formulation groups or retained canonical anchor could not be established.'
    # Every group must have mutually compatible titles, not only a shared marker.
    for key,members in groups.items():
        for a in members:
            for b in members:
                compatible,_=validate_identity_gates(a['title'],b['title'])
                if not compatible:
                    return None,'A proposed group has incompatible formulation/variant identity evidence.'
            if a.get('ingredient_source') in ('store_page_tab','historical_store_scrape','curated_v10','admin_override'):
                pass
        fingerprints=set()
        if key==anchor:
            fingerprint=formula_fingerprint(c)
            if fingerprint:fingerprints.add(fingerprint)
        for item in members:
            if item.get('ingredient_source') in ('store_page_tab','historical_store_scrape','curated_v10','admin_override'):
                fp=formula_fingerprint(item)
                if fp:fingerprints.add(fp)
        if len(fingerprints)>1:
            return None,'Credible complete INCI lists conflict within a proposed identity group.'
    if conn.execute("""SELECT 1 FROM product_review_queue WHERE status='pending'
        AND canonical_product_id=? AND kind='duplicate' AND id!=? LIMIT 1""",
        (cid,case['id'])).fetchone():
        return None,'Competing canonical duplicate review remains unresolved.'
    moves=[]
    for key,members in sorted(groups.items()):
        if key==anchor:continue
        moves.append({'variant':key,'product_ids':[o['id'] for o in members],
                      'title':members[0]['title'],'retailer_count':len(members)})
    if not moves:return None,'No separate formulation group to move.'
    return {
        'canonical_id':cid,'original_variant':anchor,
        'keep_ids':[o['id'] for o in groups[anchor]],
        'moves':moves,
        'group_count':len(groups),
        'groups':[{'variant':list(key),'product_ids':[o['id'] for o in members],
                   'stores':sorted({o['store_id'] for o in members}),
                   'retained':key==anchor}
                  for key,members in sorted(groups.items())],
    },'Confirmed '+str(len(groups))+' formulation groups across '+str(len(offers))+' retailer listings; one canonical per group, not one per listing.'


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
            proposed_plan=None
            canonical_count_before=None
            if row['verdict']=='different_product':
                # Count actual inserts for *every* applied split, not only the
                # first 25 sample cases shown in the preview.
                canonical_count_before=conn.execute(
                    'SELECT COUNT(*) FROM canonical_products').fetchone()[0]
                if len(examples)<25:
                    proposed_plan,_=plan_distinct_split(conn,_case(row),_answer(row))
            result,reason=resolve_saved_case(conn,row,threshold)
            created_canonicals=0
            if result=='split':
                created_canonicals=(conn.execute(
                    'SELECT COUNT(*) FROM canonical_products').fetchone()[0]
                    - canonical_count_before)
                if created_canonicals<1:
                    raise ValueError('Split did not create a new canonical identity.')
            if result=='manual' and not dry_run:
                # Preserve Gemini's original verdict/explanation and record the
                # current deterministic reason; a historical 0.97 rejection is
                # replaced by the latest actual blocker.
                current_reason=_answer(row)['reason']
                conn.execute("""UPDATE identity_gemini_queue
                    SET explanation=?,updated_at=CURRENT_TIMESTAMP
                    WHERE review_id=? AND status='needs_manual'""",
                    ((current_reason+' | '+reason)[:800],row['id']))
            if dry_run:
                conn.execute('ROLLBACK TO SAVEPOINT saved_gemini_case')
            conn.execute('RELEASE SAVEPOINT saved_gemini_case')
            stats[result]+=1
            if result=='split':
                stats['new_canonical_products']+=created_canonicals
            if len(examples)<25:
                example={'review_id':row['id'],'verdict':row['verdict'],
                         'confidence':row['confidence'],'action':result,'reason':reason}
                if proposed_plan:
                    example['proposed_groups']=proposed_plan['groups']
                    example['new_canonical_products']=created_canonicals
                examples.append(example)
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
            'stored_decisions_checked':stats['verified']+stats['split']+stats['manual'],
            'automatically_verified':stats['verified'],
            'automatically_split':stats['split'],
            'new_canonical_products':stats['new_canonical_products'],
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
