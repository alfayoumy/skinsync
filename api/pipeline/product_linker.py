"""
Cross-Source Product Entity Resolution & Canonical Linking Engine:
1. Clusters identical products across different stores (Source Beauty, Bloom Pharmacy, Feel22, Dr. Dawood, Bashrety, Raw African).
2. Manages the `canonical_products` table and links store listings via `products.canonical_product_id`.
3. Preserves authoritative formulations and records ambiguous identity decisions for review.
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
from pipeline.identity_review import (init_identity_review_db, assess_canonical_identity,
    record_identity_review, identity_compatible, audit_canonical_identity)
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
    init_identity_review_db(conn)
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
    """Compatibility search key backed by explicit brand aliases."""
    from pipeline.brand_registry import resolve_brand, brand_key
    resolved = resolve_brand(b)
    if not resolved: return ''
    return brand_key(resolved['name']).replace(' ', '')


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
    'Incremental stable-profile resolution; legacy IDs and INCI are preserved.'
    from pipeline.aggregation_schema import init_aggregation_schema
    from pipeline.brand_registry import seed_brands, resolve_brand, brand_key
    from pipeline.taxonomy_service import seed_taxonomy, classify_product_attributes
    from pipeline.source_registry import seed_sources
    from pipeline.canonical_identity_resolver import CanonicalIdentityResolver, extract_identity
    from pipeline.aggregation_service import queue_review
    from pipeline.offer_service import refresh_offer_aggregates
    owned = conn is None
    if owned: conn = get_connection()
    try:
        init_linker_db(conn)
        init_aggregation_schema(conn)
        seed_brands(conn); seed_taxonomy(conn); seed_sources(conn)
        products = [dict(r) for r in conn.execute('SELECT * FROM products ORDER BY id')]
        # Exact legacy labels are independent identities, never fuzzy brand aliases.
        legacy_brands = {p['brand'] for p in products if p.get('brand')}
        legacy_brands.update(r[0] for r in conn.execute('SELECT DISTINCT brand FROM canonical_products'))
        for name in sorted(legacy_brands):
            existing = resolve_brand(name, conn)
            if not existing: continue
            if not existing.get('id'):
                conn.execute("INSERT OR IGNORE INTO brands(name,status) VALUES (?,'legacy_exact')", (name,))
                bid = conn.execute('SELECT id FROM brands WHERE name=?', (name,)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO brand_aliases(alias,brand_id,approved_by) VALUES (?,?,'legacy_exact_label')", (brand_key(name), bid))
        for row in conn.execute('SELECT * FROM canonical_products').fetchall():
            canonical = dict(row)
            brand = resolve_brand(canonical['brand'], conn)
            attributes = json.loads(canonical.get('identity_attributes') or '{}')
            if not attributes.get('name'):
                attributes = extract_identity(canonical['canonical_title'], canonical['brand'], canonical.get('market'))
            conn.execute('UPDATE canonical_products SET brand_id=?,identity_attributes=? WHERE id=?',
                (brand.get('id') if brand else None, json.dumps(attributes), canonical['id']))
        resolver = CanonicalIdentityResolver(conn)
        created=0; deferred=0; reviews=0
        touched=set()
        for product in products:
            pid=product['id']; brand=resolve_brand(product.get('brand'),conn)
            title=product.get('original_title') or product['title']
            classification=classify_product_attributes(title,product.get('original_brand') or product['brand'],product.get('original_category') or '')
            conn.execute('''UPDATE products SET original_title=COALESCE(original_title,title),original_brand=COALESCE(original_brand,brand),
                original_category=COALESCE(original_category,category),brand_id=?,classification=? WHERE id=?''',
                (brand.get('id') if brand else None,json.dumps(classification.to_dict()),pid))
            identity=extract_identity(title,product['brand'])
            source_id=product['store_id'];source_pid=str(product.get('store_product_id') or pid)
            anchor=product.get('canonical_product_id')
            if anchor:
                canonical=conn.execute('SELECT * FROM canonical_products WHERE id=?',(anchor,)).fetchone()
                if canonical:
                    canonical=dict(canonical)
                    from pipeline.canonical_identity_resolver import compare_attributes
                    comparison=compare_attributes(identity,json.loads(canonical.get('identity_attributes') or '{}'))
                    anchor_identity=json.loads(canonical.get('identity_attributes') or '{}')
                    anchor_brand=resolve_brand(canonical.get('brand'),conn)
                    if identity.get('name')!=anchor_identity.get('name'):comparison['unknown'].append('legacy_title_identity')
                    if not brand or not anchor_brand or brand.get('id')!=anchor_brand.get('id'):comparison['conflicts'].append('brand')
                    if comparison['unknown'] or comparison['conflicts']:comparison['status']='requires_review'
                    if comparison['status']!='compatible':
                        queue_review(conn,'identity',source_id,source_pid,anchor,{'product_id':pid,'canonical_id':anchor,'comparison':comparison})
                        reviews+=1
                    from pipeline.identity_trust import verified_link
                    if comparison['conflicts'] or not verified_link(conn,source_id,source_pid,anchor):
                        resolver.record_link(source_id,source_pid,anchor,'accepted','legacy_preserved',{'product_id':pid,'attributes':identity})
                    # Preserving a historical association is not verification.
                    conn.execute("UPDATE canonical_products SET identity_status='legacy_preserved_unverified' WHERE id=? AND identity_status!='verified'",(anchor,))
                    touched.add(anchor)
            else:
                if classification.classification_status=='review':
                    queue_review(conn,'category',source_id,source_pid,None,{'product_id':pid,'title':title,'classification':classification.to_dict()})
                    deferred+=1;reviews+=1;continue
                decision=resolver.resolve(identity,product['brand'],title,source_id,source_pid)
                if decision.status=='ambiguous':
                    queue_review(conn,'identity',source_id,source_pid,None,{'product_id':pid,'decision':decision.to_dict()})
                    deferred+=1;reviews+=1;continue
                if decision.status=='new':
                    # New identities require independent approval in every ingestion path.
                    anchor=resolver.create_provisional(title,product['brand'],classification,identity,visible=False)
                    conn.execute('UPDATE canonical_products SET is_skincare=?,primary_image_url=? WHERE id=?',
                        (int(product.get('is_skincare',classification.is_skincare)),product.get('image_url'),anchor))
                    created+=1
                else:
                    anchor=decision.canonical_product_id
                    from pipeline.identity_review import formula_fingerprint
                    accepted=dict(conn.execute('SELECT * FROM canonical_products WHERE id=?',(anchor,)).fetchone())
                    incoming_hash=formula_fingerprint(product) if product.get('ingredient_source')=='store_page_tab' else None
                    accepted_hash=formula_fingerprint(accepted)
                    if incoming_hash and accepted_hash and incoming_hash!=accepted_hash:
                        queue_review(conn,'formulation',source_id,source_pid,anchor,{'product_id':pid,'candidate_hash':incoming_hash,'existing_hash':accepted_hash})
                        deferred+=1;reviews+=1;continue
                resolver.record_link(source_id,source_pid,anchor,'accepted',decision.method,decision.to_dict())
                resolver.add_aliases(anchor,identity,source_id)
                touched.add(anchor)
            if anchor:
                size=extract_size_details(product['title'],product.get('handle') or '',product.get('price') or 0)
                conn.execute('''UPDATE products SET canonical_product_id=?,size_raw=?,size_value=?,size_unit=?,normalized_size_ml=?,
                    normalized_size_g=?,unit_price=?,unit_measure=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                    (anchor,size['size_raw'],size['size_value'],size['size_unit'],size['normalized_size_ml'],size['normalized_size_g'],
                     size['unit_price'],size['unit_measure'],pid))
        for cid in touched: refresh_offer_aggregates(conn,cid)
        audit=audit_canonical_identity(conn,persist=True)
        for finding in audit['findings']:
            kind='duplicate' if finding['kind']=='duplicate_canonicals' else 'identity'
            cids=finding['canonical_ids']
            queue_review(conn,kind,'canonical',':'.join(str(cid) for cid in cids),cids[0] if cids else None,finding)
        conn.commit()
        try:
            from services.catalog_service import load_catalog_from_db
            load_catalog_from_db()
        except Exception as exc: logger.warning('Catalog refresh skipped: %s',exc)
        multistore=conn.execute('SELECT COUNT(*) FROM canonical_products WHERE store_count>1').fetchone()[0]
        result={'status':'success','total_products':len(products),'canonical_entities':len(touched),
            'newly_created_canonical':created,'multi_store_clusters':multistore,'ingredients_propagated':0,
            'identity_reviews':reviews+audit['review_count'],'deferred_offers':deferred}
        if task_id: log_task_event(task_id,'Incremental canonical identity resolution completed.',context_data=result,conn=conn)
        return result
    except Exception:
        conn.rollback();raise
    finally:
        if owned:conn.close()


if __name__ == "__main__":
    setup_logging()
    link_catalog_products()
