"""
Cross-Source Product Entity Resolution & Canonical Linking Engine:
1. Clusters identical products across different stores (Source Beauty, Bloom Pharmacy, Feel22, Dr. Dawood, Bashrety, Raw African).
2. Manages the `canonical_products` table and links store listings via `products.canonical_product_id`.
3. Propagates ingredient lists from stores with rich HTML descriptions to stores lacking ingredients.
4. Aggregates multi-source pricing for real-time price comparison and arbitrage tracking.
"""

import os
import sys
import re
import json
import sqlite3
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.safeguards import CosmeticCompatibilityValidator
from pipeline.task_tracker import create_subtask, complete_subtask, log_task_event
from pipeline.logging_config import setup_logging, get_logger

logger = get_logger("skincare_pipeline.linker")

VOLUME_PATTERN = re.compile(r'\b(\d+(?:\.\d+)?)\s*(ml|g|gm|oz|fl\.?\s*oz|l|liter|litres?|kg)\b', re.IGNORECASE)


def init_linker_db(conn=None) -> None:
    """Ensures canonical_products table and products.canonical_product_id column exist."""
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS canonical_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_title TEXT NOT NULL,
        brand TEXT NOT NULL,
        category TEXT NOT NULL,
        normalized_key TEXT NOT NULL UNIQUE,
        ingredients_raw TEXT,
        ingredients_parsed TEXT,
        actives TEXT,
        ingredient_source TEXT,
        inci_matched_id INTEGER,
        min_price REAL DEFAULT 0.0,
        max_price REAL DEFAULT 0.0,
        store_count INTEGER DEFAULT 1,
        primary_image_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_canonical_normalized ON canonical_products(normalized_key);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canonical_brand ON canonical_products(brand);")

    # Add canonical_product_id column to products table if missing
    cur.execute("PRAGMA table_info(products);")
    existing_cols = {r[1] for r in cur.fetchall()}
    if "canonical_product_id" not in existing_cols:
        cur.execute("ALTER TABLE products ADD COLUMN canonical_product_id INTEGER REFERENCES canonical_products(id);")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_canonical ON products(canonical_product_id);")
    conn.commit()

    if close_on_exit:
        conn.close()


def extract_volume_and_normalize(title: str, brand: str) -> Tuple[str, str]:
    """
    Normalizes product title and extracts packaging volume for precise entity resolution.
    e.g. 'CeraVe Moisturizing Cream 50ml' -> ('moisturizing cream', '50ml')
    """
    clean_t = title.lower()
    b_low = (brand or "").lower().strip()

    # Extract volume
    vol_match = VOLUME_PATTERN.search(clean_t)
    if vol_match:
        qty = vol_match.group(1)
        unit = vol_match.group(2).lower()
        if unit in ('gm', 'g'):
            unit = 'g'
        elif unit in ('ml', 'l', 'liter', 'litres'):
            unit = 'ml'
        elif 'oz' in unit:
            unit = 'oz'
        vol_str = f"{qty}{unit}"
    else:
        vol_str = ""

    # Remove volume from title for base key
    clean_base = VOLUME_PATTERN.sub('', clean_t)

    # Strip brand prefix if present
    if b_low and clean_base.startswith(b_low):
        clean_base = clean_base[len(b_low):].strip()

    # Clean punctuation and extra spaces
    clean_base = re.sub(r'[^a-z0-9\s+]', ' ', clean_base)
    clean_base = re.sub(r'\s+', ' ', clean_base).strip()

    return clean_base, vol_str


def build_cluster_key(brand: str, title: str, inci_matched_id: Optional[int], gemini_status: Optional[str]) -> str:
    """
    Constructs a deterministic canonical cluster key.
    """
    norm_b = re.sub(r'[^a-z0-9]', '', (brand or "").lower())

    # Strategy 1: Shared verified INCI formulation
    if inci_matched_id and gemini_status in ('gemini_approved', None):
        return f"inci:{norm_b}:{inci_matched_id}"

    # Strategy 2: Clean base title + volume specification
    base_t, vol = extract_volume_and_normalize(title, brand)
    norm_t = re.sub(r'[^a-z0-9]', '', base_t)
    return f"title:{norm_b}:{norm_t}:{vol}"


