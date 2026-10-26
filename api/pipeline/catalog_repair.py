"""Repair saved catalog evidence without rescraping or model calls.

Caller owns the transaction. Preview executes the identical plan in a savepoint.
No identity merges, source-policy changes or historical INCI restoration.
"""
import json
from collections import Counter, defaultdict

from pipeline.taxonomy_service import classify_product_attributes
from pipeline.canonical_identity_resolver import extract_identity, identity_name, compare_attributes
from pipeline.brand_registry import resolve_brand
from pipeline.identity_gates import validate_identity_gates
from pipeline.identity_trust import pending_identity_review

ACTOR = 'catalog_evidence_repair_v1'


def repair_explicit_hair_types(conn):
    """Explicit retailer hair types outrank a generic cream/serum vehicle."""
    from pipeline.taxonomy_service import EXPLICIT_PRODUCT_TYPES,normalize,explicit_skincare_anatomy
    counts=Counter();changes=[]
    for raw in conn.execute("SELECT id,canonical_title,category,brand,classification_status FROM canonical_products WHERE is_skincare=1 AND identity_status NOT IN ('redirected','reversed_split')").fetchall():
        row=dict(raw)
        if row['classification_status']=='approved' or explicit_skincare_anatomy(row['canonical_title']):continue
        title=classify_product_attributes(row['canonical_title'],row['brand'])
        if title.classification_status=='excluded' and title.primary_category!='Hair Care':continue
        peers=[dict(r) for r in conn.execute('''SELECT DISTINCT o.id,o.raw_title,o.raw_brand,o.raw_category,o.description
            FROM product_offers p JOIN source_product_observations o ON o.id=p.observation_id
            WHERE p.canonical_product_id=? AND o.id=(SELECT MAX(id) FROM source_product_observations
                WHERE source_id=o.source_id AND source_product_id=o.source_product_id)''',(row['id'],))]
        hair=[p for p in peers if EXPLICIT_PRODUCT_TYPES.get(normalize(p['raw_category']))=='Hair Care']
        if not hair:continue
        contradicts=False
        for peer in peers:
            kind=normalize(peer['raw_category']);typ=EXPLICIT_PRODUCT_TYPES.get(kind)
            if explicit_skincare_anatomy(peer['raw_title']) or (typ and typ!='Hair Care' and kind not in ('serum','moisturizer','moisturiser')):
                contradicts=True;break
        if contradicts:
            counts['specific_role_conflict_preserved']+=1;continue
        cls=classify_product_attributes(hair[0]['raw_title'],hair[0]['raw_brand'],hair[0]['raw_category'],hair[0]['description'] or '')
        if cls.primary_category!='Hair Care' or cls.is_skincare:continue
        value=cls.to_dict();value['evidence'].append({'source':'retained_source_product_types','observation_ids':[p['id'] for p in hair]})
        conn.execute("UPDATE canonical_products SET category='Hair Care',is_skincare=0,classification=?,classification_status='excluded',updated_at=CURRENT_TIMESTAMP WHERE id=?",(json.dumps(value),row['id']))
        changes.append({'canonical_id':row['id'],'title':row['canonical_title'],'before':row['category'],'after':'Hair Care','observation_ids':[p['id'] for p in hair]})
        counts['updated']+=1
    return {'counts':dict(counts),'changes':changes}


