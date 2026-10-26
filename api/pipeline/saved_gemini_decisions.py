"""Apply persisted Gemini identity verdicts. NEVER calls Gemini.

Different-product verdicts only split canonicals when source listings can be
partitioned by explicit variant discriminators and the current database still
matches the reviewed evidence. All changes are audited and idempotent.
"""
import json
import re
from collections import Counter

from pipeline.brand_registry import resolve_brand
from pipeline.canonical_identity_resolver import identity_name, extract_identity, compare_attributes, valid_gtin
from pipeline.identity_gates import validate_identity_gates
from pipeline.identity_review import formula_fingerprint
from pipeline.product_identity_gemini import (
    can_auto_resolve, _all_current_offers, _source_case_current, equivalent_brand, POLICY_VERSION,
)
from pipeline.review_service import split_canonical, resolve_review
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver
from pipeline.db_manager import get_connection
from pipeline.task_tracker import update_task_progress, complete_task, fail_task

MIN_CONFIDENCE=0.90
VERDICTS=('same_product','different_product')


def candidate_rows(conn,limit=5000,threshold=MIN_CONFIDENCE,review_id=None):
    """Only previously evaluated cases; do not requeue or rerun model."""
    from pipeline.identity_pairs import ensure_pair_schema
    ensure_pair_schema(conn)
    return conn.execute("""SELECT r.*,q.evidence_hash queued_hash,q.verdict,q.confidence,
        q.explanation,q.model_name,s.suggested_canonical_id,s.review_id source_case_id
        FROM product_review_queue r
        JOIN identity_gemini_queue q ON q.review_id=r.id
        LEFT JOIN identity_gemini_source_cases s ON s.review_id=r.id
        WHERE r.status='pending' AND q.status='needs_manual'
          AND q.comparison_mode='pairwise'
          AND q.confidence>=?
          AND ((s.review_id IS NULL AND q.verdict IN ('same_product','different_product'))
            OR (s.review_id IS NOT NULL AND (q.verdict='different_product' OR
                (q.verdict='same_product' AND s.suggested_canonical_id IS NOT NULL))
                AND r.reviewed_by IS NULL AND r.decision IS NULL))
        AND (? IS NULL OR r.id=?)
        ORDER BY r.id LIMIT ?""",(threshold,review_id,review_id,limit)).fetchall()


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


