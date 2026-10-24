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