def merge_exact_brand_duplicates(conn,brand_id):
    """Merge exact marketed names within an explicitly reviewed brand only."""
    from pipeline.review_service import merge_canonicals
    from pipeline.identity_trust import verified_link
    from pipeline.formulation_service import FormulationRepository
    from pipeline.inci_tokenizer import tokenize_inci_string
    groups=defaultdict(list);outcomes=[]
    for row in conn.execute("SELECT * FROM canonical_products WHERE brand_id=? AND identity_status IN ('verified','provisional')",(brand_id,)):
        groups[identity_name(row['canonical_title'],row['brand'])].append(dict(row))
    for name,rows in groups.items():
        if not name or len(rows)<2:continue
        reason=None;hashes=set();retailers=set()
        attrs=[extract_identity(r['canonical_title'],r['brand'],r['market'],json.loads(r['identity_attributes'] or '{}')) for r in rows]
        if any(compare_attributes(attrs[0],a)['conflicts'] or compare_attributes(attrs[0],a)['unknown'] for a in attrs[1:]):
            reason='Conflicting or missing formulation discriminator.'
        for row in rows:
            cid=row['id']
            if pending_identity_review(conn,cid):reason='Pending identity review.'
            offers=list(conn.execute('''SELECT o.*,s.raw_brand FROM product_offers o
                JOIN source_product_observations s ON s.id=o.observation_id WHERE o.canonical_product_id=?''',(cid,)))
            if not offers:reason='Missing current retailer listing.'
            for offer in offers:
                brand=resolve_brand(offer['raw_brand'],conn)
                if (not brand or brand['id']!=brand_id or identity_name(offer['title'],offer['raw_brand'])!=name
                        or not verified_link(conn,offer['source_id'],offer['source_product_id'],cid)):
                    reason='Retailer identity is not an exact verified match.'
                retailers.add(offer['source_id'])
            declarations=[r[0] for r in conn.execute("""SELECT raw_ingredients_found FROM inci_audit_evidence
                WHERE canonical_product_id=? AND completeness='full' AND order_verified=1
                AND source_name LIKE 'source:%'""",(cid,))]
            if row['ingredients_raw']:declarations.append(row['ingredients_raw'])
            for raw in declarations:
                hashes.add(FormulationRepository.compute_formula_hash(tokenize_inci_string(raw).tokens))
        if len(retailers)<2:reason='Needs independent retailer corroboration.'
        if len(hashes)>1:reason='Full ordered formula declarations disagree.'
        record={'canonical_ids':[r['id'] for r in rows],'marketed_identity':name}
        if reason:
            outcomes.append({**record,'action':'preserved','reason':reason});continue
        rows.sort(key=lambda r:(not bool(r['ingredients_raw']),r['identity_status']!='verified',r['id']))
        survivor=rows[0]['id'];history=[]
        conn.execute('SAVEPOINT exact_brand_duplicates')
        try:
            for row in rows[1:]:
                history.append(merge_canonicals(conn,survivor,row['id'],'catalog_repair_agent',
                    'Reviewed brand equivalence; exact marketed names, verified source associations, independent retailers and no formulation disagreement.'))
            conn.execute('RELEASE SAVEPOINT exact_brand_duplicates')
            outcomes.append({**record,'action':'merged','survivor_id':survivor,'history_ids':history})
        except ValueError as error:
            conn.execute('ROLLBACK TO SAVEPOINT exact_brand_duplicates');conn.execute('RELEASE SAVEPOINT exact_brand_duplicates')
            outcomes.append({**record,'action':'preserved','reason':str(error)})
    return outcomes


def _peer_title_classifications(conn,canonical_id,fallback_brand):
    return [classify_product_attributes(r[0],r[1]) for r in conn.execute(
        """SELECT DISTINCT p.title,COALESCE(NULLIF(o.raw_brand,''),?) FROM product_offers p
        LEFT JOIN source_product_observations o ON o.id=p.observation_id
        WHERE p.canonical_product_id=?""",(fallback_brand,canonical_id))]


