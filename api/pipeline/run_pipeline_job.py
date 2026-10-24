"""
End-to-End Production Pipeline Orchestrator:
1. Multi-source scraping across all 6 stores with HTML ingredient extraction and title enrichment
2. Global title enrichment canonicalization to guarantee no truncated titles
3. High-throughput local INCI formulation matching with FastEmbed ONNX vectors and cross-encoder
4. Auto-filling missing ingredients from matched INCI knowledge base
5. Async Gemini Flash-Lite batch verification queue for ambiguity candidates (paced for 15 RPM / 1,500 RPD)
6. Real-time catalog cache reload and telemetry reporting
"""

import os
import sys
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.db_manager import get_connection
from pipeline.harvester import sync_all_stores, STORES
from pipeline.daily_sync import run_daily_sync
from pipeline.title_enricher import run_title_enrichment
from pipeline.inci_matcher import (
    match_with_inci_db, parse_and_normalize_ingredients
)
from pipeline.gemini_worker import run_gemini_verification_queue
from pipeline.task_tracker import (
    create_task, complete_task, fail_task, update_task_progress, log_task_event
)
from pipeline.logging_config import setup_logging, get_logger
from services.catalog_service import load_catalog_from_db

setup_logging()
logger = get_logger("skincare_pipeline.orchestrator")


