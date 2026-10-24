"""Conservative canonical identity assessment and durable human review cases.

A normalized title is a search key, never permission to merge durable entities.
Review decisions acknowledge evidence; they do not delete, merge, split or publish.
"""
import hashlib
import json
from collections import defaultdict
from itertools import combinations

from pipeline.identity_gates import validate_identity_gates
from pipeline.inci_tokenizer import tokenize_inci_string
from pipeline.formulation_service import FormulationRepository


def init_identity_review_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS canonical_identity_reviews (
        id INTEGER PRIMARY KEY, case_key TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        canonical_ids TEXT NOT NULL, offer_ids TEXT NOT NULL,
        details TEXT NOT NULL, evidence_hash TEXT NOT NULL,
        reviewed_by TEXT, review_notes TEXT, reviewed_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")


def record_identity_review(conn, kind, canonical_ids, offer_ids, details):
    init_identity_review_db(conn)
    canonical_ids, offer_ids = sorted(set(canonical_ids)), sorted(set(offer_ids))
    key = json.dumps([kind, canonical_ids, offer_ids], separators=(',', ':'))
    evidence = json.dumps(details, sort_keys=True)
    fingerprint = hashlib.sha256(evidence.encode()).hexdigest()
    conn.execute("""INSERT INTO canonical_identity_reviews
        (case_key,kind,canonical_ids,offer_ids,details,evidence_hash)
        VALUES (?,?,?,?,?,?) ON CONFLICT(case_key) DO UPDATE SET
        details=excluded.details, evidence_hash=excluded.evidence_hash,
        status=CASE WHEN evidence_hash=excluded.evidence_hash THEN status ELSE 'pending' END,
        reviewed_by=CASE WHEN evidence_hash=excluded.evidence_hash THEN reviewed_by ELSE NULL END,
        review_notes=CASE WHEN evidence_hash=excluded.evidence_hash THEN review_notes ELSE NULL END,
        reviewed_at=CASE WHEN evidence_hash=excluded.evidence_hash THEN reviewed_at ELSE NULL END,
        updated_at=CURRENT_TIMESTAMP""",
        (key, kind, json.dumps(canonical_ids), json.dumps(offer_ids), evidence, fingerprint))
    return conn.execute('SELECT id FROM canonical_identity_reviews WHERE case_key=?', (key,)).fetchone()[0]


def formula_fingerprint(product):
    raw = product.get('ingredients_raw')
    if not raw:
        return None
    validation = tokenize_inci_string(raw, product_title=product.get('canonical_title') or product.get('title'))
    if validation.completeness != 'full' or validation.is_truncated:
        return None
    return FormulationRepository.compute_formula_hash(validation.tokens)


def identity_compatible(a, b):
    return validate_identity_gates(a.get('canonical_title') or a.get('title'),
        b.get('canonical_title') or b.get('title'), a.get('brand'), b.get('brand'))


def assess_canonical_identity(offers, canonicals):
    """Distinguish existing identity, new proposal, duplicate and mixed clusters."""
    by_id = {c['id']: c for c in canonicals}
    anchors = sorted({p['canonical_product_id'] for p in offers
                      if p.get('canonical_product_id') in by_id})
    conflicts = []
    for a, b in combinations(offers, 2):
        compatible, reason = identity_compatible(a, b)
        if not compatible:
            conflicts.append({'offers': [a['id'], b['id']], 'reason': reason})
    for p in offers:
        anchor = by_id.get(p.get('canonical_product_id'))
        if anchor:
            compatible, reason = identity_compatible(p, anchor)
            if not compatible:
                conflicts.append({'offers': [p['id']], 'canonical_id': anchor['id'], 'reason': reason})
    formula_hashes = set()
    for product in offers + [by_id[cid] for cid in anchors]:
        # Matched/whitelist offer fields are legacy assertions, not ground truth.
        if 'canonical_title' not in product and product.get('ingredient_source') not in (
                'store_page_tab', 'historical_store_scrape', 'curated_v10', 'admin_override'):
            continue
        fingerprint = formula_fingerprint(product)
        if fingerprint:
            formula_hashes.add(fingerprint)
    if len(formula_hashes) > 1:
        conflicts.append({'reason': 'Different complete ordered formulations require identity review.',
                          'formula_hashes': sorted(formula_hashes)})
    if conflicts:
        return {'decision': 'mixed_cluster', 'canonical_ids': anchors, 'conflicts': conflicts}
    if len(anchors) > 1:
        return {'decision': 'duplicate_canonicals', 'canonical_ids': anchors}
    if anchors:
        return {'decision': 'existing_canonical', 'canonical_id': anchors[0]}
    return {'decision': 'propose_new_canonical', 'canonical_ids': []}


def audit_canonical_identity(conn, persist=False):
    """Read-only by default; persist findings in the caller's transaction if asked."""
    # Local import avoids a circular dependency with the linker.
    from pipeline.product_linker import canonicalize_brand, clean_and_tokenize
    canonicals = [dict(r) for r in conn.execute('SELECT * FROM canonical_products ORDER BY id')]
    offers = [dict(r) for r in conn.execute('SELECT * FROM products WHERE canonical_product_id IS NOT NULL ORDER BY id')]
    groups, linked = defaultdict(list), defaultdict(list)
    for c in canonicals:
        groups[(canonicalize_brand(c['brand']), c['category'])].append(c)
    for p in offers:
        linked[p['canonical_product_id']].append(p)
    findings = []
    def add(kind, cids, pids, details):
        finding = dict(kind=kind, canonical_ids=cids, offer_ids=pids, details=details)
        if persist:
            finding['review_id'] = record_identity_review(conn, kind, cids, pids, details)
        findings.append(finding)
    for items in groups.values():
        token_sets = {c['id']: clean_and_tokenize(c['canonical_title'], c['brand'])[0] for c in items}
        for a, b in combinations(items, 2):
            x, y = token_sets[a['id']], token_sets[b['id']]
            if not x or not y or len(x & y) / len(x | y) < .8:
                continue
            compatible, _ = identity_compatible(a, b)
            if compatible:
                ha, hb = formula_fingerprint(a), formula_fingerprint(b)
                add('duplicate_canonicals', [a['id'], b['id']], [],
                    {'titles': [a['canonical_title'], b['canonical_title']],
                     'formula_conflict': bool(ha and hb and ha != hb), 'formula_hashes': [ha, hb]})
    for c in canonicals:
        members = linked[c['id']]
        assessment = assess_canonical_identity(members, [c])
        if assessment['decision'] == 'mixed_cluster':
            add('mixed_cluster', [c['id']], [p['id'] for p in members], assessment)
        # Offer ingredients remain evidence only, but disagreements require review.
        hashes = defaultdict(list)
        canonical_hash = formula_fingerprint(c)
        for p in members:
            if p.get('ingredient_source') not in ('store_page_tab', 'historical_store_scrape', 'curated_v10', 'admin_override'):
                continue
            fingerprint = formula_fingerprint(p)
            if fingerprint:
                hashes[fingerprint].append(p['id'])
        if len(set(hashes) | ({canonical_hash} if canonical_hash else set())) > 1:
            add('mixed_formulations', [c['id']], sorted(p for ids in hashes.values() for p in ids),
                {'canonical_hash': canonical_hash, 'offer_hashes': dict(hashes)})
    return {'canonical_count': len(canonicals), 'linked_offer_count': len(offers),
            'findings': findings, 'review_count': len(findings)}


def review_identity_case(conn, review_id, status, reviewer, notes):
    if status not in ('confirmed', 'dismissed') or not reviewer.strip() or not notes.strip():
        raise ValueError('A confirmed/dismissed decision, reviewer and review notes are required.')
    result = conn.execute("""UPDATE canonical_identity_reviews SET status=?,reviewed_by=?,review_notes=?,
        reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (status, reviewer, notes, review_id))
    if not result.rowcount:
        raise ValueError('Identity review case not found.')
