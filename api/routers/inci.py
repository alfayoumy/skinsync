"""
INCI Matching Router: Granular scoring mode matching, product-level updates,
and batch formulation matching with task tracking.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from pipeline.db_manager import get_connection
from pipeline.inci_matcher import match_with_inci_db, parse_and_normalize_ingredients
from pipeline.task_tracker import (
    create_task, complete_task, fail_task, create_subtask, complete_subtask,
    update_task_progress
)
from pipeline.logging_config import get_logger, log_context
from services.catalog_service import load_catalog_from_db

logger = get_logger("skincare_engine.routers.inci")

router = APIRouter(tags=["inci"])

# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class InciMatchRequest(BaseModel):
    brand: str
    title: str
    scoring_mode: str = "local"  # "local", "jev", "gemini", "all"
    allow_obf: bool = False

class ProductInciMatchRequest(BaseModel):
    scoring_mode: str = "local"
    allow_obf: bool = False
    update_product: bool = True

class BatchInciMatchRequest(BaseModel):
    product_ids: Optional[List[int]] = None
    store_id: Optional[str] = None
    limit: int = 25
    scoring_mode: str = "local"
    allow_obf: bool = False
    only_unmatched: bool = True
    background: bool = False

# ==============================================================================
# WORKER TASKS
# ==============================================================================

def _run_batch_inci_match(req: BatchInciMatchRequest, task_id: Optional[str] = None):
    with log_context(task_id=task_id):
        own_task = False
        if task_id is None:
            task_id = create_task(
                task_type="batch_match_inci",
                target_source=req.store_id or "all",
                params={"limit": req.limit, "scoring_mode": req.scoring_mode, "allow_obf": req.allow_obf, "only_unmatched": req.only_unmatched},
                auto_start=True
            )
            own_task = True

        conn = get_connection()
        try:
            cur = conn.cursor()
            query = "SELECT id, brand, title, ingredients_raw FROM products WHERE 1=1"
            params = []
            if req.product_ids:
                placeholders = ",".join("?" for _ in req.product_ids)
                query += f" AND id IN ({placeholders})"
                params.extend(req.product_ids)
            if req.store_id:
                query += " AND store_id = ?"
                params.append(req.store_id)
            if req.only_unmatched:
                query += " AND (inci_matched_id IS NULL OR gate4_jev_status IS NULL)"
            query += f" LIMIT {max(1, min(req.limit, 200))};"

            cur.execute(query, params)
            rows = cur.fetchall()

            update_task_progress(task_id, total_items=len(rows), current_stage="matching_inci_records", conn=conn)

            results = []
            matched_count = 0
            for idx, r in enumerate(rows, 1):
                pid = r["id"]
                brand = r["brand"]
                title = r["title"]
                curr_raw = r["ingredients_raw"]

                subtask_id = create_subtask(
                    task_id=task_id,
                    step_name=f"match_product_{pid}",
                    step_index=idx,
                    total_steps=len(rows),
                    metadata={"product_id": pid, "brand": brand, "title": title},
                    conn=conn
                )

                m_res = match_with_inci_db(brand, title, conn, allow_synchronous_jev=True, scoring_mode=req.scoring_mode, allow_obf=req.allow_obf)
                if m_res:
                    matched_count += 1
                    update_sql = """
                    UPDATE products
                    SET inci_matched_id = ?, inci_matched_name = ?, inci_vector_score = ?,
                        inci_token_score = ?, inci_hybrid_score = ?, inci_cross_score = ?,
                        inci_jev_score = ?, inci_gemini_score = ?, gate1_quality_status = ?,
                        gate2_safeguard_status = ?, gate3_active_status = ?, gate4_cross_status = ?,
                        gate4_jev_status = ?, gate4_gemini_status = ?, inci_safeguard_log = ?,
                        updated_at = CURRENT_TIMESTAMP
                    """
                    u_params = [
                        m_res.get("matched_id"), m_res.get("matched_name"), m_res.get("vector_score"),
                        m_res.get("token_score"), m_res.get("hybrid_score"), m_res.get("inci_cross_score"),
                        m_res.get("inci_jev_score"), m_res.get("inci_gemini_score"), m_res.get("gate1_quality_status"),
                        m_res.get("gate2_safeguard_status"), m_res.get("gate3_active_status"), m_res.get("gate4_cross_status"),
                        m_res.get("gate4_jev_status"), m_res.get("gate4_gemini_status"), m_res.get("safeguard_log")
                    ]
                    if (not curr_raw or len(curr_raw.strip()) < 10) and m_res.get("ingredients"):
                        parsed, acts = parse_and_normalize_ingredients(m_res["ingredients"])
                        update_sql += ", ingredients_raw = ?, ingredients_parsed = ?, actives = ?, ingredient_source = 'skinsafe_matched'"
                        u_params.extend([m_res["ingredients"], json.dumps(parsed), json.dumps(acts)])
                    update_sql += " WHERE id = ?;"
                    u_params.append(pid)
                    cur.execute(update_sql, u_params)
                    conn.commit()

                    complete_subtask(subtask_id, items_count=1, items_succeeded=1, metadata={"matched": True, "matched_name": m_res.get("matched_name")}, conn=conn)
                    results.append({"id": pid, "title": title, "matched": True, "matched_name": m_res.get("matched_name")})
                else:
                    complete_subtask(subtask_id, items_count=1, items_succeeded=0, metadata={"matched": False}, conn=conn)
                    results.append({"id": pid, "title": title, "matched": False})

                update_task_progress(
                    task_id=task_id,
                    processed_items=idx,
                    successful_items=matched_count,
                    current_stage=f"processed_item_{idx}_of_{len(rows)}",
                    conn=conn
                )

            load_catalog_from_db()

            if task_id:
                complete_task(
                    task_id=task_id,
                    metadata={"total_processed": len(rows), "total_matched": matched_count, "scoring_mode": req.scoring_mode},
                    conn=conn
                )

            return {
                "status": "success",
                "task_id": task_id,
                "total_processed": len(rows),
                "total_matched": matched_count,
                "scoring_mode": req.scoring_mode,
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

@router.post("/inci/match")
def match_inci_endpoint(req: InciMatchRequest):
    """
    Performs on-demand INCI formulation matching with granular scoring mode control
    ('local', 'jev', 'gemini', 'all'/'auto') and Open Beauty Facts fallback flag.
    """
    conn = get_connection()
    try:
        res = match_with_inci_db(
            brand=req.brand,
            title=req.title,
            conn=conn,
            allow_synchronous_jev=True,
            scoring_mode=req.scoring_mode,
            allow_obf=req.allow_obf
        )
        if not res:
            return {
                "matched": False,
                "brand": req.brand,
                "title": req.title,
                "scoring_mode": req.scoring_mode,
                "result": None
            }
        return {
            "matched": True,
            "brand": req.brand,
            "title": req.title,
            "scoring_mode": req.scoring_mode,
            "result": res
        }
    finally:
        conn.close()

@router.post("/products/{product_id}/match-inci")
def match_product_inci_endpoint(product_id: int, req: ProductInciMatchRequest):
    """Matches an existing product in the database against the INCI reference database and updates its telemetry."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, brand, title, ingredients_raw FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product ID {product_id} not found.")

        brand = row["brand"]
        title = row["title"]
        curr_raw = row["ingredients_raw"]

        match_res = match_with_inci_db(
            brand=brand,
            title=title,
            conn=conn,
            allow_synchronous_jev=True,
            scoring_mode=req.scoring_mode,
            allow_obf=req.allow_obf
        )

        if not match_res:
            return {
                "matched": False,
                "product_id": product_id,
                "brand": brand,
                "title": title,
                "scoring_mode": req.scoring_mode,
                "message": "No compatible INCI match found."
            }

        if req.update_product:
            update_sql = """
            UPDATE products
            SET inci_matched_id = ?,
                inci_matched_name = ?,
                inci_vector_score = ?,
                inci_token_score = ?,
                inci_hybrid_score = ?,
                inci_cross_score = ?,
                inci_jev_score = ?,
                inci_gemini_score = ?,
                gate1_quality_status = ?,
                gate2_safeguard_status = ?,
                gate3_active_status = ?,
                gate4_cross_status = ?,
                gate4_jev_status = ?,
                gate4_gemini_status = ?,
                inci_safeguard_log = ?,
                updated_at = CURRENT_TIMESTAMP
            """
            params = [
                match_res.get("matched_id"),
                match_res.get("matched_name"),
                match_res.get("vector_score"),
                match_res.get("token_score"),
                match_res.get("hybrid_score"),
                match_res.get("inci_cross_score"),
                match_res.get("inci_jev_score"),
                match_res.get("inci_gemini_score"),
                match_res.get("gate1_quality_status"),
                match_res.get("gate2_safeguard_status"),
                match_res.get("gate3_active_status"),
                match_res.get("gate4_cross_status"),
                match_res.get("gate4_jev_status"),
                match_res.get("gate4_gemini_status"),
                match_res.get("safeguard_log"),
            ]

            if (not curr_raw or len(curr_raw.strip()) < 10) and match_res.get("ingredients"):
                parsed, acts = parse_and_normalize_ingredients(match_res["ingredients"])
                update_sql += """,
                ingredients_raw = ?,
                ingredients_parsed = ?,
                actives = ?,
                ingredient_source = 'skinsafe_matched'
                """
                params.extend([match_res["ingredients"], json.dumps(parsed), json.dumps(acts)])

            update_sql += " WHERE id = ?;"
            params.append(product_id)
            cur.execute(update_sql, params)
            conn.commit()
            load_catalog_from_db()

        return {
            "matched": True,
            "product_id": product_id,
            "brand": brand,
            "title": title,
            "scoring_mode": req.scoring_mode,
            "updated": req.update_product,
            "match": match_res
        }
    finally:
        conn.close()

@router.post("/inci/batch-match")
def batch_match_inci_endpoint(req: BatchInciMatchRequest, background_tasks: BackgroundTasks):
    """
    Runs batch INCI matching across selected products or catalog items.
    Tracks execution and subtasks in pipeline control tables.
    """
    task_id = create_task(
        task_type="batch_match_inci",
        target_source=req.store_id or "all",
        params={"limit": req.limit, "scoring_mode": req.scoring_mode, "allow_obf": req.allow_obf},
        auto_start=True
    )
    if req.background:
        background_tasks.add_task(_run_batch_inci_match, req, task_id)
        return {
            "status": "accepted",
            "task_id": task_id,
            "message": f"Batch INCI matching job dispatched to background worker (mode: {req.scoring_mode}, limit: {req.limit}). Monitor progress at /tasks/{task_id}."
        }
    return _run_batch_inci_match(req, task_id)
