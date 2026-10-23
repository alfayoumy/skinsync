"""
Pipeline Task & Subtask Progress Tracker, Execution Logger, and Control Table Manager.
Provides thread-safe progress tracking, event logging with timestamps,
and scraping checkpoints for data ingestion and catalog refreshes.
"""

import os
import sys
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union

from pipeline.db_manager import get_connection

from pipeline.logging_config import setup_logging, get_logger
logger = get_logger("skincare_pipeline.task_tracker")

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _calc_duration(started_at_str: Optional[str]) -> float:
    if not started_at_str:
        return 0.0
    try:
        st = datetime.fromisoformat(started_at_str.replace(" ", "T"))
        now = datetime.now(timezone.utc)
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        return round(max(0.0, (now - st).total_seconds()), 2)
    except Exception:
        return 0.0

def _generate_id(prefix: str = "task") -> str:
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand_hex = uuid.uuid4().hex[:8]
    return f"{prefix}_{timestamp_str}_{rand_hex}"

# ==============================================================================
# 1. TASK MANAGEMENT
# ==============================================================================

def create_task(
    task_type: str,
    target_source: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    fields: Optional[List[str]] = None,
    total_items: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    auto_start: bool = False,
    conn = None
) -> str:
    """
    Creates a new pipeline task in the control table with pending/running status.
    Returns the unique task_id.
    """
    task_id = _generate_id("task")
    status = "running" if auto_start else "pending"
    started_at = _now_iso() if auto_start else None

    fields_json = json.dumps(fields) if fields is not None else None
    params_json = json.dumps(params) if params is not None else None
    meta_json = json.dumps(metadata) if metadata is not None else None

    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO pipeline_tasks (
            task_id, task_type, target_source, status, fields, params,
            total_items, metadata, started_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        """, (
            task_id, task_type, target_source, status, fields_json, params_json,
            total_items, meta_json, started_at
        ))
        conn.commit()

        # Initial task log
        log_task_event(
            task_id=task_id,
            message=f"Pipeline task '{task_type}' created for target '{target_source or 'all'}'.",
            level="INFO",
            context_data={"total_items": total_items, "params": params, "fields": fields},
            conn=conn
        )
        return task_id
    finally:
        if should_close:
            conn.close()

def start_task(task_id: str, conn = None):
    """Marks a pending task as running with current timestamp."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        UPDATE pipeline_tasks
        SET status = 'running',
            started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP
        WHERE task_id = ?;
        """, (task_id,))
        conn.commit()
        log_task_event(task_id=task_id, message="Task started execution.", level="INFO", conn=conn)
    finally:
        if should_close:
            conn.close()

def update_task_progress(
    task_id: str,
    processed_items: Optional[int] = None,
    successful_items: Optional[int] = None,
    failed_items: Optional[int] = None,
    skipped_items: Optional[int] = None,
    total_items: Optional[int] = None,
    current_stage: Optional[str] = None,
    progress_percent: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    conn = None
):
    """
    Updates progress counters, stage, and percentage for a running task.
    """
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        # Retrieve current totals if calculating percent
        cur.execute("SELECT total_items, processed_items FROM pipeline_tasks WHERE task_id = ?;", (task_id,))
        row = cur.fetchone()
        if not row:
            return

        cur_total = total_items if total_items is not None else row["total_items"]
        cur_proc = processed_items if processed_items is not None else row["processed_items"]

        calc_pct = progress_percent
        if calc_pct is None and cur_total and cur_total > 0 and cur_proc is not None:
            calc_pct = round(min(100.0, (cur_proc / cur_total) * 100.0), 2)

        upd_cols = ["updated_at = CURRENT_TIMESTAMP"]
        upd_vals = []

        if processed_items is not None:
            upd_cols.append("processed_items = ?")
            upd_vals.append(processed_items)
        if successful_items is not None:
            upd_cols.append("successful_items = ?")
            upd_vals.append(successful_items)
        if failed_items is not None:
            upd_cols.append("failed_items = ?")
            upd_vals.append(failed_items)
        if skipped_items is not None:
            upd_cols.append("skipped_items = ?")
            upd_vals.append(skipped_items)
        if total_items is not None:
            upd_cols.append("total_items = ?")
            upd_vals.append(total_items)
        if current_stage is not None:
            upd_cols.append("current_stage = ?")
            upd_vals.append(current_stage)
        if calc_pct is not None:
            upd_cols.append("progress_percent = ?")
            upd_vals.append(calc_pct)
        if metadata is not None:
            upd_cols.append("metadata = ?")
            upd_vals.append(json.dumps(metadata))

        upd_vals.append(task_id)
        cur.execute(f"UPDATE pipeline_tasks SET {', '.join(upd_cols)} WHERE task_id = ?;", tuple(upd_vals))
        conn.commit()
    finally:
        if should_close:
            conn.close()

def complete_task(
    task_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "completed",
    conn = None
) -> Dict[str, Any]:
    """
    Marks a task as completed/successful, calculating duration in seconds.
    """
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("SELECT started_at, total_items, processed_items FROM pipeline_tasks WHERE task_id = ?;", (task_id,))
        row = cur.fetchone()
        duration = _calc_duration(row["started_at"]) if row else 0.0

        meta_json = json.dumps(metadata) if metadata is not None else None
        cur.execute("""
        UPDATE pipeline_tasks
        SET status = ?,
            progress_percent = 100.0,
            completed_at = CURRENT_TIMESTAMP,
            duration_seconds = ?,
            metadata = COALESCE(?, metadata),
            updated_at = CURRENT_TIMESTAMP
        WHERE task_id = ?;
        """, (status, duration, meta_json, task_id))
        conn.commit()

        log_task_event(
            task_id=task_id,
            message=f"Task completed successfully with status '{status}' in {duration}s.",
            level="INFO",
            context_data=metadata,
            conn=conn
        )
        return {"task_id": task_id, "status": status, "duration_seconds": duration}
    finally:
        if should_close:
            conn.close()

def fail_task(
    task_id: str,
    error_message: str,
    metadata: Optional[Dict[str, Any]] = None,
    conn = None
) -> Dict[str, Any]:
    """
    Marks a task as failed with an error message and logs the failure.
    """
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("SELECT started_at FROM pipeline_tasks WHERE task_id = ?;", (task_id,))
        row = cur.fetchone()
        duration = _calc_duration(row["started_at"]) if row else 0.0

        meta_json = json.dumps(metadata) if metadata is not None else None
        cur.execute("""
        UPDATE pipeline_tasks
        SET status = 'failed',
            error_message = ?,
            completed_at = CURRENT_TIMESTAMP,
            duration_seconds = ?,
            metadata = COALESCE(?, metadata),
            updated_at = CURRENT_TIMESTAMP
        WHERE task_id = ?;
        """, (str(error_message), duration, meta_json, task_id))
        conn.commit()

        log_task_event(
            task_id=task_id,
            message=f"Task failed: {error_message}",
            level="ERROR",
            context_data=metadata,
            conn=conn
        )
        return {"task_id": task_id, "status": "failed", "error": error_message, "duration_seconds": duration}
    finally:
        if should_close:
            conn.close()

def cancel_task(task_id: str, reason: str = "User cancelled", conn = None) -> bool:
    """Cancels a pending or running task."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        UPDATE pipeline_tasks
        SET status = 'cancelled',
            error_message = ?,
            completed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE task_id = ? AND status IN ('pending', 'running');
        """, (reason, task_id))
        affected = cur.rowcount
        conn.commit()
        if affected > 0:
            log_task_event(task_id=task_id, message=f"Task cancelled. Reason: {reason}", level="WARNING", conn=conn)
            return True
        return False
    finally:
        if should_close:
            conn.close()

# ==============================================================================
# 2. SUBTASK MANAGEMENT
# ==============================================================================

def create_subtask(
    task_id: str,
    step_name: str,
    step_index: int = 0,
    total_steps: int = 1,
    metadata: Optional[Dict[str, Any]] = None,
    auto_start: bool = True,
    conn = None
) -> str:
    """
    Creates and optionally starts a subtask record under a parent task.
    Returns subtask_id.
    """
    subtask_id = _generate_id("subtask")
    status = "running" if auto_start else "pending"
    started_at = _now_iso() if auto_start else None
    meta_json = json.dumps(metadata) if metadata is not None else None

    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO pipeline_subtasks (
            subtask_id, task_id, step_name, step_index, total_steps,
            status, metadata, started_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        """, (subtask_id, task_id, step_name, step_index, total_steps, status, meta_json, started_at))
        conn.commit()

        if auto_start:
            log_task_event(
                task_id=task_id,
                subtask_id=subtask_id,
                message=f"Subtask '{step_name}' [Step {step_index}/{total_steps}] started.",
                level="DEBUG",
                context_data=metadata,
                conn=conn
            )
        return subtask_id
    finally:
        if should_close:
            conn.close()

