"""Offline rebuild using a completed, persisted Shopify discovery run.

The legacy product graph and all old approved INCI decisions are discarded.
Raw source observations and their variants are retained only for the sources
in the chosen completed discovery run; fresh canonical identities, offers and
candidate INCI evidence are derived from those recorded payloads.

The maintenance lock is committed before the verified backup and remains
visible to other connections while the rebuild transaction is in progress.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from pipeline.catalog_reset import (
    CATALOG_TABLES, _active_tasks, _active_workers, _backup,
    _control_schema, _deletion_order, _tables,
)
from pipeline.db_manager import DB_PATH, get_connection

SELECTIVE_CONFIRMATION = 'REBUILD FROM SAVED DISCOVERY'
KEEP_TABLES = {'source_product_observations', 'source_product_variants'}
# Process-local progress is best-effort; SQLite keeps the durable job outcome.
_PROGRESS = {}


def _schema(conn):
    _control_schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS catalog_selective_rebuild_jobs(
        job_id TEXT PRIMARY KEY, discovery_task_id TEXT NOT NULL,
        status TEXT NOT NULL, actor TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
        backup_path TEXT, result TEXT, error_message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT)""")


def _snapshot(conn, discovery_task_id=None):
    if 'pipeline_tasks' not in _tables(conn):
        return None, ['No completed tracked discovery is present.']
    if discovery_task_id:
        row = conn.execute("""SELECT * FROM pipeline_tasks
            WHERE task_id=? AND task_type='catalog_discovery'""",
            (discovery_task_id,)).fetchone()
    else:
        row = conn.execute("""SELECT * FROM pipeline_tasks
            WHERE task_type='catalog_discovery'
            ORDER BY created_at DESC, rowid DESC LIMIT 1""").fetchone()
    if not row:
        return None, ['No completed tracked discovery is present.']
    row = dict(row)
    errors = []
    if row['status'] != 'completed':
        errors.append('The selected discovery run has not completed successfully.')
    try:
        params = json.loads(row.get('params') or '{}')
        metadata = json.loads(row.get('metadata') or '{}')
    except (TypeError, ValueError):
        return None, ['Discovery task metadata cannot be parsed.']
    if params.get('dry_run') is not False or metadata.get('dry_run') is not False:
        errors.append('The discovery run was preview-only. Nothing was saved to rebuild.')
    source_ids = params.get('source_ids') or []
    if not isinstance(source_ids, list) or len(source_ids) < 2 or len(set(source_ids)) != len(source_ids):
        errors.append('A multi-source discovery with at least two distinct sources is required.')
        source_ids = []
    reports = metadata.get('sources') or []
    if not isinstance(reports, list):
        reports = []
    by_source = {r.get('source_id'): r for r in reports if isinstance(r, dict)}
    if set(source_ids) != set(by_source):
        errors.append('Source completion reports do not match the selected run.')
    for sid in source_ids:
        report = by_source.get(sid, {})
        if report.get('status') != 'completed' or report.get('catalog_exhausted') is not True:
            errors.append('Source ' + sid + ' has not reached the end of its Shopify catalog.')
    return {'task_id': row['task_id'], 'status': row['status'], 'params': params,
            'source_ids': source_ids, 'reports': by_source}, errors


def _source_coverage(conn, sources):
    counts = {}
    if not sources:
        return counts
    for sid in sources:
        row = conn.execute("""SELECT COUNT(*) FROM
            (SELECT source_product_id FROM source_product_observations
             WHERE source_id=? GROUP BY source_product_id)""", (sid,)).fetchone()
        counts[sid] = row[0]
    return counts


def _catalog_signature(conn):
    existing = _tables(conn)
    return {table: (conn.execute('SELECT COUNT(*),MAX(rowid) FROM "' + table + '"').fetchone()[:])
            for table in CATALOG_TABLES if table in existing}


def _selected_observation_signature(conn, ids):
    # This binds preview to actual source evidence, not just record counts.
    digest = hashlib.sha256()
    for sid in ids:
        for row in conn.execute("""SELECT source_product_id,id,payload_hash FROM
            source_product_observations WHERE source_id=?
            AND id=(SELECT MAX(id) FROM source_product_observations other
               WHERE other.source_id=source_product_observations.source_id
                 AND other.source_product_id=source_product_observations.source_product_id)
            ORDER BY source_product_id""", (sid,)):
            digest.update(json.dumps(tuple(row), separators=(',', ':'), ensure_ascii=False).encode())
            digest.update(b'\\n')
    return digest.hexdigest()


