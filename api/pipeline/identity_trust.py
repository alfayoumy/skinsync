"""Database-enforced identity trust at formulation publication boundaries."""
import json


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def pending_identity_ids(conn):
    """Canonical disputes block publication; unaccepted retrieval suggestions do not.

    A grounded pending source suggestion cannot supply canonical ingredients:
    verified_link remains mandatory at publication. It also cannot invalidate
    a different, already accepted source's product identity merely by appearing
    in the same fuzzy candidate list.
    """
    pending=set()
    present = tables(conn)
    if 'product_review_queue' in present:
        grounded={'source_product_observations','product_identity_links'}<=present
        extra=""",o.source_id,o.source_product_id,
            EXISTS(SELECT 1 FROM product_identity_links l WHERE l.source_id=r.source_id
                AND l.source_product_id=r.source_product_id AND l.status='accepted')
            FROM product_review_queue r LEFT JOIN source_product_observations o
                ON o.id=json_extract(r.details,'$.observation_id')""" if grounded else ',NULL,NULL,0 FROM product_review_queue r'
        for row in conn.execute("SELECT r.canonical_product_id,r.details,r.kind,r.source_id,r.source_product_id"+extra+
                                " WHERE r.status='pending' AND r.kind IN ('identity','duplicate')"):
            if row[0] is not None:pending.add(row[0])
            details = json.loads(row[1])
            pending.update(x for x in details.get('canonical_ids',[]) if type(x) is int)
            decision = details.get('identity') or details.get('decision') or {}
            candidates=[c for c in decision.get('candidates',[]) if isinstance(c,dict)]
            suggestion=(row[0] is None and row[2]=='identity' and row[3]==row[5]
                and row[6] is not None and (str(row[4])==str(row[6]) or str(row[4]).startswith(str(row[6])+':'))
                and not row[7] and decision.get('method')=='review_required'
                and not any(c.get(k) for c in candidates for k in ('durable_link','reviewed_link','strong_identifier')))
            if not suggestion:
                pending.update(c['canonical_product_id'] for c in candidates if type(c.get('canonical_product_id')) is int)
            # A review of an accepted source association always disputes its
            # current canonical, even if the proposed replacement differs.
            if grounded and row[7]:
                pending.update(r[0] for r in conn.execute("SELECT canonical_product_id FROM product_identity_links WHERE source_id=? AND source_product_id=? AND status='accepted'",(row[3],row[4])))
    if 'canonical_identity_reviews' in present:
        for row in conn.execute("SELECT canonical_ids FROM canonical_identity_reviews WHERE status='pending'"):
            pending.update(x for x in json.loads(row[0]) if type(x) is int)
    return pending


def pending_identity_review(conn,canonical_id):
    return canonical_id in pending_identity_ids(conn)


def verified_link(conn, source_id, source_product_id, canonical_id):
    if 'product_identity_links' not in tables(conn):
        return False
    return bool(conn.execute("""SELECT 1 FROM product_identity_links WHERE source_id=? AND source_product_id=?
        AND canonical_product_id=? AND status='accepted' AND identity_status='identity_verified'""",
        (source_id, str(source_product_id), canonical_id)).fetchone())


def publication_block(conn, candidate):
    # Legacy web/search matches have no independently resolved source identity.
    # Store the result as candidate evidence, never publish it as full INCI.
    unverified_lookup = {'openbeauty_api','skinsafe_high_match',
        'langsearch_web_search','skinsafe_matched','jev_verified_skinsafe',
        'gemini_verified_skinsafe','pending_tier7_match'}
    if candidate.source_name in unverified_lookup:
        return 'Unverified external reference identity: record evidence and review exact product before publication.'

    columns = {r[1] for r in conn.execute('PRAGMA table_info(canonical_products)')}
    if 'identity_status' not in columns:
        return 'Identity trust schema is required before publication.'
    if 'identity_status' in columns:
        row = conn.execute('SELECT identity_status FROM canonical_products WHERE id=?', (candidate.canonical_product_id,)).fetchone()
        if row and row[0] != 'verified':
            return 'Canonical identity is unverified or superseded.'
    if pending_identity_review(conn, candidate.canonical_product_id):
        return 'Pending identity review blocks formulation publication.'
    if candidate.source_name.startswith('source:'):
        row = conn.execute('SELECT source_id,source_product_id FROM source_product_observations WHERE id=?', (candidate.source_record_id,)).fetchone()
        if not row or not verified_link(conn, row[0], candidate.validation_flags.get('identity_link_product_id') or row[1], candidate.canonical_product_id):
            return 'Source identity link has not been verified.'
    else:
        origin = candidate.validation_flags.get('origin_product_id') or candidate.source_record_id
        row = conn.execute('SELECT id,store_id,store_product_id FROM products WHERE id=? AND canonical_product_id=?',
                           (origin, candidate.canonical_product_id)).fetchone() if origin else None
        if candidate.validation_flags.get('origin_product_id') and not row:
            return 'Origin retailer association does not match the canonical identity.'
        if row and not verified_link(conn, row[1], row[2] or str(row[0]), candidate.canonical_product_id):
            return 'Retailer-derived candidate requires an identity-verified association.'
        if candidate.source_name in ('store_page_tab','historical_store_scrape') and not row:
            return 'Legacy retailer association is not identity-verified.'
    return None