def repair_canonical_categories(conn):
    """Project explicit title functions, preserving human decisions and conflicts.

    Historical categories are not evidence. Every current offer with a declared
    function must agree before a canonical title can replace them.
    """
    from pipeline.taxonomy_service import explicit_skincare_anatomy
    counts=Counter();changes=[]
    for raw in conn.execute("SELECT id,canonical_title,brand,category,is_skincare,classification_status FROM canonical_products WHERE identity_status NOT IN ('redirected','reversed_split')").fetchall():
        row=dict(raw)
        if row['classification_status']=='approved':
            counts['human_approved_preserved']+=1;continue
        cls=classify_product_attributes(row['canonical_title'],row['brand'])
        if cls.classification_status not in ('auto_approved','excluded') or not any(e.get('source')=='title' for e in cls.evidence):
            counts['uncertain_preserved']+=1;continue
        if row['category']==cls.primary_category and bool(row['is_skincare'])==cls.is_skincare:
            continue
        if row['category']=='Hair Care' and cls.is_skincare and not explicit_skincare_anatomy(row['canonical_title']):
            counts['specific_hair_type_preserved']+=1;continue
        if cls.primary_category=='Moisturizer' and row['category'] in ('Body Care','Lip Care','Eye Skincare','Tanning','Treatment'):
            counts['specific_category_preserved']+=1;continue
        peers=_peer_title_classifications(conn,row['id'],row['brand'])
        if any(p.classification_status in ('auto_approved','excluded') and p.primary_category!=cls.primary_category for p in peers):
            counts['cluster_conflict_preserved']+=1;continue
        conn.execute("""UPDATE canonical_products SET category=?,is_skincare=?,classification=?,
            classification_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (cls.primary_category,int(cls.is_skincare),json.dumps(cls.to_dict()),cls.classification_status,row['id']))
        counts['updated']+=1
        changes.append({'canonical_id':row['id'],'title':row['canonical_title'],
                        'before':row['category'],'after':cls.primary_category,
                        'is_skincare':cls.is_skincare,'evidence':cls.evidence})
    return {'counts':dict(counts),'changes':changes}


def repair_categories(conn):
    counts = Counter()
    for raw in conn.execute("""SELECT r.*,o.raw_title,o.raw_brand,o.raw_category,o.description,
        o.source_product_id observed_product_id
        FROM product_review_queue r JOIN source_product_observations o
        ON o.id=json_extract(r.details,'$.observation_id') AND o.source_id=r.source_id
        WHERE r.kind='category' AND r.status='pending' AND r.reviewed_by IS NULL
        AND r.decision IS NULL ORDER BY r.id""").fetchall():
        row = dict(raw)
        # Never apply a category derived from another variant or source product.
        if (row['source_product_id'] != row['observed_product_id'] and
                not str(row['source_product_id']).startswith(str(row['observed_product_id'])+':')):
            counts['variant_review_preserved'] += 1
            continue
        latest = conn.execute('SELECT MAX(id) FROM source_product_observations WHERE source_id=? AND source_product_id=?',
                              (row['source_id'], row['observed_product_id'])).fetchone()[0]
        if latest != json.loads(row['details']).get('observation_id'):
            counts['superseded_evidence_preserved'] += 1
            continue
        cls = classify_product_attributes(row['raw_title'], row['raw_brand'], row['raw_category'] or '', row['description'] or '')
        if cls.classification_status not in ('auto_approved', 'excluded'):
            counts['uncertain_preserved'] += 1
            continue
        links = {r[0] for r in conn.execute("""SELECT canonical_product_id FROM product_identity_links
            WHERE source_id=? AND source_product_id=? AND status='accepted'""",
            (row['source_id'], row['source_product_id']))}
        cid = row['canonical_product_id']
        if cid is not None and links != {cid}:
            counts['identity_conflict_preserved'] += 1
            continue
        if cid is None and len(links) == 1:
            cid = next(iter(links))
        if cid is not None:
            canonical = conn.execute('SELECT category,classification_status,brand FROM canonical_products WHERE id=?', (cid,)).fetchone()
            if not canonical or (canonical['classification_status']=='approved' and canonical['category']!=cls.primary_category):
                counts['approved_category_preserved'] += 1
                continue
            # A source classification cannot override disagreement in its cluster.
            results = _peer_title_classifications(conn,cid,canonical['brand'])
            if any(p.classification_status in ('auto_approved','excluded') and p.primary_category != cls.primary_category for p in results):
                counts['cluster_conflict_preserved'] += 1
                continue
            conn.execute("""UPDATE canonical_products SET category=?,classification=?,classification_status=?,
                is_skincare=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND classification_status!='approved'""",
                (cls.primary_category,json.dumps(cls.to_dict()),cls.classification_status,int(cls.is_skincare),cid))
        decision = {'action':'category','category':cls.primary_category,'policy':ACTOR,
                    'classification':cls.to_dict(),'canonical_id':cid}
        conn.execute("""UPDATE product_review_queue SET status='confirmed',decision=?,reviewed_by=?,
            review_notes='Explicit product-function rules resolved category from retained source evidence.',
            updated_at=CURRENT_TIMESTAMP WHERE id=?""", (json.dumps(decision),ACTOR,row['id']))
        counts['resolved'] += 1
    return dict(counts)


def verify_corroborated_identities(conn):
    """Verify exact profiles backed by two distinct retailers and no open conflict."""
    counts = Counter()
    canonicals = [dict(r) for r in conn.execute("SELECT * FROM canonical_products WHERE identity_status NOT IN ('redirected','reversed_split')")]
    names = defaultdict(set)
    for row in canonicals:
        brand = resolve_brand(row['brand'],conn)
        if brand and brand.get('verified'):
            names[(brand['id'],identity_name(row['canonical_title'],row['brand']))].add(row['id'])
    for row in canonicals:
        if row['identity_status']!='provisional' or not row['is_skincare']:
            continue
        cid = row['id']
        offers = [dict(r) for r in conn.execute('SELECT DISTINCT source_id,source_product_id,observation_id,title FROM product_offers WHERE canonical_product_id=?',(cid,))]
        if len({o['source_id'] for o in offers}) < 2:
            counts['needs_independent_retailer'] += 1
            continue
        brand = resolve_brand(row['brand'],conn)
        expected = identity_name(row['canonical_title'],row['brand'])
        if not brand or not brand.get('verified') or names[(brand['id'],expected)]!={cid}:
            counts['brand_or_duplicate_conflict'] += 1
            continue
        if pending_identity_review(conn,cid):
            counts['pending_identity_conflict'] += 1
            continue
        reference = extract_identity(row['canonical_title'],row['brand'],row['market'])
        valid = True
        for offer in offers:
            obs = conn.execute('SELECT * FROM source_product_observations WHERE id=?',(offer['observation_id'],)).fetchone()
            link = conn.execute("""SELECT 1 FROM product_identity_links WHERE source_id=? AND source_product_id=?
                AND canonical_product_id=? AND status='accepted' AND identity_status='identity_verified'""",
                (offer['source_id'],offer['source_product_id'],cid)).fetchone()
            observed_brand = resolve_brand(obs['raw_brand'],conn) if obs else None
            if not obs or not link or not observed_brand or observed_brand['id']!=brand['id'] or identity_name(obs['raw_title'],obs['raw_brand'])!=expected:
                valid=False;break
            latest=conn.execute('SELECT MAX(id) FROM source_product_observations WHERE source_id=? AND source_product_id=?',
                (obs['source_id'],obs['source_product_id'])).fetchone()[0]
            if latest!=obs['id'] or identity_name(offer['title'],obs['raw_brand'])!=expected:
                valid=False;break
            attrs=extract_identity(obs['raw_title'],obs['raw_brand'],obs['market'],json.loads(obs['identity_attributes']))
            comparison=compare_attributes(reference,attrs)
            gates,_=validate_identity_gates(row['canonical_title'],obs['raw_title'],row['brand'],obs['raw_brand'])
            if not gates or comparison['conflicts'] or comparison['unknown']:
                valid=False;break
            variants=conn.execute('SELECT attributes FROM source_product_variants WHERE observation_id=?',(obs['id'],)).fetchall()
            if any(compare_attributes(reference,{**attrs,**json.loads(v[0])})['conflicts'] for v in variants):
                valid=False;break
        if not valid:
            counts['listing_or_variant_conflict'] += 1
            continue
        conn.execute("UPDATE canonical_products SET identity_status='verified',updated_at=CURRENT_TIMESTAMP WHERE id=?",(cid,))
        conn.execute("""INSERT INTO canonical_identity_history(operation,canonical_product_id,before_state,after_state,reviewed_by,reason)
            VALUES ('auto_verify',?,?,?,?,?)""",(cid,json.dumps({'identity_status':'provisional'}),json.dumps({'identity_status':'verified',
                'observation_ids':sorted({o['observation_id'] for o in offers}),'retailers':sorted({o['source_id'] for o in offers})}),ACTOR,
            'Exact marketed identity, verified brand, compatible variants and at least two independent retailer observations.'))
        counts['verified'] += 1
    return dict(counts)


def repair_catalog(conn, dry_run=True, approve_staged=False):
    from pipeline.saved_identity_rematch import rematch_execution_blockers
    from pipeline.catalog_reset import guard_catalog_writes
    guard_catalog_writes(conn)
    blockers=rematch_execution_blockers(conn)
    if not blockers['can_start']:
        raise ValueError('Catalog workers must finish before evidence repair.')
    if not conn.in_transaction:
        conn.execute('BEGIN')
    conn.execute('SAVEPOINT catalog_evidence_repair')
    try:
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        result={'dry_run':dry_run,'categories':repair_categories(conn)}
        result['saved_matching']=apply_saved_decisions(conn,dry_run=False)
        result['identities']=verify_corroborated_identities(conn)
        result.update(formulations={},model_calls=0)
        if approve_staged:
            from pipeline.source_formulations import resolve_source_formulations,approve_formulation_evidence
            counts=Counter()
            ids=[r[0] for r in conn.execute("SELECT id FROM canonical_products WHERE identity_status='verified' AND is_skincare=1 AND COALESCE(TRIM(ingredients_raw),'')=''")]
            for cid in ids:
                assessment=resolve_source_formulations(conn,cid,publish=False)
                if assessment['status'] not in ('ready','staged'):
                    counts[assessment['status']]+=1;continue
                eid=assessment['selected_evidence_id']
                policy=conn.execute("""SELECT s.enabled,s.source_type,s.market FROM inci_audit_evidence e
                    JOIN source_product_observations o ON o.id=e.observation_id JOIN sources s ON s.id=o.source_id
                    WHERE e.id=?""",(eid,)).fetchone()
                if not policy['enabled'] or policy['source_type']!='retailer' or policy['market']!='EG':
                    counts['source_policy_preserved']+=1;continue
                outcome=approve_formulation_evidence(conn,eid,ACTOR,
                    'User-requested catalog repair: individual retained full ordered Egyptian retailer declaration; verified canonical and source association; no competing ordered formula. No source-wide publication enabled.')
                counts['published' if outcome['published'] else 'publication_blocked']+=1
                if outcome['published']:
                    conn.execute("UPDATE formulation_resolution_queue SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE canonical_product_id=?",(cid,))
            result['formulations']=dict(counts)
        if dry_run:
            conn.execute('ROLLBACK TO SAVEPOINT catalog_evidence_repair')
        conn.execute('RELEASE SAVEPOINT catalog_evidence_repair')
        return result
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT catalog_evidence_repair')
        conn.execute('RELEASE SAVEPOINT catalog_evidence_repair')
        raise