def _source_match_plan(conn,row,case,answer):
    """Independent checks before accepting a saved source-to-canonical suggestion.

    The LLM cannot force a merge: current evidence, retailer corroboration,
    specific formulation attributes and rival candidates must all agree.
    """
    if answer['verdict']!='same_product' or type(row['suggested_canonical_id']) is not int:
        return None,'No supported same-product canonical candidate was selected.'
    if case['kind']!='identity' or case['canonical_product_id'] is not None:
        return None,'This is not an unresolved retailer-source identity.'
    if case['reviewed_by'] or case['decision']:
        return None,'A prior human review or explicit decision must not be overridden.'
    source_payload=_source_case_current(conn,row)
    if source_payload is None:
        return None,'Saved source or canonical comparison changed since Gemini responded.'
    source=source_payload['source']
    cid=row['suggested_canonical_id']
    candidates=source_payload['candidates']
    profile=next((x for x in candidates if x['canonical_product_id']==cid),None)
    if profile is None:
        return None,'Suggested canonical is not in the saved comparison.'
    source_key=source['source_product_id']
    variants=source.get('variants') or []
    if len(variants)==1 and variants[0].get('attributes'):
        import hashlib
        variant_key=source_key+':'+hashlib.sha256(json.dumps(variants[0]['attributes'],sort_keys=True).encode()).hexdigest()[:16]
    else:
        variant_key=None
    if (case['source_id']!=source['source_id']
        or case['source_product_id'] not in (source_key,variant_key)):
        return None,'Saved result does not identify which shade or scent was reviewed. A variant-specific comparison is required.'
    canonical=conn.execute('SELECT * FROM canonical_products WHERE id=?',(cid,)).fetchone()
    if not canonical:
        return None,'Suggested canonical no longer exists.'
    canonical=dict(canonical)
    if canonical['identity_status'] not in ('legacy','legacy_preserved_unverified','provisional','verified'):
        return None,'Canonical identity state is not eligible.'
    if not equivalent_brand(conn,source['brand'],canonical['brand']):
        return None,'Source and canonical brands lack independently verified equivalence.'
    a=dict(source['identity_attributes'])
    b=json.loads(canonical['identity_attributes'] or '{}')
    if not b.get('name'):
        b=extract_identity(canonical['canonical_title'],canonical['brand'],
                           canonical.get('market'),b)
    scope=source_payload.get('review_scope') or {}
    if case['source_product_id']!=source_key and scope:
        if scope.get('source_product_id')!=case['source_product_id']:
            return None,'The saved variant scope does not match this review.'
        groups={json.dumps(json.loads(v[0]).get('attributes') or {},sort_keys=True)
                for v in conn.execute('SELECT attributes FROM source_product_variants WHERE observation_id=?',(source_payload['observation_id'],))}
        if len(groups)>1:
            from pipeline.taxonomy_service import normalize
            for key,value in (scope.get('variant_attributes') or {}).items():
                if key in ('shade','fragrance','tint','spf','concentrations','formula_revision') and value and not b.get(key):
                    if not isinstance(value,str) or not re.search(r'(?<!\w)'+re.escape(normalize(value))+r'(?!\w)',normalize(canonical['canonical_title'])):
                        return None,f'The selected product does not identify {key} {value} within this multi-variant product.'
    # Vehicle/function are inferred fields, not independent manufacturer facts.
    # Compare current rules on both titles; don't preserve stale taxonomy bugs
    # such as "body lotion" versus "body musk lotion" as identity conflicts.
    from pipeline.taxonomy_service import normalize
    for attrs,title,brand in ((a,source['title'],source['brand']),(b,canonical['canonical_title'],canonical['brand'])):
        fresh=extract_identity(title,brand)
        if fresh.get('vehicle'):attrs['vehicle']=fresh['vehicle']
        function=fresh.get('function')
        if function=='Body Care':
            text=normalize(title)
            if re.search(r'\b(?:wash|cleanser|cleansing|soap|shower)\b',text):function='Cleanser'
            elif re.search(r'\b(?:scrub|exfoliat\w*)\b',text):function='Exfoliator'
            elif re.search(r'\b(?:cream|creme|lotion|milk|butter|balm|baume)\b',text):function='Moisturizer'
        if function:attrs['function']=function
    comparison=compare_attributes(a,b)
    if comparison['conflicts']:
        return None,'Product attributes conflict: '+', '.join(f"{key} (source: {a.get(key)}; selected product: {b.get(key)})" for key in comparison['conflicts'])
    # Gemini adjudicates missing metadata and retailer wording differences.
    # Unknown attributes are not contradictory attributes.
    compatible,reason=validate_identity_gates(
        source['title'],canonical['canonical_title'],canonical['brand'],canonical['brand'])
    missing_only=reason and reason.startswith(('SPF / UV protection conflict','Anatomy conflict:'))
    if not compatible and not missing_only:
        return None,'Hard variant gate rejected the source/canonical comparison: '+str(reason)
    # Do not require the deterministic exact-name matcher to solve the same
    # case again. The stored model selected one candidate from this comparison.
    for variant in variants:
        variant_attrs=variant.get('attributes') or {}
        if (compare_attributes(a,{**a,**variant_attrs})['conflicts']
                or compare_attributes({**a,**variant_attrs},b)['conflicts']):
            return None,'Saved source has internally conflicting formulation variants.'
        if any(compare_attributes({**a,**variant_attrs},{**a,**(other.get('attributes') or {})})['conflicts'] for other in variants):
            return None,'The saved comparison contains multiple distinct shades or formulations and needs a separate verdict for each variant.'
    if case['source_product_id']==source_key:
        old=conn.execute("""SELECT canonical_product_id,status FROM product_identity_links
            WHERE source_id=? AND (source_product_id=? OR substr(source_product_id,1,length(?)+1)=?||':')
            AND status IN ('accepted','rejected')""",(source['source_id'],source_key,source_key,source_key)).fetchall()
    else:
        old=conn.execute("""SELECT canonical_product_id,status FROM product_identity_links
            WHERE source_id=? AND source_product_id IN (?,?) AND status IN ('accepted','rejected')""",
            (source['source_id'],source_key,case['source_product_id'])).fetchall()
    if any((x['status']=='accepted' and x['canonical_product_id']!=cid)
           or (x['status']=='rejected' and x['canonical_product_id']==cid) for x in old):
        return None,'An existing accepted/rejected source identity decision conflicts with Gemini.'
    other_source_reviews=conn.execute("""SELECT COUNT(*) FROM product_review_queue
        WHERE id!=? AND kind='identity' AND status='pending' AND source_id=?
          AND (source_product_id=? OR source_product_id LIKE ?)""",
        (case['id'],source['source_id'],case['source_product_id'],
         case['source_product_id']+':%')).fetchone()[0]
    if other_source_reviews:
        return None,'Other source-product variant reviews remain pending.'
    legacy=conn.execute("""SELECT canonical_product_id FROM products
        WHERE store_id=? AND store_product_id=?""",
        (source['source_id'],source['source_product_id'])).fetchone()
    if legacy and legacy['canonical_product_id'] not in (None,cid):
        return None,'Existing legacy product mapping needs independent review.'
    return {'canonical_id':cid},'Applied Gemini-selected canonical; current source evidence and brand agree, with no explicit variant or prior-decision conflict.'


