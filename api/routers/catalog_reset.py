"""Administrator-only preview and backed-up reset of product-derived data."""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from pipeline.db_manager import get_connection
from pipeline.catalog_reset import preview_catalog_reset, execute_catalog_reset
from security.admin import reviewer_name

router = APIRouter(prefix='/admin/workflows/catalog', tags=['Catalog maintenance'])


class ResetRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    confirmation: str


@router.get('/reset/preview')
def reset_preview():
    conn = get_connection()
    try:
        return preview_catalog_reset(conn)
    finally:
        conn.close()


@router.post('/reset')
def reset_catalog(request: ResetRequest):
    conn = get_connection()
    try:
        return execute_catalog_reset(
            conn, request.fingerprint, request.confirmation,
            reviewer_name('unattributed'))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        conn.close()

from pipeline.catalog_selective_rebuild import (
    preview_selective_rebuild, start_selective_rebuild,
    run_selective_rebuild, selective_rebuild_status,
)


class SelectiveRebuildRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    confirmation: str
    discovery_task_id: str = Field(min_length=1)


@router.get('/selective-rebuild/preview')
def selective_rebuild_preview(discovery_task_id: str | None = None):
    conn = get_connection()
    try:
        return preview_selective_rebuild(conn, discovery_task_id)
    finally:
        conn.close()


@router.post('/selective-rebuild')
def selective_rebuild(request: SelectiveRebuildRequest, background_tasks: BackgroundTasks):
    conn = get_connection()
    try:
        job = start_selective_rebuild(
            conn, request.fingerprint, request.confirmation,
            reviewer_name('unattributed'), request.discovery_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        conn.close()
    background_tasks.add_task(run_selective_rebuild, job['job_id'])
    return job


@router.get('/selective-rebuild/jobs/{job_id}')
def selective_rebuild_job(job_id: str):
    conn = get_connection()
    try:
        return selective_rebuild_status(conn, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    finally:
        conn.close()

from pipeline.catalog_task_reconciliation import (
    preview_stale_task_reconciliation, reconcile_stale_task_records,
)


class StaleTaskReconciliationRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    confirmation: str
    workers_checked: bool = False


@router.get('/stale-tasks/preview')
def stale_tasks_preview():
    conn = get_connection()
    try:
        return preview_stale_task_reconciliation(conn)
    finally:
        conn.close()


@router.post('/stale-tasks/reconcile')
def stale_tasks_reconcile(request: StaleTaskReconciliationRequest):
    conn = get_connection()
    try:
        return reconcile_stale_task_records(
            conn, request.fingerprint, request.confirmation,
            reviewer_name('unattributed'), request.workers_checked)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        conn.close()


from pipeline.catalog_restore import (list_catalog_backups,preview_catalog_restore,
    queue_catalog_restore,cancel_catalog_restore)


class RestoreRequest(BaseModel):
    filename: str
    sha256: str = Field(min_length=64,max_length=64)
    confirmation: str


@router.get('/backups')
def catalog_backups():
    conn=get_connection()
    try:
        return list_catalog_backups(conn)
    finally:
        conn.close()


@router.get('/backups/{filename}/preview')
def catalog_backup_preview(filename: str):
    conn=get_connection()
    try:
        return preview_catalog_restore(conn,filename)
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc))
    finally:
        conn.close()


@router.post('/restore')
def schedule_catalog_restore(request: RestoreRequest):
    conn=get_connection()
    try:
        return queue_catalog_restore(conn,request.filename,request.sha256,
                                     request.confirmation,reviewer_name('unattributed'))
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc))
    finally:
        conn.close()


@router.post('/restore/cancel')
def cancel_scheduled_restore():
    conn=get_connection()
    try:
        return cancel_catalog_restore(conn)
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc))
    finally:
        conn.close()


@router.get('/quality-audit')
def catalog_quality_audit():
    """Read-only post-restore catalog completeness, identity and INCI report."""
    from pipeline.catalog_quality_audit import inspect_catalog_quality
    conn=get_connection()
    try:
        return inspect_catalog_quality(conn)
    finally:
        conn.close()
