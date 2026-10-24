"""Publication gates use immutable server snapshots and attributed reviewed labels."""
import hashlib
import json
from pipeline.review_service import require_review
from pipeline.aggregation_benchmark import evaluate_classification

MIN_SAMPLES = 100
REQUIRED_CATEGORIES = {'Sunscreen','Serum','Treatment','Cleanser','Moisturizer'}


def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def catalog_baseline(conn):
    associations={}
    for r in conn.execute('SELECT id,canonical_product_id FROM products ORDER BY id'):
        associations.setdefault(str(r[1]),[]).append(r[0])
    return {str(r['id']): {'formula': r['ingredients_raw'], 'title': r['canonical_title'], 'brand':r['brand'],
                          'legacy_product_ids':associations.get(str(r['id']),[]),
                          'formula_metadata':{k:r[k] for k in ('ingredients_parsed','ingredient_source','ingredient_source_url','formula_hash','formula_version')}}
            for r in conn.execute('SELECT * FROM canonical_products')}


def policy_hash(conn,source_id):
    from pathlib import Path
    root=Path(__file__).parent
    source=dict(conn.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone())
    for key in ('enabled','publish_enabled','last_successful_sync'):source.pop(key,None)
    files=[
        'source_adapters/__init__.py','source_adapters/extraction.py',
        'canonical_identity_resolver.py','taxonomy_service.py','brand_registry.py',
        'identity_gates.py','identity_trust.py','waterfall_readiness.py',
        'formulation_service.py','source_formulations.py','waterfall_inci_resolver.py',
        'source_registry.py','offer_service.py','source_http.py',
        'source_evaluation.py','aggregation_benchmark.py'
    ]
    payload={'source':source,'code':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files}}
    return hashlib.sha256(encode(payload).encode()).hexdigest()


def create_evaluation(conn,source_id,reviewer):
    if not conn.execute('SELECT 1 FROM sources WHERE id=?',(source_id,)).fetchone():raise ValueError('Source not found.')
    observations=[dict(r) for r in conn.execute('SELECT * FROM source_product_observations WHERE source_id=? ORDER BY id DESC',(source_id,))]
    latest={}
    for row in observations:latest.setdefault(row['source_product_id'],row)
    buckets={}
    for row in latest.values():
        category=json.loads(row['classification']).get('primary_category','Unknown')
        buckets.setdefault(category,[]).append(row)
    # Round-robin ensures a large category cannot crowd out high-risk categories.
    selected=[]
    while buckets and len(selected)<300:
        for category in list(buckets):
            row=buckets[category].pop(0)
            links=[dict(r) for r in conn.execute("SELECT * FROM product_identity_links WHERE source_id=? AND (source_product_id=? OR substr(source_product_id,1,length(?)+1)=?||':') AND status='accepted'",(source_id,row['source_product_id'],row['source_product_id'],row['source_product_id']))]
            offers=[dict(r) for r in conn.execute('SELECT source_variant_id,canonical_product_id FROM product_offers WHERE observation_id=?',(row['id'],))]
            evidence=[dict(r) for r in conn.execute('SELECT canonical_product_id,completeness,order_verified FROM inci_audit_evidence WHERE observation_id=?',(row['id'],))]
            selected.append({'observation':row,'links':links,'offers':offers,'evidence':evidence})
            if not buckets[category]:del buckets[category]
            if len(selected)>=300:break
    if not selected:raise ValueError('Collect observations before creating an evaluation.')
    cursor=conn.execute('INSERT INTO source_evaluations(source_id,samples,baseline,created_by,policy_hash) VALUES (?,?,?,?,?)',
                        (source_id,encode(selected),encode(catalog_baseline(conn)),reviewer,policy_hash(conn,source_id)))
    return {'evaluation_id':cursor.lastrowid,'sample_count':len(selected),'minimum_samples':MIN_SAMPLES}


def label_example(conn,run_id,observation_id,expected_canonical_id,expected_new,category,is_skincare,reviewer,reason):
    require_review(reviewer,reason)
    row=conn.execute('SELECT samples,created_by FROM source_evaluations WHERE id=?',(run_id,)).fetchone()
    if not row or observation_id not in {s['observation']['id'] for s in json.loads(row[0])}:raise ValueError('Observation not in this evaluation.')
    if reviewer==row['created_by']:
        raise ValueError('Evaluation labels require an independent reviewer, not the evaluation creator.')
    if expected_new == bool(expected_canonical_id):raise ValueError('Label either a new identity or an existing canonical ID.')
    if expected_canonical_id and not conn.execute('SELECT 1 FROM canonical_products WHERE id=?',(expected_canonical_id,)).fetchone():raise ValueError('Canonical not found.')
    if not conn.execute('SELECT 1 FROM product_taxonomy WHERE label=? AND is_skincare=?',(category,int(is_skincare))).fetchone():raise ValueError('Invalid taxonomy label or skincare classification.')
    if conn.execute('SELECT 1 FROM source_evaluation_labels WHERE run_id=? AND observation_id=?',(run_id,observation_id)).fetchone():raise ValueError('Labels are immutable; create a new evaluation to revise labels.')
    if conn.execute('SELECT 1 FROM source_evaluation_results WHERE run_id=?',(run_id,)).fetchone():raise ValueError('Evaluation finalized.')
    conn.execute('INSERT INTO source_evaluation_labels VALUES (?,?,?,?,?,?,?,?)',
                 (run_id,observation_id,expected_canonical_id,int(expected_new),category,int(is_skincare),reviewer,reason))
    return {'labeled':observation_id}