def run_full_pipeline(
    max_pages_per_store: int = 35,
    match_limit: int = 2000,
    gemini_limit: int = 50,
    gemini_batch_size: int = 10,
    skip_scraping: bool = False
):
    master_task_id = create_task(
        task_type="full_pipeline_run",
        target_source="all_stores",
        params={
            "max_pages_per_store": max_pages_per_store,
            "match_limit": match_limit,
            "gemini_limit": gemini_limit,
            "gemini_batch_size": gemini_batch_size,
            "skip_scraping": skip_scraping
        },
        auto_start=True
    )
    start_time = datetime.now()

    logger.info("=================================================================")
    logger.info("STARTING FULL END-TO-END DATA PIPELINE & ENRICHMENT RUN")
    logger.info(f"Master Task ID: {master_task_id}")
    logger.info("=================================================================")

    conn = get_connection()
    try:
        total_fetched = 0
        total_added = 0
        total_updated = 0

        # =====================================================================
        # PHASE 1: SCRAPE ALL 6 STORES
        # =====================================================================
        if not skip_scraping:
            logger.info("\n>>> PHASE 1: Scraping all 6 Egyptian retail sources...")
            update_task_progress(master_task_id, current_stage="scraping_all_stores", conn=conn)

            sync_res = sync_all_stores(max_pages_per_store=max_pages_per_store, task_id=master_task_id)
            total_fetched = sum(r.get("fetched", 0) for r in sync_res)
            total_added = sum(r.get("added", 0) for r in sync_res)
            total_updated = sum(r.get("updated", 0) for r in sync_res)

            logger.info(f"Phase 1 Complete: Fetched {total_fetched}, Added {total_added}, Updated {total_updated}")
            for r in sync_res:
                logger.info(f"  - {r.get('store')}: Fetched {r.get('fetched')}, Added {r.get('added')}, Updated {r.get('updated')}")
        else:
            logger.info("\n>>> PHASE 1: Store scraping skipped as requested (--skip-scraping).")

        # =====================================================================
        # PHASE 2: TITLE ENRICHMENT & CANONICALIZATION
        # =====================================================================
        logger.info("\n>>> PHASE 2: Running Title Enrichment & Truncation Remediation...")
        update_task_progress(master_task_id, current_stage="enriching_titles", conn=conn)

        title_res = run_title_enrichment(conn=conn, dry_run=False, task_id=master_task_id)
        logger.info(f"Phase 2 Complete: Enriched {title_res.get('enriched_count')}/{title_res.get('total_products')} titles.")

        # =====================================================================
        # PHASE 3: HIGH-THROUGHPUT LOCAL INCI MATCHING & INGREDIENT FILL
        # =====================================================================
        logger.info("\n>>> PHASE 3: Running High-Throughput INCI Matching & Ingredient Fill...")
        update_task_progress(master_task_id, current_stage="matching_inci_records", conn=conn)

        cur = conn.cursor()
        cur.execute("""
        SELECT id, brand, title, ingredients_raw, ingredient_source, store_id, canonical_product_id
        FROM products
        WHERE (inci_matched_id IS NULL AND (gate4_cross_status IS NULL OR gate4_cross_status = 'pending'))
           OR gate4_gemini_status = 'pending_gemini'
        LIMIT ?;
        """, (match_limit,))
        unmatched_rows = cur.fetchall()
        logger.info(f"Found {len(unmatched_rows)} products requiring INCI matching (Batch limit: {match_limit}).")

        matched_count = 0
        filled_ingredients_count = 0
        pending_gemini_count = 0

        for idx, row in enumerate(unmatched_rows, 1):
            row_dict = dict(row)
            pid = row_dict["id"]
            brand = row_dict["brand"]
            title = row_dict["title"]
            curr_raw = row_dict.get("ingredients_raw")

            AUTHENTIC_SOURCE_TYPES = {
                "store_tab", "store_page_tab", "historical_store_scrape",
                "current_store_scrape", "store_scrape", "store_description", "v10_curated"
            }
            has_source_ings = bool(
                curr_raw and len(curr_raw.strip()) >= 15 and
                row_dict.get("ingredient_source") in AUTHENTIC_SOURCE_TYPES
            )
            m_res = match_with_inci_db(
                brand=brand,
                title=title,
                conn=conn,
                allow_synchronous_jev=False,
                scoring_mode="local",
                allow_obf=False,
                has_source_ingredients=has_source_ings
            )

            if m_res and m_res.get("matched_id"):
                matched_count += 1
                matched_name = m_res.get("matched_name")
                v_score = m_res.get("vector_score")
                t_score = m_res.get("token_score")
                h_score = m_res.get("hybrid_score")
                c_score = m_res.get("inci_cross_score")
                gemini_status = m_res.get("gate4_gemini_status")
                c_status = m_res.get("gate4_cross_status")

                if gemini_status == "pending_gemini":
                    pending_gemini_count += 1

                update_sql = """
                UPDATE products
                SET inci_matched_id = ?,
                    inci_matched_name = ?,
                    inci_vector_score = ?,
                    inci_token_score = ?,
                    inci_hybrid_score = ?,
                    inci_cross_score = ?,
                    gate1_quality_status = ?,
                    gate2_safeguard_status = ?,
                    gate3_active_status = ?,
                    gate4_cross_status = ?,
                    gate4_gemini_status = ?,
                    inci_safeguard_log = ?,
                    updated_at = CURRENT_TIMESTAMP
                """
                params = [
                    m_res.get("matched_id"),
                    matched_name,
                    v_score,
                    t_score,
                    h_score,
                    c_score,
                    m_res.get("gate1_quality_status"),
                    m_res.get("gate2_safeguard_status"),
                    m_res.get("gate3_active_status"),
                    c_status,
                    gemini_status,
                    m_res.get("safeguard_log")
                ]

                # Only fill ingredients immediately if auto-approved locally (not ambiguous / pending gemini)
                # and product doesn't possess genuine store ground truth (or currently only has fallback tokens)
                ref_ings = m_res.get("ingredients")
                if gemini_status != "pending_gemini" and ref_ings and len(ref_ings.strip()) >= 15:
                    parsed, acts = parse_and_normalize_ingredients(ref_ings)


                    canon_id = row_dict.get("canonical_product_id")
                    if canon_id:
                        from pipeline.formulation_service import FormulationRepository, FormulationCandidate, match_is_verified
                        from pipeline.inci_tokenizer import tokenize_inci_string
                        tok_res = tokenize_inci_string(ref_ings, product_title=title)
                        cand = FormulationCandidate(
                            canonical_product_id=canon_id,
                            source_name="skinsafe_matched",
                            raw_ingredients=ref_ings,
                            parsed_tokens=tok_res.tokens,
                            actives=tok_res.declared_actives,
                            confidence_score=float(h_score or 0.80),
                            source_record_id=str(pid),
                            completeness=tok_res.completeness if match_is_verified(m_res) else "unverified",
                            validation_flags={"is_truncated": tok_res.is_truncated},
                            inci_matched_id=m_res.get("matched_id"),
                            inci_matched_name=matched_name
                        )
                        repo = FormulationRepository()
                        if tok_res.completeness == "full":
                            if repo.publish_formulation(candidate=cand, approval_reason="Matched in Phase 3 run_pipeline_job", force=False, conn=conn):
                                filled_ingredients_count += 1
                        else:
                            repo.record_candidate_evidence(candidate=cand, decision="candidate_evidence", discrepancy_notes="Candidate from Phase 3 INCI match logged as audit evidence.", conn=conn)

                update_sql += " WHERE id = ?;"
                params.append(pid)
                cur.execute(update_sql, params)
            else:
                cur.execute("""
                UPDATE products
                SET gate4_cross_status = 'no_inci_candidate',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (pid,))

            if idx % 100 == 0 or idx == len(unmatched_rows):
                conn.commit()
                logger.info(f"  [INCI Matching] Processed {idx}/{len(unmatched_rows)} | Matched: {matched_count} | Queued Gemini: {pending_gemini_count} | Filled Ings: {filled_ingredients_count}")

        conn.commit()
        logger.info(f"Phase 3 Complete: Processed {len(unmatched_rows)} products | Matched: {matched_count} | Queued for Gemini: {pending_gemini_count} | Filled Ingredients: {filled_ingredients_count}")

        # =====================================================================
        # PHASE 4: ASYNC BATCH GEMINI VERIFICATION QUEUE
        # =====================================================================
        logger.info("\n>>> PHASE 4: Executing Async Batch Gemini Flash-Lite Verification Queue...")
        update_task_progress(master_task_id, current_stage="evaluating_gemini_batches", conn=conn)

        gemini_res = run_gemini_verification_queue(
            limit=gemini_limit,
            batch_size=gemini_batch_size,
            all_pending=False
        )
        logger.info(f"Phase 4 Complete: Gemini evaluated {gemini_res.get('processed', 0)} items | Approved: {gemini_res.get('approved', 0)} | Rejected: {gemini_res.get('rejected', 0)} | Batches: {gemini_res.get('api_calls', 0)}")

        # =====================================================================
        # PHASE 5: CROSS-SOURCE PRODUCT LINKING & INGREDIENT PROPAGATION
        # =====================================================================
        logger.info("\n>>> PHASE 5: Linking cross-source products & propagating ingredients...")
        update_task_progress(master_task_id, current_stage="linking_canonical_products", conn=conn)

        from pipeline.product_linker import link_catalog_products
        link_res = link_catalog_products(conn=conn, task_id=master_task_id)
        logger.info(f"Phase 5 Complete: {link_res.get('multi_store_clusters', 0)} multi-store clusters linked, {link_res.get('ingredients_propagated', 0)} ingredient lists propagated across stores.")

        # =====================================================================
        # PHASE 6: REAL-TIME CATALOG CACHE RELOAD & METRICS
        # =====================================================================
        logger.info("\n>>> PHASE 6: Reloading in-memory catalog cache...")
        load_catalog_from_db()

        # Telemetry counts
        cur.execute("SELECT COUNT(*) FROM products;")
        final_total_prods = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM canonical_products;")
        final_canonical_prods = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM canonical_products WHERE store_count > 1;")
        final_multi_store = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM products WHERE ingredients_raw IS NOT NULL AND LENGTH(ingredients_raw) >= 15;")
        final_with_ings = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM products WHERE inci_matched_id IS NOT NULL;")
        final_matched_inci = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM products WHERE gate4_gemini_status = 'gemini_approved';")
        final_gemini_approved = cur.fetchone()[0]

        cur.execute("SELECT store_id, COUNT(*) FROM products GROUP BY store_id;")
        store_counts = dict(cur.fetchall())

        duration = (datetime.now() - start_time).total_seconds()
        logger.info("=================================================================")
        logger.info("END-TO-END PIPELINE RUN COMPLETED SUCCESSFULLY!")
        logger.info(f"Total Duration: {duration:.1f} seconds")
        logger.info(f"Total Catalog Products: {final_total_prods}")
        logger.info(f"Products with Ingredient Lists: {final_with_ings} ({final_with_ings/final_total_prods*100:.1f}%)")
        logger.info(f"Products with INCI Formulation Matches: {final_matched_inci} ({final_matched_inci/final_total_prods*100:.1f}%)")
        logger.info(f"Gemini AI Approved Matches: {final_gemini_approved}")
        logger.info(f"Store Distribution: {store_counts}")
        logger.info("=================================================================")

        complete_task(
            task_id=master_task_id,
            metadata={
                "duration_seconds": round(duration, 1),
                "total_products": final_total_prods,
                "with_ingredients": final_with_ings,
                "matched_inci": final_matched_inci,
                "gemini_approved": final_gemini_approved,
                "store_counts": store_counts,
                "sync_summary": {
                    "fetched": total_fetched,
                    "added": total_added,
                    "updated": total_updated
                }
            },
            conn=conn
        )

        return {
            "status": "success",
            "task_id": master_task_id,
            "duration_seconds": round(duration, 1),
            "total_products": final_total_prods,
            "products_with_ingredients": final_with_ings,
            "products_matched_inci": final_matched_inci,
            "gemini_approved": final_gemini_approved,
            "store_distribution": store_counts
        }

    except Exception as e:
        logger.error(f"Pipeline orchestrator failed: {e}")
        fail_task(master_task_id, str(e), conn=conn)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SkinSync Complete Pipeline Orchestrator")
    parser.add_argument("--pages", type=int, default=35, help="Max pages to crawl per store")
    parser.add_argument("--match-limit", type=int, default=2000, help="Max products to match against INCI DB")
    parser.add_argument("--gemini-limit", type=int, default=50, help="Max products to evaluate with Gemini")
    parser.add_argument("--gemini-batch-size", type=int, default=10, help="Batch size for Gemini verification")
    parser.add_argument("--skip-scraping", action="store_true", help="Skip crawling stores and run matching/Gemini")
    args = parser.parse_args()

    run_full_pipeline(
        max_pages_per_store=args.pages,
        match_limit=args.match_limit,
        gemini_limit=args.gemini_limit,
        gemini_batch_size=args.gemini_batch_size,
        skip_scraping=args.skip_scraping
    )
