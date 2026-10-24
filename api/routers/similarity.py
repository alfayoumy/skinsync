"""
Similarity Router: Real-time formulation similarity, active overlap, cross-encoder scoring,
and cosmetic compatibility safeguards between product pairs.
"""

import json
from typing import List, Optional, Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.db_manager import get_connection
from pipeline.config import cfg
from pipeline.logging_config import get_logger
from pipeline.inci_matcher import clean_product_title, score_cross_encoder
from pipeline.safeguards import CosmeticCompatibilityValidator
from services.catalog_service import parse_and_clean_ingredients, extract_actives_from_title
from services.recommender import compute_weighted_jaccard, compute_title_sim, evaluate_skin_safety

logger = get_logger("skincare_engine.routers.similarity")

router = APIRouter(tags=["similarity"])

# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class ProductItemPayload(BaseModel):
    id: Optional[int] = None
    title: Optional[str] = None
    brand: Optional[str] = None
    ingredients: Optional[Union[List[str], str]] = None
    is_canonical: Optional[bool] = None

class SimilarityScoreRequest(BaseModel):
    product_id_a: Optional[int] = None
    product_id_b: Optional[int] = None
    canonical_product_id_a: Optional[int] = None
    canonical_product_id_b: Optional[int] = None
    entity_type_a: Optional[str] = None  # 'canonical' or 'product'
    entity_type_b: Optional[str] = None  # 'canonical' or 'product'
    product_a: Optional[ProductItemPayload] = None
    product_b: Optional[ProductItemPayload] = None
    skin_type: str = "normal"
    include_cross_encoder: bool = True

# ==============================================================================
# RESOLVER
# ==============================================================================

