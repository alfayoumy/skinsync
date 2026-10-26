"""Archive unresolved group verdicts and prepare individual two-product requests."""
import json
from collections import Counter
from pipeline.identity_pairs import ensure_pair_schema,product_pairs
from pipeline.source_gemini_requeue import _fresh_payload,_scoped_payload,_profile
from pipeline.saved_identity_rematch import _from_frozen_observation
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity


def prepare_identity_pairs(conn,apply=False,review_ids=None,refresh_pairwise=False):
    if refresh_pairwise and (not review_ids or len(set(review_ids))>100):
        raise ValueError('Pairwise refresh requires one to 100 explicit review IDs.')
    ensure_pair_schema(conn)
    selection=("q.comparison_mode='pairwise'" if refresh_pairwise else
        "q.comparison_mode='legacy_group' AND NOT EXISTS(SELECT 1 FROM identity_gemini_pair_results p WHERE p.review_id=r.id AND p.evidence_hash=r.evidence_hash)")
    rows=conn.execute("""SELECT r.* FROM product_review_queue r JOIN identity_gemini_queue q ON q.review_id=r.id
        WHERE r.status='pending' AND q.status IN ('needs_manual','queued')
        AND r.reviewed_by IS NULL AND r.decision IS NULL AND """+selection+" ORDER BY r.id").fetchall()
    wanted=set(review_ids) if review_ids is not None else None
    counts=Counter();examples=[];cache={};fresh_cache={}
    for raw in rows:
        case=dict(raw)
        if wanted is not None and case['id'] not in wanted:continue
        case['details']=json.loads(case['details'])
        if refresh_pairwise and case['canonical_product_id'] is not None:
            counts['canonical_case_preserved']+=1;continue
        if case['canonical_product_id'] is None:
            old=conn.execute('SELECT * FROM identity_gemini_source_cases WHERE review_id=?',(case['id'],)).fetchone()
            if not old:
                counts['missing_saved_comparison']+=1;continue
            original=json.loads(old['payload_json'])
            oid=original['observation_id']
            obs=_from_frozen_observation(conn,oid,cache)
            latest=conn.execute('SELECT MAX(id) FROM source_product_observations WHERE source_id=? AND source_product_id=?',
                (obs.source_id,obs.source_product_id)).fetchone()[0]
            if latest!=oid:
                counts['changed_source_observation']+=1;continue
            if oid not in fresh_cache:fresh_cache[oid]=_fresh_payload(conn,oid,[case],cache)
            fresh=fresh_cache[oid]
            payload=fresh or original
            choices={c['canonical_product_id']:c for c in payload['candidates']}
            for candidate in original['candidates']:
                profile=_profile(conn,candidate)
                if profile:choices[profile['canonical_product_id']]=profile
            attrs=extract_identity(obs.raw_title,obs.raw_brand,obs.market,obs.identity_attributes)
            scope=_scoped_payload(payload,case['source_product_id'])
            if scope is None:
                counts['unidentified_variant']+=1;continue
            attrs.update(scope.get('review_scope',{}).get('variant_attributes') or {})
            candidates=[]
            if fresh is None:
                decision=CanonicalIdentityResolver(conn).resolve(attrs,obs.raw_brand,obs.raw_title,
                    obs.source_id,case['source_product_id'],obs.source_gtin,obs.manufacturer_sku)
                candidates=list(decision.candidates)
                if decision.canonical_product_id:candidates.append({'canonical_product_id':decision.canonical_product_id})
            for candidate in candidates:
                profile=_profile(conn,candidate)
                if profile:choices[profile['canonical_product_id']]=profile
            if not choices or len(choices)>20:
                counts['no_bounded_current_comparison']+=1;continue
            scope['candidates']=sorted(choices.values(),key=lambda x:x['canonical_product_id'])
            case['source_comparison']=scope
        try:pairs=product_pairs(case)
        except ValueError:
            counts['unscoped_product_family']+=1;continue
        if not pairs:
            counts['no_product_pair']+=1;continue
        if refresh_pairwise:
            existing={r['pair_key']:r['verdict'] for r in conn.execute(
                'SELECT pair_key,verdict FROM identity_gemini_pair_results WHERE review_id=? AND evidence_hash=?',
                (case['id'],case['evidence_hash']))}
            new_keys={p['pair_key'] for p in pairs}-set(existing)
            if not new_keys:
                counts['unchanged_comparisons_preserved']+=1;continue
            counts['new_pairs_prepared']+=len(new_keys)
            counts['completed_pairs_retained']+=sum(existing.get(p['pair_key']) is not None for p in pairs)
        counts['reviews_prepared']+=1;counts['pairs_prepared']+=len(pairs)
        if len(examples)<10 or case['id']==5226:
            examples.append({'review_id':case['id'],'pair_count':len(pairs),
                'pairs':[p['pair_products'] for p in pairs]})
        if not apply:continue
        queue=dict(conn.execute('SELECT * FROM identity_gemini_queue WHERE review_id=?',(case['id'],)).fetchone())
        source=conn.execute('SELECT * FROM identity_gemini_source_cases WHERE review_id=?',(case['id'],)).fetchone()
        conn.execute('INSERT INTO identity_gemini_legacy_history(review_id,queue_json,source_case_json) VALUES (?,?,?)',
            (case['id'],json.dumps(queue),json.dumps(dict(source)) if source else None))
        if source:
            conn.execute('UPDATE identity_gemini_source_cases SET payload_json=?,suggested_canonical_id=NULL,updated_at=CURRENT_TIMESTAMP WHERE review_id=?',
                (json.dumps(case['source_comparison'],ensure_ascii=False),case['id']))
        conn.execute("UPDATE identity_gemini_queue SET status='queued',task_id=NULL,updated_at=CURRENT_TIMESTAMP WHERE review_id=?",(case['id'],))
        if refresh_pairwise:
            conn.execute("""UPDATE identity_gemini_queue SET verdict=NULL,confidence=NULL,model_name=NULL,
                explanation='Current candidate evidence refreshed; completed pair answers retained.' WHERE review_id=?""",(case['id'],))
        for pair in pairs:
            conn.execute('''INSERT OR IGNORE INTO identity_gemini_pair_results
                (review_id,pair_key,evidence_hash,products_json,canonical_candidate_id) VALUES (?,?,?,?,?)''',
                (case['id'],pair['pair_key'],case['evidence_hash'],json.dumps(pair['pair_products'],ensure_ascii=False),pair['pair_canonical_id']))
    return {'applied':apply,'products_per_request':2,'provider_calls':0,**dict(counts),'examples':examples}