def _materialize_source_match(conn,case,cid):
    """An applied match must populate offers and the legacy projection too."""
    from pipeline.saved_identity_rematch import _from_frozen_observation
    from pipeline.offer_service import upsert_offer,refresh_offer_aggregates
    from pipeline.source_registry import get_source,url_matches_source
    oid=case['details']['observation_id']
    observation=_from_frozen_observation(conn,oid,{})
    source=get_source(conn,observation.source_id)
    if not url_matches_source(source,observation.source_url):
        raise ValueError('Saved observation URL does not match the current source registry.')
    legacy=conn.execute('SELECT id,canonical_product_id FROM products WHERE store_id=? AND store_product_id=?',
        (observation.source_id,observation.source_product_id)).fetchone()
    if legacy and legacy['canonical_product_id'] not in (None,cid):
        raise ValueError('Existing legacy product mapping conflicts with the selected canonical.')
    import hashlib
    resolver=CanonicalIdentityResolver(conn)
    variants=observation.variants
    if case['source_product_id']!=observation.source_product_id:
        variants=[v for v in variants if v.attributes and observation.source_product_id+':'+hashlib.sha256(json.dumps(v.attributes,sort_keys=True).encode()).hexdigest()[:16]==case['source_product_id']]
        if not variants:raise ValueError('Reviewed source variant is missing from the retained observation.')
    for variant in variants:
        barcode=valid_gtin(variant.gtin)
        if barcode and conn.execute("SELECT 1 FROM canonical_product_aliases WHERE alias_type='gtin' AND alias_value=? AND verified=1 AND canonical_product_id!=?",
                                    (barcode,cid)).fetchone():
            raise ValueError('Verified variant barcode belongs to a different canonical product.')
        if variant.attributes:
            key=json.dumps(variant.attributes,sort_keys=True)
            source_key=observation.source_product_id+':'+hashlib.sha256(key.encode()).hexdigest()[:16]
            resolver.record_link(observation.source_id,source_key,cid,'accepted','gemini_saved_source_identity',
                {'review_id':case['id'],'observation_id':oid},reviewer='gemini_saved_source_policy')
        upsert_offer(conn,source,observation,variant,cid,oid,legacy['id'] if legacy else None,fields_filter=[])
    if legacy and len(variants)==len(observation.variants):
        conn.execute('UPDATE products SET canonical_product_id=? WHERE id=?',(cid,legacy['id']))
        conn.execute('UPDATE product_offers SET legacy_product_id=? WHERE source_id=? AND source_product_id=? AND canonical_product_id=?',
            (legacy['id'],observation.source_id,observation.source_product_id,cid))
    refresh_offer_aggregates(conn,cid)


