"""Guarded, auditable reconciliation of clearly orphaned catalog task *records*.

This is not cancellation, not a job supervisor and not catalog reset. It
never mutates product, observation, offer, formulation or review tables.
Operators must independently verify that no external worker is still alive.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pipeline.catalog_reset import _active_workers, _tables

CONFIRMATION = 'RECONCILE STALE TASK RECORDS'
MINIMUM_AGE_HOURS = 6


def _record_age_hours(row, now):
    try:
        stamp = datetime.fromisoformat(str(row['updated_at']).replace(' ', 'T').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0., round((now-stamp).total_seconds()/3600.,2))
    except (ValueError, TypeError, OverflowError):
        return None


def _candidate_reason(row, age):
    """Only completed-stage legacy operations or test-generated artifacts.

    Unknown work and discovery are NEVER candidates, irrespective of age.
    """
    if age is None or age < MINIMUM_AGE_HOURS:
        return None
    name = str(row['task_type'] or '')
    stage = str(row['current_stage'] or '')
    if name == 'catalog_discovery':
        return None
    if re.fullmatch(r'api_[a-z0-9_]+_test', name) and not stage:
        return 'orphaned_test_task'
    if name in ('refresh_catalog','refresh_store') and re.fullmatch(r'finished_store_[a-zA-Z0-9_-]+',stage):
        return 'refresh_finished_store_but_task_not_closed'
    if name in ('batch_match_inci','batch_fill_ingredients'):
        match = re.fullmatch(r'processed_item_(\d+)_of_(\d+)',stage)
        if match and int(match.group(2)) > 0 and int(match.group(1)) == int(match.group(2)):
            return 'all_batch_items_processed_but_task_not_closed'
    return None


def _snapshot(conn):
    tables = _tables(conn)
    if 'pipeline_tasks' not in tables:
        return [], []
    rows = [dict(r) for r in conn.execute("""SELECT task_id,task_type,status,current_stage,updated_at,
            started_at,completed_at,error_message
        FROM pipeline_tasks WHERE status='running'
        ORDER BY updated_at DESC,task_id""")]
    now = datetime.now(timezone.utc)
    candidates=[]
    not_eligible=[]
    for row in rows:
        age = _record_age_hours(row,now)
        reason = _candidate_reason(row,age)
        item = {**row,'age_hours':age}
        if reason:
            item['reason']=reason
            candidates.append(item)
        else:
            item['reason']='recent_or_unknown_active_work'
            not_eligible.append(item)
    return candidates, not_eligible


def _signature(candidates, not_eligible):
    # Include ALL running records, not only candidates. An operator must
    # refresh preview if an unrelated job starts before confirmation.
    fields=('task_id','task_type','status','current_stage','updated_at','started_at')
    rows=[{k:item.get(k) for k in fields} for item in candidates+not_eligible]
    rows.sort(key=lambda x:x['task_id'])
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()


def preview_stale_task_reconciliation(conn):
    """Read-only; no state changes during preview."""
    tables=_tables(conn)
    candidates, excluded = _snapshot(conn)
    workers=_active_workers(conn,tables)
    lock=bool('catalog_reset_control' in tables and conn.execute(
        'SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone()
        and conn.execute('SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone()[0])
    errors=[]
    if not candidates:
        errors.append('No legacy task records meet the strict stale-stage and age rules.')
    if workers:
        errors.append('A catalog, Gemini or INCI worker is active; do not reconcile while it runs.')
    if lock:
        errors.append('Catalog maintenance is already in progress.')
    return {'can_reconcile':not errors, 'errors':errors,
            'fingerprint':_signature(candidates,excluded),
            'minimum_age_hours':MINIMUM_AGE_HOURS,
            'candidates':candidates,'ineligible_running':excluded,
            'active_workers':workers,'maintenance_locked':lock,
            'confirmation_phrase':CONFIRMATION,
            'note':'Preview classifies records, not live processes. The operator must independently verify that no other backend/process is performing these tasks.'}


def reconcile_stale_task_records(conn, fingerprint, confirmation, actor, workers_checked):
    if confirmation != CONFIRMATION:
        raise ValueError('Exact confirmation phrase required.')
    if not actor or not actor.strip():
        raise ValueError('Authenticated operations reviewer is required.')
    if workers_checked is not True:
        raise ValueError('Confirm you inspected all backend processes and no listed task is executing.')
    try:
        conn.execute('BEGIN IMMEDIATE')
        plan=preview_stale_task_reconciliation(conn)
        if plan['fingerprint'] != fingerprint:
            raise ValueError('Task activity changed since the preview; refresh before applying.')
        if not plan['can_reconcile']:
            raise ValueError('; '.join(plan['errors']))
        conn.execute("""CREATE TABLE IF NOT EXISTS catalog_task_reconciliation_audit(
            id INTEGER PRIMARY KEY,reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actor TEXT NOT NULL,task_id TEXT NOT NULL,old_snapshot_json TEXT NOT NULL,
            reconciliation_reason TEXT NOT NULL,preview_hash TEXT NOT NULL)""")
        for item in plan['candidates']:
            cursor=conn.execute("""UPDATE pipeline_tasks SET status='failed',
                completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP),
                error_message=?,updated_at=CURRENT_TIMESTAMP
                WHERE task_id=? AND status='running' AND updated_at=?
                  AND current_stage IS ?""",
                ('Administrative reconciliation of orphaned task record; '+item['reason']+
                 '. No worker was cancelled by this change.',item['task_id'],
                 item['updated_at'],item['current_stage']))
            if cursor.rowcount!=1:
                raise ValueError('A candidate was changed concurrently; nothing was reconciled.')
            conn.execute("""INSERT INTO catalog_task_reconciliation_audit
                (actor,task_id,old_snapshot_json,reconciliation_reason,preview_hash)
                VALUES(?,?,?,?,?)""",(actor,item['task_id'],
                    json.dumps(item,sort_keys=True),item['reason'],fingerprint))
        conn.commit()
        return {'status':'reconciled','reconciled_count':len(plan['candidates']),
                'task_ids':[c['task_id'] for c in plan['candidates']],
                'still_running_count':len(plan['ineligible_running']),
                'audit_table':'catalog_task_reconciliation_audit',
                'catalog_rows_modified':0}
    except Exception:
        conn.rollback()
        raise