def preview_selective_rebuild(conn, discovery_task_id=None, db_path=None):
    existing = _tables(conn)
    catalog_counts = _catalog_signature(conn)
    task, errors = _snapshot(conn, discovery_task_id)
    sources = task['source_ids'] if task else []
    source_details = {}
    if task:
        for sid in sources:
            source_row = conn.execute('SELECT adapter_type FROM sources WHERE id=?', (sid,)).fetchone()
            if not source_row or source_row[0] != 'shopify':
                errors.append('Source ' + sid + ' must be a registered Shopify source for offline replay.')
            source_details[sid] = {'saved_product_observations': 0,
                                   'fetched_in_run': task['reports'].get(sid, {}).get('fetched', 0)}
    if 'source_product_observations' not in existing:
        errors.append('No saved source observations table is available.')
        coverage = {}
    else:
        coverage = _source_coverage(conn, sources)
    for sid in sources:
        count = coverage.get(sid, 0)
        report = task['reports'].get(sid, {})
        source_details[sid]['saved_product_observations'] = count
        fetched = report.get('fetched') or 0
        if count <= 0 or not isinstance(fetched, int) or fetched <= 0:
            errors.append('No saved product evidence for source ' + sid + '.')
        elif count < fetched * 0.9:
            errors.append('Saved observation coverage for ' + sid + ' is below 90% of scanned products.')
    active_jobs = _active_tasks(conn, existing)
    active_workers = _active_workers(conn, existing)
    if active_jobs:
        errors.append('Other catalog jobs are active or have not fully stopped.')
    if active_workers:
        errors.append('AI or ingredient workers are still processing.')
    maintenance = conn.execute(
        'SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone() if 'catalog_reset_control' in existing else None
    if maintenance and maintenance[0]:
        errors.append('A catalog maintenance operation is already running.')
    try:
        _deletion_order(conn, (set(CATALOG_TABLES) & existing) - KEEP_TABLES, existing)
    except ValueError as exc:
        errors.append(str(exc))
    path = db_path or conn.execute('PRAGMA database_list').fetchone()[2]
    signature = {
        'db': str(Path(path).resolve()), 'task_id': task['task_id'] if task else None,
        'task_status': task['status'] if task else None, 'sources': sources,
        'catalog': catalog_counts, 'saved_observations': coverage,
        'source_evidence_hash': _selected_observation_signature(conn, sources) if coverage else None,
    }
    fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return {'can_rebuild': not errors, 'errors': errors, 'fingerprint': fingerprint,
            'discovery_task_id': task['task_id'] if task else None,
            'source_ids': sources, 'sources': source_details,
            'observations_to_replay': sum(coverage.values()),
            'legacy_products_to_purge': catalog_counts.get('products', (0,))[0],
            'legacy_canonicals_to_purge': catalog_counts.get('canonical_products', (0,))[0],
            'existing_offers_to_replace': catalog_counts.get('product_offers', (0,))[0],
            'old_inci_evidence_to_purge': catalog_counts.get('inci_audit_evidence', (0,))[0],
            'old_gemini_decisions_to_archive': catalog_counts.get('identity_gemini_queue', (0,))[0],
            'note': 'Only the latest saved raw observation per product in the selected completed discovery run sources is kept; all historical product identities, approvals and formulas are rebuilt, not trusted.'}


def start_selective_rebuild(conn, fingerprint, confirmation, actor, discovery_task_id=None, db_path=None):
    if confirmation != SELECTIVE_CONFIRMATION:
        raise ValueError('Type REBUILD FROM SAVED DISCOVERY to confirm.')
    if not actor or not actor.strip():
        raise ValueError('Authenticated reset operator required.')
    path = Path(db_path or conn.execute('PRAGMA database_list').fetchone()[2]).resolve()
    if not path.is_file():
        raise ValueError('Selective rebuild requires an on-disk SQLite database.')
    plan = preview_selective_rebuild(conn, discovery_task_id, str(path))
    if not plan['can_rebuild']:
        raise ValueError('; '.join(plan['errors']))
    if fingerprint != plan['fingerprint']:
        raise ValueError('Catalog changed since preview; refresh the preview.')
    job_id = 'selective_' + uuid.uuid4().hex
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = preview_selective_rebuild(conn, discovery_task_id, str(path))
        if not current['can_rebuild'] or fingerprint != current['fingerprint']:
            raise ValueError('Discovery or catalog changed; refresh the preview.')
        _schema(conn)
        conn.execute("""INSERT INTO catalog_reset_control(singleton,active,started_at)
            VALUES (1,1,CURRENT_TIMESTAMP)
            ON CONFLICT(singleton) DO UPDATE SET active=1,started_at=CURRENT_TIMESTAMP""")
        conn.execute("""INSERT INTO catalog_selective_rebuild_jobs
            (job_id,discovery_task_id,status,actor,snapshot_hash)
            VALUES (?,?,?,?,?)""",
            (job_id, plan['discovery_task_id'], 'queued', actor, fingerprint))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _PROGRESS[job_id] = {'processed': 0, 'total': plan['observations_to_replay']}
    return {'status': 'accepted', 'job_id': job_id,
            'discovery_task_id': plan['discovery_task_id'],
            'observations_to_replay': plan['observations_to_replay']}


def _parse_saved(conn, row, source_cache):
    from pipeline.source_adapters import get_adapter
    sid = row['source_id']
    if sid not in source_cache:
        from pipeline.source_registry import get_source
        source_cache[sid] = get_source(conn, sid)
    raw = json.loads(row['raw_payload'])
    if not isinstance(raw, dict) or not raw:
        raise ValueError('Stored source payload is not a valid Shopify product: observation ' + str(row['id']))
    parsed = get_adapter(source_cache[sid]).parse({'product': raw}, row['source_url'])
    if len(parsed) != 1:
        raise ValueError('A stored observation did not replay to one Shopify product.')
    observation = parsed[0]
    if (observation.source_product_id != row['source_product_id']
            or observation.raw_title != row['raw_title']
            or observation.raw_payload_hash != row['payload_hash']):
        raise ValueError('Source payload/hash differs from stored observation ' + str(row['id'])
                         + '; refusing to rebuild with changed evidence.')
    actual_variants = {r[0] for r in conn.execute(
        'SELECT source_variant_id FROM source_product_variants WHERE observation_id=?', (row['id'],))}
    if actual_variants != {v.source_variant_id for v in observation.variants}:
        raise ValueError('Stored variant identities differ from the source payload at observation ' + str(row['id']))
    observation.observed_at = row['observed_at']
    return observation


def _drop_immutable_triggers(conn):
    triggers = [tuple(r) for r in conn.execute("""SELECT name,sql FROM sqlite_master
        WHERE type='trigger' AND name LIKE 'immutable_%' AND tbl_name IN
        ('source_evaluations','source_evaluation_labels','source_evaluation_results','reconciliation_previews')""")]
    for name, _ in triggers:
        conn.execute('DROP TRIGGER "' + name + '"')
    return triggers


def _run_transaction(conn, plan, actor, job_id, backup):
    from pipeline.aggregation_service import process_source_observation
    existing = _tables(conn)
    selected = set(CATALOG_TABLES) & existing - KEEP_TABLES
    order = _deletion_order(conn, selected, existing)
    keep_sql = ','.join('?' for _ in plan['source_ids'])
    conn.execute("""CREATE TEMP TABLE selective_keep_obs AS
        SELECT MAX(id) AS id FROM source_product_observations
        WHERE source_id IN (""" + keep_sql + """) GROUP BY source_id,source_product_id""",
        tuple(plan['source_ids']))
    actual = conn.execute('SELECT COUNT(*) FROM selective_keep_obs').fetchone()[0]
    if actual != plan['observations_to_replay'] or not actual:
        raise ValueError('Saved observation cohort changed before the rebuild.')

    triggers = _drop_immutable_triggers(conn)
    deleted = {}
    for table in order:
        deleted[table] = conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
        conn.execute('DELETE FROM "' + table + '"')
    conn.execute("""DELETE FROM source_product_variants
        WHERE observation_id NOT IN (SELECT id FROM selective_keep_obs)""")
    conn.execute("""DELETE FROM source_product_observations
        WHERE id NOT IN (SELECT id FROM selective_keep_obs)""")
    if 'stores' in existing:
        conn.execute('UPDATE stores SET total_products=0,last_sync_at=NULL')
    if 'sources' in existing:
        conn.execute('UPDATE sources SET last_successful_sync=NULL')
    # Uncommitted clear: this connection can replay via the ordinary ingestion
    # safeguards; concurrent connections still see the committed maintenance lock
    # and the transaction holds SQLite's writer lock until completion.
    conn.execute('UPDATE catalog_reset_control SET active=0 WHERE singleton=1')
    stats = {'replayed': 0, 'matched': 0, 'provisional': 0,
             'reviews': 0, 'excluded': 0, 'offers_seen': 0}
    sources = {}
    rows = conn.execute("""SELECT o.* FROM source_product_observations o
        JOIN selective_keep_obs k ON k.id=o.id ORDER BY o.source_id,o.id""")
    for row in rows:
        item = _parse_saved(conn, row, sources)
        replay = process_source_observation(conn, item, existing_observation_id=row['id'])
        stats['replayed'] += 1
        for result in replay['results']:
            state = result['status']
            if state == 'review':
                stats['reviews'] += 1
            elif state in stats:
                stats[state] += 1
            stats['offers_seen'] += len(result.get('offer_ids') or [])
        if stats['replayed'] % 50 == 0:
            _PROGRESS[job_id] = {'processed': stats['replayed'], 'total': actual}
    if stats['replayed'] != actual:
        raise ValueError('An incomplete source observation replay cannot be published.')
    canonicals = conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
    offers = conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0]
    if not canonicals or not offers:
        raise ValueError('Offline replay did not reconstruct canonical products and offers.')
    for _, sql in triggers:
        conn.execute(sql)
    fk = list(conn.execute('PRAGMA foreign_key_check'))
    if fk:
        raise ValueError('Selective rebuild violated foreign keys; rolling back.')
    stats.update({'canonicals': canonicals, 'offers': offers,
                  'retained_observations': actual,
                  'old_product_rows_removed': deleted.get('products', 0),
                  'old_canonical_rows_removed': deleted.get('canonical_products', 0)})
    conn.execute("""INSERT INTO catalog_reset_history
        (completed_at,actor,backup_path,snapshot_hash,deleted_counts)
        VALUES(CURRENT_TIMESTAMP,?,?,?,?)""",
        (actor, str(backup), plan['fingerprint'],
         json.dumps({'mode': 'selective', 'removed': deleted, 'replay': stats}, sort_keys=True)))
    conn.execute('UPDATE catalog_selective_rebuild_jobs SET status=?, backup_path=?,result=?,completed_at=CURRENT_TIMESTAMP WHERE job_id=?',
        ('completed', str(backup), json.dumps(stats, sort_keys=True), job_id))
    return stats