def _apply_source_different(conn,row,case,answer):
    """A confident 'none of these candidates' is a useful negative decision."""
    if case['reviewed_by'] or case['decision']:
        return 'manual','A prior explicit decision must not be overridden.'
    payload=_source_case_current(conn,row)
    if not payload:
        return 'manual','Saved source or canonical comparison changed since Gemini responded.'
    source=payload['source']
    scoped=case['source_product_id']!=source['source_product_id']
    scope=payload.get('review_scope') or {}
    if (case['source_id']!=source['source_id'] or scoped and
            (scope.get('source_product_id')!=case['source_product_id'] or not scope.get('variant_attributes'))):
        return 'manual','Saved result does not identify which shade or scent was reviewed. A variant-specific comparison is required.'
    if conn.execute("SELECT 1 FROM product_identity_links WHERE source_id=? AND source_product_id IN (?,?) AND status='accepted'",
                    (case['source_id'],case['source_product_id'],source['source_product_id'])).fetchone():
        return 'manual','An accepted source identity decision must be preserved.'
    legacy=conn.execute('SELECT id,canonical_product_id FROM products WHERE store_id=? AND store_product_id=?',
                        (case['source_id'],source['source_product_id'])).fetchone()
    if legacy and legacy['canonical_product_id'] is not None:
        return 'manual','An existing product mapping must be reviewed before replacing it.'
    resolver=CanonicalIdentityResolver(conn)
    rejected=[x['canonical_product_id'] for x in payload['candidates']]
    from pipeline.saved_identity_rematch import _from_frozen_observation
    from pipeline.aggregation_service import process_source_observation
    import hashlib
    observation=_from_frozen_observation(conn,payload['observation_id'],{})
    full_variant_count=len(observation.variants)
    if scoped:
        from dataclasses import replace
        selected=[v for v in observation.variants if v.attributes and observation.source_product_id+':'+hashlib.sha256(json.dumps(v.attributes,sort_keys=True).encode()).hexdigest()[:16]==case['source_product_id']]
        if not selected:raise ValueError('Reviewed source variant is missing from the retained observation.')
        observation=replace(observation,variants=selected)
    keys={case['source_product_id']}
    for variant in observation.variants:
        if variant.attributes:
            key=json.dumps(variant.attributes,sort_keys=True)
            keys.add(observation.source_product_id+':'+hashlib.sha256(key.encode()).hexdigest()[:16])
    for key in keys:
        existing=conn.execute("SELECT 1 FROM product_identity_links WHERE source_id=? AND source_product_id=? AND status='accepted'",
                              (case['source_id'],key)).fetchone()
        if existing:raise ValueError('An existing accepted source variant must be preserved.')
        for cid in rejected:
            prior=conn.execute('SELECT status,reviewed_by FROM product_identity_links WHERE source_id=? AND source_product_id=? AND canonical_product_id=?',
                (case['source_id'],key,cid)).fetchone()
            if prior and prior['status']=='rejected' and prior['reviewed_by']:continue
            resolver.record_link(case['source_id'],key,cid,'rejected','gemini_saved_different_product',
                {'review_id':case['id'],'confidence':answer['confidence'],'model':answer['model_name']},reviewer='gemini_saved_source_policy')
    last_sync=conn.execute('SELECT last_successful_sync FROM sources WHERE id=?',(case['source_id'],)).fetchone()[0]
    result=process_source_observation(conn,observation,existing_observation_id=payload['observation_id'],replay_saved_projection=True,
        replay_variant_ids=[v.source_variant_id for v in observation.variants] if scoped else None,offer_fields=[])
    conn.execute('UPDATE sources SET last_successful_sync=? WHERE id=?',(last_sync,case['source_id']))
    outputs=result['results']
    if any(x['status'] not in ('provisional','matched','excluded') for x in outputs):
        raise ValueError('Another possible match appeared after this Gemini comparison; creating another product would need a new comparison.')
    ids={x['canonical_product_id'] for x in outputs if x.get('canonical_product_id')}
    if legacy and len(ids)==1 and len(observation.variants)==full_variant_count:
        cid=next(iter(ids))
        conn.execute('UPDATE products SET canonical_product_id=? WHERE id=?',(cid,legacy['id']))
        conn.execute('UPDATE product_offers SET legacy_product_id=? WHERE source_id=? AND source_product_id=?',
                     (legacy['id'],case['source_id'],case['source_product_id']))
    reason='Applied different-product verdict: rejected compared candidates and retained the distinct source product.'
    _close_decision(conn,case,answer,'reject_candidates',reason,{'rejected_canonical_ids':rejected,'canonical_ids':sorted(ids)})
    return 'source_rejected',reason

