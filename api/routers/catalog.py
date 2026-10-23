"""
Catalog Router: Selective product attribute refreshes, store syncs, and title enrichment.
"""

from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from pipeline.harvester import refresh_catalog
from pipeline.title_enricher import run_title_enrichment
from pipeline.task_tracker import create_task, fail_task
from pipeline.logging_config import get_logger, log_context
from services.catalog_service import load_catalog_from_db

logger = get_logger("skincare_engine.routers.catalog")

router = APIRouter(tags=["catalog"])

# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class CatalogRefreshRequest(BaseModel):
    store_id: Optional[str] = None
    fields: Optional[List[str]] = None
    max_pages: Optional[int] = 50
    background: bool = True

class StoreRefreshRequest(BaseModel):
    fields: Optional[List[str]] = None
    max_pages: Optional[int] = 50
    background: bool = True

class TitleEnrichmentRequest(BaseModel):
    dry_run: bool = False
    background: bool = True

# ==============================================================================
# WORKER TASKS
# ==============================================================================

def _run_refresh_catalog_task(store_id: Optional[str], fields: Optional[List[str]], max_pages: int, task_id: Optional[str] = None):
    with log_context(task_id=task_id):
        try:
            res = refresh_catalog(store_id=store_id, fields=fields, max_pages=max_pages, task_id=task_id)
            load_catalog_from_db()
            return res
        except Exception as e:
            logger.error(f"Catalog refresh error: {e}")
            if task_id:
                fail_task(task_id, str(e))
            return {"status": "error", "message": str(e), "task_id": task_id}

def _run_title_enrichment_task(dry_run: bool, task_id: Optional[str] = None):
    with log_context(task_id=task_id):
        try:
            res = run_title_enrichment(dry_run=dry_run, task_id=task_id)
            if not dry_run:
                load_catalog_from_db()
            return res
        except Exception as e:
            logger.error(f"Title enrichment error: {e}")
            if task_id:
                fail_task(task_id, str(e))
            return {"status": "error", "message": str(e), "task_id": task_id}

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.post("/products/refresh")
def refresh_products_endpoint(req: CatalogRefreshRequest, background_tasks: BackgroundTasks):
    """
    Refreshes catalog products across a specific source or all sources,
    selectively updating specific fields ('price', 'stock', 'image', 'ingredients', or 'all').
    Tracks execution and subtasks in pipeline control tables.
    """
    task_id = create_task(
        task_type="refresh_catalog",
        target_source=req.store_id or "all",
        fields=req.fields,
        params={"max_pages": req.max_pages or 50},
        auto_start=True
    )
    if req.background:
        background_tasks.add_task(_run_refresh_catalog_task, req.store_id, req.fields, req.max_pages or 50, task_id)
        return {
            "status": "accepted",
            "task_id": task_id,
            "message": f"Catalog refresh dispatched to background worker (store: {req.store_id or 'all'}, fields: {req.fields or 'all'}). Monitor progress at /tasks/{task_id}."
        }
    return _run_refresh_catalog_task(req.store_id, req.fields, req.max_pages or 50, task_id)

@router.post("/stores/{store_id}/refresh")
def refresh_store_endpoint(store_id: str, background_tasks: BackgroundTasks, req: Optional[StoreRefreshRequest] = None):
    """
    Refreshes products for a specific source ID across selected or all fields.
    Tracks execution and subtasks in pipeline control tables.
    """
    fields = req.fields if req else None
    max_pages = req.max_pages if req and req.max_pages else 50
    bg = req.background if req else True
    task_id = create_task(
        task_type="refresh_store",
        target_source=store_id,
        fields=fields,
        params={"max_pages": max_pages},
        auto_start=True
    )
    if bg:
        background_tasks.add_task(_run_refresh_catalog_task, store_id, fields, max_pages, task_id)
        return {
            "status": "accepted",
            "task_id": task_id,
            "message": f"Store refresh dispatched for store '{store_id}' (fields: {fields or 'all'}). Monitor progress at /tasks/{task_id}."
        }
    return _run_refresh_catalog_task(store_id, fields, max_pages, task_id)

@router.post("/products/enrich-titles")
def enrich_product_titles_endpoint(background_tasks: BackgroundTasks, req: Optional[TitleEnrichmentRequest] = None):
    """
    Enriches product titles across the catalog by reconciling curated titles,
    extracting qualifiers from URL handles, and prefixing bare brand names.
    Supports dry-run preview and asynchronous task tracking.
    """
    dry_run = req.dry_run if req else False
    bg = req.background if req else True
    task_id = create_task(
        task_type="title_enrichment",
        target_source="catalog",
        params={"dry_run": dry_run},
        auto_start=True
    )
    if bg:
        background_tasks.add_task(_run_title_enrichment_task, dry_run, task_id)
        return {
            "status": "accepted",
            "task_id": task_id,
            "dry_run": dry_run,
            "message": f"Title enrichment dispatched to background worker (dry_run: {dry_run}). Monitor progress at /tasks/{task_id}."
        }
    return _run_title_enrichment_task(dry_run, task_id)
