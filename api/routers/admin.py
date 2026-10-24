"""
Admin & Observatory Router: Full catalog inspection, product telemetry forensics,
cross-store linking audit, INCI matching gate logs, and similar product breakdown.
"""
from security.admin import reviewer_name

import json
import re
import uuid
import threading
from collections import deque
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pipeline.db_manager import (
    get_connection, load_whitelist_from_db, add_whitelist_ingredient,
    delete_whitelist_ingredient, update_whitelist_ingredient, get_whitelist_stats
)
from pipeline.whitelist_matcher import (
    extract_ingredients_via_whitelist, reload_whitelist_cache
)
from pipeline.logging_config import get_logger
from services.catalog_service import parse_and_clean_ingredients, extract_actives_from_title
from services.recommender import compute_weighted_jaccard, get_recommendations
from dermo_data import evaluate_product_dermo

logger = get_logger("skincare_engine.routers.admin")

router = APIRouter(prefix="/admin", tags=["Admin & Observatory"])


class WhitelistIngredientCreate(BaseModel):
    name: str
    canonical_name: Optional[str] = None
    category: str = "general"
    is_active: bool = True


class WhitelistIngredientUpdate(BaseModel):
    name: Optional[str] = None
    canonical_name: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


class WhitelistReconcileRequest(BaseModel):
    limit: int = 100
    category: Optional[str] = None
    store_id: Optional[str] = None
    min_ingredients: int = 1


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

        registry_sources = []
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sources'").fetchone():
            from pipeline.source_registry import list_source_inventory
            registry_sources = list_source_inventory(conn)
        source_summary = {
            'registered': len(registry_sources),
            'collection_enabled': sum(bool(s['enabled']) for s in registry_sources),
            'staged': sum(not s['enabled'] for s in registry_sources),
            'publication_approved': sum(bool(s['publish_enabled']) for s in registry_sources),
            'legacy_stores': len(stores_breakdown)
        }

        # Recent Tasks
        cur.execute("""
        SELECT task_id, task_type, target_source, status, progress_percent,
               total_items, processed_items, duration_seconds, started_at, completed_at
        FROM pipeline_tasks
        ORDER BY created_at DESC
        LIMIT 6;
        """)
        recent_tasks = [dict(r) for r in cur.fetchall()]

        from pipeline.rate_limiter import langsearch_limiter
        ls_quota = langsearch_limiter.get_quota_status()

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
            "sources": registry_sources,
            "source_summary": source_summary,
            "recent_tasks": recent_tasks,
            "langsearch_quota": ls_quota
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
               p.ingredient_source, p.ingredient_source_url,
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
def get_product_telemetry_forensics(
    product_id: int,
    category_mode: str = "same_category",
    target_category: Optional[str] = None
):
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

        AUTHENTIC_SOURCE_TYPES = {
            "store_tab", "store_page_tab", 
            "current_store_scrape", "store_scrape", "store_description", "v10_curated"
        }
        has_source_ings = bool(
            raw_ings and len(raw_ings.strip()) >= 15 and 
            product.get("ingredient_source") in AUTHENTIC_SOURCE_TYPES
        )

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
                   min_price, max_price, store_count, primary_image_url,
                   ingredients_raw, ingredients_parsed, actives,
                   ingredient_source, ingredient_source_url,
                   formula_version, formula_hash, formulation_published_at,
                   formulation_approval_reason
            FROM canonical_products
            WHERE id = ?;
            """, (product["canonical_product_id"],))
            canon_row = cur.fetchone()
            if canon_row:
                canonical_info = dict(canon_row)
                if canonical_info.get("ingredients_parsed") and isinstance(canonical_info["ingredients_parsed"], str):
                    try:
                        canonical_info["ingredients_parsed"] = json.loads(canonical_info["ingredients_parsed"])
                    except Exception:
                        pass
                if canonical_info.get("actives") and isinstance(canonical_info["actives"], str):
                    try:
                        canonical_info["actives"] = json.loads(canonical_info["actives"])
                    except Exception:
                        pass
                price_diff = round(max(0.0, canonical_info["max_price"] - canonical_info["min_price"]), 2)
                savings_pct = round((price_diff / canonical_info["max_price"] * 100.0), 1) if canonical_info["max_price"] > 0 else 0.0
                canonical_info["price_difference_egp"] = price_diff
                canonical_info["savings_percent"] = savings_pct

                # Query audit evidence history
                cur.execute("""
                SELECT source_name, source_record_id, evidence_url, confidence_score, completeness,
                       decision, discrepancy_notes, audited_by, created_at
                FROM inci_audit_evidence
                WHERE canonical_product_id = ?
                ORDER BY created_at DESC;
                """, (product["canonical_product_id"],))
                canonical_info["audit_evidence"] = [dict(r) for r in cur.fetchall()]

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
            rec_res = get_recommendations(
                target_title=product["title"],
                skin_type="normal",
                category_mode=category_mode,
                target_category=target_category
            )
            if rec_res and "results" in rec_res:
                for r in rec_res["results"]:
                    similar_alternatives.append({
                        "id": r.get("id"),
                        "canonical_product_id": r.get("canonical_product_id"),
                        "title": r.get("title"),
                        "brand": r.get("brand"),
                        "category": r.get("category"),
                        "vehicle": r.get("vehicle"),
                        "price": r.get("price"),
                        "store_count": r.get("store_count", 1),
                        "min_price": r.get("min_price"),
                        "max_price": r.get("max_price"),
                        "url": r.get("url"),
                        "image_url": r.get("image_url"),
                        "match_score": r.get("match_score"),
                        "cross_encoder_score": r.get("cross_score"),
                        "ai_provider": r.get("ai_provider"),
                        "shared_actives": r.get("shared_actives", []),
                        "differing_actives": r.get("differing_actives", []),
                        "formula_similarity_percent": r.get("formula_similarity_percent"),
                        "match_summary": r.get("match_summary", "")
                    })
        except Exception as e:
            logger.warning(f"Similar alternatives generation failed for {product_id}: {e}")

        # 7. Tier Resolutions Snapshot
        tier_resolutions = None
        if product.get("tier_resolutions"):
            try:
                tier_resolutions = json.loads(product["tier_resolutions"])
            except Exception:
                pass

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
                "ingredient_source": product.get("ingredient_source"),
                "ingredient_source_url": product.get("ingredient_source_url"),
                "created_at": product.get("created_at"),
                "updated_at": product.get("updated_at")
            },
            "ingredients_forensics": {
                "has_ingredients": bool(raw_ings and len(raw_ings) >= 15),
                "ingredient_source": product.get("ingredient_source") or "none",
                "ingredient_source_url": product.get("ingredient_source_url"),
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
            "similar_formulation_matches": similar_alternatives,
            "tier_resolutions": tier_resolutions
        }
    finally:
        conn.close()


@router.get("/products/{product_id}/alternatives")
def get_product_alternatives_endpoint(
    product_id: int,
    category_mode: str = "same_category",
    target_category: Optional[str] = None
):
    """
    Returns alternative products/dupes for the given product, supporting
    same_category, cross_category, or specific category filtering.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, brand, category FROM products WHERE id = ?;", (product_id,))
        p = cur.fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Product not found")

        rec_res = get_recommendations(
            target_title=p["title"],
            skin_type="normal",
            category_mode=category_mode,
            target_category=target_category
        )
        if not rec_res:
            return {"source": {"id": p["id"], "title": p["title"], "brand": p["brand"], "category": p["category"]}, "results": []}

        return rec_res
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