def complete_subtask(
    subtask_id: str,
    items_count: int = 0,
    items_succeeded: int = 0,
    items_failed: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    conn = None
):
    """Marks a subtask completed with item stats and duration."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("SELECT task_id, step_name, started_at FROM pipeline_subtasks WHERE subtask_id = ?;", (subtask_id,))
        row = cur.fetchone()
        if not row:
            return

        duration = _calc_duration(row["started_at"])

        meta_json = json.dumps(metadata) if metadata is not None else None
        cur.execute("""
        UPDATE pipeline_subtasks
        SET status = 'completed',
            items_count = ?,
            items_succeeded = ?,
            items_failed = ?,
            completed_at = CURRENT_TIMESTAMP,
            duration_seconds = ?,
            metadata = COALESCE(?, metadata),
            updated_at = CURRENT_TIMESTAMP
        WHERE subtask_id = ?;
        """, (items_count, items_succeeded, items_failed, duration, meta_json, subtask_id))
        conn.commit()

        log_task_event(
            task_id=row["task_id"],
            subtask_id=subtask_id,
            message=f"Subtask '{row['step_name']}' completed ({items_succeeded}/{items_count} items succeeded in {duration}s).",
            level="DEBUG",
            context_data=metadata,
            conn=conn
        )
    finally:
        if should_close:
            conn.close()

def fail_subtask(
    subtask_id: str,
    error_message: str,
    conn = None
):
    """Marks a subtask as failed with an error message."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("SELECT task_id, step_name, started_at FROM pipeline_subtasks WHERE subtask_id = ?;", (subtask_id,))
        row = cur.fetchone()
        if not row:
            return

        duration = _calc_duration(row["started_at"])

        cur.execute("""
        UPDATE pipeline_subtasks
        SET status = 'failed',
            error_message = ?,
            completed_at = CURRENT_TIMESTAMP,
            duration_seconds = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE subtask_id = ?;
        """, (str(error_message), duration, subtask_id))
        conn.commit()

        log_task_event(
            task_id=row["task_id"],
            subtask_id=subtask_id,
            message=f"Subtask '{row['step_name']}' failed: {error_message}",
            level="ERROR",
            conn=conn
        )
    finally:
        if should_close:
            conn.close()

