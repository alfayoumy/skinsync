"""Read-only waterfall eligibility and conservative cross-retailer identity certification.

A valid formula can never be published merely because an old canonical mapping exists.
Certification requires agreeing, independently sourced product titles and variants.
"""
import json
from collections import defaultdict

from pipeline.brand_registry import resolve_brand
from pipeline.canonical_identity_resolver import (
    CanonicalIdentityResolver, identity_name, extract_identity, compare_attributes
)
from pipeline.identity_gates import validate_identity_gates

ACCEPTED_SOURCES = {
    'store_page_tab', 'openbeauty_api',
    'skinsafe_high_match', 'langsearch_web_search', 'admin_override',
    'curated_v10', 'jev_verified_skinsafe', 'gemini_verified_skinsafe',
}


def pending_identity_ids(conn):
    pending = set()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'product_review_queue' in tables:
        for row in conn.execute("""SELECT canonical_product_id,details
            FROM product_review_queue WHERE status='pending' AND kind IN ('identity','duplicate')"""):
            if row[0] is not None:
                pending.add(row[0])
            details = json.loads(row[1] or '{}')
            pending.update(x for x in details.get('canonical_ids', []) if isinstance(x,int))
            decision = details.get('identity') or details.get('decision') or {}
            pending.update(x.get('canonical_product_id') for x in decision.get('candidates', [])
                           if isinstance(x,dict) and isinstance(x.get('canonical_product_id'),int))
    if 'canonical_identity_reviews' in tables:
        for row in conn.execute("SELECT canonical_ids FROM canonical_identity_reviews WHERE status='pending'"):
            pending.update(x for x in json.loads(row[0]) if isinstance(x,int))
    return pending


def eligible_waterfall(conn, revalidate_all=False, limit=None):
    """Return a reproducible preflight and the eligible canonical IDs; never mutate."""
    rows=[dict(r) for r in conn.execute("""SELECT id,identity_status,ingredients_raw,ingredient_source
        FROM canonical_products WHERE is_skincare=1 ORDER BY id""")]
    scoped=[r for r in rows if revalidate_all or
            not r['ingredients_raw'] or not str(r['ingredients_raw']).strip() or
            not r['ingredient_source'] or r['ingredient_source'] not in ACCEPTED_SOURCES]
    pending=pending_identity_ids(conn)
    verified=[r for r in scoped if r['identity_status']=='verified']
    ready=[r['id'] for r in verified if r['id'] not in pending]
    count=len(ready) if limit is None else min(len(ready),max(0,int(limit)))
    from collections import Counter
    states=dict(Counter(r['identity_status'] or 'unknown' for r in scoped))
    # Provisional is normal for fresh discovery, but still cannot publish INCI
    # until separately corroborated or explicitly reviewed.
    certified_candidates=certifiable_cross_store(conn,limit=1000)
    known_multi_store=len(certified_candidates)
    return {
        'total_skincare':len(rows), 'in_scope':len(scoped),
        'blocked_unverified':len(scoped)-len(verified),
        'blocked_pending_review':sum(r['id'] in pending for r in verified),
        'identity_status_breakdown':states,
        'certifiable_cross_store_preview_count':known_multi_store,
        'certifiable_preview_capped':known_multi_store==1000,
        'eligible_before_limit':len(ready), 'eligible':count,
        'eligible_ids':ready[:count], 'scope':'all_skincare' if revalidate_all else 'unmatched_only',
    }


