"""Administrator-only preview and backed-up reset of product-derived data."""
from fastapi import APIRouter, HTTPException
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
