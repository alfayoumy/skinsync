"""
Dedicated Asynchronous Gate 4 Worker for Jev 1.13 Semantic Verification.
Decoupled from store crawling and initial matching.
Processes products marked 'pending_jev' at a smooth, rate-limited pace.
"""

import os
import sys
import time
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.inci_matcher import verify_match_with_jev, score_cross_encoder
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

from pipeline.logging_config import setup_logging, get_logger
logger = get_logger("skincare_pipeline.jev_worker")

def run_jev_verification_queue(limit: int = None):
    """
    Pulls products in 'pending_jev' status and evaluates them with Jev 1.13 Free.
    """
    if limit is None:
        limit = cfg.JEV_QUEUE_BATCH_SIZE
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    SELECT id, title, brand, inci_matched_name, inci_hybrid_score, ingredients_raw
    FROM products
    WHERE gate4_jev_status = 'pending_jev'
      AND inci_matched_name IS NOT NULL
      AND (ingredients_raw IS NULL OR LENGTH(TRIM(ingredients_raw)) < 15 OR ingredient_source IN ('inci_matched', 'pending_tier7_match') OR ingredient_source IS NULL)
    ORDER BY id ASC
    LIMIT ?;
    """, (limit,))

    queue = cur.fetchall()
    logger.info(f"=== Starting Gate 4 Jev Verification Worker ===")
    logger.info(f"Found {len(queue)} products queued for Jev semantic evaluation.")

    if not queue:
        conn.close()
        return {"processed": 0, "approved": 0, "rejected": 0}

    approved = 0
    rejected = 0
    rate_limited = 0

    for idx, p in enumerate(queue, 1):
        pid = p["id"]
        title = p["title"]
        cand_name = p["inci_matched_name"]
        h_score = p["inci_hybrid_score"]

        # 1. Compute local ONNX Cross-Encoder score
        cross_score = score_cross_encoder(title, cand_name)
        cross_status = "cross_approved" if cross_score >= getattr(cfg, "CROSS_ENCODER_CONFIDENCE_THRESHOLD", 0.70) else "cross_ambiguous"

        # 2. Primary: Try Jev 1.13
        verdict = verify_match_with_jev(title, cand_name)

        if verdict is True:
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
                SET gate4_jev_status = 'jev_approved', inci_jev_score = 0.95,
                    gate4_cross_status = ?, inci_cross_score = ?,
                    ingredients_raw = ?, ingredients_parsed = ?, actives = ?,
                    ingredient_source = 'skinsafe_matched',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (cross_status, cross_score, ref_ings, json.dumps(parsed), json.dumps(acts), pid))
            else:
                cur.execute("""
                UPDATE products
                SET gate4_jev_status = 'jev_approved', inci_jev_score = 0.95,
                    gate4_cross_status = ?, inci_cross_score = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (cross_status, cross_score, pid))
            conn.commit()
            approved += 1
            logger.info(f"[{idx}/{len(queue)}] [JEV APPROVED] '{title[:35]}' == '{cand_name[:35]}' (Score: {(h_score or 0.0):.2f} | Cross: {cross_score:.2f})")
        elif verdict is False:
            cur.execute("""
            UPDATE products
            SET gate4_jev_status = 'jev_rejected', inci_jev_score = 0.15,
                gate4_cross_status = ?, inci_cross_score = ?,
                ingredients_raw = CASE WHEN ingredient_source IN ('skinsafe_matched', 'pending_tier7_match') THEN NULL ELSE ingredients_raw END,
                ingredients_parsed = CASE WHEN ingredient_source IN ('skinsafe_matched', 'pending_tier7_match') THEN NULL ELSE ingredients_parsed END,
                actives = CASE WHEN ingredient_source IN ('skinsafe_matched', 'pending_tier7_match') THEN NULL ELSE actives END,
                ingredient_source = CASE WHEN ingredient_source IN ('skinsafe_matched', 'pending_tier7_match') THEN NULL ELSE ingredient_source END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (cross_status, cross_score, pid))
            conn.commit()
            rejected += 1
            logger.info(f"[{idx}/{len(queue)}] [JEV REJECTED] '{title[:35]}' != '{cand_name[:35]}' (Quarantined)")
        else:
            # 3. Jev rate-limited (HTTP 429) or unavailable -> Mark as jev_rate_limited and queue for dedicated Gemini worker
            rate_limited += 1
            cur.execute("""
            UPDATE products
            SET gate4_jev_status = 'jev_rate_limited',
                gate4_gemini_status = COALESCE(gate4_gemini_status, 'pending_gemini'),
                gate4_cross_status = ?, inci_cross_score = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (cross_status, cross_score, pid))
            conn.commit()
            logger.warning(f"[{idx}/{len(queue)}] [JEV RATE LIMITED / SKIPPED] '{title[:35]}' handed off to Gemini queue (pending_gemini). (Cross: {cross_score:.2f})")

        # Pacing between Jev requests
        if getattr(cfg, "JEV_REQUEST_INTERVAL_SECONDS", 2.0) > 0:
            time.sleep(cfg.JEV_REQUEST_INTERVAL_SECONDS)

    conn.close()
    logger.info("=== Gate 4 Jev Worker Run Complete ===")
    logger.info(f"Processed: {len(queue)} | Approved: {approved} | Rejected: {rejected} | Rate Limited: {rate_limited}")

    return {
        "processed": len(queue),
        "approved": approved,
        "rejected": rejected,
        "rate_limited": rate_limited
    }

if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description="Run asynchronous Gate 4 Jev verification queue")
    parser.add_argument("--limit", type=int, default=cfg.JEV_QUEUE_BATCH_SIZE, help=f"Number of pending products to verify (default: {cfg.JEV_QUEUE_BATCH_SIZE})")
    args = parser.parse_args()

    run_jev_verification_queue(limit=args.limit)