def _resolve_product_for_similarity(
    pid: Optional[int],
    payload: Optional[ProductItemPayload],
    conn,
    canonical_pid: Optional[int] = None,
    entity_type: Optional[str] = None
):
    cur = conn.cursor()

    # Explicit Canonical Resolution
    target_canonical_id = canonical_pid if canonical_pid is not None else (pid if entity_type == "canonical" else None)
    if target_canonical_id is not None:
        cur.execute("""
        SELECT id, canonical_title as title, brand, ingredients_raw, ingredients_parsed, actives
        FROM canonical_products
        WHERE id = ?;
        """, (target_canonical_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Canonical product with ID {target_canonical_id} not found in database.")
        raw_ings = row["ingredients_raw"] or ""
        parsed = json.loads(row["ingredients_parsed"]) if row["ingredients_parsed"] else []
        if not parsed and raw_ings:
            parsed, _ = parse_and_clean_ingredients(raw_ings)
        acts = set(json.loads(row["actives"])) if row["actives"] else set()
        if not acts:
            acts = extract_actives_from_title(row["title"])
            if raw_ings:
                _, p_acts = parse_and_clean_ingredients(raw_ings)
                acts = acts.union(p_acts)
        return {
            "id": row["id"],
            "canonical_product_id": row["id"],
            "title": row["title"],
            "brand": row["brand"],
            "parsed_ingredients": parsed,
            "actives": acts,
            "raw_ingredients": raw_ings,
            "is_authoritative_canonical": True
        }

    # Explicit Product Offer Resolution
    if pid is not None and entity_type == "product":
        cur.execute("""
        SELECT p.id, p.title, p.brand, p.ingredients_raw, p.ingredients_parsed, p.actives, p.canonical_product_id,
               c.canonical_title, c.ingredients_raw as canon_raw, c.ingredients_parsed as canon_parsed, c.actives as canon_actives
        FROM products p
        LEFT JOIN canonical_products c ON c.id = p.canonical_product_id
        WHERE p.id = ?;
        """, (pid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product offer with ID {pid} not found in database.")
        if row["canonical_product_id"] and row["canon_raw"]:
            title = row["canonical_title"] or row["title"]
            raw_ings = row["canon_raw"]
            parsed = json.loads(row["canon_parsed"]) if row["canon_parsed"] else []
            acts = set(json.loads(row["canon_actives"])) if row["canon_actives"] else set()
        else:
            title = row["title"]
            raw_ings = row["ingredients_raw"] or ""
            parsed = json.loads(row["ingredients_parsed"]) if row["ingredients_parsed"] else []
            acts = set(json.loads(row["actives"])) if row["actives"] else set()
        if not parsed and raw_ings:
            parsed, _ = parse_and_clean_ingredients(raw_ings)
        if not acts:
            acts = extract_actives_from_title(title)
            if raw_ings:
                _, p_acts = parse_and_clean_ingredients(raw_ings)
                acts = acts.union(p_acts)
        return {
            "id": row["id"],
            "canonical_product_id": row["canonical_product_id"],
            "title": title,
            "brand": row["brand"],
            "parsed_ingredients": parsed,
            "actives": acts,
            "raw_ingredients": raw_ings,
            "is_authoritative_canonical": bool(row["canonical_product_id"] and row["canon_raw"])
        }

    # Standard Fallback Resolution (Checks canonical first, then retailer products)
    if pid is not None:
        cur.execute("""
        SELECT id, canonical_title as title, brand, ingredients_raw, ingredients_parsed, actives
        FROM canonical_products
        WHERE id = ?;
        """, (pid,))
        row = cur.fetchone()
        if row:
            raw_ings = row["ingredients_raw"] or ""
            parsed = json.loads(row["ingredients_parsed"]) if row["ingredients_parsed"] else []
            if not parsed and raw_ings:
                parsed, _ = parse_and_clean_ingredients(raw_ings)
            acts = set(json.loads(row["actives"])) if row["actives"] else set()
            if not acts:
                acts = extract_actives_from_title(row["title"])
                if raw_ings:
                    _, p_acts = parse_and_clean_ingredients(raw_ings)
                    acts = acts.union(p_acts)
            return {
                "id": row["id"],
                "canonical_product_id": row["id"],
                "title": row["title"],
                "brand": row["brand"],
                "parsed_ingredients": parsed,
                "actives": acts,
                "raw_ingredients": raw_ings,
                "is_authoritative_canonical": True
            }

        cur.execute("""
        SELECT p.id, p.title, p.brand, p.ingredients_raw, p.ingredients_parsed, p.actives, p.canonical_product_id,
               c.canonical_title, c.ingredients_raw as canon_raw, c.ingredients_parsed as canon_parsed, c.actives as canon_actives
        FROM products p
        LEFT JOIN canonical_products c ON c.id = p.canonical_product_id
        WHERE p.id = ?;
        """, (pid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product with ID {pid} not found in database.")

        if row["canonical_product_id"] and row["canon_raw"]:
            title = row["canonical_title"] or row["title"]
            raw_ings = row["canon_raw"]
            parsed = json.loads(row["canon_parsed"]) if row["canon_parsed"] else []
            acts = set(json.loads(row["canon_actives"])) if row["canon_actives"] else set()
        else:
            title = row["title"]
            raw_ings = row["ingredients_raw"] or ""
            parsed = json.loads(row["ingredients_parsed"]) if row["ingredients_parsed"] else []
            acts = set(json.loads(row["actives"])) if row["actives"] else set()


        if not parsed and raw_ings:
            parsed, _ = parse_and_clean_ingredients(raw_ings)
        if not acts:
            acts = extract_actives_from_title(title)
            if raw_ings:
                _, p_acts = parse_and_clean_ingredients(raw_ings)
                acts = acts.union(p_acts)

        return {
            "id": row["id"],
            "canonical_product_id": row["canonical_product_id"],
            "title": title,
            "brand": row["brand"],
            "parsed_ingredients": parsed,
            "actives": acts,
            "raw_ingredients": raw_ings,
            "is_authoritative_canonical": bool(row["canonical_product_id"] and row["canon_raw"])
        }
    elif payload is not None:
        title = payload.title or ""
        brand = payload.brand or "Unknown"
        ings_input = payload.ingredients or []
        if isinstance(ings_input, str):
            parsed, acts = parse_and_clean_ingredients(ings_input)
            raw_ings = ings_input
        else:
            parsed = [str(x).strip().lower() for x in ings_input if str(x).strip()]
            raw_ings = ", ".join(parsed)
            _, acts = parse_and_clean_ingredients(raw_ings)
        t_acts = extract_actives_from_title(title)
        acts = acts.union(t_acts)
        return {
            "id": payload.id,
            "title": title,
            "brand": brand,
            "parsed_ingredients": parsed,
            "actives": acts,
            "raw_ingredients": raw_ings
        }
    else:
        raise HTTPException(status_code=400, detail="Must provide either product ID or product payload.")

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.post("/similarity/score")
def score_similarity_endpoint(req: SimilarityScoreRequest):
    """
    Computes pairwise formulation similarity and cosmetic compatibility between two products.
    Accepts either database product IDs or custom product payloads.
    Evaluates:
    - 3-zone FDA/EU weighted Jaccard similarity
    - Active molecule overlap
    - Product title similarity
    - Cosmetic compatibility safeguards
    - FastEmbed cross-encoder score
    - Dermatological safety and tolerance breakdown
    """
    conn = get_connection()
    try:
        prod_a = _resolve_product_for_similarity(
            req.product_id_a, req.product_a, conn,
            canonical_pid=req.canonical_product_id_a,
            entity_type=req.entity_type_a
        )
        prod_b = _resolve_product_for_similarity(
            req.product_id_b, req.product_b, conn,
            canonical_pid=req.canonical_product_id_b,
            entity_type=req.entity_type_b
        )
    finally:
        conn.close()

    parsed_a = prod_a["parsed_ingredients"]
    parsed_b = prod_b["parsed_ingredients"]
    acts_a = prod_a["actives"]
    acts_b = prod_b["actives"]

    # 1. 3-Zone Weighted Jaccard
    inci_sim = compute_weighted_jaccard(parsed_a, parsed_b) if (parsed_a and parsed_b) else 0.0

    # 2. Actives Overlap
    if acts_a and acts_b:
        inter_act = len(acts_a.intersection(acts_b))
        union_act = len(acts_a.union(acts_b))
        actives_sim = (inter_act / union_act) if union_act > 0 else 0.0
    elif not acts_a and not acts_b:
        actives_sim = 0.5
    else:
        actives_sim = 0.1

    # 3. Title Sim
    title_sim = compute_title_sim(prod_a["title"], prod_b["title"])

    # 4. Formula Sim
    if parsed_a and parsed_b:
        formula_sim = (actives_sim * 0.50) + (inci_sim * 0.35) + (title_sim * 0.15)
    else:
        formula_sim = (actives_sim * 0.60) + (title_sim * 0.40)

    # 5. Cross-Encoder
    cross_score = None
    if req.include_cross_encoder and cfg.ENABLE_CROSS_ENCODER:
        try:
            cand_a_text = f"{prod_a['brand']} {clean_product_title(prod_a['title'])}. Actives: {', '.join(sorted(acts_a)) if acts_a else 'N/A'}. Ingredients: {', '.join(parsed_a[:8]) if parsed_a else 'N/A'}"
            cand_b_text = f"{prod_b['brand']} {clean_product_title(prod_b['title'])}. Ingredients: {', '.join(parsed_b[:8]) if parsed_b else 'N/A'}"
            cross_score = score_cross_encoder(cand_a_text, cand_b_text)
            cross_score = float(round(cross_score, 4))
        except Exception as e:
            logger.warning(f"Cross-encoder scoring error: {e}")

    # 6. Safeguards
    compat_ok, reason = CosmeticCompatibilityValidator.validate_pair(prod_a["title"], prod_b["title"])

    # 7. Skin Safety
    safety_a, warn_a = evaluate_skin_safety(parsed_a, acts_a, req.skin_type)
    safety_b, warn_b = evaluate_skin_safety(parsed_b, acts_b, req.skin_type)

    # 8. Calibrated Match Score
    if req.skin_type == "normal":
        base_match = 45.0 + (formula_sim * 53.0)
    else:
        mean_safety = (safety_a + safety_b) / 2.0
        base_match = 45.0 + (formula_sim * 39.0) + ((mean_safety / 100.0) * 14.5)

    if cross_score is not None:
        final_match = (base_match * 0.75) + (cross_score * 100 * 0.25)
    else:
        final_match = base_match

    final_match = float(round(min(99.0, max(15.0, final_match)), 1))

    return {
        "match_score": final_match,
        "formula_similarity": float(round(formula_sim, 4)),
        "inci_weighted_jaccard": float(round(inci_sim, 4)),
        "actives_overlap": float(round(actives_sim, 4)),
        "title_similarity": float(round(title_sim, 4)),
        "cross_encoder_score": cross_score,
        "safeguards": {
            "is_compatible": compat_ok,
            "notes": reason or "Direct compatibility passed"
        },
        "skin_type": req.skin_type,
        "product_a": {
            "id": prod_a["id"],
            "title": prod_a["title"],
            "brand": prod_a["brand"],
            "actives": sorted(list(acts_a)),
            "safety_score": safety_a,
            "warnings": warn_a
        },
        "product_b": {
            "id": prod_b["id"],
            "title": prod_b["title"],
            "brand": prod_b["brand"],
            "actives": sorted(list(acts_b)),
            "safety_score": safety_b,
            "warnings": warn_b
        }
    }