def run_selective_rebuild(job_id, db_path=None):
    """FastAPI background job. Success is one atomic transaction, otherwise rollback."""
    path = Path(db_path or DB_PATH).resolve()
    conn = get_connection(str(path))
    backup = None
    try:
        job = conn.execute('SELECT * FROM catalog_selective_rebuild_jobs WHERE job_id=?', (job_id,)).fetchone()
        if not job or job['status'] != 'queued':
            raise ValueError('Selective rebuild job was not queued.')
        conn.execute("UPDATE catalog_selective_rebuild_jobs SET status='running' WHERE job_id=?", (job_id,))
        conn.commit()
        # A lock holder can preview ignoring its own maintenance flag by
        # checking only invariant fields and the actual evidence fingerprint.
        plan = preview_selective_rebuild(conn, job['discovery_task_id'], str(path))
        blocked = [e for e in plan['errors'] if e != 'A catalog maintenance operation is already running.']
        if blocked or plan['fingerprint'] != job['snapshot_hash']:
            raise ValueError('Evidence changed while the rebuild was queued: ' + '; '.join(blocked))
        backup = _backup(conn, path)
        conn.execute('BEGIN IMMEDIATE')
        latest = preview_selective_rebuild(conn, job['discovery_task_id'], str(path))
        blocked = [e for e in latest['errors'] if e != 'A catalog maintenance operation is already running.']
        if blocked or latest['fingerprint'] != job['snapshot_hash']:
            raise ValueError('Evidence changed during backup; rebuild aborted.')
        stats = _run_transaction(conn, latest, job['actor'], job_id, backup)
        conn.commit()
        _PROGRESS[job_id] = {'processed': stats['replayed'], 'total': stats['replayed']}
    except Exception as exc:
        conn.rollback()
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("""UPDATE catalog_selective_rebuild_jobs
            SET status='failed',backup_path=?,error_message=?,completed_at=CURRENT_TIMESTAMP WHERE job_id=?""",
            (str(backup) if backup else None, str(exc)[:1500], job_id))
        conn.execute('UPDATE catalog_reset_control SET active=0 WHERE singleton=1')
        conn.commit()
    finally:
        _PROGRESS.pop(job_id, None)
        conn.close()


def selective_rebuild_status(conn, job_id):
    row = conn.execute('SELECT * FROM catalog_selective_rebuild_jobs WHERE job_id=?',(job_id,)).fetchone()
    if not row:
        raise ValueError('Unknown selective rebuild job.')
    result = dict(row)
    result['result'] = json.loads(result['result']) if result['result'] else None
    result['progress'] = _PROGRESS.get(job_id)
    return result
