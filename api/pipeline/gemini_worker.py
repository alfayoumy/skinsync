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
from typing import List, Dict, Any, Optional, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.inci_matcher import verify_batch_with_gemini, score_cross_encoder
from pipeline.identity_gates import validate_identity_gates
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

from pipeline.logging_config import setup_logging, get_logger
logger = get_logger("skincare_pipeline.gemini_worker")

from pipeline.formulation_service import FormulationRepository, FormulationCandidate
formulation_repo = FormulationRepository()

def chunk_list(lst: List[Any], chunk_size: int) -> List[List[Any]]:
    """Splits a list into chunks of at most chunk_size items."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def run_gemini_verification_queue(
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
    all_pending: bool = False,
    stop_event: Optional[Any] = None,
    pause_event: Optional[Any] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    log_callback: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Pulls products queued for Gemini semantic evaluation and processes them in batches.
    Supports lifecycle events (pause, stop) and real-time telemetry streaming.
    
    Args:
        limit: Total maximum products to process in this worker run (default: cfg.GEMINI_QUEUE_BATCH_SIZE).
        batch_size: Number of product pairs bundled into a single Gemini prompt (default: cfg.GEMINI_BATCH_SIZE).
        all_pending: If True, evaluates any product with missing Gemini status, not just pending_gemini / jev_rate_limited.
        stop_event: Optional threading.Event to gracefully stop processing.
        pause_event: Optional threading.Event to pause processing.
        progress_callback: Optional callback for status and progress updates.
        log_callback: Optional callback for live log lines.
    """
    def emit_log(msg: str, level: str = "info"):
        if level == "warning":
            logger.warning(msg)
        elif level == "error":
            logger.error(msg)
        else:
            logger.info(msg)
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    if limit is None:
        limit = getattr(cfg, "GEMINI_QUEUE_BATCH_SIZE", 50)
    # One retailer/reference identity pair per physical provider request.
    batch_size = 1

    conn = get_connection()
    cur = conn.cursor()

    if all_pending:
        query = """
        SELECT id, title, brand, inci_matched_name, inci_hybrid_score, ingredients_raw, canonical_product_id
        FROM products
        WHERE inci_matched_name IS NOT NULL
          AND (gate4_gemini_status IS NULL OR gate4_gemini_status = 'pending_gemini')
          AND (gate4_jev_status IS NULL OR gate4_jev_status != 'jev_approved')
          AND (ingredients_raw IS NULL OR LENGTH(TRIM(ingredients_raw)) < 15 OR ingredient_source IN ('inci_matched', 'pending_tier7_match') OR ingredient_source IS NULL)
        ORDER BY id ASC
        LIMIT ?;
        """
    else:
        query = """
        SELECT id, title, brand, inci_matched_name, inci_hybrid_score, ingredients_raw, canonical_product_id
        FROM products
        WHERE inci_matched_name IS NOT NULL
          AND (
            gate4_gemini_status = 'pending_gemini'
            OR (gate4_jev_status = 'jev_rate_limited' AND (gate4_gemini_status IS NULL OR gate4_gemini_status = 'pending_gemini'))
          )
          AND (ingredients_raw IS NULL OR LENGTH(TRIM(ingredients_raw)) < 15 OR ingredient_source IN ('inci_matched', 'pending_tier7_match') OR ingredient_source IS NULL)
        ORDER BY id ASC
        LIMIT ?;
        """

    try:
        cur.execute(query, (limit,))
        queue = [dict(r) for r in cur.fetchall()]

        emit_log(f"=== Starting Gate 4 Gemini Batch Verification Worker ===")
        emit_log(f"Found {len(queue)} products queued for Gemini evaluation (Batch Size: {batch_size}, Limit: {limit}).")

        if not queue:
            return {"processed": 0, "approved": 0, "rejected": 0, "rate_limited": 0, "api_calls": 0, "total_queued": 0}

        batches = chunk_list(queue, batch_size)
        approved = 0
        rejected = 0
        rate_limited = 0
        api_calls = 0

        cross_threshold = getattr(cfg, "CROSS_ENCODER_CONFIDENCE_THRESHOLD", 0.85)
        gemini_threshold = getattr(cfg, "GEMINI_CONFIDENCE_THRESHOLD", 0.70)

        for b_idx, batch in enumerate(batches, 1):
            if stop_event and stop_event.is_set():
                emit_log(f"Gemini worker execution stopped by user request before batch {b_idx}/{len(batches)}.")
                break

            while pause_event and pause_event.is_set():
                if stop_event and stop_event.is_set():
                    break
                if progress_callback:
                    progress_callback({
                        "status": "paused",
                        "current_product": f"[PAUSED] Batch {b_idx}/{len(batches)}",
                        "processed": approved + rejected + rate_limited,
                        "approved": approved,
                        "rejected": rejected,
                        "rate_limited": rate_limited,
                        "api_calls": api_calls,
                        "total_items": len(queue),
                        "total_batches": len(batches),
                        "current_batch": b_idx
                    })
                time.sleep(0.5)

            if stop_event and stop_event.is_set():
                break

            first_title = batch[0]["title"][:30] if batch else ""
            if progress_callback:
                progress_callback({
                    "status": "running",
                    "current_product": f"Batch {b_idx}/{len(batches)} ({len(batch)} items: '{first_title}...')",
                    "processed": approved + rejected + rate_limited,
                    "approved": approved,
                    "rejected": rejected,
                    "rate_limited": rate_limited,
                    "api_calls": api_calls,
                    "total_items": len(queue),
                    "total_batches": len(batches),
                    "current_batch": b_idx
                })

            emit_log(f"--- Processing Batch {b_idx}/{len(batches)} ({len(batch)} items) ---")

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

            # 2. Exactly one two-product comparison per provider request.
            evaluations = verify_batch_with_gemini(batch_items)
            api_calls += 1

            if evaluations is None:
                # API rate limit reached or network failure
                rate_limited += len(batch)
                emit_log(f"[Batch {b_idx}] Gemini API returned None (Rate limited or daily quota exhausted). Leaving {len(batch)} items in queue.", level="warning")
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
                    compatible, identity_reason = validate_identity_gates(title, cand_name)
                    if not compatible:
                        is_same, reason = False, identity_reason
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
                        canon_id = p.get("canonical_product_id") if isinstance(p, dict) else p.get("canonical_product_id")
                        from pipeline.inci_matcher import parse_and_normalize_ingredients
                        import json
                        parsed, acts = ([], [])
                        if ref_ings and len(ref_ings.strip()) >= 15:
                            parsed, acts = parse_and_normalize_ingredients(ref_ings)

                        cur.execute("""
                        UPDATE products
                        SET gate4_gemini_status = 'gemini_approved', inci_gemini_score = ?,
                            gate4_cross_status = ?, inci_cross_score = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                        """, (conf, c_status, c_score, pid))

                        # Publish authoritative formulation to canonical_products & inci_audit_evidence
                        if canon_id and ref_ings and len(ref_ings.strip()) >= 15:
                            cand_obj = FormulationCandidate(
                                canonical_product_id=canon_id,
                                source_name="gemini_verified_skinsafe",
                                raw_ingredients=ref_ings,
                                parsed_tokens=parsed,
                                actives=acts,
                                confidence_score=conf,
                                source_record_id=str(pid),
                                completeness="full",
                                validation_flags={"evaluator": "gemini", "reason": reason, "cross_score": c_score}
                            )
                            formulation_repo.publish_formulation(
                                candidate=cand_obj,
                                approval_reason=f"Approved by Gemini batch worker (conf: {conf:.2f}). {reason}",
                                force=False,
                                conn=conn
                            )

                        approved += 1
                        batch_approved += 1
                        emit_log(f"  [GEMINI BATCH APPROVED] ID {pid}: '{title[:32]}' == '{cand_name[:32]}' (Conf: {conf:.2f} | Reason: {reason[:40]})")
                    else:
                        cur.execute("""
                        UPDATE products
                        SET gate4_gemini_status = 'gemini_rejected', inci_gemini_score = ?,
                            gate4_cross_status = ?, inci_cross_score = ?,




                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                        """, (conf, c_status, c_score, pid))
                        rejected += 1
                        batch_rejected += 1
                        emit_log(f"  [GEMINI BATCH REJECTED] ID {pid}: '{title[:32]}' != '{cand_name[:32]}' (Conf: {conf:.2f} | Reason: {reason[:40]})")
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
                    emit_log(f"  [GEMINI BATCH OMITTED] ID {pid}: Item missing from LLM response. Kept in pending_gemini.", level="warning")

            conn.commit()
            emit_log(f"Batch {b_idx} committed: {batch_approved} approved, {batch_rejected} rejected.")

            if progress_callback:
                progress_callback({
                    "status": "running",
                    "current_product": f"Batch {b_idx}/{len(batches)} finished",
                    "processed": approved + rejected + rate_limited,
                    "approved": approved,
                    "rejected": rejected,
                    "rate_limited": rate_limited,
                    "api_calls": api_calls,
                    "total_items": len(queue),
                    "total_batches": len(batches),
                    "current_batch": b_idx
                })

        emit_log("=== Gate 4 Gemini Batch Worker Run Complete ===")
        emit_log(f"Processed: {approved + rejected + rate_limited} | Approved: {approved} | Rejected: {rejected} | Rate Limited: {rate_limited} | API Calls: {api_calls}")

        return {
            "processed": approved + rejected + rate_limited,
            "approved": approved,
            "rejected": rejected,
            "rate_limited": rate_limited,
            "api_calls": api_calls,
            "total_queued": len(queue)
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
