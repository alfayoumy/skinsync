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
        is_skincare INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_canonical_normalized ON canonical_products(normalized_key);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canonical_brand ON canonical_products(brand);")

    # Add canonical_product_id and is_skincare columns to products table if missing
    cur.execute("PRAGMA table_info(products);")
    existing_cols = {r[1] for r in cur.fetchall()}
    if "canonical_product_id" not in existing_cols:
        cur.execute("ALTER TABLE products ADD COLUMN canonical_product_id INTEGER REFERENCES canonical_products(id);")
    if "is_skincare" not in existing_cols:
        cur.execute("ALTER TABLE products ADD COLUMN is_skincare INTEGER DEFAULT 0;")

    cur.execute("PRAGMA table_info(canonical_products);")
    existing_can_cols = {r[1] for r in cur.fetchall()}
    if "is_skincare" not in existing_can_cols:
        cur.execute("ALTER TABLE canonical_products ADD COLUMN is_skincare INTEGER DEFAULT 0;")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_canonical ON products(canonical_product_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_is_skincare ON products(is_skincare);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canonical_is_skincare ON canonical_products(is_skincare);")
    conn.commit()

    if close_on_exit:
        conn.close()


STOP_MARKETING_WORDS = {
    'daily', 'classic', 'essential', 'original', 'ultra', 'lightweight', 'rich',
    'soothing', 'nourishing', 'advanced', 'pure', 'intensive', 'gentle'
}

SKIN_TYPE_PATTERNS = [
    r'\bfor\s+(?:dry|very\s+dry|normal|oily|combination|sensitive)\s+to\s+(?:dry|very\s+dry|normal|oily|combination|sensitive)\s+skin\b',
    r'\bfor\s+(?:dry|very\s+dry|normal|oily|combination|sensitive)\s+skin\b',
    r'\b(?:dry|very\s+dry|normal|oily|combination|sensitive)\s+to\s+(?:dry|very\s+dry|normal|oily|combination|sensitive)\s+skin\b',
    r'\b(?:dry|very\s+dry|normal|oily|combination|sensitive)\s+skin\b',
    r'\b(?:face\s+and\s+body|face\s+&\s+body|visage\s+et\s+corps)\b',
    r'\b(?:normal\s+to\s+dry|dry\s+to\s+very\s+dry)\b',
    r'\b(?:with\s+sunscreen)\b',
]


def extract_volume_and_normalize(title: str, brand: str) -> Tuple[str, str]:
    """
    Normalizes product title and extracts packaging volume for precise entity resolution.
    Handles UK/US spellings, filler marketing adjectives, and skin-type claims.
    e.g. 'CeraVe Moisturising Lotion For Dry To Very Dry Skin 236Ml.' -> ('lotion:moisturizing', '236ml')
    """
    t = title.lower()
    b_low = (brand or "").lower().strip()

    # 1. Extract volume
    vol_match = VOLUME_PATTERN.search(t)
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
        t = t[:vol_match.start()] + ' ' + t[vol_match.end():]
    else:
        vol_str = ""

    # 2. Normalize UK to US spellings
    t = re.sub(r'\bmoisturising\b', 'moisturizing', t)
    t = re.sub(r'\bcleansing\b', 'cleanse', t)
    t = re.sub(r'\bcreme\b', 'cream', t)
    t = re.sub(r'\bcolour\b', 'color', t)

    # 3. Strip skin type claims & formatting
    for pat in SKIN_TYPE_PATTERNS:
        t = re.sub(pat, ' ', t)

    # 4. Strip brand name prefix/suffix
    if b_low:
        t = re.sub(r'\b' + re.escape(b_low) + r'\b', ' ', t)

    words = [re.sub(r'[^a-z0-9]', '', w) for w in t.split()]
    has_timing = any(w in ('pm', 'am') for w in words)
    has_vehicle = any(w in ('lotion', 'cream', 'gel') for w in words)

    meaningful = []
    for w in words:
        if not w or len(w) <= 1 or w in STOP_MARKETING_WORDS:
            continue
        if w == 'moisturizing' and has_timing and has_vehicle:
            continue
        meaningful.append(w)

    sorted_tokens = sorted(meaningful)
    base_key = ':'.join(sorted_tokens) if sorted_tokens else re.sub(r'[^a-z0-9]', '', t)
    return base_key, vol_str


def build_cluster_key(brand: str, title: str, inci_matched_id: Optional[int], gemini_status: Optional[str]) -> str:
    """
    Constructs a deterministic canonical cluster key.
    """
    norm_b = re.sub(r'[^a-z0-9]', '', (brand or "").lower())

    # Strategy 1: Shared verified INCI formulation
    if inci_matched_id and gemini_status in ('gemini_approved', None):
        return f"inci:{norm_b}:{inci_matched_id}"

    # Strategy 2: Sorted base title tokens + volume specification
    base_t, vol = extract_volume_and_normalize(title, brand)
    norm_t = re.sub(r'[^a-z0-9:]', '', base_t)
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
           inci_matched_id, gate4_gemini_status, is_skincare
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
        is_skin_cluster = 1 if any(p.get("is_skincare") == 1 for p in items) else 0

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
                is_skincare = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (best_title, best_brand, best_category, best_raw, best_parsed, best_acts,
                  best_src, best_inci_id, min_p, max_p, store_cnt, primary_img, is_skin_cluster, canonical_id))
        else:
            cur.execute("""
            INSERT INTO canonical_products (
                canonical_title, brand, category, normalized_key,
                ingredients_raw, ingredients_parsed, actives, ingredient_source,
                inci_matched_id, min_price, max_price, store_count, primary_image_url, is_skincare
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (best_title, best_brand, best_category, key, best_raw, best_parsed, best_acts,
                  best_src, best_inci_id, min_p, max_p, store_cnt, primary_img, is_skin_cluster))
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

    # Purge any deprecated canonical clusters not referenced by any products
    cur.execute("""
    DELETE FROM canonical_products
    WHERE id NOT IN (SELECT DISTINCT canonical_product_id FROM products WHERE canonical_product_id IS NOT NULL);
    """)

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