# ==============================================================================
# 3. TASK EXECUTION EVENT LOGGING
# ==============================================================================

def log_task_event(
    task_id: str,
    message: str,
    level: str = "INFO",
    subtask_id: Optional[str] = None,
    context_data: Optional[Dict[str, Any]] = None,
    conn = None
):
    """
    Records an execution log entry for a task or subtask with ISO timestamp.
    """
    ctx_json = json.dumps(context_data) if context_data is not None else None
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO pipeline_task_logs (task_id, subtask_id, level, message, context_data, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
        """, (task_id, subtask_id, level.upper(), message, ctx_json))
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to record pipeline log event for {task_id}: {e}")
    finally:
        if should_close:
            conn.close()

# ==============================================================================
# 4. SCRAPE STATE & CHECKPOINTS
# ==============================================================================

def get_scrape_checkpoint(store_id: str, conn = None) -> Optional[Dict[str, Any]]:
    """Returns the current scrape checkpoint for a store."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT store_id, last_task_id, last_page_scraped, total_pages_detected,
               total_products_scraped, last_scraped_at, last_successful_sync_at,
               sync_cursor, status, metadata, updated_at
        FROM scrape_checkpoints
        WHERE store_id = ?;
        """, (store_id,))
        row = cur.fetchone()
        if not row:
            return None
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        return {
            "store_id": row["store_id"],
            "last_task_id": row["last_task_id"],
            "last_page_scraped": row["last_page_scraped"],
            "total_pages_detected": row["total_pages_detected"],
            "total_products_scraped": row["total_products_scraped"],
            "last_scraped_at": row["last_scraped_at"],
            "last_successful_sync_at": row["last_successful_sync_at"],
            "sync_cursor": row["sync_cursor"],
            "status": row["status"],
            "metadata": meta,
            "updated_at": row["updated_at"]
        }
    finally:
        if should_close:
            conn.close()

def list_scrape_checkpoints(conn = None) -> List[Dict[str, Any]]:
    """Returns all scrape checkpoints across stores."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT c.store_id, s.name as store_name, c.last_task_id, c.last_page_scraped,
               c.total_pages_detected, c.total_products_scraped, c.last_scraped_at,
               c.last_successful_sync_at, c.sync_cursor, c.status, c.metadata, c.updated_at
        FROM scrape_checkpoints c
        LEFT JOIN stores s ON c.store_id = s.id
        ORDER BY c.store_id ASC;
        """)
        rows = cur.fetchall()
        result = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            result.append({
                "store_id": r["store_id"],
                "store_name": r["store_name"] or r["store_id"],
                "last_task_id": r["last_task_id"],
                "last_page_scraped": r["last_page_scraped"],
                "total_pages_detected": r["total_pages_detected"],
                "total_products_scraped": r["total_products_scraped"],
                "last_scraped_at": r["last_scraped_at"],
                "last_successful_sync_at": r["last_successful_sync_at"],
                "sync_cursor": r["sync_cursor"],
                "status": r["status"],
                "metadata": meta,
                "updated_at": r["updated_at"]
            })
        return result
    finally:
        if should_close:
            conn.close()

def update_scrape_checkpoint(
    store_id: str,
    task_id: Optional[str] = None,
    page_scraped: Optional[int] = None,
    total_pages: Optional[int] = None,
    products_count: Optional[int] = None,
    is_success: bool = True,
    sync_cursor: Optional[str] = None,
    status: str = "idle",
    metadata: Optional[Dict[str, Any]] = None,
    conn = None
):
    """Updates or creates a store scrape checkpoint."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        meta_json = json.dumps(metadata) if metadata is not None else None
        succ_sql = "CURRENT_TIMESTAMP" if is_success else "last_successful_sync_at"

        cur.execute(f"""
        INSERT INTO scrape_checkpoints (
            store_id, last_task_id, last_page_scraped, total_pages_detected,
            total_products_scraped, last_scraped_at, last_successful_sync_at,
            sync_cursor, status, metadata, updated_at
        ) VALUES (
            ?, ?, COALESCE(?, 0), COALESCE(?, 0),
            COALESCE(?, 0), CURRENT_TIMESTAMP, {succ_sql},
            ?, ?, ?, CURRENT_TIMESTAMP
        )
        ON CONFLICT(store_id) DO UPDATE SET
            last_task_id = COALESCE(excluded.last_task_id, scrape_checkpoints.last_task_id),
            last_page_scraped = COALESCE(excluded.last_page_scraped, scrape_checkpoints.last_page_scraped),
            total_pages_detected = COALESCE(excluded.total_pages_detected, scrape_checkpoints.total_pages_detected),
            total_products_scraped = scrape_checkpoints.total_products_scraped + COALESCE(excluded.total_products_scraped, 0),
            last_scraped_at = CURRENT_TIMESTAMP,
            last_successful_sync_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE scrape_checkpoints.last_successful_sync_at END,
            sync_cursor = COALESCE(excluded.sync_cursor, scrape_checkpoints.sync_cursor),
            status = excluded.status,
            metadata = COALESCE(excluded.metadata, scrape_checkpoints.metadata),
            updated_at = CURRENT_TIMESTAMP;
        """, (
            store_id, task_id, page_scraped, total_pages, products_count,
            sync_cursor, status, meta_json, is_success
        ))
        conn.commit()
    finally:
        if should_close:
            conn.close()

# ==============================================================================
# 5. QUERY & REPORTING APIS
# ==============================================================================

def get_task_details(
    task_id: str,
    include_subtasks: bool = True,
    include_logs: bool = True,
    log_limit: int = 50,
    conn = None
) -> Optional[Dict[str, Any]]:
    """Retrieves full task execution details, subtasks, and logs."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT task_id, task_type, target_source, status, fields, params,
               total_items, processed_items, successful_items, failed_items,
               skipped_items, progress_percent, current_stage, error_message,
               metadata, started_at, completed_at, duration_seconds,
               created_at, updated_at
        FROM pipeline_tasks
        WHERE task_id = ?;
        """, (task_id,))
        row = cur.fetchone()
        if not row:
            return None

        fields = json.loads(row["fields"]) if row["fields"] else None
        params = json.loads(row["params"]) if row["params"] else None
        meta = json.loads(row["metadata"]) if row["metadata"] else None

        task_data = {
            "task_id": row["task_id"],
            "task_type": row["task_type"],
            "target_source": row["target_source"],
            "status": row["status"],
            "fields": fields,
            "params": params,
            "total_items": row["total_items"],
            "processed_items": row["processed_items"],
            "successful_items": row["successful_items"],
            "failed_items": row["failed_items"],
            "skipped_items": row["skipped_items"],
            "progress_percent": row["progress_percent"],
            "current_stage": row["current_stage"],
            "error_message": row["error_message"],
            "metadata": meta,
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "duration_seconds": row["duration_seconds"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "subtasks": [],
            "logs": []
        }

        if include_subtasks:
            cur.execute("""
            SELECT subtask_id, step_name, step_index, total_steps, status,
                   items_count, items_succeeded, items_failed, error_message,
                   metadata, started_at, completed_at, duration_seconds, created_at
            FROM pipeline_subtasks
            WHERE task_id = ?
            ORDER BY step_index ASC, created_at ASC;
            """, (task_id,))
            sub_rows = cur.fetchall()
            for s in sub_rows:
                s_meta = json.loads(s["metadata"]) if s["metadata"] else None
                task_data["subtasks"].append({
                    "subtask_id": s["subtask_id"],
                    "step_name": s["step_name"],
                    "step_index": s["step_index"],
                    "total_steps": s["total_steps"],
                    "status": s["status"],
                    "items_count": s["items_count"],
                    "items_succeeded": s["items_succeeded"],
                    "items_failed": s["items_failed"],
                    "error_message": s["error_message"],
                    "metadata": s_meta,
                    "started_at": s["started_at"],
                    "completed_at": s["completed_at"],
                    "duration_seconds": s["duration_seconds"],
                    "created_at": s["created_at"]
                })

        if include_logs:
            cur.execute("""
            SELECT id, subtask_id, level, message, context_data, created_at
            FROM pipeline_task_logs
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT ?;
            """, (task_id, max(1, min(log_limit, 500))))
            log_rows = cur.fetchall()
            for l in reversed(log_rows):
                ctx = json.loads(l["context_data"]) if l["context_data"] else None
                task_data["logs"].append({
                    "id": l["id"],
                    "subtask_id": l["subtask_id"],
                    "level": l["level"],
                    "message": l["message"],
                    "context_data": ctx,
                    "created_at": l["created_at"]
                })

        return task_data
    finally:
        if should_close:
            conn.close()

def list_tasks(
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    target_source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    conn = None
) -> List[Dict[str, Any]]:
    """Lists recent pipeline tasks matching optional filters."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        query = "SELECT task_id, task_type, target_source, status, total_items, processed_items, successful_items, failed_items, progress_percent, current_stage, duration_seconds, started_at, completed_at, created_at FROM pipeline_tasks WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status.lower().strip())
        if task_type:
            query += " AND task_type = ?"
            params.append(task_type.lower().strip())
        if target_source:
            query += " AND target_source = ?"
            params.append(target_source.lower().strip())

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?;"
        params.extend([max(1, min(limit, 200)), max(0, offset)])

        cur.execute(query, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        if should_close:
            conn.close()

def get_task_subtasks(
    task_id: str,
    limit: int = 100,
    offset: int = 0,
    conn = None
) -> List[Dict[str, Any]]:
    """Returns subtasks for a specific task."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT subtask_id, task_id, step_name, step_index, total_steps, status,
               items_count, items_succeeded, items_failed, error_message,
               metadata, started_at, completed_at, duration_seconds, created_at, updated_at
        FROM pipeline_subtasks
        WHERE task_id = ?
        ORDER BY step_index ASC, created_at ASC
        LIMIT ? OFFSET ?;
        """, (task_id, max(1, min(limit, 500)), max(0, offset)))
        rows = cur.fetchall()
        result = []
        for s in rows:
            s_meta = json.loads(s["metadata"]) if s["metadata"] else None
            result.append({
                "subtask_id": s["subtask_id"],
                "task_id": s["task_id"],
                "step_name": s["step_name"],
                "step_index": s["step_index"],
                "total_steps": s["total_steps"],
                "status": s["status"],
                "items_count": s["items_count"],
                "items_succeeded": s["items_succeeded"],
                "items_failed": s["items_failed"],
                "error_message": s["error_message"],
                "metadata": s_meta,
                "started_at": s["started_at"],
                "completed_at": s["completed_at"],
                "duration_seconds": s["duration_seconds"],
                "created_at": s["created_at"],
                "updated_at": s["updated_at"]
            })
        return result
    finally:
        if should_close:
            conn.close()

def get_task_logs(
    task_id: str,
    subtask_id: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    conn = None
) -> List[Dict[str, Any]]:
    """Returns execution logs for a specific task or subtask."""
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        query = "SELECT id, task_id, subtask_id, level, message, context_data, created_at FROM pipeline_task_logs WHERE task_id = ?"
        params = [task_id]
        if subtask_id:
            query += " AND subtask_id = ?"
            params.append(subtask_id)
        if level:
            query += " AND level = ?"
            params.append(level.upper().strip())
        query += " ORDER BY id DESC LIMIT ? OFFSET ?;"
        params.extend([max(1, min(limit, 500)), max(0, offset)])
        cur.execute(query, params)
        rows = cur.fetchall()
        result = []
        for l in rows:
            ctx = json.loads(l["context_data"]) if l["context_data"] else None
            result.append({
                "id": l["id"],
                "task_id": l["task_id"],
                "subtask_id": l["subtask_id"],
                "level": l["level"],
                "message": l["message"],
                "context_data": ctx,
                "created_at": l["created_at"]
            })
        return result
    finally:
        if should_close:
            conn.close()

# ==============================================================================
# 6. CONTEXT MANAGERS FOR CLEAN INGESTION CODE
# ==============================================================================

class TaskTracker:
    """
    Context manager for automated task lifecycle management:
    creates, starts, logs progress, catches errors, and completes automatically.
    """
    def __init__(
        self,
        task_type: str,
        target_source: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        fields: Optional[List[str]] = None,
        total_items: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        conn = None
    ):
        self.task_type = task_type
        self.target_source = target_source
        self.params = params
        self.fields = fields
        self.total_items = total_items
        self.metadata = metadata
        self.conn = conn
        self.task_id = None

    def __enter__(self):
        self.task_id = create_task(
            task_type=self.task_type,
            target_source=self.target_source,
            params=self.params,
            fields=self.fields,
            total_items=self.total_items,
            metadata=self.metadata,
            auto_start=True,
            conn=self.conn
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            fail_task(self.task_id, str(exc_val), conn=self.conn)
            return False  # Re-raise exception
        complete_task(self.task_id, conn=self.conn)
        return False

    def update(self, **kwargs):
        update_task_progress(self.task_id, conn=self.conn, **kwargs)

    def log(self, message: str, level: str = "INFO", subtask_id: str = None, context_data: dict = None):
        log_task_event(self.task_id, message, level=level, subtask_id=subtask_id, context_data=context_data, conn=self.conn)

    def subtask(self, step_name: str, step_index: int = 0, total_steps: int = 1, metadata: dict = None):
        return SubtaskTracker(self.task_id, step_name, step_index, total_steps, metadata, conn=self.conn)

class SubtaskTracker:
    """Context manager for nested subtask tracking."""
    def __init__(self, task_id: str, step_name: str, step_index: int = 0, total_steps: int = 1, metadata: dict = None, conn = None):
        self.task_id = task_id
        self.step_name = step_name
        self.step_index = step_index
        self.total_steps = total_steps
        self.metadata = metadata
        self.conn = conn
        self.subtask_id = None
        self.items_count = 0
        self.items_succeeded = 0
        self.items_failed = 0

    def __enter__(self):
        self.subtask_id = create_subtask(
            task_id=self.task_id,
            step_name=self.step_name,
            step_index=self.step_index,
            total_steps=self.total_steps,
            metadata=self.metadata,
            auto_start=True,
            conn=self.conn
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            fail_subtask(self.subtask_id, str(exc_val), conn=self.conn)
            return False
        complete_subtask(
            subtask_id=self.subtask_id,
            items_count=self.items_count,
            items_succeeded=self.items_succeeded,
            items_failed=self.items_failed,
            metadata=self.metadata,
            conn=self.conn
        )
        return False

    def log(self, message: str, level: str = "INFO", context_data: dict = None):
        log_task_event(self.task_id, message, level=level, subtask_id=self.subtask_id, context_data=context_data, conn=self.conn)