def certifiable_cross_store(conn, limit=None):
    """Require exact normalized identity from at least two independent stores, across ALL members."""
    pending=pending_identity_ids(conn)
    groups=defaultdict(list)
    for row in conn.execute("""SELECT id,canonical_product_id,store_id,store_product_id,
        COALESCE(original_title,title) title,COALESCE(original_brand,brand) brand,
        ingredients_raw,ingredient_source
        FROM products WHERE canonical_product_id IS NOT NULL ORDER BY canonical_product_id,id"""):
        groups[row['canonical_product_id']].append(dict(row))
    # Reject competing same-brand marketed identities even if two retailers
    # independently point at one provisional canonical. A duplicate pending
    # canonical cannot be safely ignored as an alternate formulation.
    profiles=defaultdict(set)
    for row in conn.execute("""SELECT id,brand_id,canonical_title,brand FROM canonical_products
        WHERE identity_status NOT IN ('redirected','reversed_split')"""):
        if row['brand_id']:
            profiles[(row['brand_id'],identity_name(row['canonical_title'],row['brand']))].add(row['id'])
    candidates=[]
    for canonical in conn.execute("""SELECT id,canonical_title,brand,brand_id,identity_status,is_skincare,
        ingredients_raw,classification_status
        FROM canonical_products WHERE is_skincare=1 AND identity_status IN
        ('legacy','legacy_preserved_unverified','provisional') ORDER BY id"""):
        cid=canonical['id']
        if cid in pending or not canonical['brand_id']:
            continue
        offers=groups.get(cid,[])
        if len({p['store_id'] for p in offers})<2:
            continue
        expected=identity_name(canonical['canonical_title'],canonical['brand'])
        if not expected or profiles[(canonical['brand_id'],expected)]!={cid}:continue
        if canonical['classification_status'] not in ('approved','auto_approved'):
            continue
        if conn.execute("""SELECT 1 FROM product_review_queue
            WHERE status='pending' AND kind='category' AND canonical_product_id=? LIMIT 1""",(cid,)).fetchone():
            continue
        identity=extract_identity(canonical['canonical_title'],canonical['brand'])
        good=True
        for offer in offers:
            brand=resolve_brand(offer['brand'],conn)
            if not brand or brand.get('id')!=canonical['brand_id'] or identity_name(offer['title'],offer['brand'])!=expected:
                good=False;break
            compatible,_=validate_identity_gates(offer['title'],canonical['canonical_title'],offer['brand'],canonical['brand'])
            if not compatible or compare_attributes(extract_identity(offer['title'],offer['brand']),identity)['conflicts']:
                good=False;break
        if good:
            # Exact titles do not rule out reformulations. Existing credible
            # full declarations must not disagree within a certified cluster.
            from pipeline.identity_review import formula_fingerprint
            hashes=set()
            canonical_hash=formula_fingerprint(dict(canonical))
            if canonical_hash:hashes.add(canonical_hash)
            for offer in offers:
                if offer.get('ingredient_source') in (
                    'store_page_tab','admin_override','curated_v10'
                ):
                    existing_hash=formula_fingerprint(offer)
                    if existing_hash:hashes.add(existing_hash)
            # Fresh replay stores full ordered declarations in audit evidence,
            # not in legacy products.ingredients_raw. Two incompatible
            # source formulas for the same identity must NOT be certified
            # simply because both retailer titles are identical.
            if 'inci_audit_evidence' in {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
                for evidence in conn.execute("""SELECT parsed_tokens
                    FROM inci_audit_evidence WHERE canonical_product_id=?
                    AND completeness='full' AND order_verified=1
                    AND source_name LIKE 'source:%'""",(cid,)):
                    try:
                        tokens=json.loads(evidence['parsed_tokens'] or '[]')
                        if isinstance(tokens,list) and tokens:
                            hashes.add(tuple(str(t).strip().casefold() for t in tokens))
                    except (ValueError,TypeError):
                        continue
            if len(hashes)>1:
                continue
            candidates.append((cid,offers))
            if limit is not None and len(candidates)>=int(limit):break
    return candidates


def certify_cross_store(conn, reviewer, apply=False, limit=200):
    """Never approve a single-source or conflicting legacy cluster. Applying requires admin identity scope."""
    if not reviewer or not reviewer.strip():raise ValueError('Reviewer attribution required.')
    if not 1<=limit<=1000:raise ValueError('Certification limit must be between 1 and 1000.')
    candidates=certifiable_cross_store(conn,limit)
    if apply:
        resolver=CanonicalIdentityResolver(conn)
        for cid,offers in candidates:
            for p in offers:
                resolver.record_link(p['store_id'],str(p['store_product_id'] or p['id']),cid,
                    'accepted','cross_store_exact_identity',{'product_id':p['id'],
                    'independent_stores':sorted({o['store_id'] for o in offers}),
                    'rule':'all_members_exact_identity_and_hard_gates'})
            old=conn.execute("SELECT identity_status FROM canonical_products WHERE id=?",(cid,)).fetchone()[0]
            updated=conn.execute("""UPDATE canonical_products SET identity_status='verified',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND identity_status IN ('legacy','legacy_preserved_unverified','provisional')""",(cid,))
            if updated.rowcount!=1:
                raise ValueError('Identity changed during independent cross-store certification.')
            conn.execute("""INSERT INTO canonical_identity_history(operation,canonical_product_id,
                before_state,after_state,reviewed_by,reason)
                VALUES(?,?,?,?,?,?)""",('cross_store_exact_certification',cid,
                json.dumps({'identity_status':old}),
                json.dumps({'identity_status':'verified','independent_stores':sorted({o['store_id'] for o in offers})}),
                reviewer,'Exact registered brand, marketed identity and variant across independent stores; no competing canonical or pending review.'))
    return {'dry_run':not apply,'certifiable':len(candidates),'processed':len(candidates) if apply else 0,
            'canonical_ids':[cid for cid,_ in candidates[:25]],
            'rule':'two independent stores; reviewed brand, every title and formulation discriminator agree; unique canonical; approved category; no pending reviews',
            'limit':limit}
