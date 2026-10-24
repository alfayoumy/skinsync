"""
Reconciles the skincare catalog using the authoritative 6-tier INCI Waterfall Pipeline.
Purges unvalidated langsearch and cluster_propagation collisions, re-evaluates all
pending products through strict vehicle/relevance gates, and enforces authentic formulations.
"""

import os
import sys
import json
import sqlite3
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.db_manager import get_connection, DB_PATH
from pipeline.waterfall_inci_resolver import run_waterfall_pipeline, sanitize_inci_list
from pipeline.inci_matcher import is_valid_inci_list, parse_and_normalize_ingredients

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reconcile_catalog")

def reconcile_catalog():
    conn = get_connection(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 60000;")
    cur = conn.cursor()

    try:
        # Step 1: Reset flawed langsearch, cluster_propagation, and pending_tier7_match entries
        logger.info("Step 1: Purging flawed langsearch and cluster entries for fresh waterfall...")
        cur.execute("""
        UPDATE products
        SET ingredients_raw = NULL,
            ingredients_parsed = NULL,
            actives = NULL,
            ingredient_source = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE is_skincare = 1 AND ingredient_source IN ('langsearch_web_search', 'cluster_propagation', 'pending_tier7_match');
        """)
        purged_count = cur.rowcount
        conn.commit()
        logger.info(f"Purged {purged_count} products for strict waterfall re-evaluation.")

        # Step 2: Execute Authoritative Waterfall Pipeline across catalog
        logger.info("Step 2: Executing Authoritative Waterfall Pipeline...")
        t0 = time.time()
        stats = run_waterfall_pipeline(
            conn=conn,
            revalidate_all=False,
            langsearch_enabled=True,
            dry_run=False,
            max_workers=6
        )
        elapsed = time.time() - t0
        logger.info(f"Waterfall pipeline finished in {elapsed:.1f}s.")
        logger.info(f"Waterfall stats: {stats}")

        # Step 3: Ensure remaining empty products without Tier 7 queue are cleanly marked NULL
        cur.execute("""
        UPDATE products
        SET ingredient_source = NULL
        WHERE is_skincare = 1 AND (ingredients_raw IS NULL OR TRIM(ingredients_raw) = '')
          AND ingredient_source != 'pending_tier7_match';
        """)
        conn.commit()

        # Step 4: Final Catalog Distribution
        cur.execute("""
        SELECT COALESCE(ingredient_source, 'NULL') as src, COUNT(*)
        FROM products
        WHERE is_skincare = 1
        GROUP BY src
        ORDER BY COUNT(*) DESC;
        """)
        dist = cur.fetchall()
        logger.info("=" * 60)
        logger.info("FINAL AUTHORITATIVE CATALOG DISTRIBUTION (is_skincare = 1):")
        for src, cnt in dist:
            logger.info(f"  {src:<25}: {cnt}")
        logger.info("=" * 60)

    finally:
        conn.close()

if __name__ == "__main__":
    reconcile_catalog()
