"""
Ingredients Router: Single product and batch ingredient extraction from store HTML or INCI reference.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from pipeline.db_manager import get_connection
from pipeline.inci_matcher import (
    extract_ingredients_from_html, match_with_inci_db, parse_and_normalize_ingredients
)
from pipeline.task_tracker import (
    create_task, complete_task, fail_task, create_subtask, complete_subtask,
    update_task_progress
)
from pipeline.logging_config import get_logger, log_context
from services.catalog_service import load_catalog_from_db

logger = get_logger("skincare_engine.routers.ingredients")

router = APIRouter(tags=["ingredients"])

# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class FillIngredientsRequest(BaseModel):
    source: str = "auto"  # "store", "inci", "auto"
    overwrite: bool = False

class BatchFillIngredientsRequest(BaseModel):
    product_ids: Optional[List[int]] = None
    store_id: Optional[str] = None
    source: str = "auto"
    only_empty: bool = True
    overwrite: bool = False
    limit: int = 50
    background: bool = False

# ==============================================================================
# WORKER FUNCTIONS
# ==============================================================================

def _fill_single_product_ingredients(product_row, source: str, overwrite: bool, conn):
    cur = conn.cursor()
    cur.execute("SELECT c.ingredients_raw FROM products p LEFT JOIN canonical_products c ON c.id=p.canonical_product_id WHERE p.id=?", (product_row["id"],))
    current = cur.fetchone()
    curr_raw = current[0] if current else None
    if curr_raw and curr_raw.strip() and not overwrite:
        return {"status": "skipped", "message": "Canonical product already has an authoritative formulation.", "ingredients_raw": curr_raw}
    identity_verified = True

    raw_text = None
    source_used = None

    # Step 1: Store HTML description
    if source in ("store", "auto"):
        desc = product_row["description_html"]
        if desc:
            extracted = extract_ingredients_from_html(desc)
            if extracted and len(extracted) >= 15:
                raw_text = extracted
                source_used = "store_description"

    # Step 2: Reference IDs alone are proposals; require a verified identity match.
    if (not raw_text or len(raw_text) < 15) and source in ("inci", "auto"):
        from pipeline.formulation_service import match_is_verified
        m_res = match_with_inci_db(product_row["brand"], product_row["title"], conn, allow_synchronous_jev=False, scoring_mode="local")
        if m_res and m_res.get("ingredients"):
            raw_text = m_res["ingredients"]
            source_used = "skinsafe_matched"
            identity_verified = match_is_verified(m_res)

    # Step 3: Whitelist Description Lookup Fallback (STRICT PRECEDENCE: only if Steps 1 & 2 yield no ingredients)
    if (not raw_text or len(raw_text) < 10) and source in ("whitelist", "auto"):
        desc = product_row["description_html"]
        if desc:
            from pipeline.whitelist_matcher import extract_ingredients_via_whitelist
            w_res = extract_ingredients_via_whitelist(desc, conn=conn)
            if w_res and w_res.get("ingredients_raw"):
                raw_text = w_res["ingredients_raw"]
                source_used = "whitelist_extracted"

    if not raw_text or len(raw_text.strip()) < 10:
        return {
            "status": "not_found",
            "message": f"No ingredients found using source mode '{source}'."
        }

    from pipeline.formulation_service import FormulationRepository, FormulationCandidate
    from pipeline.inci_tokenizer import tokenize_inci_string

    tok_res = tokenize_inci_string(raw_text, product_title=product_row["title"] if "title" in product_row.keys() else None)
    parsed = tok_res.tokens
    actives = tok_res.declared_actives

    canon_id = product_row["canonical_product_id"] if "canonical_product_id" in product_row.keys() else None
    if not canon_id:
        cur.execute("SELECT canonical_product_id FROM products WHERE id = ?;", (product_row["id"],))
        c_row = cur.fetchone()
        if c_row:
            canon_id = c_row["canonical_product_id"]

    published = False
    if canon_id:
        repo = FormulationRepository()
        cand = FormulationCandidate(
            canonical_product_id=canon_id,
            source_name=source_used,
            raw_ingredients=raw_text,
            parsed_tokens=parsed,
            actives=actives,
            confidence_score=0.90 if source_used == "store_description" else (0.80 if source_used == "skinsafe_matched" else 0.50),
            source_record_id=str(product_row["id"]),
            completeness="partial" if source_used == "whitelist_extracted" else (tok_res.completeness if identity_verified else "unverified"),
            validation_flags={"source_used": source_used, "is_truncated": tok_res.is_truncated}
        )
        if tok_res.completeness == "full":
            published = repo.publish_formulation(cand, approval_reason=f"Filled via ingredients router ({source_used})", force=False, conn=conn)
        else:
            repo.record_candidate_evidence(cand, decision="candidate_evidence", discrepancy_notes="Incomplete ingredients from ingredients router logged as audit evidence.", conn=conn)

    conn.commit()

    return {
        "status": "success" if published else "candidate_only",
        "product_id": product_row["id"],
        "source_used": source_used,
        "ingredients_raw": raw_text,
        "parsed_count": len(parsed),
        "actives": actives
    }

def _run_batch_fill_ingredients(req: BatchFillIngredientsRequest, task_id: Optional[str] = None):
    with log_context(task_id=task_id):
        own_task = False
        if task_id is None:
            task_id = create_task(
                task_type="batch_fill_ingredients",
                target_source=req.store_id or "all",
                params={"limit": req.limit, "source": req.source, "only_empty": req.only_empty, "overwrite": req.overwrite},
                auto_start=True
            )
            own_task = True

        conn = get_connection()
        try:
            cur = conn.cursor()
            query = "SELECT id, brand, title, description_html, ingredients_raw, inci_matched_id FROM products WHERE 1=1"
            params = []
            if req.product_ids:
                placeholders = ",".join("?" for _ in req.product_ids)
                query += f" AND id IN ({placeholders})"
                params.extend(req.product_ids)
            if req.store_id:
                query += " AND store_id = ?"
                params.append(req.store_id)
            if req.only_empty and not req.overwrite:
                query += " AND (ingredients_raw IS NULL OR LENGTH(TRIM(ingredients_raw)) < 10)"
            query += f" LIMIT {max(1, min(req.limit, 200))};"

            cur.execute(query, params)
            rows = cur.fetchall()

            update_task_progress(task_id, total_items=len(rows), current_stage="filling_product_ingredients", conn=conn)

            results = []
            success_count = 0
            for idx, r in enumerate(rows, 1):
                pid = r["id"]
                subtask_id = create_subtask(
                    task_id=task_id,
                    step_name=f"fill_product_{pid}",
                    step_index=idx,
                    total_steps=len(rows),
                    metadata={"product_id": pid, "title": r["title"]},
                    conn=conn
                )
                res = _fill_single_product_ingredients(r, req.source, req.overwrite, conn)
                is_ok = (res.get("status") == "success")
                if is_ok:
                    success_count += 1
                    complete_subtask(subtask_id, items_count=1, items_succeeded=1, metadata=res, conn=conn)
                else:
                    complete_subtask(subtask_id, items_count=1, items_succeeded=0, metadata=res, conn=conn)

                results.append({"id": pid, "title": r["title"], "status": res.get("status"), "source": res.get("source_used")})
                update_task_progress(
                    task_id=task_id,
                    processed_items=idx,
                    successful_items=success_count,
                    current_stage=f"processed_item_{idx}_of_{len(rows)}",
                    conn=conn
                )

            load_catalog_from_db()

            if task_id:
                complete_task(
                    task_id=task_id,
                    metadata={"total_processed": len(rows), "total_filled": success_count, "source": req.source},
                    conn=conn
                )

            return {
                "status": "success",
                "task_id": task_id,
                "processed": len(rows),
                "filled": success_count,
                "source": req.source,
                "details": results
            }
        except Exception as e:
            if task_id:
                fail_task(task_id, str(e), conn=conn)
            raise
        finally:
            conn.close()

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.post("/products/{product_id}/fill-ingredients")
def fill_product_ingredients_endpoint(product_id: int, req: FillIngredientsRequest):
    """
    Fills a single product's ingredients from store description ('store'),
    INCI reference ('inci'), or fallback ('auto').
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, brand, title, description_html, ingredients_raw, inci_matched_id FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product ID {product_id} not found.")

        result = _fill_single_product_ingredients(row, req.source, req.overwrite, conn)
        if result["status"] == "success":
            load_catalog_from_db()
        return result
    finally:
        conn.close()

@router.post("/products/ingredients/batch-fill")
def batch_fill_ingredients_endpoint(req: BatchFillIngredientsRequest, background_tasks: BackgroundTasks):
    """
    Batch populates missing product ingredients from store description or INCI reference.
    Tracks execution and subtasks in pipeline control tables.
    """
    task_id = create_task(
        task_type="batch_fill_ingredients",
        target_source=req.store_id or "all",
        params={"limit": req.limit, "source": req.source},
        auto_start=True
    )
    if req.background:
        background_tasks.add_task(_run_batch_fill_ingredients, req, task_id)
        return {
            "status": "accepted",
            "task_id": task_id,
            "message": f"Batch ingredients fill job dispatched (source: {req.source}, limit: {req.limit}). Monitor progress at /tasks/{task_id}."
        }
    return _run_batch_fill_ingredients(req, task_id)