def link_catalog_products(conn=None, task_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes cross-source product clustering, canonical link creation, and ingredient propagation.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    init_linker_db(conn=conn)
    cur = conn.cursor()

    logger.info("Starting cross-source product clustering and canonical entity linking...")

    # Fetch all catalog products
    cur.execute("""
    SELECT id, store_id, title, brand, category, price, is_available, image_url,
           ingredients_raw, ingredients_parsed, actives, ingredient_source,
           inci_matched_id, gate4_gemini_status
    FROM products
    ORDER BY id ASC;
    """)
    products = [dict(r) for r in cur.fetchall()]
    total_products = len(products)
    logger.info(f"Loaded {total_products} products for cross-store entity resolution.")

    # 1. Group products into canonical clusters
    clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in products:
        b = p["brand"] or "Generic"
        t = p["title"] or ""
        inci_id = p["inci_matched_id"]
        g_status = p["gate4_gemini_status"]

        key = build_cluster_key(b, t, inci_id, g_status)
        clusters[key].append(p)

    logger.info(f"Formed {len(clusters)} distinct canonical product clusters.")

    # 2. Process each cluster: assign/update canonical_product and propagate ingredients
    canonical_created = 0
    multi_store_clusters = 0
    ingredients_propagated = 0

    cur.execute("BEGIN TRANSACTION;")

    for key, items in clusters.items():
        is_multi_store = len(set(p["store_id"] for p in items)) > 1
        if is_multi_store:
            multi_store_clusters += 1

        # Select best canonical title (longest, most descriptive)
        best_title = max(items, key=lambda x: len(x.get("title") or ""))["title"]
        best_brand = items[0]["brand"] or "Generic"
        best_category = items[0]["category"] or "Skincare"

        # Find primary image
        primary_img = next((p["image_url"] for p in items if p["image_url"]), None)

        # Find highest quality ingredient list in the cluster
        best_ing_item = None
        for p in items:
            raw = p.get("ingredients_raw")
            if raw and len(raw.strip()) >= 15:
                if best_ing_item is None or len(raw) > len(best_ing_item["ingredients_raw"]):
                    best_ing_item = p

        best_raw = best_ing_item["ingredients_raw"] if best_ing_item else None
        best_parsed = best_ing_item["ingredients_parsed"] if best_ing_item else None
        best_acts = best_ing_item["actives"] if best_ing_item else None
        best_src = best_ing_item["ingredient_source"] if best_ing_item else None
        best_inci_id = next((p["inci_matched_id"] for p in items if p["inci_matched_id"]), None)

        # Price calculations (prefer available listings)
        avail_prices = [p["price"] for p in items if p["price"] and p["price"] > 0 and p["is_available"]]
        all_prices = [p["price"] for p in items if p["price"] and p["price"] > 0]
        prices_to_use = avail_prices if avail_prices else all_prices

        min_p = min(prices_to_use) if prices_to_use else 0.0
        max_p = max(prices_to_use) if prices_to_use else 0.0
        store_cnt = len(set(p["store_id"] for p in items))

        # Check if canonical_products already has this key
        cur.execute("SELECT id FROM canonical_products WHERE normalized_key = ?;", (key,))
        existing_row = cur.fetchone()

        if existing_row:
            canonical_id = existing_row[0]
            cur.execute("""
            UPDATE canonical_products
            SET canonical_title = ?, brand = ?, category = ?,
                ingredients_raw = COALESCE(?, ingredients_raw),
                ingredients_parsed = COALESCE(?, ingredients_parsed),
                actives = COALESCE(?, actives),
                ingredient_source = COALESCE(?, ingredient_source),
                inci_matched_id = COALESCE(?, inci_matched_id),
                min_price = ?, max_price = ?, store_count = ?,
                primary_image_url = COALESCE(?, primary_image_url),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (best_title, best_brand, best_category, best_raw, best_parsed, best_acts,
                  best_src, best_inci_id, min_p, max_p, store_cnt, primary_img, canonical_id))
        else:
            cur.execute("""
            INSERT INTO canonical_products (
                canonical_title, brand, category, normalized_key,
                ingredients_raw, ingredients_parsed, actives, ingredient_source,
                inci_matched_id, min_price, max_price, store_count, primary_image_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (best_title, best_brand, best_category, key, best_raw, best_parsed, best_acts,
                  best_src, best_inci_id, min_p, max_p, store_cnt, primary_img))
            canonical_id = cur.lastrowid
            canonical_created += 1

        # Link all products to canonical_id and propagate ingredients if missing
        for p in items:
            pid = p["id"]
            curr_raw = p.get("ingredients_raw")

            if (not curr_raw or len(curr_raw.strip()) < 15) and best_raw:
                cur.execute("""
                UPDATE products
                SET canonical_product_id = ?,
                    ingredients_raw = ?,
                    ingredients_parsed = ?,
                    actives = ?,
                    ingredient_source = 'cross_store_linked',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (canonical_id, best_raw, best_parsed, best_acts, pid))
                ingredients_propagated += 1
            else:
                cur.execute("""
                UPDATE products
                SET canonical_product_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (canonical_id, pid))

    conn.commit()

    logger.info("=================================================================")
    logger.info("CROSS-SOURCE PRODUCT LINKING COMPLETED")
    logger.info(f"Total Products Processed:       {total_products}")
    logger.info(f"Canonical Entities Managed:     {len(clusters)} (New: {canonical_created})")
    logger.info(f"Multi-Store Products:           {multi_store_clusters}")
    logger.info(f"Ingredients Propagated:         {ingredients_propagated} listings")
    logger.info("=================================================================")

    if task_id:
        log_task_event(
            task_id,
            f"Cross-source linking finished: {multi_store_clusters} multi-store clusters, {ingredients_propagated} ingredient lists propagated.",
            level="INFO",
            conn=conn
        )

    res = {
        "status": "success",
        "total_products": total_products,
        "canonical_entities": len(clusters),
        "newly_created_canonical": canonical_created,
        "multi_store_clusters": multi_store_clusters,
        "ingredients_propagated": ingredients_propagated
    }

    if close_on_exit:
        conn.close()

    return res


if __name__ == "__main__":
    setup_logging()
    link_catalog_products()
