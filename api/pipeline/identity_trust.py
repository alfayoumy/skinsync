"""Database-enforced identity trust at formulation publication boundaries."""
import json


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def pending_identity_review(conn, canonical_id):
    present = tables(conn)
    if 'product_review_queue' in present:
        for row in conn.execute("SELECT canonical_product_id,details FROM product_review_queue WHERE status='pending' AND kind IN ('identity','duplicate')"):
            if row[0] == canonical_id:
                return True
            details = json.loads(row[1])
            if canonical_id in details.get('canonical_ids', []):
                return True
            decision = details.get('identity') or details.get('decision') or {}
            if any(c.get('canonical_product_id') == canonical_id for c in decision.get('candidates', [])):
                return True
    if 'canonical_identity_reviews' in present:
        for row in conn.execute("SELECT canonical_ids FROM canonical_identity_reviews WHERE status='pending'"):
            if canonical_id in json.loads(row[0]):
                return True
    return False


def verified_link(conn, source_id, source_product_id, canonical_id):
    if 'product_identity_links' not in tables(conn):
        return False
    return bool(conn.execute("""SELECT 1 FROM product_identity_links WHERE source_id=? AND source_product_id=?
        AND canonical_product_id=? AND status='accepted' AND identity_status='identity_verified'""",
        (source_id, str(source_product_id), canonical_id)).fetchone())


def publication_block(conn, candidate):
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
