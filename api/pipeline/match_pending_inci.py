"""
Dedicated Background Worker for INCI Formulation Backfill.
Decoupled from store crawling to allow controlled, rate-limited execution
using FastEmbed ONNX vector search, Rule Safeguards, and Jev 1.13 Free.
"""

import os
import sys
import json
import time
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.inci_matcher import match_with_inci_db, parse_and_normalize_ingredients
from pipeline.whitelist_matcher import extract_ingredients_via_whitelist
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

logger = logging.getLogger("skincare_pipeline.match_pending_inci")

PRIORITY_CATEGORIES = (
    "Serum", "Moisturizer", "Sunscreen", "Cleanser", "Toner", "Exfoliator", "Eye Skincare", "Eye Care", "Lip Care"
)

def run_inci_backfill(limit: int = None, category_filter: str = None):
    """
    Backfills missing ingredients for priority skincare products.
    """
    if limit is None:
        limit = cfg.INCI_BACKFILL_BATCH_SIZE
    conn = get_connection()
    cur = conn.cursor()

    query = """
    SELECT id, title, brand, category, product_url, description_html, canonical_product_id
    FROM products
    WHERE (ingredients_raw IS NULL OR ingredients_raw = '')
      AND ingredient_source IS NULL
      AND brand IS NOT NULL AND LOWER(brand) NOT IN ('unknown', 'general')
    """
    params = []

    if category_filter:
        query += " AND category = ?"
        params.append(category_filter)
    else:
        placeholders = ",".join("?" for _ in PRIORITY_CATEGORIES)
        query += f" AND category IN ({placeholders})"
        params.extend(PRIORITY_CATEGORIES)

    query += " ORDER BY id ASC"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    cur.execute(query, params)
    pending = [dict(r) for r in cur.fetchall()]

    logger.info(f"--- Starting INCI Backfill Worker ---")
    logger.info(f"Found {len(pending)} priority skincare products awaiting formulation matching.")

    matched_count = 0
    skipped_count = 0

    for idx, p in enumerate(pending, 1):
        pid = p["id"]
        title = p["title"]
        brand = p["brand"]
        cat = p["category"]
        canon_id = p.get("canonical_product_id")

        match_res = match_with_inci_db(brand, title, conn)

        if match_res and isinstance(match_res, dict):
            raw_ings = match_res["ingredients"]
            parsed_ings, actives = parse_and_normalize_ingredients(raw_ings)

            # Route authoritative publication to canonical_products via FormulationRepository
            if canon_id and raw_ings and len(raw_ings.strip()) >= 15:
                from pipeline.formulation_service import FormulationRepository, FormulationCandidate, match_is_verified
                from pipeline.inci_tokenizer import tokenize_inci_string
                tok_res = tokenize_inci_string(raw_ings, product_title=title)
                cand = FormulationCandidate(
                    canonical_product_id=canon_id,
                    source_name=match_res.get("source", "skinsafe_matched"),
                    raw_ingredients=raw_ings,
                    parsed_tokens=tok_res.tokens,
                    actives=tok_res.declared_actives,
                    confidence_score=float(match_res.get("hybrid_score", 0.80)),
                    source_record_id=str(pid),
                    completeness=tok_res.completeness if match_is_verified(match_res) else "unverified",
                    validation_flags={"is_truncated": tok_res.is_truncated},
                    inci_matched_id=match_res.get("matched_id"),
                    inci_matched_name=match_res.get("matched_name")
                )
                repo = FormulationRepository()
                if tok_res.completeness == "full":
                    repo.publish_formulation(candidate=cand, approval_reason=f"Matched via INCI backfill worker ({match_res.get('source', 'skinsafe')})", force=False, conn=conn)
                else:
                    repo.record_candidate_evidence(candidate=cand, decision="candidate_evidence", discrepancy_notes="Candidate from INCI backfill logged as audit evidence.", conn=conn)

            cur.execute("""
            UPDATE products
            SET inci_matched_id = ?, inci_matched_name = ?,
                inci_vector_score = ?, inci_token_score = ?,
                inci_hybrid_score = ?, inci_cross_score = ?,
                inci_jev_score = ?, inci_gemini_score = ?,
                gate1_quality_status = ?, gate2_safeguard_status = ?,
                gate3_active_status = ?, gate4_cross_status = ?,
                gate4_jev_status = ?, gate4_gemini_status = ?,
                inci_safeguard_log = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (
                match_res.get("matched_id"), match_res.get("matched_name"),
                match_res.get("vector_score"), match_res.get("token_score"),
                match_res.get("hybrid_score"), match_res.get("inci_cross_score"),
                match_res.get("inci_jev_score"), match_res.get("inci_gemini_score"),
                match_res.get("gate1_quality_status"), match_res.get("gate2_safeguard_status"),
                match_res.get("gate3_active_status"), match_res.get("gate4_cross_status"),
                match_res.get("gate4_jev_status"), match_res.get("gate4_gemini_status"),
                match_res.get("safeguard_log"), pid
            ))
            conn.commit()
            matched_count += 1
            h_score = match_res.get("hybrid_score", 0.0)
            src = match_res.get("source", "skinsafe")
            g4 = match_res.get("gate4_jev_status", "pending")
            logger.info(f"[{idx}/{len(pending)}] [MATCHED via {src} ({h_score:.2f}) | Gate 4: {g4}] {brand} - {title[:35]}...")
        else:
            # Fallback Priority 3: Whitelist Lookup on Description
            # STRICT PRECEDENCE: only executed for products with no ingredients after no skinsafe/beautyapi match
            desc = p.get("description_html")
            w_res = extract_ingredients_via_whitelist(desc, conn=conn) if desc else None
            if w_res and w_res.get("ingredients_raw"):
                raw_ings = w_res["ingredients_raw"]
                # Whitelist results are strictly partial callouts: record in audit evidence, never publish as full formulation
                if canon_id:
                    from pipeline.formulation_service import FormulationRepository, FormulationCandidate, match_is_verified
                    from pipeline.inci_tokenizer import tokenize_inci_string
                    tok_res = tokenize_inci_string(raw_ings, product_title=title)
                    cand = FormulationCandidate(
                        canonical_product_id=canon_id,
                        source_name="whitelist_extracted",
                        raw_ingredients=raw_ings,
                        parsed_tokens=tok_res.tokens,
                        actives=tok_res.declared_actives,
                        confidence_score=0.50,
                        source_record_id=str(pid),
                        completeness="partial",
                        validation_flags={"is_truncated": tok_res.is_truncated, "partial_reason": "whitelist_description_fallback"}
                    )
                    FormulationRepository().record_candidate_evidence(
                        candidate=cand,
                        decision="candidate_evidence",
                        discrepancy_notes=f"Extracted {w_res['matched_count']} verified whitelist ingredients. Preserved as audit evidence.",
                        conn=conn
                    )

                cur.execute("""
                UPDATE products
                SET ingredient_source = 'whitelist_extracted',
                    gate1_quality_status = 'passed_whitelist_extracted',
                    gate2_safeguard_status = 'passed_direct',
                    gate3_active_status = 'passed_active_verified',
                    gate4_cross_status = 'whitelist_extracted',
                    gate4_jev_status = 'bypassed_whitelist_lookup',
                    inci_safeguard_log = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (
                    f"Extracted {w_res['matched_count']} verified ingredients via description whitelist fallback (partial evidence)",
                    pid
                ))
                conn.commit()
                matched_count += 1
                logger.info(f"[{idx}/{len(pending)}] [EXTRACTED via Whitelist ({w_res['matched_count']} ings)] {brand} - {title[:35]}...")
            else:
                cur.execute("""
                UPDATE products
                SET ingredient_source = 'unmatched',
                    gate1_quality_status = 'failed_no_match',
                    gate2_safeguard_status = 'failed_no_match',
                    gate3_active_status = 'failed_no_match',
                    gate4_jev_status = 'skipped_no_match',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (pid,))
                conn.commit()
                skipped_count += 1
            if idx % 10 == 0 or idx == len(pending):
                logger.info(f"[{idx}/{len(pending)}] Progress: {matched_count} matched/extracted, {skipped_count} unmatched/skipped")

    conn.close()
    logger.info(f"--- INCI Backfill Worker Complete ---")
    logger.info(f"Successfully Backfilled: {matched_count} | Skipped/No Match: {skipped_count}")

    return {
        "total_processed": len(pending),
        "matched": matched_count,
        "skipped": skipped_count
    }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Backfill missing INCI formulations using Vector + Rule + Jev pipeline")
    parser.add_argument("--limit", type=int, default=cfg.INCI_BACKFILL_BATCH_SIZE, help=f"Maximum number of products to process in this run (default: {cfg.INCI_BACKFILL_BATCH_SIZE})")
    parser.add_argument("--category", type=str, default=None, help="Filter by specific category (e.g. Serum, Sunscreen)")
    args = parser.parse_args()

    run_inci_backfill(limit=args.limit, category_filter=args.category)
