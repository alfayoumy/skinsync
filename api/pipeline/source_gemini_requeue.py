"""Backed-up rebuild of identity-only Gemini cases from immutable saved observations.

No scraping, provider calls, canonical writes, review decisions, or INCI changes.
The previous identity Gemini verdicts survive in the restorable SQLite backup.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

from pipeline.saved_identity_rematch import (
    _eligible, _from_frozen_observation, rematch_execution_blockers,
)
from pipeline.canonical_identity_resolver import (
    CanonicalIdentityResolver, extract_identity,
)
from pipeline.brand_registry import resolve_brand
from pipeline.catalog_reset import _backup, _control_schema

CONFIRMATION = 'RESET IDENTITY GEMINI AND REQUEUE'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _profile(conn, candidate):
    cid = candidate.get('canonical_product_id')
    if type(cid) is not int:
        return None
    row = conn.execute("""SELECT id,canonical_title,brand,identity_status,identity_attributes
        FROM canonical_products WHERE id=? AND NOT EXISTS (
        SELECT 1 FROM canonical_redirects WHERE canonical_product_id=?)""", (cid, cid)).fetchone()
    if row is None:
        return None
    offers = [dict(x) for x in conn.execute("""SELECT source_id AS retailer,
        title FROM product_offers WHERE canonical_product_id=? ORDER BY id LIMIT 8""", (cid,))]
    legacy = [dict(x) for x in conn.execute("""SELECT store_id AS retailer,
        COALESCE(original_title,title) AS title FROM products
        WHERE canonical_product_id=? ORDER BY id LIMIT 8""", (cid,))]
    accepted = conn.execute("""SELECT COUNT(*) FROM product_identity_links
        WHERE canonical_product_id=? AND status='accepted'""", (cid,)).fetchone()[0]
    # A name-only, unverified orphan is NOT grounded comparison evidence.
    if not (offers or legacy or accepted or row['identity_status']=='verified'):
        return None
    return {
        'canonical_product_id': cid,
        'canonical_title': row['canonical_title'], 'brand': row['brand'],
        'identity_status': row['identity_status'],
        'identity_attributes': json.loads(row['identity_attributes'] or '{}'),
        'comparison': {
            'conflicts': candidate.get('conflicts') or [],
            'unknown': candidate.get('unknown') or [],
            'similarity': candidate.get('similarity'),
        },
        'retailer_listings': (offers + legacy)[:12],
        'accepted_source_link_count': accepted,
    }


def _fresh_payload(conn, observation_id, reviews, cache):
    """Recompute CURRENT comparisons; do not reuse stale Gemini candidates."""
    conn.execute('SAVEPOINT requeue_readonly_identity')
    try:
        obs = _from_frozen_observation(conn, observation_id, cache)
        brand = resolve_brand(obs.raw_brand, conn)
        attrs = extract_identity(obs.raw_title,
            brand['name'] if brand else obs.raw_brand, obs.market, obs.identity_attributes)
        resolver = CanonicalIdentityResolver(conn)
        choices = {}
        variants = obs.variants or [None]
        for variant in variants:
            variant_attrs = variant.attributes if variant else {}
            identity = {**attrs, **variant_attrs}
            key = _json(variant_attrs)
            linked_id = obs.source_product_id
            if variant_attrs:
                linked_id += ':' + hashlib.sha256(key.encode()).hexdigest()[:16]
            answer = resolver.resolve(identity, obs.raw_brand, obs.raw_title,
                obs.source_id, linked_id,
                (variant.gtin if variant else None) or obs.source_gtin, obs.manufacturer_sku)
            if answer.status != 'ambiguous':
                continue
            candidates = list(answer.candidates)
            fallback = answer.details.get('canonical_product_id')
            if type(fallback) is int and not candidates:
                candidates = [{'canonical_product_id': fallback,
                               'unknown': [], 'conflicts': [],
                               'similarity': None}]
            for candidate in candidates:
                profile = _profile(conn, candidate)
                if profile is not None:
                    choices[profile['canonical_product_id']] = profile
        if not choices:
            return None
        # Large competing identity sets should be handled by a person, never
        # silently truncate candidate lists sent to a model.
        if len(choices) > 20:
            return None
        payload = {
            'mode': 'saved_source_identity_candidates_v1',
            'observation_id': observation_id,
            'source': {
                'source_id': obs.source_id,
                'source_product_id': obs.source_product_id,
                'title': obs.raw_title, 'brand': obs.raw_brand,
                'url': obs.source_url, 'gtin': obs.source_gtin,
                'manufacturer_sku': obs.manufacturer_sku,
                'identity_attributes': attrs,
                'variants': [
                    {'title': v.title, 'sku': v.sku, 'gtin': v.gtin,
                     'attributes': v.attributes} for v in obs.variants[:20]
                ],
            },
            'candidates': sorted(choices.values(), key=lambda c: c['canonical_product_id']),
            'note': 'Saved retailer/canonical evidence only; unverified names are not proof of identity.',
        }
        return payload
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT requeue_readonly_identity')
        conn.execute('RELEASE SAVEPOINT requeue_readonly_identity')


def _plan(conn):
    grouped, excluded = _eligible(conn, 10000)
    cache, proposed, skipped = {}, [], Counter()
    for oid, reviews in grouped.items():
        try:
            payload = _fresh_payload(conn, oid, reviews, cache)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            skipped['invalid_saved_evidence'] += len(reviews)
            continue
        if payload is None:
            skipped['no_grounded_current_candidate'] += len(reviews)
            continue
        for review in reviews:
            # One pending review is one tracked Gemini decision. Never
            # send completed, reviewed, or evidence-mismatched decisions.
            proposed.append((review['id'], review['evidence_hash'], _json(payload)))
    return grouped, excluded, proposed, dict(skipped)


def preview_identity_requeue(conn):
    from pipeline.aggregation_schema import ensure_identity_gemini_schema
    ensure_identity_gemini_schema(conn)
    grouped, excluded, proposed, skipped = _plan(conn)
    old = [tuple(x) for x in conn.execute("""SELECT review_id,status,evidence_hash,
        attempts,verdict,confidence,model_name,explanation FROM identity_gemini_queue
        ORDER BY review_id""")]
    counters = {r[0]: r[1] for r in conn.execute(
        'SELECT status,COUNT(*) FROM identity_gemini_queue GROUP BY status')}
    signature = hashlib.sha256(_json([
        proposed, old
    ]).encode()).hexdigest()
    blockers = rematch_execution_blockers(conn)
    return {
        'fingerprint': signature,
        'can_apply': blockers['can_start'] and bool(proposed),
        'blockers': blockers,
        'source_observations': len(grouped),
        'remaining_review_cases': sum(len(rs) for rs in grouped.values()),
        'requeueable_review_cases': len(proposed),
        'skipped': skipped,
        'excluded': excluded,
        'old_queue_count': len(old),
        'old_queue_statuses': counters,
        'gemini_calls': 0,
        'confirmation_phrase': CONFIRMATION,
        'note': 'All previous identity Gemini queue responses are archived in a new full SQLite backup, then removed from the live identity queue. Only evidence-backed unresolved source cases are requeued. Nothing runs Gemini until a separate explicit run.',
    }


def reset_and_requeue_identity(conn, fingerprint, confirmation, db_path=None):
    """Backup + atomic identity queue replacement, with catalog maintenance lock."""
    from pipeline.aggregation_schema import ensure_identity_gemini_schema
    if confirmation != CONFIRMATION:
        raise ValueError('Type RESET IDENTITY GEMINI AND REQUEUE exactly.')
    ensure_identity_gemini_schema(conn)
    path = Path(db_path or conn.execute('PRAGMA database_list').fetchone()[2]).resolve()
    if not path.is_file():
        raise ValueError('Queue reset requires an on-disk SQLite catalog.')
    conn.commit()
    try:
        conn.execute('BEGIN IMMEDIATE')
        before = preview_identity_requeue(conn)
        if not before['can_apply'] or before['fingerprint'] != fingerprint:
            raise ValueError('Queue evidence changed or a worker is active. Refresh preview.')
        _control_schema(conn)
        conn.execute("""INSERT INTO catalog_reset_control(singleton,active,started_at)
            VALUES(1,1,CURRENT_TIMESTAMP)
            ON CONFLICT(singleton) DO UPDATE SET active=1,started_at=CURRENT_TIMESTAMP""")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    try:
        rescue = _backup(conn, path)
        conn.execute('BEGIN IMMEDIATE')
        current = preview_identity_requeue(conn)
        if current['fingerprint'] != fingerprint or current['blockers']['active_jobs'] or current['blockers']['active_workers']:
            raise ValueError('Identity evidence or worker state changed during backup.')
        grouped, excluded, proposed, skipped = _plan(conn)
        old_count = conn.execute('SELECT COUNT(*) FROM identity_gemini_queue').fetchone()[0]
        # Only product identity Gemini; legacy INCI/Gate-4 queues are separate.
        conn.execute('DELETE FROM identity_gemini_queue')
        conn.execute('DELETE FROM identity_gemini_source_cases')
        for rid, evidence_hash, payload in proposed:
            conn.execute("""INSERT INTO identity_gemini_source_cases
                (review_id,evidence_hash,payload_json) VALUES(?,?,?)""",
                (rid,evidence_hash,payload))
            conn.execute("""INSERT INTO identity_gemini_queue
                (review_id,evidence_hash,status,attempts,confidence,verdict,explanation,
                 model_name,task_id,queued_by)
                VALUES (?,?,'queued',0,NULL,NULL,NULL,NULL,NULL,'saved_source_requeue_v1')""",
                (rid,evidence_hash))
        conn.execute("""INSERT INTO identity_gemini_requeue_history
            (backup_path,fingerprint,prior_queue_count,requeued_count,skipped_json)
            VALUES (?,?,?,?,?)""", (str(rescue),fingerprint,old_count,len(proposed),_json(skipped)))
        conn.execute("UPDATE catalog_reset_control SET active=0 WHERE singleton=1")
        conn.commit()
        return {'status': 'completed', 'backup_path': str(rescue),
                'old_identity_queue_deleted': old_count,
                'new_identity_queue_count': len(proposed),
                'skipped': skipped, 'provider_calls': 0,
                'reviews_changed': 0, 'products_changed': 0, 'ingredients_changed': 0}
    except Exception:
        conn.rollback()
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE catalog_reset_control SET active=0 WHERE singleton=1')
            conn.commit()
        except Exception:
            conn.rollback()
        raise
