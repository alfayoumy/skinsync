"""
Dedicated Asynchronous Gate 4 Worker for Gemini 3.5 Flash-Lite Batch Verification.
Processes candidate matches in configurable multi-product batches (e.g. 10 products per API call),
staying strictly within 15 Requests Per Minute (RPM) and 1,500 Requests Per Day (RPD) limits.
"""

import os
import sys
import time
import logging
import argparse
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.inci_matcher import verify_batch_with_gemini, score_cross_encoder
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

from pipeline.logging_config import setup_logging, get_logger
logger = get_logger("skincare_pipeline.gemini_worker")

def chunk_list(lst: List[Any], chunk_size: int) -> List[List[Any]]:
    """Splits a list into chunks of at most chunk_size items."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def run_gemini_verification_queue(limit: Optional[int] = None, batch_size: Optional[int] = None, all_pending: bool = False) -> Dict[str, Any]:
    """
    Pulls products queued for Gemini semantic evaluation and processes them in batches.
    
    Args:
        limit: Total maximum products to process in this worker run (default: cfg.GEMINI_QUEUE_BATCH_SIZE).
        batch_size: Number of product pairs bundled into a single Gemini prompt (default: cfg.GEMINI_BATCH_SIZE).
        all_pending: If True, evaluates any product with missing Gemini status, not just pending_gemini / jev_rate_limited.
    """
    if limit is None:
        limit = getattr(cfg, "GEMINI_QUEUE_BATCH_SIZE", 50)
    if batch_size is None or batch_size <= 0:
        batch_size = getattr(cfg, "GEMINI_BATCH_SIZE", 10)

    conn = get_connection()
    cur = conn.cursor()

    if all_pending:
        query = """
        SELECT id, title, brand, inci_matched_name, inci_hybrid_score, ingredients_raw
        FROM products
        WHERE inci_matched_name IS NOT NULL
          AND (gate4_gemini_status IS NULL OR gate4_gemini_status = 'pending_gemini')
          AND (gate4_jev_status IS NULL OR gate4_jev_status != 'jev_approved')
        ORDER BY id ASC
        LIMIT ?;
        """
    else:
        query = """
        SELECT id, title, brand, inci_matched_name, inci_hybrid_score, ingredients_raw
        FROM products
        WHERE inci_matched_name IS NOT NULL
          AND (
            gate4_gemini_status = 'pending_gemini'
            OR (gate4_jev_status = 'jev_rate_limited' AND (gate4_gemini_status IS NULL OR gate4_gemini_status = 'pending_gemini'))
          )
        ORDER BY id ASC
        LIMIT ?;
        """

    try:
        cur.execute(query, (limit,))
        queue = [dict(r) for r in cur.fetchall()]

        logger.info(f"=== Starting Gate 4 Gemini Batch Verification Worker ===")
        logger.info(f"Found {len(queue)} products queued for Gemini evaluation (Batch Size: {batch_size}).")

        if not queue:
            return {"processed": 0, "approved": 0, "rejected": 0, "rate_limited": 0, "api_calls": 0}

        batches = chunk_list(queue, batch_size)
        approved = 0
        rejected = 0
        rate_limited = 0
        api_calls = 0

        cross_threshold = getattr(cfg, "CROSS_ENCODER_CONFIDENCE_THRESHOLD", 0.85)
        gemini_threshold = getattr(cfg, "GEMINI_CONFIDENCE_THRESHOLD", 0.70)

        for b_idx, batch in enumerate(batches, 1):
            logger.info(f"--- Processing Batch {b_idx}/{len(batches)} ({len(batch)} items) ---")

            # 1. Compute local ONNX Cross-Encoder scores for the batch
            batch_items = []
            cross_scores = {}
            for p in batch:
                pid = p["id"]
                title = p["title"]
                cand_name = p["inci_matched_name"]
                c_score = score_cross_encoder(title, cand_name)
                cross_scores[pid] = c_score
                batch_items.append({
                    "id": pid,
                    "store_title": title,
                    "candidate_title": cand_name
                })

            # 2. Call Gemini 3.5 Flash-Lite batch verification (1 request for the entire batch)
            evaluations = verify_batch_with_gemini(batch_items)
            api_calls += 1

            if evaluations is None:
                # API rate limit reached or network failure
                rate_limited += len(batch)
                logger.warning(f"[Batch {b_idx}] Gemini API returned None (Rate limited or daily quota exhausted). Leaving {len(batch)} items in queue.")
                # Record cross-encoder scores so progress isn't lost
                for p in batch:
                    pid = p["id"]
                    c_score = cross_scores.get(pid, 0.5)
                    c_status = "cross_approved" if c_score >= cross_threshold else "cross_ambiguous"
                    cur.execute("""
                    UPDATE products
                    SET gate4_gemini_status = 'pending_gemini',
                        gate4_cross_status = ?, inci_cross_score = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """, (c_status, c_score, pid))
                conn.commit()
                break

            # 3. Update SQLite for each item in the batch
            batch_approved = 0
            batch_rejected = 0

            for p in batch:
                pid = p["id"]
                title = p["title"]
                cand_name = p["inci_matched_name"]
                c_score = cross_scores.get(pid, 0.5)
                c_status = "cross_approved" if c_score >= cross_threshold else "cross_ambiguous"

                if pid in evaluations:
                    is_same, conf, reason = evaluations[pid]
                    if is_same and conf >= gemini_threshold:
                        # If product has no ingredients, backfill from matched inci_reference
                        cur.execute("""
                        SELECT r.ingredients
                        FROM products p
                        JOIN inci_reference r ON r.id = p.inci_matched_id
                        WHERE p.id = ?;
                        """, (pid,))
                        ref_row = cur.fetchone()
                        ref_ings = ref_row[0] if ref_row else None

                        curr_raw = p.get("ingredients_raw") if isinstance(p, dict) else p["ingredients_raw"]
                        if (not curr_raw or len(curr_raw.strip()) < 10) and ref_ings and len(ref_ings.strip()) >= 15:
                            from pipeline.inci_matcher import parse_and_normalize_ingredients
                            import json
                            parsed, acts = parse_and_normalize_ingredients(ref_ings)
                            cur.execute("""
                            UPDATE products
                            SET gate4_gemini_status = 'gemini_approved', inci_gemini_score = ?,
                                gate4_cross_status = ?, inci_cross_score = ?,
                                ingredients_raw = ?, ingredients_parsed = ?, actives = ?,
                                ingredient_source = 'skinsafe_matched',
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?;
                            """, (conf, c_status, c_score, ref_ings, json.dumps(parsed), json.dumps(acts), pid))
                        else:
                            cur.execute("""
                            UPDATE products
                            SET gate4_gemini_status = 'gemini_approved', inci_gemini_score = ?,
                                gate4_cross_status = ?, inci_cross_score = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?;
                            """, (conf, c_status, c_score, pid))
                        approved += 1
                        batch_approved += 1
                        logger.info(f"  [GEMINI BATCH APPROVED] ID {pid}: '{title[:32]}' == '{cand_name[:32]}' (Conf: {conf:.2f} | Reason: {reason[:40]})")
                    else:
                        cur.execute("""
                        UPDATE products
                        SET gate4_gemini_status = 'gemini_rejected', inci_gemini_score = ?,
                            gate4_cross_status = ?, inci_cross_score = ?,
                            ingredients_raw = CASE WHEN ingredient_source = 'skinsafe_matched' THEN NULL ELSE ingredients_raw END,
                            ingredients_parsed = CASE WHEN ingredient_source = 'skinsafe_matched' THEN NULL ELSE ingredients_parsed END,
                            actives = CASE WHEN ingredient_source = 'skinsafe_matched' THEN NULL ELSE actives END,
                            ingredient_source = CASE WHEN ingredient_source = 'skinsafe_matched' THEN NULL ELSE ingredient_source END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                        """, (conf, c_status, c_score, pid))
                        rejected += 1
                        batch_rejected += 1
                        logger.info(f"  [GEMINI BATCH REJECTED] ID {pid}: '{title[:32]}' != '{cand_name[:32]}' (Conf: {conf:.2f} | Reason: {reason[:40]})")
                else:
                    # Omitted in LLM response: leave for retry
                    rate_limited += 1
                    cur.execute("""
                    UPDATE products
                    SET gate4_gemini_status = 'pending_gemini',
                        gate4_cross_status = ?, inci_cross_score = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """, (c_status, c_score, pid))
                    logger.warning(f"  [GEMINI BATCH OMITTED] ID {pid}: Item missing from LLM response. Kept in pending_gemini.")

            conn.commit()
            logger.info(f"Batch {b_idx} committed: {batch_approved} approved, {batch_rejected} rejected.")

        logger.info("=== Gate 4 Gemini Batch Worker Run Complete ===")
        logger.info(f"Processed: {approved + rejected + rate_limited} | Approved: {approved} | Rejected: {rejected} | Rate Limited: {rate_limited} | API Calls: {api_calls}")

        return {
            "processed": approved + rejected + rate_limited,
            "approved": approved,
            "rejected": rejected,
            "rate_limited": rate_limited,
            "api_calls": api_calls
        }
    finally:
        conn.close()

if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description="Run asynchronous Gate 4 Gemini batch verification worker")
    parser.add_argument("--limit", type=int, default=getattr(cfg, "GEMINI_QUEUE_BATCH_SIZE", 50), help="Total products to verify (default: from config)")
    parser.add_argument("--batch-size", type=int, default=getattr(cfg, "GEMINI_BATCH_SIZE", 10), help="Number of products evaluated per Gemini prompt (default: from config)")
    parser.add_argument("--all-pending", action="store_true", help="Evaluate all unverified products, not only those handed off from Jev")
    args = parser.parse_args()

    run_gemini_verification_queue(limit=args.limit, batch_size=args.batch_size, all_pending=args.all_pending)