# ==============================================================================
# INGREDIENT WHITELIST MANAGEMENT ENDPOINTS
# ==============================================================================

@router.get("/whitelist")
def get_whitelist_ingredients(
    q: Optional[str] = Query(None, description="Search by ingredient name or canonical name"),
    category: Optional[str] = Query(None, description="Filter by functional category (active, botanical, humectant, etc.)"),
    is_active: Optional[bool] = Query(None, description="Filter by active status (true/false)"),
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset")
):
    """
    Returns paginated list of verified ingredients in SQLite whitelist with search and category filters.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        where_clauses = []
        params = []

        if q and q.strip():
            where_clauses.append("(name LIKE ? OR canonical_name LIKE ?)")
            q_param = f"%{q.strip()}%"
            params.extend([q_param, q_param])

        if category and category.strip():
            where_clauses.append("category = ?")
            params.append(category.strip().lower())

        if is_active is not None:
            where_clauses.append("is_active = ?")
            params.append(1 if is_active else 0)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Total count query
        count_sql = f"SELECT COUNT(*) FROM ingredient_whitelist {where_sql};"
        cur.execute(count_sql, params)
        total = cur.fetchone()[0]

        # Paginated items query
        data_sql = f"""
        SELECT id, name, canonical_name, category, is_active, created_at, updated_at
        FROM ingredient_whitelist
        {where_sql}
        ORDER BY LENGTH(name) DESC, name ASC
        LIMIT ? OFFSET ?;
        """
        cur.execute(data_sql, params + [limit, offset])
        items = [dict(r) for r in cur.fetchall()]

        stats = get_whitelist_stats(conn=conn)

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "stats": stats
        }
    finally:
        conn.close()


@router.post("/whitelist")
def create_whitelist_ingredient(req: WhitelistIngredientCreate):
    """
    Adds a new verified ingredient to the SQLite whitelist and refreshes in-memory matcher cache.
    """
    conn = get_connection()
    try:
        clean_name = req.name.strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="Ingredient name cannot be empty.")

        arabic_pattern = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u0870-\u08FF\uFB50-\uFDFF\uFE70-\uFEFC]')
        if arabic_pattern.search(clean_name) or (req.canonical_name and arabic_pattern.search(req.canonical_name)):
            raise HTTPException(status_code=400, detail="Arabic text is not permitted in INCI ingredient names.")

        new_id = add_whitelist_ingredient(
            name=clean_name,
            canonical_name=req.canonical_name,
            category=req.category,
            is_active=1 if req.is_active else 0,
            conn=conn
        )
        if not new_id:
            raise HTTPException(status_code=500, detail="Failed to insert ingredient into whitelist.")

        # Invalidate in-memory matcher cache
        reload_whitelist_cache(conn=conn)

        cur = conn.cursor()
        cur.execute("SELECT id, name, canonical_name, category, is_active, created_at, updated_at FROM ingredient_whitelist WHERE id = ?;", (new_id,))
        created = dict(cur.fetchone())

        logger.info(f"Added verified whitelist ingredient #{new_id}: '{clean_name}' (category={req.category})")
        return {"status": "success", "ingredient": created}
    finally:
        conn.close()


@router.put("/whitelist/{ingredient_id}")
def update_whitelist_ingredient_endpoint(ingredient_id: int, req: WhitelistIngredientUpdate):
    """
    Updates an existing ingredient in the SQLite whitelist and refreshes in-memory matcher cache.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM ingredient_whitelist WHERE id = ?;", (ingredient_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Whitelist ingredient #{ingredient_id} not found.")

        updates = {k: v for k, v in req.dict().items() if v is not None}
        if not updates:
            raise HTTPException(status_code=400, detail="No fields provided for update.")

        arabic_pattern = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u0870-\u08FF\uFB50-\uFDFF\uFE70-\uFEFC]')
        if any(k in updates and isinstance(updates[k], str) and arabic_pattern.search(updates[k]) for k in ('name', 'canonical_name')):
            raise HTTPException(status_code=400, detail="Arabic text is not permitted in INCI ingredient names.")

        success = update_whitelist_ingredient(ingredient_id, updates, conn=conn)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update whitelist ingredient.")

        reload_whitelist_cache(conn=conn)

        cur.execute("SELECT id, name, canonical_name, category, is_active, created_at, updated_at FROM ingredient_whitelist WHERE id = ?;", (ingredient_id,))
        updated = dict(cur.fetchone())

        logger.info(f"Updated whitelist ingredient #{ingredient_id}: {updates}")
        return {"status": "success", "ingredient": updated}
    finally:
        conn.close()


