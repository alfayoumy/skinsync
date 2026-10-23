"""
Admin & Observatory Router: Full catalog inspection, product telemetry forensics,
cross-store linking audit, INCI matching gate logs, and similar product breakdown.
"""

import json
import sqlite3
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pipeline.db_manager import get_connection
from pipeline.logging_config import get_logger
from services.catalog_service import parse_and_clean_ingredients, extract_actives_from_title
from services.recommender import compute_weighted_jaccard, get_recommendations
from dermo_data import evaluate_product_dermo

logger = get_logger("skincare_engine.routers.admin")

router = APIRouter(prefix="/admin", tags=["Admin & Observatory"])


@router.get("/overview")
def get_admin_overview():
    """
    Returns high-level system vitals, catalog counters, store breakdown,
    and recent pipeline execution tasks.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        # Catalog Totals
        cur.execute("SELECT COUNT(*) FROM products;")
        total_products = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM canonical_products;")
        total_canonical = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM canonical_products WHERE store_count > 1;")
        multi_store_clusters = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM products WHERE ingredients_raw IS NOT NULL AND LENGTH(ingredients_raw) >= 15;")
        products_with_ingredients = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM products WHERE is_skincare = 1;")
        active_skincare_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM products WHERE is_skincare = 0;")
        suppressed_non_skincare_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM products WHERE inci_matched_id IS NOT NULL;")
        inci_matched_count = cur.fetchone()[0]

        cur.execute("""
        SELECT COUNT(*) FROM products 
        WHERE gate4_gemini_status = 'gemini_approved' 
           OR gate4_cross_status = 'auto_approved'
           OR gate4_jev_status = 'passed_jev';
        """)
        ai_verified_count = cur.fetchone()[0]

        # Store Breakdown
        cur.execute("""
        SELECT s.id, s.name, s.domain, s.platform, s.is_active, s.last_sync_at,
               COUNT(p.id) as total_products,
               SUM(CASE WHEN p.is_skincare = 1 THEN 1 ELSE 0 END) as skincare_products,
               SUM(CASE WHEN p.ingredients_raw IS NOT NULL AND LENGTH(p.ingredients_raw) >= 15 THEN 1 ELSE 0 END) as with_ingredients,
               SUM(CASE WHEN p.canonical_product_id IS NOT NULL THEN 1 ELSE 0 END) as linked_products,
               ROUND(AVG(p.price), 2) as avg_price
        FROM stores s
        LEFT JOIN products p ON p.store_id = s.id
        GROUP BY s.id
        ORDER BY total_products DESC;
        """)
        stores_breakdown = [dict(r) for r in cur.fetchall()]

        # Recent Tasks
        cur.execute("""
        SELECT task_id, task_type, target_source, status, progress_percent,
               total_items, processed_items, duration_seconds, started_at, completed_at
        FROM pipeline_tasks
        ORDER BY created_at DESC
        LIMIT 6;
        """)
        recent_tasks = [dict(r) for r in cur.fetchall()]

        return {
            "catalog_vitals": {
                "total_store_listings": total_products,
                "canonical_entities": total_canonical,
                "multi_store_clusters": multi_store_clusters,
                "active_skincare_products": active_skincare_count,
                "suppressed_non_skincare": suppressed_non_skincare_count,
                "products_with_ingredients": products_with_ingredients,
                "inci_matched_products": inci_matched_count,
                "ai_verified_products": ai_verified_count,
                "coverage_percent": round((products_with_ingredients / total_products * 100.0), 1) if total_products else 0.0
            },
            "stores": stores_breakdown,
            "recent_tasks": recent_tasks
        }
    finally:
        conn.close()


@router.get("/brands")
def list_admin_brands(limit: int = Query(200, ge=10, le=1000, description="Max brands to return")):
    """Returns distinct brands with product counts across active and suppressed catalog."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT brand, COUNT(*) as count, COUNT(*) as total_listings,
               SUM(CASE WHEN is_skincare = 1 THEN 1 ELSE 0 END) as skincare_count
        FROM products
        WHERE brand IS NOT NULL AND TRIM(brand) != ''
        GROUP BY brand
        ORDER BY count DESC
        LIMIT ?;
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.get("/products")
def list_admin_products(
    q: Optional[str] = Query(None, description="Search keyword in title, brand, handle or URL"),
    store_id: Optional[str] = Query(None, description="Filter by retail store ID"),
    brand: Optional[str] = Query(None, description="Filter by brand name"),
    is_skincare: Optional[bool] = Query(None, description="Filter active skincare (true) vs suppressed non-skincare (false)"),
    category: Optional[str] = Query(None, description="Filter by product category"),
    is_available: Optional[bool] = Query(None, description="Filter by availability: true (in stock) or false (out of stock)"),
    has_ingredients: Optional[bool] = Query(None, description="Filter products with/without full ingredients"),
    ingredient_source: Optional[str] = Query(None, description="Filter by ingredient source"),
    is_multi_store: Optional[bool] = Query(None, description="Filter products with offers in multiple stores"),
    gate_status: Optional[str] = Query(None, description="Filter by INCI match gate status"),
    min_price: Optional[float] = Query(None, ge=0, description="Minimum price in EGP"),
    max_price: Optional[float] = Query(None, ge=0, description="Maximum price in EGP"),
    sort_by: str = Query("id", description="Field to sort by: id, price, title, updated_at, inci_hybrid_score, store_count"),
    order: str = Query("asc", description="Sort order: asc or desc"),
    limit: int = Query(25, ge=1, le=500, description="Items per page"),
    offset: int = Query(0, ge=0, description="Page offset")
):
    """
    Paginated, multi-filter search across all 22,800 catalog listings with INCI, cluster, and skincare suppression metadata.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        where_clauses = []
        params = []

        if q:
            term = f"%{q.strip()}%"
            where_clauses.append("(p.title LIKE ? OR p.brand LIKE ? OR p.handle LIKE ? OR p.product_url LIKE ?)")
            params.extend([term, term, term, term])

        if store_id:
            where_clauses.append("p.store_id = ?")
            params.append(store_id)

        if brand:
            where_clauses.append("LOWER(p.brand) = LOWER(?)")
            params.append(brand.strip())

        if is_skincare is True:
            where_clauses.append("p.is_skincare = 1")
        elif is_skincare is False:
            where_clauses.append("p.is_skincare = 0")

        if category:
            where_clauses.append("p.category = ?")
            params.append(category)

        if is_available is True:
            where_clauses.append("p.is_available = 1")
        elif is_available is False:
            where_clauses.append("p.is_available = 0")

        if has_ingredients is True:
            where_clauses.append("(p.ingredients_raw IS NOT NULL AND LENGTH(p.ingredients_raw) >= 15)")
        elif has_ingredients is False:
            where_clauses.append("(p.ingredients_raw IS NULL OR LENGTH(p.ingredients_raw) < 15)")

        if ingredient_source:
            where_clauses.append("p.ingredient_source = ?")
            params.append(ingredient_source)

        if is_multi_store is True:
            where_clauses.append("(c.store_count > 1)")
        elif is_multi_store is False:
            where_clauses.append("(c.store_count <= 1 OR c.store_count IS NULL)")

        if min_price is not None:
            where_clauses.append("p.price >= ?")
            params.append(min_price)

        if max_price is not None:
            where_clauses.append("p.price <= ?")
            params.append(max_price)

        if gate_status:
            where_clauses.append("""
            (p.gate4_gemini_status = ? 
             OR p.gate4_cross_status = ? 
             OR p.gate4_jev_status = ? 
             OR p.gate2_safeguard_status = ?)
            """)
            params.extend([gate_status, gate_status, gate_status, gate_status])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Validate sorting
        allowed_sorts = {
            "id": "p.id",
            "price": "p.price",
            "title": "p.title",
            "updated_at": "p.updated_at",
            "inci_hybrid_score": "p.inci_hybrid_score",
            "store_count": "COALESCE(c.store_count, 1)"
        }
        sort_col = allowed_sorts.get(sort_by, "p.id")
        order_dir = "DESC" if order.lower() == "desc" else "ASC"

        # Count total
        count_sql = f"""
        SELECT COUNT(*)
        FROM products p
        LEFT JOIN canonical_products c ON c.id = p.canonical_product_id
        {where_sql};
        """
        cur.execute(count_sql, params)
        total = cur.fetchone()[0]

        # Fetch records
        query_sql = f"""
        SELECT p.id, p.store_id, s.name as store_name, p.title, p.clean_title, p.brand, p.category,
               p.price, p.compare_at_price, p.is_available, p.image_url, p.product_url,
               p.ingredient_source,
               CASE WHEN p.ingredients_raw IS NOT NULL AND LENGTH(p.ingredients_raw) >= 15 THEN 1 ELSE 0 END as has_ingredients,
               p.canonical_product_id, c.canonical_title, COALESCE(c.store_count, 1) as store_count,
               c.min_price, c.max_price,
               p.inci_matched_id, p.inci_matched_name, p.inci_hybrid_score, p.inci_cross_score,
               p.gate1_quality_status, p.gate2_safeguard_status, p.gate4_cross_status, p.gate4_gemini_status,
               p.is_skincare, p.updated_at
        FROM products p
        LEFT JOIN stores s ON s.id = p.store_id
        LEFT JOIN canonical_products c ON c.id = p.canonical_product_id
        {where_sql}
        ORDER BY {sort_col} {order_dir}
        LIMIT ? OFFSET ?;
        """
        cur.execute(query_sql, params + [limit, offset])
        items = [dict(r) for r in cur.fetchall()]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "count": len(items),
            "products": items
        }
    finally:
        conn.close()


@router.get("/products/{product_id}/telemetry")
def get_product_telemetry_forensics(product_id: int):
    """
    Comprehensive forensic audit trail for a single product listing:
    1. Raw store scraping details & HTML descriptions.
    2. Parsed ingredient tokens, actives, and source history.
    3. Multi-gate INCI matching telemetry, dense vector similarity, and decision logs.
    4. Cross-store canonical entity resolution, sibling store offers, and price arbitrage.
    5. Clinical dermatological analysis (comedogenicity, irritancy).
    6. Live top 5 formulation alternatives and similarity matching breakdown.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1. Fetch Master Product Row
        cur.execute("""
        SELECT p.*, s.name as store_name, s.domain as store_domain
        FROM products p
        LEFT JOIN stores s ON s.id = p.store_id
        WHERE p.id = ?;
        """, (product_id,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found.")

        product = dict(row)

        # 2. Parse Ingredients & Actives
        raw_ings = product.get("ingredients_raw") or ""
        parsed_ings = []
        if product.get("ingredients_parsed"):
            try:
                parsed_ings = json.loads(product["ingredients_parsed"])
            except Exception:
                pass
        if not parsed_ings and raw_ings:
            parsed_ings, _ = parse_and_clean_ingredients(raw_ings)

        actives_list = []
        if product.get("actives"):
            try:
                actives_list = json.loads(product["actives"])
            except Exception:
                pass
        if not actives_list:
            acts = extract_actives_from_title(product.get("title", ""))
            if raw_ings:
                _, p_acts = parse_and_clean_ingredients(raw_ings)
                acts = acts.union(p_acts)
            actives_list = sorted(list(acts))

        has_source_ings = bool(raw_ings and len(raw_ings.strip()) >= 15 and product.get("ingredient_source") != "inci_matched")

        # 3. Reference INCI Information & Forensic Candidate Resolution
        reference_info = None
        top_candidate = None
        decision_summary = None
        decision_reason = product.get("inci_safeguard_log")

        if product.get("inci_matched_id"):
            cur.execute("""
            SELECT id, brand, product_name, ingredients, source
            FROM inci_reference
            WHERE id = ?;
            """, (product["inci_matched_id"],))
            ref_row = cur.fetchone()
            if ref_row:
                ref_dict = dict(ref_row)
                ref_parsed, ref_acts = parse_and_clean_ingredients(ref_dict["ingredients"])
                reference_info = {
                    "id": ref_dict["id"],
                    "brand": ref_dict["brand"],
                    "product_name": ref_dict["product_name"],
                    "ingredients_raw": ref_dict["ingredients"],
                    "source": ref_dict["source"],
                    "parsed_tokens_count": len(ref_parsed),
                    "actives": list(ref_acts)
                }
                top_candidate = reference_info
                decision_summary = "Approved Reference (Source Ground Truth Preserved)" if has_source_ings else "Approved Reference Match"
        elif product.get("inci_matched_name"):
            decision_summary = product.get("gate4_gemini_status") or product.get("gate4_cross_status") or "Evaluated"
            top_candidate = {
                "id": None,
                "brand": product.get("brand"),
                "product_name": product.get("inci_matched_name"),
                "ingredients_raw": None,
                "source": "candidate_lookup"
            }
        else:
            try:
                from pipeline.inci_matcher import match_with_inci_db
                local_m = match_with_inci_db(
                    brand=product.get("brand", ""),
                    title=product.get("title", ""),
                    conn=conn,
                    scoring_mode="local",
                    has_source_ingredients=has_source_ings
                )
                if local_m and local_m.get("matched_id"):
                    cur.execute("""
                    SELECT id, brand, product_name, ingredients, source
                    FROM inci_reference
                    WHERE id = ?;
                    """, (local_m["matched_id"],))
                    r_row = cur.fetchone()
                    if r_row:
                        r_dict = dict(r_row)
                        r_parsed, r_acts = parse_and_clean_ingredients(r_dict["ingredients"])
                        top_candidate = {
                            "id": r_dict["id"],
                            "brand": r_dict["brand"],
                            "product_name": r_dict["product_name"],
                            "ingredients_raw": r_dict["ingredients"],
                            "source": r_dict["source"],
                            "parsed_tokens_count": len(r_parsed),
                            "actives": list(r_acts),
                            "cross_encoder_score": local_m.get("inci_cross_score"),
                            "hybrid_score": local_m.get("hybrid_score")
                        }
                    decision_reason = local_m.get("safeguard_log")
                    if has_source_ings:
                        decision_summary = "Bypassed: Ground Truth Ingredients Present (LLM Prohibited)"
                    elif local_m.get("gate4_cross_status") == "cross_approved":
                        decision_summary = "Auto-Approved by Cross-Encoder"
                    else:
                        decision_summary = local_m.get("gate4_cross_status") or "Evaluated"
            except Exception as e:
                logger.warning(f"Live candidate lookup error for product #{product_id}: {e}")

        if not decision_summary:
            if has_source_ings:
                decision_summary = "Source Ingredients Active (Ground Truth Preserved)"
            else:
                decision_summary = product.get("gate4_gemini_status") or product.get("gate4_cross_status") or "Pending Evaluation"

        # 4. Cross-Store Canonical Offers & Price Arbitrage
        canonical_info = None
        sibling_offers = []
        if product.get("canonical_product_id"):
            cur.execute("""
            SELECT id, canonical_title, brand, category, normalized_key,
                   min_price, max_price, store_count, primary_image_url
            FROM canonical_products
            WHERE id = ?;
            """, (product["canonical_product_id"],))
            canon_row = cur.fetchone()
            if canon_row:
                canonical_info = dict(canon_row)
                price_diff = round(max(0.0, canonical_info["max_price"] - canonical_info["min_price"]), 2)
                savings_pct = round((price_diff / canonical_info["max_price"] * 100.0), 1) if canonical_info["max_price"] > 0 else 0.0
                canonical_info["price_difference_egp"] = price_diff
                canonical_info["savings_percent"] = savings_pct

                # Sibling store listings
                cur.execute("""
                SELECT p.id as product_id, p.store_id, s.name as store_name, p.title as store_title,
                       p.price, p.compare_at_price, p.is_available, p.product_url, p.image_url,
                       p.ingredient_source
                FROM products p
                LEFT JOIN stores s ON s.id = p.store_id
                WHERE p.canonical_product_id = ?
                ORDER BY p.price ASC;
                """, (product["canonical_product_id"],))
                listings = [dict(r) for r in cur.fetchall()]

                # Mark best deal
                in_stock_offers = [o for o in listings if o["is_available"] and o["price"] > 0]
                best_deal = in_stock_offers[0] if in_stock_offers else (listings[0] if listings else None)
                for o in listings:
                    o["is_best_deal"] = (best_deal is not None and o["product_id"] == best_deal["product_id"])
                    o["is_current_product"] = (o["product_id"] == product_id)
                sibling_offers = listings

        # 5. Clinical Dermatological Evaluation
        dermo_eval = None
        if parsed_ings or raw_ings:
            try:
                dermo_eval = evaluate_product_dermo(parsed_ings or raw_ings, skin_type="acne_prone")
            except Exception as e:
                logger.warning(f"Clinical dermo evaluation failed for product {product_id}: {e}")

        # 6. Top Similar Products & Match Breakdown
        similar_alternatives = []
        try:
            rec_res = get_recommendations(target_title=product["title"], skin_type="normal")
            if rec_res and "results" in rec_res:
                target_parsed = parsed_ings
                target_actives = set(actives_list)

                for r in rec_res["results"][:8]:
                    cand_id = r.get("id")
                    cand_parsed = []
                    cand_acts = set()

                    if cand_id:
                        cur.execute("SELECT ingredients_parsed, ingredients_raw, actives, canonical_product_id FROM products WHERE id = ?;", (cand_id,))
                        c_row = cur.fetchone()
                        if c_row:
                            if product.get("canonical_product_id") and c_row["canonical_product_id"] == product.get("canonical_product_id"):
                                continue
                            if c_row["ingredients_parsed"]:
                                try:
                                    cand_parsed = json.loads(c_row["ingredients_parsed"])
                                except Exception:
                                    pass
                            if not cand_parsed and c_row["ingredients_raw"]:
                                cand_parsed, _ = parse_and_clean_ingredients(c_row["ingredients_raw"])

                            if c_row["actives"]:
                                try:
                                    cand_acts = set(json.loads(c_row["actives"]))
                                except Exception:
                                    pass
                            if not cand_acts and cand_parsed:
                                _, p_a = parse_and_clean_ingredients(c_row["ingredients_raw"] or "")
                                cand_acts = p_a

                    shared_acts = sorted(list(target_actives.intersection(cand_acts)))
                    diff_acts = sorted(list(cand_acts.difference(target_actives)))
                    formula_jaccard = round(compute_weighted_jaccard(target_parsed, cand_parsed) * 100.0, 1) if target_parsed and cand_parsed else None

                    similar_alternatives.append({
                        "id": cand_id,
                        "title": r.get("title"),
                        "brand": r.get("brand"),
                        "price": r.get("price"),
                        "url": r.get("url"),
                        "image_url": r.get("image_url"),
                        "match_score": r.get("match_score"),
                        "cross_encoder_score": r.get("cross_score"),
                        "ai_provider": r.get("ai_provider"),
                        "shared_actives": shared_acts,
                        "differing_actives": diff_acts,
                        "formula_similarity_percent": formula_jaccard,
                        "match_summary": f"Shares {len(shared_acts)} key active(s): {', '.join(shared_acts) if shared_acts else 'Base formulation compatibility'}"
                    })
        except Exception as e:
            logger.warning(f"Similar alternatives generation failed for {product_id}: {e}")

        # Assemble Full Telemetry Response
        return {
            "product": {
                "id": product["id"],
                "store_id": product["store_id"],
                "store_name": product.get("store_name"),
                "store_domain": product.get("store_domain"),
                "store_product_id": product.get("store_product_id"),
                "handle": product.get("handle"),
                "title": product.get("title"),
                "clean_title": product.get("clean_title"),
                "brand": product.get("brand"),
                "category": product.get("category"),
                "is_skincare": int(product.get("is_skincare") if product.get("is_skincare") is not None else 0),
                "canonical_product_id": product.get("canonical_product_id"),
                "price": product.get("price"),
                "compare_at_price": product.get("compare_at_price"),
                "is_available": bool(product.get("is_available")),
                "product_url": product.get("product_url"),
                "image_url": product.get("image_url"),
                "description_html": product.get("description_html"),
                "created_at": product.get("created_at"),
                "updated_at": product.get("updated_at")
            },
            "ingredients_forensics": {
                "has_ingredients": bool(raw_ings and len(raw_ings) >= 15),
                "ingredient_source": product.get("ingredient_source") or "none",
                "ingredients_raw": raw_ings,
                "parsed_tokens_count": len(parsed_ings),
                "ingredients_parsed": parsed_ings,
                "actives": actives_list
            },
            "inci_matching_telemetry": {
                "inci_matched_id": product.get("inci_matched_id"),
                "inci_matched_name": product.get("inci_matched_name"),
                "has_source_ingredients": has_source_ings,
                "decision_summary": decision_summary,
                "decision_reason": decision_reason,
                "top_candidate": top_candidate,
                "reference_knowledge_base": reference_info,
                "scores": {
                    "vector_similarity": product.get("inci_vector_score"),
                    "token_similarity": product.get("inci_token_score"),
                    "hybrid_score": product.get("inci_hybrid_score"),
                    "cross_encoder_score": product.get("inci_cross_score"),
                    "jev_score": product.get("inci_jev_score"),
                    "gemini_score": product.get("inci_gemini_score")
                },
                "gates": {
                    "gate1_quality_status": product.get("gate1_quality_status"),
                    "gate2_safeguard_status": product.get("gate2_safeguard_status"),
                    "gate3_active_status": product.get("gate3_active_status"),
                    "gate4_cross_status": product.get("gate4_cross_status"),
                    "gate4_jev_status": product.get("gate4_jev_status"),
                    "gate4_gemini_status": product.get("gate4_gemini_status")
                },
                "safeguard_log": product.get("inci_safeguard_log")
            },
            "cross_store_linking": {
                "canonical_product_id": product.get("canonical_product_id"),
                "canonical_entity": canonical_info,
                "total_store_offers": len(sibling_offers),
                "sibling_offers": sibling_offers
            },
            "clinical_dermatology": dermo_eval,
            "similar_formulation_matches": similar_alternatives
        }
    finally:
        conn.close()


class ClassificationUpdate(BaseModel):
    is_skincare: Optional[int] = None
    category: Optional[str] = None


@router.patch("/products/{product_id}/classification")
def update_product_classification(product_id: int, update: ClassificationUpdate):
    """
    Manual override control: Updates a product's skincare status (1=active, 0=suppressed) and category.
    Refreshes the in-memory catalog cache dynamically.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, is_skincare, category FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")

        sets = []
        params = []
        if update.is_skincare is not None:
            val = 1 if update.is_skincare in (1, True, "1", "true") else 0
            sets.append("is_skincare = ?")
            params.append(val)
        if update.category is not None and update.category.strip():
            sets.append("category = ?")
            params.append(update.category.strip())

        if not sets:
            raise HTTPException(status_code=400, detail="No fields provided for classification update.")

        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(product_id)

        cur.execute(f"UPDATE products SET {', '.join(sets)} WHERE id = ?;", params)
        conn.commit()

        # Fetch updated record
        cur.execute("SELECT id, title, is_skincare, category, updated_at FROM products WHERE id = ?;", (product_id,))
        updated = dict(cur.fetchone())

        # Reload in-memory catalog index
        try:
            from services.catalog_service import load_catalog_from_db
            load_catalog_from_db()
        except Exception as e:
            logger.warning(f"Could not reload in-memory cache: {e}")

        logger.info(f"Classification updated for Product #{product_id}: is_skincare={updated['is_skincare']}, category={updated['category']}")
        return {"status": "success", "product": updated}
    finally:
        conn.close()


@router.post("/pipeline/trigger-linker")
def trigger_product_linker():
    """Triggers the cross-source product clustering and variant alignment pipeline."""
    from pipeline.product_linker import link_catalog_products
    metrics = link_catalog_products()
    return metrics


@router.post("/pipeline/trigger-classifier")
def trigger_catalog_classifier():
    """Triggers the taxonomy classifier and skincare suppression pipeline."""
    from pipeline.classifier import classify_catalog_products
    metrics = classify_catalog_products()
    return metrics