def finalize_evaluation(conn,run_id,reviewer):
    row=conn.execute('SELECT * FROM source_evaluations WHERE id=?',(run_id,)).fetchone()
    if not row:raise ValueError('Evaluation not found.')
    samples=json.loads(row['samples'])
    labels={r['observation_id']:dict(r) for r in conn.execute('SELECT * FROM source_evaluation_labels WHERE run_id=?',(run_id,))}
    if reviewer==row['created_by'] or reviewer in {v['reviewed_by'] for v in labels.values()}:
        raise ValueError('Evaluation finalization must be independent of collection and labeling.')
    if len(samples)<MIN_SAMPLES or len(labels)!=len(samples):raise ValueError('At least 100 distinct products and all reviewed labels are required.')
    if len({r['expected_canonical_id'] for r in labels.values() if r['expected_canonical_id']}) + sum(r['expected_new'] for r in labels.values()) < 50:
        raise ValueError('At least 50 distinct reviewed marketed identities are required.')
    coverage={r['category'] for r in labels.values()}
    if not REQUIRED_CATEGORIES<=coverage:raise ValueError('Evaluation must cover sunscreen, serum, treatment, cleanser and moisturizer.')
    if any(sum(r['category']==category for r in labels.values())<10 for category in REQUIRED_CATEGORIES):raise ValueError('At least 10 reviewed examples per high-risk category are required.')
    gold=[];correct=0;linked=0;false_merges=0;wrong_formula=0;offer_correct=0;offer_total=0;unintended=0
    for sample in samples:
        obs=sample['observation'];label=labels[obs['id']]
        gold.append({'title':obs['raw_title'],'brand':obs['raw_brand'],'source_category':obs['raw_category'] or '', 'category':label['category'],'is_skincare':bool(label['is_skincare'])})
        ids={l['canonical_product_id'] for l in sample['links'] if l['identity_status']=='identity_verified' and not l['reviewed_by']}
        auto_ids={l['canonical_product_id'] for l in sample['links'] if l['method'] in ('new_identity','distinct_variant')}
        if ids:
            linked+=1
            ok=not label['expected_new'] and ids=={label['expected_canonical_id']}
            # New provisional identities are measured separately from existing auto-links.
            if label['expected_new'] and ids==auto_ids:ok=True
            correct+=int(ok);false_merges+=int(not ok)
        if auto_ids and not label['expected_new']:unintended+=1
        allowed=ids if label['expected_new'] else {label['expected_canonical_id']}
        for offer in sample['offers']:
            offer_total+=1;offer_correct+=int(offer['canonical_product_id'] in allowed)
        wrong_formula+=sum(e['canonical_product_id'] not in allowed for e in sample['evidence'])
    baseline=json.loads(row['baseline']);current=catalog_baseline(conn)
    durability=all(cid in current and current[cid]['title']==v['title'] and current[cid]['brand']==v['brand'] and current[cid]['legacy_product_ids']==v['legacy_product_ids'] for cid,v in baseline.items())
    preservation=all(cid in current and current[cid]['formula']==v['formula'] and current[cid]['formula_metadata']==v['formula_metadata'] for cid,v in baseline.items())
    report=evaluate_classification(gold)
    report.update(source_id=row['source_id'],reviewed_examples=len(samples),auto_link_count=linked,
                  auto_link_precision=correct/max(1,linked),offer_association_accuracy=offer_correct/max(1,offer_total),
                  offer_count=offer_total,canonical_durability=int(durability),inci_preservation=int(preservation),
                  critical_false_merges=false_merges,wrong_product_formulas=wrong_formula,unintended_new_entities=unintended,
                  evaluation_id=run_id,category_coverage=sorted(coverage),
                  ordered_inci_sample_count=sum(any(e['completeness']=='full' and e['order_verified'] for e in s['evidence']) for s in samples))
    digest=hashlib.sha256(encode(report).encode()).hexdigest()
    conn.execute('INSERT INTO source_evaluation_results(run_id,report,report_hash,finalized_by) VALUES (?,?,?,?)',(run_id,encode(report),digest,reviewer))
    return {'evaluation_id':run_id,'report_hash':digest,'report':report}
