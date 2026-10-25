"""Guarded, backed-up reset of product-derived catalog state.

Old Gemini decisions are archived in the SQLite backup, never replayed
against newly allocated canonical IDs without fresh evidence verification.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

CATALOG_TABLES = (
    'inci_research_queue', 'inci_research_runs',
    'identity_gemini_source_cases', 'identity_gemini_requeue_history',
    'identity_gemini_queue', 'product_review_queue',
    'canonical_redirects', 'canonical_identity_constraints',
    'canonical_identity_history', 'canonical_product_aliases',
    'formulation_resolution_queue', 'formulation_versions',
    'inci_audit_evidence', 'product_offers', 'source_product_variants',
    'product_identity_links', 'source_product_observations',
    'legacy_identity_mapping', 'price_history', 'scrape_checkpoints',
    'pipeline_task_logs', 'pipeline_subtasks', 'pipeline_tasks',
    'sync_runs', 'aggregation_rollouts', 'products', 'canonical_products',
    'canonical_identity_reviews', 'source_evaluations',
    'source_evaluation_labels', 'source_evaluation_results',
    'reconciliation_previews',
)
PRESERVED_TABLES = (
    'sources', 'stores', 'brands', 'brand_aliases', 'product_taxonomy',
    'inci_reference', 'ingredient_synonyms', 'clinical_ingredients',
    'ingredient_whitelist', 'api_rate_limits', 'api_request_log',
)
CONFIRMATION = 'RESET ALL PRODUCT DATA'


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _control_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS catalog_reset_control(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        active INTEGER NOT NULL DEFAULT 0, started_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS catalog_reset_history(
        id INTEGER PRIMARY KEY, completed_at TEXT NOT NULL, actor TEXT NOT NULL,
        backup_path TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
        deleted_counts TEXT NOT NULL)""")


