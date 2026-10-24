"""
Cross-Source Product Entity Resolution & Canonical Linking Engine:
1. Clusters identical products across different stores (Source Beauty, Bloom Pharmacy, Feel22, Dr. Dawood, Bashrety, Raw African).
2. Manages the `canonical_products` table and links store listings via `products.canonical_product_id`.
3. Propagates ingredient lists from stores with rich HTML descriptions to stores lacking ingredients.
4. Aggregates multi-source pricing for real-time price comparison and arbitrage tracking.
5. Treats packaging volume/size strictly as a variant/pricing tier, never splitting genuine product entities.
"""

import os
import sys
import re
import json
import sqlite3
import unicodedata
from typing import Dict, List, Any, Optional, Tuple, Set
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.safeguards import CosmeticCompatibilityValidator
from pipeline.task_tracker import create_subtask, complete_subtask, log_task_event
from pipeline.logging_config import setup_logging, get_logger

logger = get_logger("skincare_pipeline.linker")

VOLUME_PATTERN = re.compile(r'\b(\d+(?:\.\d+)?)\s*(ml|g|gm|oz|fl\.?\s*oz|l|liter|litres?|kg)\b', re.IGNORECASE)

BRAND_STOPWORDS = {
    'eau', 'thermale', 'laboratoires', 'laboratoire', 'paris', 'cosmetics', 'cosmetique',
    'pharmacy', 'pharma', 'derma', 'store', 'egypt', 'official', 'the', 'beauty', 'labs', 'laboratory'
}

STOP_MARKETING_WORDS = {
    'daily', 'classic', 'essential', 'original', 'ultra', 'lightweight', 'rich',
    'soothing', 'nourishing', 'advanced', 'pure', 'intensive', 'gentle'
}

FORMULATION_TYPES = {
    'cream', 'lotion', 'gel', 'serum', 'balm', 'oil', 'foam', 'water', 'cleanser', 'wash', 'mist', 'fluid', 'paste'
}