@router.delete("/whitelist/{ingredient_id}")
def delete_whitelist_ingredient_endpoint(ingredient_id: int):
    """
    Deletes an ingredient from the SQLite whitelist by ID and refreshes in-memory matcher cache.
    """
    conn = get_connection()
    try:
        success = delete_whitelist_ingredient(ingredient_id, conn=conn)
        if not success:
            raise HTTPException(status_code=404, detail=f"Whitelist ingredient #{ingredient_id} not found.")

        reload_whitelist_cache(conn=conn)
        logger.info(f"Deleted whitelist ingredient #{ingredient_id}")
        return {"status": "success", "message": f"Whitelist ingredient #{ingredient_id} deleted."}
    finally:
        conn.close()


@router.post("/whitelist/reconcile-pending")
def reconcile_pending_products_with_whitelist(req: WhitelistReconcileRequest):
    """
    Strict Fallback Pipeline Runner:
    Scans products that have NO ingredients after failing direct store INCI extraction
    and failing SkinSAFE / BeautyAPI match.
    Runs the whitelist lookup extractor on their authentic source descriptions.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        # Target products: skincare items with no ingredients and non-empty description
        # that have no INCI match or failed INCI match
        query = """
        SELECT id, title, brand, category, store_id, description_html, canonical_product_id
        FROM products
        WHERE is_skincare = 1
          AND (ingredients_raw IS NULL OR TRIM(ingredients_raw) = '')
          AND description_html IS NOT NULL
          AND LENGTH(TRIM(description_html)) > 30
          AND (inci_matched_id IS NULL OR ingredient_source IS NULL OR ingredient_source = 'unmatched')
        """
        params = []
        if req.category:
            query += " AND category = ?"
            params.append(req.category)
        if req.store_id:
            query += " AND store_id = ?"
            params.append(req.store_id)

        query += " ORDER BY id ASC LIMIT ?;"
        params.append(min(max(1, req.limit), 500))

        cur.execute(query, params)
        pending = cur.fetchall()

        logger.info(f"--- Starting Whitelist Fallback Reconciliation for {len(pending)} pending products ---")

        extracted_count = 0
        still_empty_count = 0
        samples = []

        for p in pending:
            pid = p["id"]
            desc = p["description_html"]
            canon_id = p["canonical_product_id"]
            res = extract_ingredients_via_whitelist(desc, conn=conn, min_ingredients=req.min_ingredients)

            if res and res.get("ingredients_raw"):
                raw_ings = res["ingredients_raw"]
                parsed_ings = res["ingredients_parsed"]
                actives = res["actives"]
                matched_count = res["matched_count"]

                cur.execute("""
                UPDATE products
                SET ingredients_raw = ?,
                    ingredients_parsed = ?,
                    actives = ?,
                    ingredient_source = 'whitelist_extracted',
                    inci_safeguard_log = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (
                    raw_ings,
                    json.dumps(parsed_ings),
                    json.dumps(actives),
                    f"Extracted {matched_count} verified ingredients via description whitelist fallback",
                    pid
                ))

                # Log to inci_audit_evidence as partial candidate evidence only (never authoritative canonical formulation)
                if canon_id:
                    cur.execute("""
                    INSERT INTO inci_audit_evidence (
                        canonical_product_id, source_name, source_record_id,
                        raw_ingredients_found, parsed_tokens, confidence_score,
                        completeness, decision, discrepancy_notes, audited_by
                    ) VALUES (?, 'whitelist_description', ?, ?, ?, 0.60, 'partial', 'candidate_evidence', ?, 'admin_whitelist_reconciler');
                    """, (
                        canon_id,
                        str(pid),
                        raw_ings,
                        json.dumps(parsed_ings),
                        f"Extracted {matched_count} verified ingredients via description whitelist fallback"
                    ))
                extracted_count += 1
                if len(samples) < 10:
                    samples.append({
                        "product_id": pid,
                        "title": p["title"],
                        "brand": p["brand"],
                        "matched_count": matched_count,
                        "ingredients_raw": raw_ings[:80] + "..." if len(raw_ings) > 80 else raw_ings,
                        "actives": actives
                    })
            else:
                still_empty_count += 1

        conn.commit()

        if extracted_count > 0:
            try:
                from services.catalog_service import load_catalog_from_db
                load_catalog_from_db()
            except Exception as e:
                logger.warning(f"Could not refresh catalog service: {e}")

        logger.info(f"Whitelist Reconciliation complete: {extracted_count} extracted, {still_empty_count} still empty out of {len(pending)}.")
        return {
            "status": "success",
            "processed": len(pending),
            "extracted": extracted_count,
            "still_empty": still_empty_count,
            "sample_extractions": samples
        }
    finally:
        conn.close()


