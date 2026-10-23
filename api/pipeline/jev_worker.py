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
from pipeline.inci_matcher import verify_match_with_jev, verify_match_with_gemini, score_cross_encoder
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

logger = logging.getLogger("skincare_pipeline.jev_worker")

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
            UPDATE products
            SET gate4_jev_status = 'jev_approved', inci_jev_score = 0.95,
                gate4_cross_status = ?, inci_cross_score = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (cross_status, cross_score, pid))
            conn.commit()
            approved += 1
            logger.info(f"[{idx}/{len(queue)}] [JEV APPROVED] '{title[:35]}' == '{cand_name[:35]}' (Score: {h_score:.2f} | Cross: {cross_score:.2f})")
        elif verdict is False:
            cur.execute("""
            UPDATE products
            SET gate4_jev_status = 'jev_rejected', inci_jev_score = 0.15,
                gate4_cross_status = ?, inci_cross_score = ?,
                ingredients_raw = NULL, ingredients_parsed = NULL, actives = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (cross_status, cross_score, pid))
            conn.commit()
            rejected += 1
            logger.info(f"[{idx}/{len(queue)}] [JEV REJECTED] '{title[:35]}' != '{cand_name[:35]}' (Quarantined)")
        else:
            # 3. Fallback: Jev rate-limited (HTTP 429) or missing key -> Call Gemini 2.5 Flash-Lite
            gemini_res = verify_match_with_gemini(title, cand_name)
            if gemini_res is not None:
                g_ok, g_conf = gemini_res
                if g_ok:
                    cur.execute("""
                    UPDATE products
                    SET gate4_jev_status = 'jev_rate_limited',
                        gate4_gemini_status = 'gemini_approved', inci_gemini_score = ?,
                        gate4_cross_status = ?, inci_cross_score = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """, (g_conf, cross_status, cross_score, pid))
                    conn.commit()
                    approved += 1
                    logger.info(f"[{idx}/{len(queue)}] [GEMINI FALLBACK APPROVED] '{title[:35]}' == '{cand_name[:35]}' (Conf: {g_conf:.2f} | Cross: {cross_score:.2f})")
                else:
                    cur.execute("""
                    UPDATE products
                    SET gate4_jev_status = 'jev_rate_limited',
                        gate4_gemini_status = 'gemini_rejected', inci_gemini_score = ?,
                        gate4_cross_status = ?, inci_cross_score = ?,
                        ingredients_raw = NULL, ingredients_parsed = NULL, actives = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """, (g_conf, cross_status, cross_score, pid))
                    conn.commit()
                    rejected += 1
                    logger.info(f"[{idx}/{len(queue)}] [GEMINI FALLBACK REJECTED] '{title[:35]}' != '{cand_name[:35]}' (Quarantined)")
            else:
                rate_limited += 1
                cur.execute("""
                UPDATE products
                SET gate4_cross_status = ?, inci_cross_score = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (cross_status, cross_score, pid))
                conn.commit()
                logger.warning(f"[{idx}/{len(queue)}] [RATE LIMITED / SKIPPED] '{title[:35]}' remains in pending_jev (Cross-Encoder: {cross_score:.2f})")

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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Run asynchronous Gate 4 Jev verification queue")
    parser.add_argument("--limit", type=int, default=cfg.JEV_QUEUE_BATCH_SIZE, help=f"Number of pending products to verify (default: {cfg.JEV_QUEUE_BATCH_SIZE})")
    args = parser.parse_args()

    run_jev_verification_queue(limit=args.limit)
