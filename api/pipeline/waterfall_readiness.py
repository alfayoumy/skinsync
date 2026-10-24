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
    'store_page_tab', 'historical_store_scrape', 'openbeauty_api',
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
    return {
        'total_skincare':len(rows), 'in_scope':len(scoped),
        'blocked_unverified':len(scoped)-len(verified),
        'blocked_pending_review':sum(r['id'] in pending for r in verified),
        'eligible_before_limit':len(ready), 'eligible':count,
        'eligible_ids':ready[:count], 'scope':'all_skincare' if revalidate_all else 'unmatched_only',
    }


def certifiable_cross_store(conn, limit=None):
    """Require exact normalized identity from at least two independent stores, across ALL members."""
    pending=pending_identity_ids(conn)
    groups=defaultdict(list)
    for row in conn.execute("""SELECT id,canonical_product_id,store_id,store_product_id,
        COALESCE(original_title,title) title,COALESCE(original_brand,brand) brand
        FROM products WHERE canonical_product_id IS NOT NULL ORDER BY canonical_product_id,id"""):
        groups[row['canonical_product_id']].append(dict(row))
    candidates=[]
    for canonical in conn.execute("""SELECT id,canonical_title,brand,brand_id,identity_status,is_skincare
        FROM canonical_products WHERE is_skincare=1 AND identity_status IN
        ('legacy','legacy_preserved_unverified') ORDER BY id"""):
        cid=canonical['id']
        if cid in pending or not canonical['brand_id']:
            continue
        offers=groups.get(cid,[])
        if len({p['store_id'] for p in offers})<2:
            continue
        expected=identity_name(canonical['canonical_title'],canonical['brand'])
        if not expected:continue
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
            conn.execute("""UPDATE canonical_products SET identity_status='verified',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND identity_status IN ('legacy','legacy_preserved_unverified')""",(cid,))
    return {'dry_run':not apply,'certifiable':len(candidates),'processed':len(candidates) if apply else 0,
            'canonical_ids':[cid for cid,_ in candidates[:25]],
            'rule':'two independent stores; every title, brand and formulation discriminator must agree',
            'limit':limit}