class WaterfallResolveRequest(BaseModel):
    limit: Optional[int] = None
    revalidate_all: bool = False
    langsearch_enabled: bool = True
    queue_jev: bool = True
    queue_gemini: bool = True
    queue_inci_research: bool = False
    dry_run: bool = False


class WaterfallBatchConfigRequest(BaseModel):
    scope: str = "unmatched_only"  # "unmatched_only" or "all_skincare"
    limit: Optional[int] = None
    langsearch_enabled: bool = True
    queue_jev: bool = True
    queue_gemini: bool = True
    queue_inci_research: bool = False
    max_workers: int = 6
    dry_run: bool = False


class WaterfallBatchRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self.is_running = False
        self.task_id = None
        self.status = "idle"  # idle, running, completed, cancelled, failed
        self.started_at = None
        self.completed_at = None
        self.duration_seconds = 0.0
        self.total_items = 0
        self.processed_items = 0
        self.progress_percent = 0.0
        self.current_stage = "Idle"
        self.current_product = ""
        self.stats = {
            "total_processed": 0,
            "resolved": 0,
            "unresolved": 0,
            "by_method": {}
        }
        self.recent_logs = deque(maxlen=100)
        self.config = {}
        self.eligibility = None
        self.cancel_event = threading.Event()
        self._thread = None

    def start(self, config: Dict[str, Any]) -> str:
        with self._lock:
            if self.is_running:
                raise HTTPException(status_code=409, detail=f"A waterfall batch task is already running (Task ID: {self.task_id}). Please wait or cancel it first.")

            task_id = f"waterfall_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
            self.task_id = task_id
            self.is_running = True
            self.status = "running"
            self.started_at = datetime.now(timezone.utc).isoformat()
            self.completed_at = None
            self.duration_seconds = 0.0
            self.total_items = 0
            self.processed_items = 0
            self.progress_percent = 0.0
            self.current_stage = "Initializing"
            self.current_product = "Scanning catalog for targets..."
            self.stats = {"total_processed": 0, "resolved": 0, "unresolved": 0, "by_method": {}}
            self.recent_logs.clear()
            self.config = config
            self.eligibility = None
            self.cancel_event.clear()

            self._add_log(f"Started waterfall batch (Scope: {config.get('scope')}, Limit: {config.get('limit') or 'All'}, LangSearch: {config.get('langsearch_enabled')}, Jev: {config.get('queue_jev')}, Gemini: {config.get('queue_gemini')}, Workers: {config.get('max_workers', 6)})")

            self._thread = threading.Thread(target=self._execute_batch, args=(task_id, config), daemon=True)
            self._thread.start()
            return task_id

    def cancel(self) -> bool:
        with self._lock:
            if not self.is_running:
                return False
            self.cancel_event.set()
            self._add_log("Cancellation requested by admin user.")
            return True

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            duration = self.duration_seconds
            if self.started_at:
                try:
                    st = datetime.fromisoformat(self.started_at)
                    end_time = datetime.fromisoformat(self.completed_at) if self.completed_at else datetime.now(timezone.utc)
                    duration = round(max(0.0, (end_time - st).total_seconds()), 1)
                except Exception:
                    pass
            return {
                "is_running": self.is_running,
                "task_id": self.task_id,
                "status": self.status,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "duration_seconds": duration,
                "total_items": self.total_items,
                "processed_items": self.processed_items,
                "progress_percent": round(self.progress_percent, 1),
                "current_stage": self.current_stage,
                "current_product": self.current_product,
                "stats": self.stats,
                "eligibility": self.eligibility,
                "recent_logs": list(self.recent_logs),
                "config": self.config
            }

    def _add_log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.recent_logs.append(f"[{ts}] {msg}")

    def _execute_batch(self, task_id: str, config: Dict[str, Any]):
        try:
            from pipeline.waterfall_inci_resolver import run_waterfall_pipeline

            def progress_cb(stage, processed, total, stats, current_product, last_outcome=None):
                with self._lock:
                    self.current_stage = stage
                    self.processed_items = processed
                    self.total_items = total
                    if total > 0:
                        self.progress_percent = min(100.0, round((processed / total) * 100, 1))
                    if current_product:
                        self.current_product = current_product
                    if stats:
                        self.stats = stats
                    if last_outcome:
                        status_tag = last_outcome.get("status")
                        m = last_outcome.get("method") or "empty"
                        title = last_outcome.get("title") or current_product
                        pid = last_outcome.get("product_id")
                        if status_tag == "resolved":
                            self._add_log(f"[{m.upper()}] #{pid} {title[:45]}")
                        elif status_tag == "pending_llm_verification":
                            self._add_log(f"[TIER7_LLM_QUEUED] #{pid} {title[:45]}")
                        elif status_tag == "candidate_only" and last_outcome.get('candidate'):
                            candidate=last_outcome['candidate']
                            self._add_log(f"[{candidate.source_name.upper()}] Canonical #{last_outcome.get('canonical_id')} "
                                          f"{str(title)[:45]} — evidence only; INCI publication not approved")
                        elif status_tag == "unresolved":
                            self._add_log(f"[NO_VERIFIED_FORMULA] Canonical #{last_outcome.get('canonical_id')} {str(title)[:45]}")

            revalidate = (config.get("scope") == "all_skincare")
            res_stats = run_waterfall_pipeline(
                limit=config.get("limit"),
                revalidate_all=revalidate,
                langsearch_enabled=config.get("langsearch_enabled", True),
                queue_jev=config.get("queue_jev", True),
                queue_gemini=config.get("queue_gemini", True),
                queue_inci_research=config.get("queue_inci_research", False),
                max_workers=config.get("max_workers", 6),
                dry_run=config.get("dry_run", False),
                progress_callback=progress_cb,
                cancel_event=self.cancel_event
            )

            with self._lock:
                self.stats = res_stats
                self.eligibility = res_stats.get('eligibility')
                if not self.total_items:
                    self.total_items = res_stats.get('total_processed', 0)
                self.status = "cancelled" if self.cancel_event.is_set() else (
                    "blocked" if self.total_items == 0 else "completed")
                self.completed_at = datetime.now(timezone.utc).isoformat()
                self.is_running = False
                self.progress_percent = 100.0 if not self.cancel_event.is_set() else self.progress_percent
                if self.total_items == 0:
                    counts=self.eligibility or {}
                    self._add_log("BLOCKED: No verified eligible canonical products. "
                                  f"In scope: {counts.get('in_scope',0)}; unverified: {counts.get('blocked_unverified',0)}; "
                                  f"pending identity review: {counts.get('blocked_pending_review',0)}. "
                                  "Run controlled identity certification or resolve identity reviews first.")
                else:
                    self._add_log(f"Batch execution finished: {self.status}. Total resolved: {res_stats.get('resolved', 0)}/{self.total_items}")

            if not config.get("dry_run", False) and res_stats.get("resolved", 0) > 0:
                try:
                    from services.catalog_service import load_catalog_from_db
                    load_catalog_from_db()
                except Exception as ex:
                    logger.warning(f"Failed to reload catalog cache: {ex}")

        except Exception as e:
            logger.error(f"Waterfall batch failed: {e}", exc_info=True)
            with self._lock:
                self.status = "failed"
                self.is_running = False
                self.completed_at = datetime.now(timezone.utc).isoformat()
                self._add_log(f"ERROR: {str(e)}")