def apply_pair_negatives(conn,case,threshold=MIN_CONFIDENCE):
    """Apply each supported negative pair without requiring a positive winner."""
    from pipeline.identity_pairs import product_pairs
    if not case.get('source_comparison'):return 0
    changed=0
    for pair in product_pairs(case):
        stored=conn.execute("""SELECT confidence,model_name FROM identity_gemini_pair_results
            WHERE review_id=? AND pair_key=? AND evidence_hash=? AND verdict='different_product'
            AND confidence>=?""",(case['id'],pair['pair_key'],case['evidence_hash'],threshold)).fetchone()
        if not stored:continue
        target=pair['pair_canonical_id']
        old=conn.execute('SELECT status,reviewed_by FROM product_identity_links WHERE source_id=? AND source_product_id=? AND canonical_product_id=?',
            (case['source_id'],case['source_product_id'],target)).fetchone()
        if old and (old['status']=='accepted' or old['reviewed_by']):continue
        CanonicalIdentityResolver(conn).record_link(case['source_id'],case['source_product_id'],target,'rejected',
            'gemini_pairwise_different',{'review_id':case['id'],'pair_key':pair['pair_key'],
            'confidence':stored['confidence'],'model':stored['model_name']},reviewer='gemini_pairwise_identity_policy')
        changed+=1
    return changed


def resolve_saved_case(conn,row,threshold=MIN_CONFIDENCE):
    """Apply one stored verdict. Returns a durable outcome, never a model call."""
    case=_case(row)
    answer=_answer(row)
    if (case['status']!='pending' or row['queued_hash']!=case['evidence_hash']
        or answer['verdict'] not in VERDICTS or answer['confidence']<threshold):
        return 'manual','Stored decision is missing or its evidence has changed.'
    if row['source_case_id'] is not None:
        if answer['verdict']=='different_product':
            return _apply_source_different(conn,row,case,answer)
        plan,reason=_source_match_plan(conn,row,case,answer)
        if not plan:return 'manual',reason
        cid=plan['canonical_id']
        _close_decision(conn,case,answer,'link',reason,{'canonical_product_id':cid})
        payload=_source_case_current(conn,row)
        CanonicalIdentityResolver(conn).add_aliases(cid,payload['source']['identity_attributes'],case['source_id'])
        evidence={'review_id':case['id'],'model':answer['model_name'],'confidence':answer['confidence'],
                  'verdict':answer['verdict'],'policy':'gemini_semantic_source_v3'}
        CanonicalIdentityResolver(conn).record_link(case['source_id'],case['source_product_id'],cid,
            'accepted','gemini_saved_source_identity',evidence,reviewer='gemini_saved_source_policy')
        conn.execute('UPDATE product_review_queue SET decision=? WHERE id=?',
            (json.dumps({'action':'link','canonical_product_id':cid,**evidence}),case['id']))
        _materialize_source_match(conn,case,cid)
        apply_pair_negatives(conn,{**case,'source_comparison':payload},threshold)
        conn.execute("""UPDATE identity_gemini_queue SET status='auto_resolved',
            explanation=?,updated_at=CURRENT_TIMESTAMP WHERE review_id=? AND status='needs_manual'""",
            ((answer['reason']+' | '+reason)[:800],case['id']))
        return 'source_linked',reason
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


