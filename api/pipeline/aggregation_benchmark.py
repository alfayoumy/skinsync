"""Deterministic gold-set evaluation and explicit source rollout gates."""
from collections import Counter
import hashlib
import json
from pipeline.taxonomy_service import classify_product_attributes


def evaluate_classification(gold):
    matrix=Counter();false_excluded=0;eligible_count=0
    records=[]
    for example in gold:
        result=classify_product_attributes(example['title'],example.get('brand',''),example.get('source_category',''))
        expected=example['category'];predicted=result.primary_category
        matrix[(expected,predicted)]+=1
        if example['is_skincare']:
            eligible_count+=1
            if not result.is_skincare:false_excluded+=1
        records.append({'title':example['title'],'expected':expected,'predicted':predicted,
                        'status':result.classification_status,'correct':expected==predicted})
    labels=sorted({key[0] for key in matrix})
    scores={}
    for label in labels:
        tp=matrix[(label,label)]
        fp=sum(n for (truth,prediction),n in matrix.items() if prediction==label and truth!=label)
        fn=sum(n for (truth,prediction),n in matrix.items() if truth==label and prediction!=label)
        precision=tp/max(1,tp+fp);recall=tp/max(1,tp+fn)
        scores[label]={'precision':precision,'recall':recall,'f1':2*precision*recall/max(1e-12,precision+recall)}
    return {'sample_count':len(gold),'accuracy':sum(r['correct'] for r in records)/max(1,len(records)),
        'macro_f1':sum(v['f1'] for v in scores.values())/max(1,len(scores)),
        'skincare_false_exclusion_rate':false_excluded/max(1,eligible_count),'per_category':scores,'records':records}


def source_coverage_report(conn,source_id):
    observations=[dict(r) for r in conn.execute('SELECT * FROM source_product_observations WHERE source_id=? ORDER BY id',(source_id,))]
    latest={o['source_product_id']:o for o in observations}
    links=[dict(r) for r in conn.execute("SELECT * FROM product_identity_links WHERE source_id=? AND status='accepted' AND identity_status='identity_verified'",(source_id,))]
    full={r[0] for r in conn.execute("SELECT DISTINCT observation_id FROM inci_audit_evidence WHERE source_name=? AND completeness='full' AND order_verified=1",('source:'+source_id,))}
    return {'source_id':source_id,'discovered_products':len(latest),'snapshots':len(observations),
        'uniquely_matched_canonicals':len({l['canonical_product_id'] for l in links}),
        'additional_offers':conn.execute('SELECT COUNT(*) FROM product_offers WHERE source_id=?',(source_id,)).fetchone()[0],
        'ingredient_candidates':sum(bool(o['ingredient_text']) for o in latest.values()),
        'validated_full_inci_products':sum(o['id'] in full for o in latest.values()),
        'ambiguous_records':conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE source_id=? AND status='pending'",(source_id,)).fetchone()[0],
        'published_formulations':conn.execute("SELECT COUNT(DISTINCT canonical_product_id) FROM inci_audit_evidence WHERE source_name=? AND decision='approved_authoritative'",('source:'+source_id,)).fetchone()[0]}


def enable_source_rollout(conn,source_id,report,reviewer,notes):
    from pipeline.review_service import require_review
    require_review(reviewer,notes)
    if not isinstance(report,int):
        raise ValueError('A finalized server evaluation ID is required; caller metrics are not accepted.')
    artifact=conn.execute('SELECT e.source_id,r.report,r.report_hash FROM source_evaluation_results r JOIN source_evaluations e ON e.id=r.run_id WHERE r.run_id=?',(report,)).fetchone()
    if not artifact or artifact['source_id']!=source_id:
        raise ValueError('Finalized source-specific evaluation not found.')
    report=json.loads(artifact['report'])
    from pipeline.source_evaluation import MIN_SAMPLES,catalog_baseline
    if report.get('reviewed_examples',0)<MIN_SAMPLES or report.get('auto_link_count',0)<50:
        raise ValueError('Insufficient reviewed products or automatic identity links.')
    source=conn.execute('SELECT provides_prices,provides_inci FROM sources WHERE id=?',(source_id,)).fetchone()
    if source[0] and report.get('offer_count',0)<50:raise ValueError('Insufficient evaluated offers.')
    if source[1] and report.get('ordered_inci_sample_count',0)<50:raise ValueError('Insufficient full ordered INCI samples.')
    baseline=json.loads(conn.execute('SELECT baseline FROM source_evaluations WHERE id=?',(report['evaluation_id'],)).fetchone()[0])
    current=catalog_baseline(conn)
    if any(cid not in current or current[cid]!=value for cid,value in baseline.items()):raise ValueError('Catalog changed since evaluation; create a new run.')
    from pipeline.source_evaluation import policy_hash
    policy=conn.execute('SELECT policy_hash FROM source_evaluations WHERE id=?',(report['evaluation_id'],)).fetchone()[0]
    if policy!=policy_hash(conn,source_id):raise ValueError('Source policy or matching code changed; create a new run.')
    latest={r[0] for r in conn.execute('SELECT MAX(id) FROM source_product_observations WHERE source_id=? GROUP BY source_product_id',(source_id,))}
    samples=json.loads(conn.execute('SELECT samples FROM source_evaluations WHERE id=?',(report['evaluation_id'],)).fetchone()[0])
    if any(s['observation']['id'] not in latest for s in samples):raise ValueError('Source observations changed; create a new evaluation.')
    for sample in samples:
        obs=sample['observation']
        links=[dict(r) for r in conn.execute("SELECT * FROM product_identity_links WHERE source_id=? AND (source_product_id=? OR substr(source_product_id,1,length(?)+1)=?||':') AND status='accepted'",(source_id,obs['source_product_id'],obs['source_product_id'],obs['source_product_id']))]
        offers=[dict(r) for r in conn.execute('SELECT source_variant_id,canonical_product_id FROM product_offers WHERE observation_id=?',(obs['id'],))]
        if links!=sample['links'] or offers!=sample['offers']:raise ValueError('Identity links or offers changed; create a new evaluation.')
    required={'macro_f1':.95,'auto_link_precision':.995,'offer_association_accuracy':1,
              'canonical_durability':1,'inci_preservation':1}
    for key,minimum in required.items():
        if report.get(key,-1)<minimum:raise ValueError('Release gate failed: '+key)
    for key in ('critical_false_merges','wrong_product_formulas','unintended_new_entities'):
        if report.get(key)!=0:raise ValueError('Release gate failed: '+key)
    if report.get('skincare_false_exclusion_rate',1)>.01:raise ValueError('Skincare exclusion release gate failed.')
    pending=conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE source_id=? AND status='pending'",(source_id,)).fetchone()[0]
    if pending:raise ValueError('Resolve pending source review cases before enabling publication.')
    if not conn.execute('SELECT 1 FROM sources WHERE id=?',(source_id,)).fetchone():raise ValueError('Source not found.')
    encoded=json.dumps(report,sort_keys=True)
    digest=artifact['report_hash']
    conn.execute('INSERT INTO aggregation_rollouts(source_id,report_hash,reviewed_by,notes) VALUES (?,?,?,?)',(source_id,digest,reviewer,notes))
    conn.execute('UPDATE sources SET enabled=1,publish_enabled=1 WHERE id=?',(source_id,))
    from pipeline.offer_service import refresh_offer_aggregates
    for row in conn.execute('SELECT DISTINCT canonical_product_id FROM product_offers WHERE source_id=?',(source_id,)).fetchall():
        refresh_offer_aggregates(conn,row[0])
    return {'source_id':source_id,'report_hash':digest,'publish_enabled':True}