_waterfall_batch_runner = WaterfallBatchRunner()


@router.get("/waterfall/batch/eligibility")
def get_waterfall_batch_eligibility(scope: str = "unmatched_only", limit: Optional[int] = None):
    from pipeline.waterfall_readiness import eligible_waterfall
    if scope not in ('unmatched_only','all_skincare'):
        raise HTTPException(status_code=400,detail='Invalid waterfall scope.')
    conn=get_connection()
    try:
        counts=eligible_waterfall(conn,revalidate_all=scope=='all_skincare',limit=limit)
        return {k:v for k,v in counts.items() if k!='eligible_ids'}
    finally:conn.close()


class IdentityCertificationRequest(BaseModel):
    apply: bool = False
    limit: int = 200


@router.post("/waterfall/identity-certification")
def certify_waterfall_identities(req: IdentityCertificationRequest):
    from pipeline.waterfall_readiness import certify_cross_store
    from security.admin import reviewer_name
    conn=get_connection()
    try:
        result=certify_cross_store(conn,reviewer_name('admin'),apply=req.apply,limit=req.limit)
        if req.apply:conn.commit()
        else:conn.rollback()
        return result
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400,detail=str(exc))
    except Exception:
        conn.rollback()
        raise
    finally:conn.close()


@router.post("/waterfall/batch/start")
def start_waterfall_batch_endpoint(req: WaterfallBatchConfigRequest):
    """
    Initiates asynchronous multi-pass waterfall INCI resolution across skincare products
    with progress tracking, cancellation support, and custom queue toggles.
    """
    from pipeline.waterfall_readiness import eligible_waterfall
    conn=get_connection()
    try:
        readiness=eligible_waterfall(conn,revalidate_all=req.scope=="all_skincare",limit=req.limit)
    finally:
        conn.close()
    if not readiness['eligible']:
        raise HTTPException(status_code=409,detail={
            'reason':'no_verified_eligible_products',
            'message':'No verified canonical products are eligible. Review or certify matching legacy identities first.',
            'eligibility':{k:v for k,v in readiness.items() if k!='eligible_ids'}
        })
    task_id = _waterfall_batch_runner.start(req.dict())
    return {
        "status": "started",
        "task_id": task_id,
        "config": req.dict()
    }


