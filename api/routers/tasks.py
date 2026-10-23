"""
Tasks Router: Real-time progress monitoring, task control, subtasks, logs, and scraping checkpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.task_tracker import (
    list_tasks, get_task_details, get_task_subtasks, get_task_logs,
    cancel_task, list_scrape_checkpoints, get_scrape_checkpoint
)
from pipeline.logging_config import get_logger

logger = get_logger("skincare_engine.routers.tasks")

router = APIRouter(tags=["tasks"])

# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class CancelTaskRequest(BaseModel):
    reason: Optional[str] = "User requested cancellation"

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.get("/tasks")
def list_pipeline_tasks_endpoint(
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    target_source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Lists data scraping, ingestion, and catalog refresh pipeline tasks with pagination and filters.
    """
    tasks = list_tasks(
        status=status,
        task_type=task_type,
        target_source=target_source,
        limit=limit,
        offset=offset
    )
    return {
        "status": "success",
        "total": len(tasks),
        "limit": limit,
        "offset": offset,
        "tasks": tasks
    }

@router.get("/tasks/{task_id}")
def get_pipeline_task_endpoint(
    task_id: str,
    include_subtasks: bool = True,
    include_logs: bool = True,
    log_limit: int = 50
):
    """
    Retrieves real-time execution progress, item counters, subtask statuses, and logs for a pipeline task.
    """
    task = get_task_details(
        task_id=task_id,
        include_subtasks=include_subtasks,
        include_logs=include_logs,
        log_limit=log_limit
    )
    if not task:
        raise HTTPException(status_code=404, detail=f"Pipeline task '{task_id}' not found.")
    return task

@router.get("/tasks/{task_id}/subtasks")
def get_pipeline_subtasks_endpoint(
    task_id: str,
    limit: int = 100,
    offset: int = 0
):
    """
    Returns granular subtasks (e.g. per-page crawling, chunk batches) for a specific pipeline task.
    """
    task = get_task_details(task_id=task_id, include_subtasks=False, include_logs=False)
    if not task:
        raise HTTPException(status_code=404, detail=f"Pipeline task '{task_id}' not found.")
    subtasks = get_task_subtasks(task_id=task_id, limit=limit, offset=offset)
    return {
        "task_id": task_id,
        "total": len(subtasks),
        "subtasks": subtasks
    }

@router.get("/tasks/{task_id}/logs")
def get_pipeline_task_logs_endpoint(
    task_id: str,
    subtask_id: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """
    Returns chronological execution log events and diagnostic context for a pipeline task.
    """
    task = get_task_details(task_id=task_id, include_subtasks=False, include_logs=False)
    if not task:
        raise HTTPException(status_code=404, detail=f"Pipeline task '{task_id}' not found.")
    logs = get_task_logs(task_id=task_id, subtask_id=subtask_id, level=level, limit=limit, offset=offset)
    return {
        "task_id": task_id,
        "total": len(logs),
        "logs": logs
    }

@router.post("/tasks/{task_id}/cancel")
def cancel_pipeline_task_endpoint(task_id: str, req: Optional[CancelTaskRequest] = None):
    """Cancels a pending or running pipeline task."""
    task = get_task_details(task_id=task_id, include_subtasks=False, include_logs=False)
    if not task:
        raise HTTPException(status_code=404, detail=f"Pipeline task '{task_id}' not found.")
    if task["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel task '{task_id}' because its status is already '{task['status']}'."
        )

    reason = req.reason if (req and req.reason) else "User requested cancellation"
    cancelled = cancel_task(task_id=task_id, reason=reason)
    if not cancelled:
        raise HTTPException(status_code=500, detail=f"Failed to cancel task '{task_id}'.")
    return {
        "status": "cancelled",
        "task_id": task_id,
        "message": f"Task '{task_id}' was successfully cancelled.",
        "reason": reason
    }

@router.get("/scraping/checkpoints")
def list_scraping_checkpoints_endpoint():
    """Lists store scrape checkpoints showing last scraped page, timestamp, and status."""
    checkpoints = list_scrape_checkpoints()
    return {
        "status": "success",
        "total": len(checkpoints),
        "checkpoints": checkpoints
    }

@router.get("/scraping/checkpoints/{store_id}")
def get_store_scraping_checkpoint_endpoint(store_id: str):
    """Retrieves the current scrape checkpoint and sync cursor for a specific store."""
    cp = get_scrape_checkpoint(store_id=store_id)
    if not cp:
        raise HTTPException(status_code=404, detail=f"Scrape checkpoint for store '{store_id}' not found.")
    return cp