def guard_catalog_writes(conn):
    """Prevent ingestion or new task creation while a reset is in progress."""
    if 'catalog_reset_control' in _tables(conn):
        row = conn.execute('SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone()
        if row and row[0]:
            raise ValueError('Catalog reset in progress: ingestion and job creation are blocked.')


def _active_tasks(conn, tables):
    """Report executing jobs, not idle work awaiting an explicit start.

    Discovery is inserted with status='running' at submission, and a newly
    cancelled discovery can still have an in-flight page. Ordinary pending
    tasks may persist indefinitely without an executing worker and cannot
    block catalog maintenance. Running records are never ignored solely
    because their heartbeat is old; an active job may be slow.
    """
    if 'pipeline_tasks' not in tables:
        return []
    return [dict(row) for row in conn.execute("""SELECT task_id,task_type,status,
            current_stage,updated_at,started_at
        FROM pipeline_tasks
        WHERE (status='running' AND NOT (task_type='catalog_discovery' AND COALESCE(current_stage,'')='worker_stopped'))
        OR (task_type='catalog_discovery' AND status='cancelled'
            AND COALESCE(current_stage,'')!='worker_stopped'
            AND updated_at >= datetime('now','-1 hour'))
        ORDER BY created_at DESC LIMIT 20""")]


def _active_workers(conn, tables):
    names = []
    if 'identity_gemini_queue' in tables and conn.execute(
        "SELECT 1 FROM identity_gemini_queue WHERE status='processing' LIMIT 1").fetchone():
        names.append('identity_gemini_queue')
    if 'inci_research_runs' in tables and conn.execute(
        "SELECT 1 FROM inci_research_runs WHERE status IN ('running','cancelling') LIMIT 1").fetchone():
        names.append('inci_research_worker')
    manager = getattr(sys.modules.get('pipeline.queue_runner'), 'master_queue_manager', None)
    if manager:
        for name in ('jev', 'gemini'):
            if getattr(getattr(manager, name), 'is_running', False):
                names.append(name + '_worker')
    return names


def _deletion_order(conn, selected, existing):
    """Delete FK children first and reject unknown dependent tables."""
    refs = {table: {r[2] for r in conn.execute('PRAGMA foreign_key_list("' + table + '")')}
            for table in existing if not table.startswith('sqlite_')}
    for table in existing - selected:
        if refs.get(table, set()) & selected:
            raise ValueError('Unknown/preserved dependent table ' + table + ' prevents reset.')
    remaining = set(selected)
    result = []
    while remaining:
        children = sorted(t for t in remaining if not any(
            t in refs.get(other, ()) for other in remaining if other != t))
        if not children:
            raise ValueError('Cyclic foreign-key dependencies require a reviewed migration.')
        result.extend(children)
        remaining.difference_update(children)
    return result


def preview_catalog_reset(conn, db_path=None):
    existing = _tables(conn)
    selected = set(CATALOG_TABLES) & existing
    _deletion_order(conn, selected, existing)
    counts = {}
    for table in sorted(selected):
        count, last_id = conn.execute('SELECT COUNT(*),MAX(rowid) FROM "' + table + '"').fetchone()
        counts[table] = {'rows': count, 'max_rowid': last_id}
    active = _active_tasks(conn, existing)
    workers = _active_workers(conn, existing)
    row = conn.execute('SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone() if 'catalog_reset_control' in existing else None
    locked = bool(row and row[0])
    path = db_path or conn.execute('PRAGMA database_list').fetchone()[2]
    fingerprint = hashlib.sha256(json.dumps(
        {'db': str(Path(path).resolve()), 'tables': counts}, sort_keys=True
    ).encode()).hexdigest()
    preserved = {t: conn.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
                 for t in PRESERVED_TABLES if t in existing}
    return {'dry_run': True, 'can_reset': not (active or workers or locked),
            'active_jobs': active, 'active_workers': workers, 'reset_in_progress': locked,
            'tables': counts, 'records_to_delete': sum(x['rows'] for x in counts.values()),
            'preserved_tables': preserved, 'fingerprint': fingerprint,
            'note': 'Gemini decisions and all prior product records are preserved in the backup, not in the new live catalog.'}


def _backup(conn, path):
    directory = Path(os.environ.get('SKINSYNC_CATALOG_BACKUP_DIR') or
                     str(path.parent / 'backups' / 'catalog')).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / ('catalog-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                          + '-' + uuid.uuid4().hex[:8] + '.sqlite3')
    partial = target.with_name(target.name + '.partial')
    try:
        with sqlite3.connect(partial) as destination:
            conn.backup(destination, pages=100)
            ok = destination.execute('PRAGMA quick_check').fetchone()[0]
            if ok != 'ok':
                raise ValueError('SQLite backup integrity check failed: ' + str(ok))
        os.chmod(partial, 0o600)
        os.replace(partial, target)
        return target
    finally:
        if partial.exists():
            partial.unlink()


def execute_catalog_reset(conn, fingerprint, confirmation, actor, db_path=None):
    if confirmation != CONFIRMATION:
        raise ValueError('Type RESET ALL PRODUCT DATA to confirm.')
    if not actor or not actor.strip():
        raise ValueError('Authenticated reset operator required.')
    path = Path(db_path or conn.execute('PRAGMA database_list').fetchone()[2]).resolve()
    if not path.is_file():
        raise ValueError('Reset requires an on-disk SQLite database.')
    snapshot = preview_catalog_reset(conn, str(path))
    if not snapshot['can_reset']:
        raise ValueError('Active jobs, workers or another reset prevent catalog reset.')
    if fingerprint != snapshot['fingerprint']:
        raise ValueError('Catalog changed since preview; refresh the preview.')

    # Claim a durable maintenance lock before copying the SQLite snapshot.
    try:
        conn.execute('BEGIN IMMEDIATE')
        _control_schema(conn)
        latest = preview_catalog_reset(conn, str(path))
        if not latest['can_reset'] or fingerprint != latest['fingerprint']:
            raise ValueError('Catalog changed or a job started; refresh the preview.')
        conn.execute("""INSERT INTO catalog_reset_control(singleton,active,started_at)
            VALUES(1,1,CURRENT_TIMESTAMP)
            ON CONFLICT(singleton) DO UPDATE SET active=1,started_at=CURRENT_TIMESTAMP""")
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    try:
        backup = _backup(conn, path)
        conn.execute('BEGIN IMMEDIATE')
        latest = preview_catalog_reset(conn, str(path))
        if latest['fingerprint'] != fingerprint or latest['active_jobs'] or latest['active_workers']:
            raise ValueError('Catalog changed during backup; reset aborted.')
        existing = _tables(conn)
        order = _deletion_order(conn, set(CATALOG_TABLES) & existing, existing)
        # Evaluation DELETE triggers are suspended only in this transaction.
        triggers = [tuple(r) for r in conn.execute("""SELECT name,sql FROM sqlite_master
            WHERE type='trigger' AND name LIKE 'immutable_%' AND tbl_name IN
            ('source_evaluations','source_evaluation_labels','source_evaluation_results','reconciliation_previews')""")]
        for name, _ in triggers:
            conn.execute('DROP TRIGGER "' + name + '"')
        deleted = {}
        for table in order:
            deleted[table] = conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
            conn.execute('DELETE FROM "' + table + '"')
        for _, sql in triggers:
            conn.execute(sql)
        if 'stores' in existing:
            conn.execute('UPDATE stores SET total_products=0,last_sync_at=NULL')
        if 'sources' in existing:
            conn.execute('UPDATE sources SET last_successful_sync=NULL')
        if list(conn.execute('PRAGMA foreign_key_check')):
            raise ValueError('Foreign-key integrity check failed; reset rolled back.')
        conn.execute("""INSERT INTO catalog_reset_history
            (completed_at,actor,backup_path,snapshot_hash,deleted_counts)
            VALUES(CURRENT_TIMESTAMP,?,?,?,?)""",
            (actor, str(backup), fingerprint, json.dumps(deleted, sort_keys=True)))
        conn.execute('UPDATE catalog_reset_control SET active=0 WHERE singleton=1')
        conn.commit()
        return {'status': 'completed', 'backup_path': str(backup),
                'deleted_counts': deleted, 'records_deleted': sum(deleted.values()),
                'preserved_tables': latest['preserved_tables'],
                'note': 'Old product IDs and Gemini decisions remain in the archived backup only.'}
    except Exception:
        conn.rollback()
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE catalog_reset_control SET active=0 WHERE singleton=1')
            conn.commit()
        except Exception:
            conn.rollback()
        raise