@router.get("/waterfall/batch/status")
def get_waterfall_batch_status_endpoint():
    """
    Returns real-time progress, tier outcome counters, duration, and rolling logs for the waterfall batch.
    """
    return _waterfall_batch_runner.get_status()


@router.post("/waterfall/batch/cancel")
def cancel_waterfall_batch_endpoint():
    """
    Signals graceful cancellation to the active waterfall batch resolution task.
    """
    success = _waterfall_batch_runner.cancel()
    return {
        "status": "cancelling" if success else "not_running",
        "message": "Cancellation request acknowledged." if success else "No waterfall batch task is currently running."
    }


@router.post("/waterfall/resolve")
def run_waterfall_endpoint(req: WaterfallResolveRequest):
    """
    Executes the waterfall INCI resolution pipeline synchronously across skincare products.
    """
    from pipeline.waterfall_inci_resolver import run_waterfall_pipeline
    conn = get_connection()
    try:
        stats = run_waterfall_pipeline(
            conn=conn,
            limit=req.limit,
            revalidate_all=req.revalidate_all,
            langsearch_enabled=req.langsearch_enabled,
            queue_jev=req.queue_jev,
            queue_gemini=req.queue_gemini,
            queue_inci_research=req.queue_inci_research,
            dry_run=req.dry_run
        )
        if stats.get("resolved", 0) > 0 and not req.dry_run:
            try:
                from services.catalog_service import load_catalog_from_db
                load_catalog_from_db()
            except Exception as e:
                logger.warning(f"Could not refresh catalog service: {e}")
        return stats
    finally:
        conn.close()


@router.post("/products/{product_id}/waterfall-resolve")
def resolve_single_product_waterfall_endpoint(product_id: int):
    """
    Executes the 6-tier waterfall INCI resolution for a single product and returns the tier outcome.
    """
    from pipeline.waterfall_inci_resolver import execute_inci_waterfall_for_product
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, title, brand, product_url, description_html, ingredients_raw, ingredient_source, canonical_product_id
        FROM products
        WHERE id = ?;
        """, (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")

        product = dict(row)
        res = execute_inci_waterfall_for_product(product, conn=conn, dry_run=False)
        conn.commit()

        if res.get("status") == "resolved":
            try:
                from services.catalog_service import load_catalog_from_db
                load_catalog_from_db()
            except Exception as e:
                logger.warning(f"Could not refresh catalog service: {e}")

        return res
    finally:
        conn.close()


class ApplyTierRequest(BaseModel):
    tier_code: str
    custom_raw: Optional[str] = None


class ExecuteAllTiersRequest(BaseModel):
    langsearch_enabled: bool = True


@router.get("/products/{product_id}/tier-resolutions")
def get_product_tier_resolutions_endpoint(product_id: int):
    """
    Returns the stored multi-tier INCI resolution payload for a product.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, title, brand, ingredient_source, ingredients_raw, tier_resolutions
        FROM products WHERE id = ?;
        """, (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")

        p = dict(row)
        resolutions = None
        if p.get("tier_resolutions"):
            try:
                resolutions = json.loads(p["tier_resolutions"])
            except Exception:
                pass

        return {
            "product_id": product_id,
            "title": p.get("title"),
            "brand": p.get("brand"),
            "current_source": p.get("ingredient_source"),
            "has_resolutions": resolutions is not None,
            "tier_resolutions": resolutions
        }
    finally:
        conn.close()


@router.post("/products/{product_id}/execute-all-tiers")
def execute_all_tiers_endpoint(product_id: int, req: Optional[ExecuteAllTiersRequest] = None):
    """
    Executes all 7 tiers of INCI ingredient resolution independently for a single product,
    stores the outcome in products.tier_resolutions, and returns the full comparative payload.
    """
    from pipeline.waterfall_inci_resolver import execute_all_tiers_for_product
    langsearch_enabled = req.langsearch_enabled if req else True
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, title, brand, product_url, description_html, ingredients_raw, ingredient_source, canonical_product_id, tier_resolutions
        FROM products WHERE id = ?;
        """, (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")

        product = dict(row)
        res = execute_all_tiers_for_product(
            product,
            conn=conn,
            langsearch_enabled=langsearch_enabled,
            write_to_db=True
        )
        conn.commit()

        return res
    finally:
        conn.close()


@router.post("/products/{product_id}/apply-tier")
def apply_tier_resolution_endpoint(product_id: int, req: ApplyTierRequest):
    """
    Applies a specific tier outcome (tier1..tier7) as the product's active ingredient formula.
    """
    from pipeline.waterfall_inci_resolver import apply_tier_resolution_for_product
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE id = ?;", (product_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")

        result = apply_tier_resolution_for_product(
            product_id,
            tier_code=req.tier_code,
            conn=conn,
            custom_raw=req.custom_raw
        )
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to apply tier {req.tier_code} to #{product_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to apply tier resolution: {e}")
    finally:
        conn.close()


class OverrideLangSearchQuotaRequest(BaseModel):
    tokens_used: Optional[int] = None
    reason: str = "Manual Admin / Daily Allowance Quota Update"


