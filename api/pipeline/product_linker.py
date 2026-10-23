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
import unicodedata
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

VOLUME_EQUIVALENCES = {
    # 15ml / 0.5oz
    '14ml': '15ml', '15g': '15ml', '15gm': '15ml', '0.5oz': '15ml',
    # 30ml / 1oz
    '28g': '30ml', '28gm': '30ml', '30g': '30ml', '30gm': '30ml', '1oz': '30ml',
    # 50ml / 1.7oz
    '40g': '40ml', '50g': '50ml', '50gm': '50ml', '1.7oz': '50ml',
    # 52ml / 52g (CeraVe AM/PM)
    '52ml': '52ml', '52g': '52ml', '52gm': '52ml',
    # 100ml / 3.4oz
    '100g': '100ml', '100gm': '100ml', '3.4oz': '100ml',
    # 150ml / 5oz
    '150g': '150ml', '150gm': '150ml', '5oz': '150ml',
    # 177ml / 6oz / 177g (CeraVe)
    '177g': '177ml', '177gm': '177ml', '6oz': '177ml',
    # 200ml / 6.7oz
    '200g': '200ml', '200gm': '200ml', '6.7oz': '200ml', '6.8oz': '200ml', '7oz': '200ml',
    # 236ml / 8oz
    '237ml': '236ml', '236g': '236ml', '236gm': '236ml', '8oz': '236ml',
    # 250ml / 8.5oz
    '250g': '250ml', '250gm': '250ml', '8.4oz': '250ml', '8.5oz': '250ml',
    # 300ml / 10oz
    '300g': '300ml', '300gm': '300ml', '10oz': '300ml',
    # 340ml / 12oz / 340g (CeraVe)
    '340g': '340ml', '340gm': '340ml', '355ml': '340ml', '12oz': '340ml',
    # 400ml / 14oz
    '400g': '400ml', '400gm': '400ml', '13.5oz': '400ml', '14oz': '400ml',
    # 473ml / 16oz / 454g (CeraVe 16oz tub / pump)
    '454g': '473ml', '454gm': '473ml', '16oz': '473ml',
    # 500ml / 16.9oz
    '500g': '500ml', '500gm': '500ml', '16.9oz': '500ml', '17oz': '500ml',
    # 1000ml / 1L / 1kg
    '1l': '1000ml', '1liter': '1000ml', '1000g': '1000ml', '1kg': '1000ml',
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


def canonicalize_volume(vol: str) -> str:
    """Maps cosmetic packaging volume strings to standardized equivalent volumes."""
    if not vol:
        return ""
    v = vol.lower().strip()
    return VOLUME_EQUIVALENCES.get(v, v)


def clean_text_for_matching(text: str) -> str:
    """Strips invisible formatting marks (LTR/RTL/zero-width) and decomposes accents."""
    if not text:
        return ""
    # Strip invisible formatting characters
    text = re.sub(r'[\u200e\u200f\u200b\u202a-\u202e\xa0]', ' ', text)
    # Strip accents (NFKD decomposition: e.g. Sébium -> Sebium, Tolériane -> Toleriane)
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return text


def parse_volume(text: str) -> Tuple[str, str]:
    """Extracts raw volume string and returns text without volume."""
    vol_match = VOLUME_PATTERN.search(text)
    if not vol_match:
        return "", text
    qty = vol_match.group(1)
    unit = vol_match.group(2).lower()
    if unit in ('gm', 'g'):
        unit = 'g'
    elif unit in ('ml', 'l', 'liter', 'litres'):
        unit = 'ml'
    elif 'oz' in unit:
        unit = 'oz'
    try:
        f_qty = float(qty)
        qty_str = str(int(f_qty)) if f_qty.is_integer() else str(f_qty)
    except (ValueError, TypeError):
        qty_str = qty
    vol_str = f"{qty_str}{unit}"
    clean_t = text[:vol_match.start()] + ' ' + text[vol_match.end():]
    return vol_str, clean_t


def extract_volume_and_normalize(title: str, brand: str, handle: str = "") -> Tuple[str, str]:
    """
    Normalizes product title and extracts packaging volume for precise entity resolution.
    Handles UK/US spellings, filler marketing adjectives, skin-type claims, and handle fallbacks.
    e.g. 'CeraVe Moisturising Lotion For Dry To Very Dry Skin 236Ml.' -> ('lotion:moisturizing', '236ml')
    """
    t = clean_text_for_matching(title or "").lower().replace('-', ' ')
    b_low = clean_text_for_matching(brand or "").lower().strip()
    h_clean = clean_text_for_matching(handle or "").lower().replace('-', ' ')

    # 1. Extract volume (title first, then handle fallback)
    vol_str, t = parse_volume(t)
    if not vol_str and h_clean:
        vol_str, _ = parse_volume(h_clean)

    # 2. Normalize UK to US spellings and cosmetic synonyms
    t = re.sub(r'\ba\.m\.?\b', 'am', t)
    t = re.sub(r'\bp\.m\.?\b', 'pm', t)
    t = re.sub(r'\bmoisturising\b', 'moisturizing', t)
    t = re.sub(r'\bcleansing\b', 'cleanse', t)
    t = re.sub(r'\bcreme\b', 'cream', t)
    t = re.sub(r'\bcolour\b', 'color', t)
    t = re.sub(r'\bsalicylic\s+acid\b', 'sa', t)

    # 3. Strip skin type claims & formatting
    for pat in SKIN_TYPE_PATTERNS:
        t = re.sub(pat, ' ', t)

    # 4. Strip brand name variants (with/without hyphens and spaces)
    if b_low:
        b_variants = {b_low, b_low.replace('-', ' '), b_low.replace('-', ''), b_low.replace(' ', '')}
        for bv in b_variants:
            if bv:
                t = re.sub(r'\b' + re.escape(bv) + r'\b', ' ', t)

    words = [re.sub(r'[^a-z0-9]', '', w) for w in t.split()]
    has_timing = any(w in ('pm', 'am') for w in words)
    has_vehicle = any(w in ('lotion', 'cream', 'gel') for w in words)
    has_num = any(re.search(r'\d', w) for w in words)

    meaningful = []
    for w in words:
        if not w or len(w) <= 1 or w in STOP_MARKETING_WORDS:
            continue
        if w in ('moisturizing', 'facial') and has_timing and has_vehicle:
            continue
        if has_num and w in ('serum', 'solution'):
            continue
        meaningful.append(w)

    sorted_tokens = sorted(meaningful)
    base_key = ':'.join(sorted_tokens) if sorted_tokens else re.sub(r'[^a-z0-9]', '', t)
    return base_key, vol_str


def build_cluster_key(
    brand: str,
    title: str,
    inci_matched_id: Optional[int] = None,
    gemini_status: Optional[str] = None,
    handle: str = ""
) -> str:
    """
    Constructs a deterministic canonical cluster key.
    """
    clean_b = clean_text_for_matching(brand or "").lower()
    norm_b = re.sub(r'[^a-z0-9]', '', clean_b)

    # Strategy 1: Shared verified INCI formulation
    if inci_matched_id and gemini_status in ('gemini_approved', None):
        return f"inci:{norm_b}:{inci_matched_id}"

    # Strategy 2: Sorted base title tokens + canonical volume specification
    base_t, vol = extract_volume_and_normalize(title, brand, handle=handle)
    canon_vol = canonicalize_volume(vol)
    norm_t = re.sub(r'[^a-z0-9:]', '', base_t)
    return f"title:{norm_b}:{norm_t}:{canon_vol}"


def link_catalog_products(conn=None, task_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes cross-source product clustering, canonical link creation, and ingredient propagation.
    Uses a 2-pass variant alignment algorithm to unify un-sized listings with explicit volume variants.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    init_linker_db(conn=conn)
    cur = conn.cursor()

    logger.info("Starting cross-source product clustering and canonical entity linking...")

    # Fetch all catalog products with compatibility check for schema columns
    cur.execute("PRAGMA table_info(products);")
    p_cols = {r[1] for r in cur.fetchall()}
    handle_expr = "handle" if "handle" in p_cols else "'' AS handle"
    skin_expr = "is_skincare" if "is_skincare" in p_cols else "0 AS is_skincare"

    cur.execute(f"""
    SELECT id, store_id, title, brand, category, price, is_available, image_url,
           ingredients_raw, ingredients_parsed, actives, ingredient_source,
           inci_matched_id, gate4_gemini_status, {handle_expr}, {skin_expr}
    FROM products
    ORDER BY id ASC;
    """)
    products = [dict(r) for r in cur.fetchall()]
    total_products = len(products)
    logger.info(f"Loaded {total_products} products for cross-store entity resolution.")

    # Pass 1: Form initial sized clusters and index brand_base keys
    clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    brand_base_to_keys: Dict[str, List[str]] = defaultdict(list)
    unsized_items: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for p in products:
        b = p["brand"] or "Generic"
        t = p["title"] or ""
        h = p.get("handle") or ""
        inci_id = p["inci_matched_id"]
        g_status = p["gate4_gemini_status"]

        clean_b = clean_text_for_matching(b).lower()
        norm_b = re.sub(r'[^a-z0-9]', '', clean_b)

        if inci_id and g_status in ('gemini_approved', None):
            key = f"inci:{norm_b}:{inci_id}"
            clusters[key].append(p)
        else:
            base_t, vol = extract_volume_and_normalize(t, b, handle=h)
            norm_t = re.sub(r'[^a-z0-9:]', '', base_t)
            brand_base = f"title:{norm_b}:{norm_t}"
            canon_vol = canonicalize_volume(vol)
            if canon_vol:
                sized_key = f"{brand_base}:{canon_vol}"
                if sized_key not in clusters:
                    brand_base_to_keys[brand_base].append(sized_key)
                clusters[sized_key].append(p)
            else:
                unsized_items[brand_base].append(p)

    # Pass 2: Align un-sized listings to sized clusters by price proximity
    aligned_count = 0
    for brand_base, u_list in unsized_items.items():
        candidate_keys = brand_base_to_keys.get(brand_base, [])
        unplaced = []
        for u in u_list:
            u_price = u.get("price")
            u_store = u.get("store_id")
            best_cand = None
            best_diff = 999.0

            if u_price and u_price > 0 and candidate_keys:
                for cand_key in candidate_keys:
                    c_items = clusters[cand_key]
                    # Disallow multiple listings from the same store in one variant cluster
                    if any(ci["store_id"] == u_store for ci in c_items):
                        continue
                    prices = [ci["price"] for ci in c_items if ci.get("price") and ci["price"] > 0]
                    if not prices:
                        continue
                    avg_p = sum(prices) / len(prices)
                    diff = abs(u_price - avg_p) / avg_p
                    if diff <= 0.40 and diff < best_diff:
                        best_diff = diff
                        best_cand = cand_key

            if best_cand:
                clusters[best_cand].append(u)
                aligned_count += 1
            else:
                unplaced.append(u)

        if unplaced:
            clusters[f"{brand_base}:"].extend(unplaced)

    logger.info(f"Formed {len(clusters)} distinct canonical product clusters (Aligned {aligned_count} un-sized listings to physical size variants).")

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
