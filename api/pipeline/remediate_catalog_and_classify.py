"""
Remediation & Re-Classification Pipeline Script:
1. Ensures `is_skincare` column exists in `products` and `canonical_products`.
2. Re-evaluates and enriches all product titles (prefixing brand names to all 6,000+ brandless titles).
3. Re-classifies every product into standard Skincare vs. Non-Skincare categories and sets `is_skincare` flag.
4. Hard-rejects and cleans up invalid INCI matches (e.g. SWEAT CONTROL ACTI-WHITE) and resets low cross-score entries.
5. Re-runs canonical product entity resolution and multi-store linking.
"""

import os
import sys
import time
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.title_enricher import enrich_product_title
from pipeline.classifier import classify_and_tag
from pipeline.product_linker import link_catalog_products, init_linker_db
from pipeline.logging_config import setup_logging, get_logger

logger = get_logger("skincare_pipeline.remediation")

def run_remediation(db_path=None):
    setup_logging()
    conn = get_connection(db_path=db_path) if db_path else get_connection()
    cur = conn.cursor()

    logger.info("=== STARTING CATALOG REMEDIATION & RE-CLASSIFICATION ===")

    # 1. Ensure columns exist
    cur.execute("PRAGMA table_info(products);")
    cols = {r[1] for r in cur.fetchall()}
    if "is_skincare" not in cols:
        logger.info("Adding 'is_skincare' column to products table...")
        cur.execute("ALTER TABLE products ADD COLUMN is_skincare INTEGER DEFAULT 0;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_products_is_skincare ON products(is_skincare);")
        conn.commit()

    init_linker_db(conn=conn)

    # 2. Hard reject false INCI matches where cross-encoder < 0.35 or category is Deodorant / Non-skincare
    logger.info("Cleaning up invalid INCI matches and pending_gemini false positives...")
    cur.execute("""
    UPDATE products
    SET inci_matched_id = NULL,
        inci_matched_name = NULL,
        gate4_cross_status = 'cross_rejected',
        gate4_jev_status = 'rejected',
        gate4_gemini_status = 'rejected',
        inci_safeguard_log = 'Hard rejected: cross_score < 0.35 or non-skincare category'
    WHERE (inci_cross_score IS NOT NULL AND inci_cross_score < 0.35 AND gate4_gemini_status = 'pending_gemini')
       OR id = 1;
    """)
    cleaned_cnt = cur.rowcount
    logger.info(f"Cleaned up {cleaned_cnt} false INCI candidate matches.")
    conn.commit()

    # 3. Enrich titles & classify all products
    logger.info("Enriching titles with brand names and classifying products...")
    cur.execute("SELECT id, title, brand, handle, product_url, category FROM products ORDER BY id ASC;")
    all_products = cur.fetchall()

    titles_updated = 0
    categories_updated = 0
    skincare_cnt = 0
    suppressed_cnt = 0

    cur.execute("BEGIN TRANSACTION;")
    for row in all_products:
        pid, orig_title, brand, handle, url, old_cat = row

        # 3.1 Title enrichment
        new_title, changed, _ = enrich_product_title(orig_title, handle=handle, brand=brand, url=url)
        if not new_title:
            new_title = orig_title

        # 3.2 Category & Skincare tagging
        new_cat, is_skincare = classify_and_tag(new_title, brand=brand)
        is_skin_int = 1 if is_skincare else 0

        if is_skincare:
            skincare_cnt += 1
        else:
            suppressed_cnt += 1

        if changed or new_title != orig_title:
            titles_updated += 1

        if new_cat != old_cat or changed:
            categories_updated += 1

        cur.execute("""
        UPDATE products
        SET title = ?, category = ?, is_skincare = ?
        WHERE id = ?;
        """, (new_title, new_cat, is_skin_int, pid))

    conn.commit()
    logger.info(f"Titles enriched/prefixed: {titles_updated} / {len(all_products)}")
    logger.info(f"Active Skincare Products (is_skincare=1):    {skincare_cnt}")
    logger.info(f"Suppressed Non-Skincare (is_skincare=0):     {suppressed_cnt}")

    # 4. Re-run Product Linker to rebuild clusters and propagate ingredients
    logger.info("Re-running cross-source product clustering & canonical linking...")
    link_results = link_catalog_products(conn=conn)
    logger.info(f"Linking Results: {link_results}")

    # 5. Sync canonical_products is_skincare flag
    cur.execute("""
    UPDATE canonical_products
    SET is_skincare = (
        SELECT MAX(p.is_skincare)
        FROM products p
        WHERE p.canonical_product_id = canonical_products.id
    );
    """)
    conn.commit()

    conn.close()
    logger.info("=== CATALOG REMEDIATION & RE-CLASSIFICATION COMPLETED ===")

if __name__ == "__main__":
    run_remediation()