@router.get("/langsearch/quota-status")
def get_langsearch_quota_endpoint():
    """
    Returns real-time LangSearch token quota, rate limits (5 RPS, 1M TPM), and 00:00 UTC countdown.
    """
    from pipeline.rate_limiter import langsearch_limiter
    return langsearch_limiter.get_quota_status()


@router.post("/langsearch/set-quota-exhausted")
def set_langsearch_quota_exhausted_endpoint(req: Optional[OverrideLangSearchQuotaRequest] = None):
    """
    Sets the LangSearch daily token usage / exhaustion state until 00:00 UTC.
    """
    from pipeline.rate_limiter import langsearch_limiter
    toks = req.tokens_used if req else None
    reason = req.reason if req else "Daily allowance exhausted"
    langsearch_limiter.mark_daily_quota_exhausted(tokens_used=toks, reason=reason)
    return langsearch_limiter.get_quota_status()


@router.post("/langsearch/reset-quota")
def reset_langsearch_quota_endpoint():
    """
    Manually resets LangSearch token usage counter back to 0 and clears the exhaustion flag.
    """
    from pipeline.rate_limiter import langsearch_limiter
    langsearch_limiter.reset_daily_quota()
    return langsearch_limiter.get_quota_status()


@router.get("/apis/status")
def get_all_apis_status_endpoint():
    """
    Aggregates real-time health, rate limits, daily quotas, and queue metrics
    for all 3 external APIs: Google Gemini, Jev 1.13, and LangSearch.
    """
    from pipeline.rate_limiter import langsearch_limiter
    from pipeline.config import cfg

    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1. Gemini Metrics
        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_gemini_status = 'pending_gemini';")
        gemini_pending = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_gemini_status = 'gemini_approved';")
        gemini_approved = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_gemini_status = 'gemini_rejected';")
        gemini_rejected = cur.fetchone()[0]

        gemini_api_key = getattr(cfg, "GEMINI_API_KEY", "") or ""
        gemini_configured = bool(gemini_api_key.strip() and len(gemini_api_key.strip()) > 5)

        # 2. Jev Metrics
        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'pending_jev';")
        jev_pending = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'jev_approved';")
        jev_approved = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'jev_rejected';")
        jev_rejected = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'jev_rate_limited';")
        jev_rate_limited = cur.fetchone()[0]

        # 3. LangSearch Metrics
        ls_quota = langsearch_limiter.get_quota_status()
        cur.execute("SELECT COUNT(*) FROM products WHERE ingredient_source = 'langsearch_web_search';")
        ls_resolved = cur.fetchone()[0]

        # Issues aggregation
        issues = []
        if ls_quota.get("is_daily_exhausted"):
            issues.append(f"LangSearch daily token allowance is 100% exhausted ({ls_quota.get('tokens_used_today', 0):,} / {ls_quota.get('daily_token_limit', 0):,} tokens). Paused until 00:00 UTC (Resets in {ls_quota.get('resets_in_human', '')}).")
        if jev_rate_limited > 0:
            issues.append(f"Jev has {jev_rate_limited} products in backoff/rate-limited status, routed to Gemini queue.")
        if not gemini_configured:
            issues.append("Gemini API key is not configured in environment (GEMINI_API_KEY).")

        return {
            "summary": {
                "has_issues": len(issues) > 0,
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            "gemini": {
                "name": "Google Gemini API",
                "role": "Gate 4 Disambiguation & Tier 7 Fallback Validator",
                "model": getattr(cfg, "GEMINI_MODEL_NAME", "gemini-3.5-flash-lite"),
                "is_configured": gemini_configured,
                "status": "operational" if gemini_configured else "not_configured",
                "limits": {
                    "max_rpm": getattr(cfg, "GEMINI_MAX_RPM", 15),
                    "max_rpd": getattr(cfg, "GEMINI_MAX_RPD", 1500),
                    "batch_size": getattr(cfg, "GEMINI_BATCH_SIZE", 10),
                    "pace_delay_seconds": getattr(cfg, "GEMINI_PACE_DELAY_SECONDS", 4.0),
                    "timeout_seconds": getattr(cfg, "GEMINI_TIMEOUT_SECONDS", 35.0)
                },
                "queue_metrics": {
                    "pending_count": gemini_pending,
                    "approved_count": gemini_approved,
                    "rejected_count": gemini_rejected
                }
            },
            "jev": {
                "name": "Jev 1.13 Semantic Engine",
                "role": "Gate 4 Primary Semantic Matcher & Tier 7 Pre-Filter",
                "model": getattr(cfg, "JEV_MODEL_NAME", "jev-1.13-free"),
                "is_configured": True,
                "status": "rate_limited" if jev_rate_limited > 0 else "operational",
                "limits": {
                    "pace_interval_seconds": getattr(cfg, "JEV_REQUEST_INTERVAL_SECONDS", 2.0),
                    "backoff_seconds": getattr(cfg, "JEV_RATE_LIMIT_BACKOFF_SECONDS", 10.0),
                    "timeout_seconds": getattr(cfg, "JEV_TIMEOUT_SECONDS", 8.0),
                    "max_rps": round(1.0 / max(0.1, getattr(cfg, "JEV_REQUEST_INTERVAL_SECONDS", 2.0)), 2)
                },
                "queue_metrics": {
                    "pending_count": jev_pending,
                    "approved_count": jev_approved,
                    "rejected_count": jev_rejected,
                    "rate_limited_handoff_count": jev_rate_limited
                }
            },
            "langsearch": {
                "name": "LangSearch Web Search Engine",
                "role": "Tier 4 Web INCI Retrieval & INKEEDecoder Scraper",
                "is_configured": bool(getattr(cfg, "LANGSEARCH_API_KEY", "")),
                "status": "daily_exhausted" if ls_quota.get("is_daily_exhausted") else "operational",
                "is_daily_exhausted": bool(ls_quota.get("is_daily_exhausted")),
                "exhausted_reason": ls_quota.get("exhausted_reason"),
                "tokens_used_today": ls_quota.get("tokens_used_today", 0),
                "daily_token_limit": ls_quota.get("daily_token_limit", 10_000_000),
                "daily_used_percentage": ls_quota.get("daily_used_percentage", 0.0),
                "resets_in_human": ls_quota.get("resets_in_human", ""),
                "resets_at_utc": ls_quota.get("resets_at_utc", ""),
                "limits": {
                    "max_rps": ls_quota.get("max_rps", 5.0),
                    "rolling_tpm_limit": ls_quota.get("rolling_tpm_limit", 1_000_000),
                    "daily_token_limit": ls_quota.get("daily_token_limit", 10_000_000),
                    "preferred_domains": ["inkeedecoder.com", "incidecoder.com"],
                    "blacklisted_domains": ["incibeauty.com"]
                },
                "metrics": {
                    "resolved_products_count": ls_resolved
                }
            }
        }
    finally:
        conn.close()


# ==============================================================================
# GATE 4 LLM QUEUE LIFECYCLE & TELEMETRY ENDPOINTS (JEV & GEMINI)
# ==============================================================================

from pipeline.queue_runner import master_queue_manager


class QueueStartRequest(BaseModel):
    worker: str = "jev"  # "jev", "gemini", or "both"
    limit: Optional[int] = None
    batch_size: Optional[int] = None  # for Gemini (default: 10)
    all_pending: bool = False  # for Gemini


class QueueControlRequest(BaseModel):
    worker: str = "all"  # "jev", "gemini", or "all"


@router.get("/queues/status")
def get_queue_status():
    """Returns real-time execution status, progress, stats, and queue depths for Jev and Gemini."""
    return master_queue_manager.get_status()


@router.post("/queues/start")
def start_queue(req: QueueStartRequest):
    """Starts asynchronous Jev, Gemini, or Sequential verification queue."""
    try:
        config = {
            "limit": req.limit,
            "batch_size": req.batch_size,
            "all_pending": req.all_pending
        }
        res = master_queue_manager.start(req.worker, config)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/queues/pause")
def pause_queue(req: QueueControlRequest):
    """Pauses Jev, Gemini, or all active queue workers."""
    return {"status": "paused", "results": master_queue_manager.pause(req.worker)}


@router.post("/queues/resume")
def resume_queue(req: QueueControlRequest):
    """Resumes paused Jev, Gemini, or all queue workers."""
    return {"status": "resumed", "results": master_queue_manager.resume(req.worker)}


@router.post("/queues/stop")
def stop_queue(req: QueueControlRequest):
    """Gracefully terminates Jev, Gemini, or all active queue workers."""
    return {"status": "stopping", "results": master_queue_manager.stop(req.worker)}






class IdentityReviewDecision(BaseModel):
    status: str
    reviewer: str
    notes: str


@router.get('/identity/audit')
def get_identity_audit():
    """Inspect possible duplicate canonicals and mixed clusters without mutations."""
    from pipeline.identity_review import audit_canonical_identity
    conn = get_connection()
    try:
        return audit_canonical_identity(conn)
    finally:
        conn.close()


@router.post('/identity/audit')
def record_identity_audit():
    """Save idempotent review cases; canonical IDs and formulations are preserved."""
    from pipeline.identity_review import audit_canonical_identity
    conn = get_connection()
    try:
        result = audit_canonical_identity(conn, persist=True)
        conn.commit()
        return result
    finally:
        conn.close()


@router.get('/identity/reviews')
def list_identity_reviews(status: Optional[str] = None):
    from pipeline.identity_review import init_identity_review_db
    conn = get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_identity_reviews'").fetchone():
            return {'reviews': [], 'total': 0}
        query = 'SELECT * FROM canonical_identity_reviews'
        if status:
            rows = conn.execute(query + ' WHERE status=? ORDER BY id', (status,))
        else:
            rows = conn.execute(query + ' ORDER BY id')
        result = []
        for row in rows:
            item = dict(row)
            for field in ('canonical_ids', 'offer_ids', 'details'):
                item[field] = json.loads(item[field])
            result.append(item)
        return {'reviews': result, 'total': len(result)}
    finally:
        conn.close()


@router.post('/identity/reviews/{review_id}')
def decide_identity_review(review_id: int, request: IdentityReviewDecision):
    from pipeline.identity_review import review_identity_case, init_identity_review_db
    conn = get_connection()
    try:
        init_identity_review_db(conn)
        review_identity_case(conn, review_id, request.status, reviewer_name(request.reviewer), request.notes)
        conn.commit()
        return {'status': request.status, 'review_id': review_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