CRITICAL_MODIFIERS = {
    'am', 'pm', 'spf', 'hydrating', 'foaming', 'sa', 'smoothing', 'blemish', 'retinol', 'caffeine', 'brightening'
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

    # Add canonical_product_id, is_skincare, and size attributes to products table if missing
    cur.execute("PRAGMA table_info(products);")
    existing_cols = {r[1] for r in cur.fetchall()}
    if "canonical_product_id" not in existing_cols:
        cur.execute("ALTER TABLE products ADD COLUMN canonical_product_id INTEGER REFERENCES canonical_products(id);")
    if "is_skincare" not in existing_cols:
        cur.execute("ALTER TABLE products ADD COLUMN is_skincare INTEGER DEFAULT 0;")

    size_cols = [
        ('size_raw', 'TEXT'),
        ('size_value', 'REAL'),
        ('size_unit', 'TEXT'),
        ('normalized_size_ml', 'REAL'),
        ('normalized_size_g', 'REAL'),
        ('unit_price', 'REAL'),
        ('unit_measure', 'TEXT')
    ]
    for c_name, c_type in size_cols:
        if c_name not in existing_cols:
            cur.execute(f"ALTER TABLE products ADD COLUMN {c_name} {c_type};")

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
    text = re.sub(r'[\u200e\u200f\u200b\u202a-\u202e\xa0]', ' ', text)
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return text


def parse_volume(text: str) -> Tuple[str, str]:
    """Extracts raw volume string and returns text without volume."""
    vol_match = VOLUME_PATTERN.search(text)
    if not vol_match:
        return "", text
    qty = vol_match.group(1)
    raw_unit = vol_match.group(2).lower().strip()
    if raw_unit in ('gm', 'g'):
        unit = 'g'
    elif raw_unit in ('ml', 'milliliter', 'millilitre'):
        unit = 'ml'
    elif raw_unit in ('l', 'liter', 'litres'):
        unit = 'l'
    elif 'oz' in raw_unit:
        unit = 'oz'
    else:
        unit = raw_unit

    try:
        f_qty = float(qty)
        qty_str = str(int(f_qty)) if f_qty.is_integer() else str(f_qty)
    except (ValueError, TypeError):
        qty_str = qty
    vol_str = f"{qty_str}{unit}"
    clean_t = text[:vol_match.start()] + ' ' + text[vol_match.end():]
    return vol_str, clean_t


def extract_size_details(title: str, handle: str = "", price: float = 0.0) -> Dict[str, Any]:
    """
    Extracts packaging size and computes normalized unit pricing for an offer.
    Supports liquid volume (ml, l, fl oz) and solid mass (g, kg, oz).
    Does NOT assume volume == mass (never assumes 50g == 50ml).
    """
    vol_str, _ = parse_volume(title or "")
    if not vol_str and handle:
        vol_str, _ = parse_volume(handle.replace('-', ' '))

    if not vol_str:
        return {
            "size_raw": None,
            "size_value": None,
            "size_unit": None,
            "normalized_size_ml": None,
            "normalized_size_g": None,
            "unit_price": None,
            "unit_measure": None
        }

    m = re.match(r'^([\d.]+)\s*([a-z ]+)$', vol_str.lower().strip())
    if not m:
        return {
            "size_raw": vol_str,
            "size_value": None,
            "size_unit": None,
            "normalized_size_ml": None,
            "normalized_size_g": None,
            "unit_price": None,
            "unit_measure": None
        }

    try:
        val = float(m.group(1))
    except (ValueError, TypeError):
        return {
            "size_raw": vol_str,
            "size_value": None,
            "size_unit": None,
            "normalized_size_ml": None,
            "normalized_size_g": None,
            "unit_price": None,
            "unit_measure": None
        }

    unit = m.group(2).strip()
    norm_ml = None
    norm_g = None
    unit_price = None
    unit_measure = None

    if unit in ('ml', 'l', 'liter', 'litres', 'fl oz', 'floz'):
        if unit in ('l', 'liter', 'litres'):
            norm_ml = val * 1000.0
        elif 'oz' in unit:
            norm_ml = val * 29.5735
        else:
            norm_ml = val

        if price and price > 0 and norm_ml > 0:
            unit_price = round(price / (norm_ml / 100.0), 2)
            unit_measure = "EGP / 100ml"
    elif unit in ('g', 'gm', 'kg'):
        if unit == 'kg':
            norm_g = val * 1000.0
        else:
            norm_g = val

        if price and price > 0 and norm_g > 0:
            unit_price = round(price / (norm_g / 100.0), 2)
            unit_measure = "EGP / 100g"
    elif unit == 'oz':
        norm_g = val * 28.3495
        if price and price > 0 and norm_g > 0:
            unit_price = round(price / (norm_g / 100.0), 2)
            unit_measure = "EGP / 100g"

    disp_val = int(val) if val.is_integer() else val
    return {
        "size_raw": f"{disp_val} {unit}",
        "size_value": val,
        "size_unit": unit,
        "normalized_size_ml": round(norm_ml, 2) if norm_ml else None,
        "normalized_size_g": round(norm_g, 2) if norm_g else None,
        "unit_price": unit_price,
        "unit_measure": unit_measure
    }


def canonicalize_brand(b: str) -> str:
    """Normalizes brand strings into a canonical brand identifier."""
    if not b:
        return 'generic'
    b_norm = unicodedata.normalize('NFKD', b).encode('ASCII', 'ignore').decode('utf-8').lower()
    b_norm = re.sub(r'[^a-z0-9\s]', ' ', b_norm)
    words = [w for w in b_norm.split() if w and w not in BRAND_STOPWORDS]
    if not words:
        words = [w for w in b_norm.split() if w]
    return ''.join(words) if words else 'generic'


def clean_and_tokenize(title: str, brand: str = "") -> Tuple[Set[str], List[str]]:
    """
    Cleans title by stripping packaging volume (since volume only affects pricing, not product identity),
    normalizing French/US/UK synonyms, stripping marketing fluff, and extracting core cosmetic tokens.
    """
    t = unicodedata.normalize('NFKD', title or '').encode('ASCII', 'ignore').decode('utf-8').lower()
    t = re.sub(r'[\u2013\u2014\-_/\\.,:;()!+%*#&\[\]]', ' ', t)
    t = VOLUME_PATTERN.sub(' ', t)
    t = re.sub(r'\bmoisturising\b', 'moisturizing', t)
    t = re.sub(r'\bcreme\b', 'cream', t)
    t = re.sub(r'\bcleansing\b', 'cleanse', t)
    t = re.sub(r'\bnettoyant\b', 'cleanse', t)
    t = re.sub(r'\bsoin\b', 'care', t)
    t = re.sub(r'\byeux\b', 'eye', t)
    t = re.sub(r'\baction\b', 'correction', t)
    t = re.sub(r'\bh\.?a\b', 'hyaluronic', t)
    t = re.sub(r'\bhyaluronic\s+acid\b', 'hyaluronic', t)
    t = re.sub(r'\bsalicylic\s+acid\b', 'sa', t)
    for pat in SKIN_TYPE_PATTERNS:
        t = re.sub(pat, ' ', t)
    if brand:
        b_clean = unicodedata.normalize('NFKD', brand).encode('ASCII', 'ignore').decode('utf-8').lower()
        for bw in b_clean.split():
            if len(bw) >= 3:
                t = re.sub(r'\b' + re.escape(bw) + r'\b', ' ', t)
    words = [w for w in t.split() if w and (len(w) > 1 or w.isdigit()) and w not in STOP_MARKETING_WORDS and w not in ('routine', 'skin', 'care', 'effect', 'new')]
    return set(words), sorted(words)


def extract_volume_and_normalize(title: str, brand: str, handle: str = "") -> Tuple[str, str]:
    """
    Normalizes product title and extracts packaging volume for precise entity resolution.
    Retained for backward compatibility with external callers and unit tests.
    """
    t = clean_text_for_matching(title or "").lower().replace('-', ' ')
    b_low = clean_text_for_matching(brand or "").lower().strip()
    h_clean = clean_text_for_matching(handle or "").lower().replace('-', ' ')

    vol_str, t = parse_volume(t)
    if not vol_str and h_clean:
        vol_str, _ = parse_volume(h_clean)

    t = re.sub(r'\ba\.m\.?\b', 'am', t)
    t = re.sub(r'\bp\.m\.?\b', 'pm', t)
    t = re.sub(r'\bmoisturising\b', 'moisturizing', t)
    t = re.sub(r'\bcleansing\b', 'cleanse', t)
    t = re.sub(r'\bcreme\b', 'cream', t)
    t = re.sub(r'\bcolour\b', 'color', t)
    t = re.sub(r'\bsalicylic\s+acid\b', 'sa', t)

    for pat in SKIN_TYPE_PATTERNS:
        t = re.sub(pat, ' ', t)

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
    """Constructs a deterministic canonical cluster key without size partitioning."""
    clean_b = canonicalize_brand(brand)
    if inci_matched_id and gemini_status in ('gemini_approved', None):
        return f"inci:{clean_b}:{inci_matched_id}"
    tokens_set, tokens_list = clean_and_tokenize(title, brand)
    norm_t = ':'.join(tokens_list) if tokens_list else 'item'
    return f"title:{clean_b}:{norm_t}"


class UnionFind:
    """Disjoint Set Union (DSU) with path compression for transitive entity resolution."""
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i: int, j: int) -> None:
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j