def _apply_saved_decisions(conn,limit=5000,threshold=MIN_CONFIDENCE,dry_run=False,
                          after_case=None,cancelled=None):
    if not 1<=limit<=20000:raise ValueError('Limit must be between 1 and 20000.')
    if not 0.9<=threshold<=1.0:raise ValueError('Confidence threshold must be between 0.90 and 1.0.')
    rows=candidate_rows(conn,limit,threshold)
    source_counts=conn.execute("""SELECT COUNT(*) total,
        SUM(CASE WHEN q.confidence>=? AND r.reviewed_by IS NULL AND r.decision IS NULL
            AND (q.verdict='different_product' OR (q.verdict='same_product' AND s.suggested_canonical_id IS NOT NULL))
            THEN 1 ELSE 0 END) eligible
        FROM product_review_queue r JOIN identity_gemini_queue q ON q.review_id=r.id
        JOIN identity_gemini_source_cases s ON s.review_id=r.id
        WHERE r.status='pending' AND q.status='needs_manual' AND q.comparison_mode='pairwise'
          AND q.evidence_hash=r.evidence_hash""",(threshold,)).fetchone()
    stats=Counter()
    blocked=Counter()
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
                if row['source_case_id'] is None and len(examples)<25:
                    proposed_plan,_=plan_distinct_split(conn,_case(row),_answer(row))
            result,reason=resolve_saved_case(conn,row,threshold)
            created_canonicals=0
            if result in ('split','source_rejected'):
                created_canonicals=(conn.execute(
                    'SELECT COUNT(*) FROM canonical_products').fetchone()[0]
                    - canonical_count_before)
                if result=='split' and created_canonicals<1:
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
            if result=='manual':blocked[reason]+=1
            if result in ('split','source_rejected'):
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
            blocked['Application could not be completed: '+str(exc)[:180]]+=1
            if len(examples)<25:
                examples.append({'review_id':row['id'],'action':'manual',
                                 'reason':'Automated checks could not safely apply this decision: '+str(exc)[:180]})
        if after_case and (i%25==0 or i==len(rows)):
            after_case(i,dict(stats))
    source_total=source_counts['total']
    source_eligible=source_counts['eligible'] or 0
    return {'dry_run':dry_run,'no_gemini_calls':True,'threshold':threshold,
            'source_verdicts_seen':source_total,
            'source_verdicts_eligible':source_eligible,
            'source_verdicts_not_eligible':source_total-source_eligible,
            'stored_decisions_checked':sum(stats[k] for k in ('verified','source_linked','source_rejected','split','manual')),
            'automatically_verified':stats['verified'],
            'source_matches_linked':stats['source_linked'],
            'source_comparisons_rejected':stats['source_rejected'],
            'automatically_split':stats['split'],
            'new_canonical_products':stats['new_canonical_products'],
            'still_needs_review':stats['manual'],
            'blocked_reasons':[{'reason':reason,'count':count} for reason,count in blocked.most_common()],
            'sample_results':examples,
            'message':'Saved decisions were replayed without any additional Gemini requests.'}


def apply_saved_decisions(conn,limit=5000,threshold=MIN_CONFIDENCE,dry_run=True,
                          after_case=None,cancelled=None):
    """Preview the whole batch in order, then roll back its complete state.

    Later decisions must see earlier accepted links and newly distinct products
    during preview just as they do during application. Never commit a preview
    through a progress callback.
    """
    if not dry_run:
        return _apply_saved_decisions(conn,limit,threshold,False,after_case,cancelled)
    if not conn.in_transaction:conn.execute('BEGIN')
    conn.execute('SAVEPOINT preview_saved_gemini_batch')
    try:
        result=_apply_saved_decisions(conn,limit,threshold,False,None,cancelled)
        result['dry_run']=True
        return result
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT preview_saved_gemini_batch')
        conn.execute('RELEASE SAVEPOINT preview_saved_gemini_batch')


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
                successful_items=sum(stats.get(k,0) for k in ('verified','source_linked','source_rejected','split')),
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