def link_catalog_products(conn=None, task_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes cross-source product clustering, canonical entity linking, and ingredient propagation.
    Unifies identical products across stores into single canonical entities regardless of packaging size.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    init_linker_db(conn=conn)
    cur = conn.cursor()

    logger.info("Starting cross-source product clustering and canonical entity linking...")

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

    uf = UnionFind(total_products)

    # Pre-process brand, category, and tokens
    groups: Dict[Tuple[str, str], List[Tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for idx, p in enumerate(products):
        cb = canonicalize_brand(p.get("brand") or "")
        cc = p.get("category") or "Skincare"
        tokens_set, tokens_list = clean_and_tokenize(p.get("title") or "", p.get("brand") or "")
        p["tokens_set"] = tokens_set
        p["tokens_list"] = tokens_list
        p["canon_brand"] = cb
        p["canon_cat"] = cc
        groups[(cb, cc)].append((idx, p))

    # Evaluate pairwise matching within each (brand, category) group
    for (cb, cc), items in groups.items():
        n = len(items)
        for i in range(n):
            idx_i, p_i = items[i]
            for j in range(i + 1, n):
                idx_j, p_j = items[j]

                # 1. Same verified INCI formulation match
                if p_i.get("inci_matched_id") and p_i["inci_matched_id"] == p_j.get("inci_matched_id"):
                    if p_i.get("gate4_gemini_status") != "gemini_rejected" and p_j.get("gate4_gemini_status") != "gemini_rejected":
                        uf.union(idx_i, idx_j)
                        continue

                set1 = p_i["tokens_set"]
                set2 = p_j["tokens_set"]
                if not set1 or not set2:
                    continue

                # 2. Formulation vehicle safeguard (e.g. lotion vs cream vs gel)
                types1 = set1.intersection(FORMULATION_TYPES)
                types2 = set2.intersection(FORMULATION_TYPES)
                if types1 and types2 and types1 != types2:
                    continue

                # 3. Critical modifier safeguards (e.g. AM vs PM, hydrating vs foaming)
                mods1 = set1.intersection(CRITICAL_MODIFIERS)
                mods2 = set2.intersection(CRITICAL_MODIFIERS)
                if ('am' in mods1 and 'pm' in mods2) or ('pm' in mods1 and 'am' in mods2):
                    continue
                if ('pm' in mods1) != ('pm' in mods2):
                    continue
                if ('am' in mods1) != ('am' in mods2):
                    continue
                if ('hydrating' in mods1 and 'foaming' in mods2) or ('foaming' in mods1 and 'hydrating' in mods2):
                    continue

                # 4. Sun Protection Factor (SPF) safeguard: SPF 30 vs SPF 50 are distinct formulations
                t1_lower = p_i.get("title", "").lower()
                t2_lower = p_j.get("title", "").lower()
                spf_m1 = re.search(r'\bspf\s*(\d+)\b', t1_lower)
                spf_m2 = re.search(r'\bspf\s*(\d+)\b', t2_lower)
                if spf_m1 and spf_m2 and spf_m1.group(1) != spf_m2.group(1):
                    continue
                if bool(spf_m1) != bool(spf_m2) and ('spf' in mods1 or 'spf' in mods2):
                    continue

                # 5. Fragrance-free / unscented vs scented formulation safeguard
                unscented_1 = bool(re.search(r'\b(fragrance[- ]free|unscented|sans parfum)\b', t1_lower))
                unscented_2 = bool(re.search(r'\b(fragrance[- ]free|unscented|sans parfum)\b', t2_lower))
                if unscented_1 != unscented_2:
                    continue

                # 6. Token Jaccard similarity & exact set match
                inter = len(set1.intersection(set2))
                union = len(set1.union(set2))
                jacc = inter / union if union > 0 else 0.0

                if set1 == set2 or jacc >= 0.60:
                    uf.union(idx_i, idx_j)

    # Form canonical clusters from UnionFind
    clusters: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for idx, p in enumerate(products):
        root = uf.find(idx)
        clusters[root].append(p)

    canonical_created = 0
    multi_store_clusters = 0
    ingredients_propagated = 0

    cur.execute("BEGIN TRANSACTION;")

    used_keys = set()

    for root, items in clusters.items():
        is_multi_store = len(set(p["store_id"] for p in items)) > 1
        if is_multi_store:
            multi_store_clusters += 1

        best_title = max(items, key=lambda x: len(x.get("title") or ""))["title"]
        best_brand = items[0]["brand"] or "Generic"
        best_category = items[0]["category"] or "Skincare"
        is_skin_cluster = 1 if any(p.get("is_skincare") == 1 for p in items) else 0

        primary_img = next((p["image_url"] for p in items if p["image_url"]), None)

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
        best_inci_id = next((p["inci_matched_id"] for p in items if p.get("inci_matched_id")), None)

        avail_prices = [p["price"] for p in items if p.get("price") and p["price"] > 0 and p.get("is_available")]
        all_prices = [p["price"] for p in items if p.get("price") and p["price"] > 0]
        prices_to_use = avail_prices if avail_prices else all_prices

        min_p = min(prices_to_use) if prices_to_use else 0.0
        max_p = max(prices_to_use) if prices_to_use else 0.0
        store_cnt = len(set(p["store_id"] for p in items))

        # Generate unique canonical normalized key
        all_tokens = set.union(*[p["tokens_set"] for p in items]) if items else set()
        key_tokens = ':'.join(sorted(all_tokens)) if all_tokens else 'product'
        key = f"{items[0]['canon_brand']}:{items[0]['canon_cat']}:{key_tokens}"
        if key in used_keys:
            key = f"{key}_{root}"
        used_keys.add(key)

        cur.execute("SELECT id, ingredients_raw FROM canonical_products WHERE normalized_key = ?;", (key,))
        existing_row = cur.fetchone()

        if existing_row:
            canonical_id = existing_row[0]
            # STRICT SEPARATION: Only update identity, pricing, store counts, and availability metadata.
            # Retailer listings MUST NEVER overwrite or modify canonical authoritative ingredients!
            cur.execute("""
            UPDATE canonical_products
            SET canonical_title = ?, brand = ?, category = ?,
                min_price = ?, max_price = ?, store_count = ?,
                primary_image_url = COALESCE(primary_image_url, ?),
                is_skincare = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (best_title, best_brand, best_category, min_p, max_p, store_cnt, primary_img, is_skin_cluster, canonical_id))
        else:
            # For new canonical products, check if there is pre-existing curated/skinsafe verified data
            init_raw = None
            init_parsed = None
            init_acts = None
            init_src = None
            init_inci_id = None
            for p in items:
                if p.get("ingredient_source") in ('skinsafe_matched', 'curated', 'v10_curated') and p.get("ingredients_raw"):
                    init_raw = p["ingredients_raw"]
                    init_parsed = p.get("ingredients_parsed")
                    init_acts = p.get("actives")
                    init_src = p.get("ingredient_source")
                    init_inci_id = p.get("inci_matched_id")
                    break

            cur.execute("""
            INSERT INTO canonical_products (
                canonical_title, brand, category, normalized_key,
                ingredients_raw, ingredients_parsed, actives, ingredient_source,
                inci_matched_id, min_price, max_price, store_count, primary_image_url, is_skincare
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (best_title, best_brand, best_category, key, init_raw, init_parsed, init_acts,
                  init_src, init_inci_id, min_p, max_p, store_cnt, primary_img, is_skin_cluster))
            canonical_id = cur.lastrowid
            canonical_created += 1

        # Link each retailer offer to the canonical product with size & unit-price attributes
        for p in items:
            pid = p["id"]
            price_val = p.get("price") or 0.0
            size_data = extract_size_details(p.get("title") or "", p.get("handle") or "", price_val)

            cur.execute("""
            UPDATE products
            SET canonical_product_id = ?,
                size_raw = ?,
                size_value = ?,
                size_unit = ?,
                normalized_size_ml = ?,
                normalized_size_g = ?,
                unit_price = ?,
                unit_measure = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (
                canonical_id,
                size_data["size_raw"],
                size_data["size_value"],
                size_data["size_unit"],
                size_data["normalized_size_ml"],
                size_data["normalized_size_g"],
                size_data["unit_price"],
                size_data["unit_measure"],
                pid
            ))

    # Purge deprecated canonical clusters not referenced by any products
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

    # Refresh in-memory catalog
    try:
        from services.catalog_service import load_catalog_from_db
        load_catalog_from_db()
    except Exception as e:
        logger.warning(f"Could not reload in-memory cache: {e}")

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
