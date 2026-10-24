"""
Authoritative Waterfall INCI Resolver Pipeline.
Implements the 7-tier waterfall methodology for resolving cosmetic ingredients:
1. Current store page ingredient list (then Historical Store Scrapes Jan 10-12, 2026) - dedicated tab only
2. OpenBeauty API (world.openbeautyfacts.org)
3. High SkinSAFE Match (strict brand, vehicle, and line alignment against inci_reference >= 0.50 overlap)
4. LangSearch Web Search API (searches '{brand} {title} inci ingredients' and parses INCI from top results)
5. Store page description whitelisted ingredients (extracts verified chemical ingredients via whitelist)
6. Cross-store same-product cluster propagation (copies ingredients from resolved cluster partners)
7. Pending Jev & Pending Gemini arbitration from Tier 3 SkinSAFE match (if Tiers 4, 5, 6 all fail)

Every resolved product records its exact waterfall method in `products.ingredient_source`.
"""

import os
import sys
import re
import json
import time
import urllib.parse
import urllib.request
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List, Tuple, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except Exception:
    pass

from pipeline.db_manager import get_connection, DB_PATH
from pipeline.inci_matcher import (
    parse_and_normalize_ingredients, is_valid_inci_list,
    query_open_beauty_facts, CosmeticCompatibilityValidator,
    clean_product_title
)
from pipeline.whitelist_matcher import extract_ingredients_via_whitelist

logger = logging.getLogger("skincare_pipeline.waterfall_resolver")

# Canonical cosmetic vehicle categories for strict Tier 3 compatibility
VEHICLES = [
    'cleanser', 'wash', 'cream', 'gel', 'serum', 'lotion', 'mask', 
    'oil', 'spray', 'balm', 'toner', 'scrub', 'shampoo', 'sunscreen', 
    'fluid', 'stick', 'pads', 'foam', 'ampoule', 'essence', 'peel', 'micellar'
]

# Historical store scrapes cache
_HISTORICAL_SCRAPES_CACHE: Optional[Dict[str, str]] = None

def get_vehicles(text: str) -> List[str]:
    """Extracts all cosmetic vehicles present in text."""
    if not text:
        return []
    low = text.lower()
    return [v for v in VEHICLES if re.search(r'\b' + v + r'\b', low)]

def clean_title_tokens(text: str, brand: str = "") -> Set[str]:
    """Extracts semantic title tokens, stripping volumes, units, and brand terms."""
    if not text:
        return set()
    clean = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ml|g|gm|oz|fl\s*oz|count|pack|pcs|piece|fl|tin|kg)\b', ' ', text, flags=re.I)
    clean = re.sub(r'[\(\)\[\]\+\-–—:,#%*]', ' ', clean)
    tokens = set(re.findall(r'[a-z0-9]+', clean.lower()))
    if brand:
        b_tokens = set(re.findall(r'[a-z0-9]+', brand.lower()))
        tokens = tokens - b_tokens
    return tokens

INCI_ABBREVIATIONS = {'denat', 'vit', 'ext', 'no', 'ci', 'leaf', 'root', 'pwd'}

def sanitize_inci_list(text: str) -> str:
    """
    Cleans and purges trailing retail narrative, usage directions, and marketing sentences
    from extracted INCI formulations.
    """
    if not text:
        return ""
    s = text.strip()

    # 1. Truncate at explicit retail section headers or marketing stop phrases
    stop_phrases = [
        r'\.\s*(?:The list of ingredients|Ingredients are subject to|Please refer to the ingredients|For the latest information)',
        r'\.\s*(?:Avoid contact with eyes|In case of contact with eyes|Rinse thoroughly)',
        r'\.\s*(?:How to use|Directions|Mode d\'emploi|Mode of application|Application|Apply morning|Apply to)',
        r'\.\s*(?:Caution|Warning|Precautions|Attention|For external use only|Keep out of reach)',
        r'\.\s*(?:Main ingredients|Active ingredients|Key ingredients|Actifs principaux)\s*[:\-\n]',
        r'\b(?:Line\s+[a-zA-Z0-9\s]+\s*For\s+age|Line\s+jejuFor\s+age)\b',
        r'\b(?:MAIN INGREDIENTS|ACTIVE INGREDIENTS)\s+.*$',
        r'\b(?:Function\s*[:\-\n]|Volume\s*[:\-\n]|Certificate\s*[:\-\n]|Shelf life\s*[:\-\n]|Format\s*[:\-\n]).*$',
        r'\bINCI\s*\[[0-9\-]+\]',
        r'\.\s*(?:Apply\s+dots|Refresh\s+whenever|pair\s+it\s+with|Use\s+as\s+desired|are\s+listed\s+among|alongside\s+the\s+listed)',
        r'\.\s*Moisturizing and smoothing active ingredients\b.*$'
    ]
    for pat in stop_phrases:
        s = re.split(pat, s, flags=re.IGNORECASE)[0].strip()

    # 2. Sentence boundary check after periods (respecting INCI abbreviations like 'Alcohol Denat.')
    parts = re.split(r'(\.\s+)', s)
    clean_parts = []
    for i in range(0, len(parts), 2):
        chunk = parts[i]
        sep = parts[i+1] if i+1 < len(parts) else ''

        last_word = re.findall(r'[a-zA-Z]+', chunk)
        last_tok = last_word[-1].lower() if last_word else ''

        if last_tok in INCI_ABBREVIATIONS:
            clean_parts.append(chunk + sep)
            continue

        if i+2 < len(parts):
            next_chunk = parts[i+2]
            prose_words = {'is', 'are', 'was', 'were', 'the', 'this', 'that', 'with', 'for', 'from', 'helps', 'to', 'your', 'skin', 'formula', 'suitable'}
            next_words = set(re.findall(r'\b[a-z]+\b', next_chunk.lower()))
            if len(next_words & prose_words) >= 2 and len(next_chunk.split(',')) <= 1:
                clean_parts.append(chunk)
                break
        clean_parts.append(chunk + sep)

    s = ''.join(clean_parts).strip()
    s = re.sub(r'[\.\s,;:\-]+$', '', s).strip()
    return s

# ==============================================================================
# TIER 1: STORE INGREDIENTS TAB & HISTORICAL STORE SCRAPES
# ==============================================================================

def load_historical_scrapes_index() -> Dict[str, str]:
    """Loads and caches authentic store scrapes from Jan 10-12, 2026."""
    global _HISTORICAL_SCRAPES_CACHE
    if _HISTORICAL_SCRAPES_CACHE is not None:
        return _HISTORICAL_SCRAPES_CACHE

    import csv
    cache: Dict[str, str] = {}
    candidates_root = [
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        os.getcwd(),
        "/mnt/windows/Users/AmrAlfayoumy/Downloads/skincare"
    ]
    base_dir = next((d for d in candidates_root if os.path.isdir(os.path.join(d, "data", "legacy_csvs"))), candidates_root[0])

    scrape_files = [
        os.path.join(base_dir, "data", "legacy_csvs", "store_scrapes", "sourcebeauty_corrected_details.csv"),
        os.path.join(base_dir, "data", "legacy_csvs", "store_scrapes", "feel22_enriched_final.csv"),
        os.path.join(base_dir, "data", "legacy_csvs", "store_scrapes", "bloom_pharmacy_ready.csv"),
        os.path.join(base_dir, "data", "legacy_csvs", "master", "master_product_database.csv")
    ]

    for fpath in scrape_files:
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    url = (r.get("url") or r.get("product_url") or "").strip()
                    title = (r.get("title") or "").strip().lower()
                    ings = (r.get("ingredients") or "").strip()
                    if ings and is_valid_inci_list(ings):
                        if url:
                            cache[url] = ings
                        if title and title not in cache:
                            cache[title] = ings
                        clean_t = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ml|g|gm|oz|fl\s*oz)\b|[()\[\]\+]', ' ', title, flags=re.I).strip()
                        clean_t = re.sub(r'\s+', ' ', clean_t)
                        if clean_t and clean_t not in cache:
                            cache[clean_t] = ings
        except Exception as ex:
            logger.warning(f"Error loading historical scrape file {fpath}: {ex}")

    _HISTORICAL_SCRAPES_CACHE = cache
    logger.info(f"Loaded {len(_HISTORICAL_SCRAPES_CACHE)} verified historical store scrape formulations.")
    return _HISTORICAL_SCRAPES_CACHE

def resolve_tier1_store_tab(product: Dict[str, Any], historical_index: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Tier 1: Checks dedicated ingredients tab from current scrape or Jan 2026 historical scrape.
    Only accepts if source had a dedicated ingredients tab and passes is_valid_inci_list.
    """
    curr_raw = product.get("ingredients_raw")
    curr_src = product.get("ingredient_source")

    # Only accept current product ingredients if it was verified from a dedicated store tab
    if curr_raw and is_valid_inci_list(curr_raw) and curr_src == "store_page_tab":
        return {
            "ingredients_raw": curr_raw.strip(),
            "method": "store_page_tab"
        }

    # Check historical store scrape index by URL, Title, or Clean Title
    url = (product.get("product_url") or "").strip()
    title = (product.get("title") or "").strip().lower()
    clean_t = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ml|g|gm|oz|fl\s*oz)\b|[()\[\]\+]', ' ', title, flags=re.I).strip()
    clean_t = re.sub(r'\s+', ' ', clean_t)

    for key in (url, title, clean_t):
        if key and key in historical_index:
            hist_ings = historical_index[key]
            if is_valid_inci_list(hist_ings):
                return {
                    "ingredients_raw": hist_ings.strip(),
                    "method": "historical_store_scrape"
                }

    return None

# ==============================================================================
# TIER 2: OPENBEAUTY API
# ==============================================================================

_OPENBEAUTY_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}
_OPENBEAUTY_LOCK = threading.Lock()

def resolve_tier2_openbeauty(brand: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Tier 2: Queries Open Beauty Facts API with Gate 1, 2, and 3 validation.
    """
    if not brand or not title:
        return None

    clean_t = re.sub(r'\b\d+\s*(?:ml|g|oz|fl\s*oz)\b|[()\[\]\+]', ' ', title, flags=re.I).strip()
    words = clean_t.split()[:4]
    search_key = f"{brand.lower().strip()}_{' '.join(words).lower()}"

    with _OPENBEAUTY_LOCK:
        if search_key in _OPENBEAUTY_CACHE:
            return _OPENBEAUTY_CACHE[search_key]

    try:
        match = query_open_beauty_facts(brand, title)
        if match and match.get("ingredients") and is_valid_inci_list(match["ingredients"]):
            res = {
                "ingredients_raw": match["ingredients"].strip(),
                "matched_name": match.get("product_name"),
                "method": "openbeauty_api"
            }
            with _OPENBEAUTY_LOCK:
                _OPENBEAUTY_CACHE[search_key] = res
            return res
    except Exception as e:
        logger.debug(f"OpenBeauty query failed for {brand} | {title}: {e}")

    with _OPENBEAUTY_LOCK:
        _OPENBEAUTY_CACHE[search_key] = None
    return None

# ==============================================================================
# TIER 3: HIGH SKINSAFE MATCH
# ==============================================================================

_SKINSAFE_BRAND_INDEX: Optional[Dict[str, List[Tuple]]] = None
_SKINSAFE_INDEX_LOCK = threading.Lock()

def load_skinsafe_reference_index(conn=None) -> Dict[str, List[Tuple]]:
    """Loads and indexes SkinSAFE reference products by brand in memory for instant thread-safe lookups."""
    global _SKINSAFE_BRAND_INDEX
    with _SKINSAFE_INDEX_LOCK:
        if _SKINSAFE_BRAND_INDEX is not None:
            return _SKINSAFE_BRAND_INDEX

        should_close = False
        if conn is None:
            conn = get_connection(DB_PATH)
            should_close = True

        try:
            cur = conn.cursor()
            cur.execute("SELECT id, product_name, brand, ingredients FROM inci_reference WHERE source = 'skinsafe';")
            rows = cur.fetchall()
            index: Dict[str, List[Tuple]] = {}
            for r in rows:
                c_id, p_name, b_name, ings = r[0], r[1], r[2], r[3]
                if not b_name:
                    continue
                clean_b = re.sub(r'[^a-z0-9]', '', b_name.lower())
                if clean_b:
                    index.setdefault(clean_b, []).append((c_id, p_name, b_name, ings))
            _SKINSAFE_BRAND_INDEX = index
            logger.info(f"Loaded {len(rows)} SkinSAFE reference products across {len(_SKINSAFE_BRAND_INDEX)} distinct brands into memory.")
            return _SKINSAFE_BRAND_INDEX
        finally:
            if should_close:
                conn.close()

def resolve_tier3_skinsafe_with_candidate(brand: str, title: str, conn=None) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Tier 3 & Tier 7 Candidate Resolver:
    - If best candidate overlap >= 0.50: returns (high_match_dict, None) for instant Tier 3 resolution.
    - If 0.20 <= best candidate overlap < 0.50: returns (None, ambiguous_candidate_dict) as fallback candidate for Tier 7.
    - Otherwise returns (None, None).
    """
    if not brand or not title:
        return None, None

    ref_index = load_skinsafe_reference_index(conn=conn)
    clean_b = re.sub(r'[^a-z0-9]', '', brand.lower())
    if not clean_b:
        return None, None

    candidates = ref_index.get(clean_b, [])
    if not candidates:
        for k, v in ref_index.items():
            if clean_b in k or k in clean_b:
                candidates.extend(v)
                if len(candidates) > 150:
                    break

    if not candidates:
        return None, None

    import unicodedata
    def norm_ascii(s: str) -> str:
        if not s: return ""
        return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('utf-8').lower()

    t_norm = norm_ascii(title)
    v_prod = get_vehicles(t_norm)
    words_p = clean_title_tokens(t_norm, brand)

    best_cand = None
    best_overlap = 0.0

    # Fatal cross-variant keywords that must not cross over
    variant_pairs = [
        ({'sebium'}, {'sensibio'}),
        ({'green tea'}, {'pomegranate'}),
        ({'bean'}, {'centella'}),
        ({'succinic'}, {'deep pore'}),
        ({'acti-repair'}, {'acti-clear'}),
        ({'tone-up', 'tone up'}, {'soothing sun cream'}),
        ({'balm'}, {'pad', 'pads'}),
        ({'cream'}, {'mist'}),
        ({'wash'}, {'gel'})
    ]

    for row in candidates:
        c_id, p_name, b_name, ings = row[0], row[1], row[2], row[3]

        if not ings or not is_valid_inci_list(ings):
            continue

        p_norm = norm_ascii(p_name)

        # 1. Vehicle compatibility check
        v_cand = get_vehicles(p_norm)
        if v_prod and v_cand and set(v_prod).isdisjoint(set(v_cand)):
            continue

        # 2. Bundle / set check
        is_bundle_prod = any(k in t_norm for k in ['bundle', 'set', ' duo', 'pack', ' kit', 'piece', 'pcs'])
        is_bundle_cand = any(k in p_norm for k in ['bundle', 'set', ' duo', 'pack', ' kit', 'piece', 'pcs'])
        if is_bundle_prod != is_bundle_cand:
            continue

        # 3. Cross-variant conflict check
        has_conflict = False
        for set_a, set_b in variant_pairs:
            has_a_in_prod = any(k in t_norm for k in set_a)
            has_b_in_cand = any(k in p_norm for k in set_b)
            has_b_in_prod = any(k in t_norm for k in set_b)
            has_a_in_cand = any(k in p_norm for k in set_a)
            if (has_a_in_prod and has_b_in_cand) or (has_b_in_prod and has_a_in_cand):
                has_conflict = True
                break
        if has_conflict:
            continue

        # 4. Token overlap
        words_c = clean_title_tokens(p_norm, b_name)
        overlap = len(words_p & words_c) / max(len(words_p | words_c), 1)

        if overlap > best_overlap:
            best_overlap = overlap
            best_cand = (c_id, p_name, b_name, ings, overlap)

    # High confidence threshold for Tier 3
    if best_cand and best_overlap >= 0.50:
        return {
            "ingredients_raw": best_cand[3].strip(),
            "inci_matched_id": best_cand[0],
            "inci_matched_name": best_cand[1],
            "confidence": best_cand[4],
            "method": "skinsafe_high_match"
        }, None

    # Ambiguity zone threshold for Tier 7 (Jev & Gemini fallback)
    if best_cand and best_overlap >= 0.20:
        return None, {
            "id": best_cand[0],
            "product_name": best_cand[1],
            "brand": best_cand[2],
            "ingredients": best_cand[3],
            "confidence": best_cand[4]
        }

    return None, None


def resolve_tier3_skinsafe_high(brand: str, title: str, conn=None) -> Optional[Dict[str, Any]]:
    """
    Tier 3: Strict high-confidence match against SkinSAFE reference catalog.
    Enforces brand alignment, identical cosmetic vehicle, zero variant conflicts,
    and high title overlap (Jaccard >= 0.50).
    """
    high_match, _ = resolve_tier3_skinsafe_with_candidate(brand, title, conn=conn)
    return high_match

# ==============================================================================
# TIER 4: LANGSEARCH WEB SEARCH API
# ==============================================================================

def extract_inci_from_web_text(text: str) -> Optional[str]:
    """Extracts and validates a chemical INCI formulation block from webpage text."""
    if not text or len(text) < 40:
        return None

    patterns = [
        # Explicit INCI header pattern
        r'(?:Ingredients|Full Ingredients|INCI List|INCI|Active Ingredients)\s*[:\-\n]\s*([A-Za-z0-9\s,;.\(\)\[\]\/\-\+%•]{40,2500})',
        # Formulations starting with Aqua / Water and common solvents
        r'(?:Water\/Aqua\/Eau|Aqua\/Water\/Eau|Aqua|Water)[,\s]+(?:Glycerin|Butylene Glycol|Cetearyl Alcohol|Propanediol|Dipropylene Glycol)[A-Za-z0-9\s,;.\(\)\[\]\/\-\+%•]{30,2000}',
        # Plant extract starting formulations
        r'(?:Centella Asiatica Extract|Lactobacillus\/Soybean Ferment Extract|Aloe Barbadensis Leaf Juice)[A-Za-z0-9\s,;.\(\)\[\]\/\-\+%•]{30,2000}'
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1) if m.groups() else m.group(0)
            # Cut off at common footer/section breaks
            for stop in [
                'How to use', 'Directions', 'Caution', 'Warning', 'Disclaimer',
                'Product details', 'About the brand', 'Customer reviews', 'Reviews',
                'Shipping', 'Frequently asked', 'Related products', 'For the latest information',
                'Frequently bought', 'This item', 'Please refer to', 'Read more', 'Check packaging'
            ]:
                idx = candidate.lower().find(stop.lower())
                if idx > 35:
                    candidate = candidate[:idx]

            candidate = re.sub(r'\s+', ' ', candidate).strip('.,;:- \n\t')
            if is_valid_inci_list(candidate) and len(candidate.split(',')) >= 3:
                return candidate

    return None

_LANGSEARCH_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}
_LANGSEARCH_LOCK = threading.Lock()
_LAST_LANGSEARCH_TIME = 0.0
_LANGSEARCH_EXHAUSTED = False

def _throttle_langsearch():
    global _LAST_LANGSEARCH_TIME
    with _LANGSEARCH_LOCK:
        now = time.time()
        elapsed = now - _LAST_LANGSEARCH_TIME
        if elapsed < 0.25:
            time.sleep(0.25 - elapsed)
        _LAST_LANGSEARCH_TIME = time.time()

def resolve_tier4_langsearch(brand: str, title: str, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Tier 4: Queries LangSearch Web Search API for '{brand} {title} inci ingredients'
    and extracts authentic INCI formulation from top beauty/retailer results.
    """
    global _LANGSEARCH_EXHAUSTED
    if _LANGSEARCH_EXHAUSTED:
        return None

    if not brand or not title:
        return None

    if not api_key:
        api_key = os.getenv("LANGSEARCH_API_KEY")
    if not api_key:
        return None

    clean_t = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ml|g|gm|oz|fl\s*oz)\b|[()\[\]\+]', ' ', title, flags=re.I).strip()
    cache_key = f"{brand.lower().strip()}_{clean_t.lower()}"
    with _LANGSEARCH_LOCK:
        if cache_key in _LANGSEARCH_CACHE:
            return _LANGSEARCH_CACHE[cache_key]

    _throttle_langsearch()

    query = f"{brand} {clean_t} inci ingredients"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "query": query,
        "count": 5,
        "contents": {"text": True},
        "freshness": "noLimit"
    }

    try:
        req = urllib.request.Request(
            "https://api.langsearch.com/v1/web-search",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("data", {}).get("webPages", {}).get("value", [])

            for p in pages:
                p_name = (p.get("name") or "").lower()
                p_url = (p.get("url") or "").lower()
                p_snippet = (p.get("snippet") or "").lower()
                clean_b = re.sub(r'[^a-z0-9]', '', brand.lower())

                # Gate 1: Brand relevance - brand must appear in page name, url, or snippet
                if clean_b and clean_b not in re.sub(r'[^a-z0-9]', '', p_name) and clean_b not in re.sub(r'[^a-z0-9]', '', p_url) and clean_b not in re.sub(r'[^a-z0-9]', '', p_snippet):
                    continue

                # Gate 2: Semantic product title token check
                title_words = clean_title_tokens(title, brand)
                filler = {'cream', 'gel', 'serum', 'lotion', 'face', 'body', 'skin', 'care', 'cleanser', 'wash', 'moisturizer', 'daily', 'treatment'}
                key_words = title_words - filler
                if key_words:
                    combined = f"{p_name} {p_url} {p_snippet}"
                    if not any(w in combined for w in key_words):
                        continue

                web_text = p.get("text") or ""
                extracted = extract_inci_from_web_text(web_text)
                if extracted:
                    sanitized = sanitize_inci_list(extracted)
                    if not is_valid_inci_list(sanitized) or len(sanitized.split(',')) < 2:
                        continue

                    # Gate 3: Foreign brand cross-contamination guard
                    low_san = sanitized.lower()
                    if 'uriage' in low_san and 'uriage' not in brand.lower():
                        continue
                    if any(z in low_san for z in ['ziaja', 'line jeju', 'jeju']) and 'ziaja' not in brand.lower() and 'jeju' not in title.lower():
                        continue

                    res = {
                        "ingredients_raw": sanitized.strip(),
                        "source_url": p.get("url"),
                        "method": "langsearch_web_search"
                    }
                    with _LANGSEARCH_LOCK:
                        _LANGSEARCH_CACHE[cache_key] = res
                    return res
    except Exception as ex:
        err_str = str(ex).lower()
        if "429" in err_str or "allowance" in err_str or "rate" in err_str:
            _LANGSEARCH_EXHAUSTED = True
            logger.warning(f"LangSearch API allowance exceeded or rate limited ({ex}). Circuit-breaker activated for remainder of run.")
        else:
            logger.debug(f"LangSearch query failed for {query}: {ex}")

    with _LANGSEARCH_LOCK:
        _LANGSEARCH_CACHE[cache_key] = None
    return None

# ==============================================================================
# TIER 5: STORE DESCRIPTION WHITELISTED INGREDIENTS
# ==============================================================================

def resolve_tier5_whitelist_description(description_html: str, conn=None, fallback_raw: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Tier 5: Scans product store description for verified ingredients using SQLite whitelist.
    Requires at least 2 verified chemical/botanical ingredients.
    """
    text_to_scan = description_html or ""
    if (not text_to_scan or len(text_to_scan.strip()) < 20) and fallback_raw:
        text_to_scan = fallback_raw

    if not text_to_scan or len(text_to_scan.strip()) < 10:
        return None
    try:
        extracted = extract_ingredients_via_whitelist(text_to_scan, conn=conn, min_ingredients=2)
        if extracted and extracted.get("ingredients_raw"):
            return {
                "ingredients_raw": extracted["ingredients_raw"].strip(),
                "matched_count": extracted.get("matched_count", 0),
                "matched_ingredients": extracted.get("matched_ingredients", []),
                "method": "whitelist_description"
            }
    except Exception as ex:
        logger.debug(f"Whitelist extraction failed: {ex}")
    return None

# ==============================================================================
# TIER 6: SAME-PRODUCT CLUSTER PROPAGATION
# ==============================================================================

def resolve_tier6_cluster_propagation(product_id: int, canonical_id: Optional[int], brand: str, title: str, conn=None) -> Optional[Dict[str, Any]]:
    """
    Tier 6: Propagates ingredients from a resolved cluster partner in canonical_products
    or matching cross-store listing with identical brand and normalized clean title.
    """
    should_close = False
    if conn is None:
        conn = get_connection(DB_PATH)
        should_close = True

    try:
        cur = conn.cursor()

        # Method A: Via canonical_product_id if set (same cluster entity)
        if canonical_id:
            cur.execute("""
            SELECT id, title, ingredients_raw, ingredients_parsed, actives, ingredient_source
            FROM products
            WHERE canonical_product_id = ? AND id != ?
              AND ingredients_raw IS NOT NULL AND TRIM(ingredients_raw) != ''
              AND ingredient_source NOT IN ('cluster_propagation', 'unmatched')
            LIMIT 5;
            """, (canonical_id, product_id))
            target_v = set(get_vehicles(title))
            for row in cur.fetchall():
                other_id, other_title, other_raw, other_parsed, other_actives, other_src = row[0], row[1], row[2], row[3], row[4], row[5]
                other_v = set(get_vehicles(other_title))
                if target_v and other_v and target_v.isdisjoint(other_v):
                    continue
                if other_raw and is_valid_inci_list(other_raw):
                    return {
                        "ingredients_raw": sanitize_inci_list(other_raw),
                        "ingredients_parsed": other_parsed,
                        "actives": other_actives,
                        "method": "cluster_propagation"
                    }

        # Method B: Matching brand and title tokens across stores
        t_tokens = clean_title_tokens(title, brand)
        target_v = set(get_vehicles(title))
        if brand and len(t_tokens) >= 2:
            cur.execute("""
            SELECT id, title, ingredients_raw, ingredients_parsed, actives, ingredient_source
            FROM products
            WHERE id != ? AND LOWER(brand) = ? 
              AND ingredients_raw IS NOT NULL AND TRIM(ingredients_raw) != ''
              AND ingredient_source NOT IN ('cluster_propagation', 'unmatched')
              AND is_skincare = 1
            LIMIT 30;
            """, (product_id, brand.lower().strip()))
            for r in cur.fetchall():
                other_id, other_title, other_raw, other_parsed, other_actives, other_src = r[0], r[1], r[2], r[3], r[4], r[5]
                other_tokens = clean_title_tokens(other_title, brand)
                overlap = len(t_tokens & other_tokens) / max(len(t_tokens | other_tokens), 1)
                if overlap < 0.70:
                    continue
                other_v = set(get_vehicles(other_title))
                if target_v and other_v and target_v.isdisjoint(other_v):
                    continue
                if other_raw and is_valid_inci_list(other_raw):
                    return {
                        "ingredients_raw": sanitize_inci_list(other_raw),
                        "ingredients_parsed": other_parsed,
                        "actives": other_actives,
                        "method": "cluster_propagation"
                    }

        return None
    finally:
        if should_close:
            conn.close()

# ==============================================================================
# TIER 7: PENDING JEV & PENDING GEMINI FROM TIER 3 (IF TIERS 4, 5, 6 ALL FAIL)
# ==============================================================================

def resolve_tier7_pending_llm(
    product_id: int,
    candidate: Dict[str, Any],
    conn=None,
    write_to_db: bool = True,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Tier 7: Queues ambiguous SkinSAFE candidate from Tier 3 for Jev and Gemini verification
    when Tiers 4, 5, and 6 have all failed to find ingredients.
    Sets gate4_jev_status='pending_jev', gate4_gemini_status='pending_gemini',
    records inci_matched_id and inci_matched_name, and sets ingredient_source='pending_tier7_match'.
    Leaves ingredients_raw as NULL until an LLM verifier (Jev or Gemini) approves it.
    """
    cand_id = candidate["id"]
    cand_name = candidate["product_name"]
    conf = round(candidate.get("confidence", 0.0), 3)

    if not dry_run and conn and product_id and write_to_db:
        cur = conn.cursor()
        cur.execute("""
        UPDATE products
        SET inci_matched_id = ?,
            inci_matched_name = ?,
            inci_token_score = ?,
            gate4_jev_status = 'pending_jev',
            gate4_gemini_status = 'pending_gemini',
            gate4_cross_status = 'pending',
            ingredient_source = 'pending_tier7_match',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """, (cand_id, cand_name, conf, product_id))
        conn.commit()

    return {
        "status": "pending_llm_verification",
        "product_id": product_id,
        "method": "tier7_pending_llm",
        "inci_matched_id": cand_id,
        "inci_matched_name": cand_name,
        "confidence": conf,
        "candidate": candidate
    }

# ==============================================================================
# WATERFALL CONTROLLER & DISPATCHER
# ==============================================================================

def execute_inci_waterfall_for_product(
    product: Dict[str, Any],
    conn=None,
    historical_index: Optional[Dict[str, str]] = None,
    langsearch_enabled: bool = True,
    allow_tier7: bool = True,
    dry_run: bool = False,
    write_to_db: bool = False
) -> Dict[str, Any]:
    """
    Executes the 7-tier waterfall resolution for a single skincare product.
    Returns resolution result with exact method recorded.
    """
    if historical_index is None:
        historical_index = load_historical_scrapes_index()

    p_id = product.get("id")
    title = product.get("title") or ""
    brand = product.get("brand") or ""
    desc_html = product.get("description_html") or ""
    canonical_id = product.get("canonical_product_id")

    res = None
    tier3_candidate = None

    # Tier 1: Current store tab / Historical store scrapes
    res = resolve_tier1_store_tab(product, historical_index)

    # Tier 2: OpenBeauty API
    if not res:
        res = resolve_tier2_openbeauty(brand, title)

    # Tier 3: High SkinSAFE Match (and capture ambiguous candidate for Tier 7)
    if not res:
        res, tier3_candidate = resolve_tier3_skinsafe_with_candidate(brand, title, conn=conn)

    # Tier 4: LangSearch Web Search API
    if not res and langsearch_enabled:
        res = resolve_tier4_langsearch(brand, title)

    curr_raw = product.get("ingredients_raw")

    # Tier 5: Store Page Description Whitelisted Ingredients
    if not res and (desc_html or curr_raw):
        res = resolve_tier5_whitelist_description(desc_html, conn=conn, fallback_raw=curr_raw)

    # Tier 6: Same-Product Cluster Propagation
    if not res:
        res = resolve_tier6_cluster_propagation(p_id, canonical_id, brand, title, conn=conn)

    # Tier 7: Pending Jev & Pending Gemini from Tier 3 if Tiers 4, 5, 6 all fail
    if not res and allow_tier7 and tier3_candidate:
        return resolve_tier7_pending_llm(p_id, tier3_candidate, conn=conn, write_to_db=write_to_db, dry_run=dry_run)

    # If successfully resolved, parse actives and commit
    if res and res.get("ingredients_raw"):
        raw_ings = sanitize_inci_list(res["ingredients_raw"]).strip()
        method = res["method"]

        # Parse normalized tokens and actives if not already pre-parsed
        if res.get("ingredients_parsed") and res.get("actives"):
            parsed_ings = res["ingredients_parsed"]
            actives = res["actives"]
            if isinstance(parsed_ings, list):
                parsed_ings = json.dumps(parsed_ings)
            if isinstance(actives, list):
                actives = json.dumps(actives)
        else:
            p_tokens, act_list = parse_and_normalize_ingredients(raw_ings)
            parsed_ings = json.dumps(p_tokens)
            actives = json.dumps(act_list)

        if not dry_run and conn and p_id and write_to_db:
            cur = conn.cursor()
            cur.execute("""
            UPDATE products
            SET ingredients_raw = ?,
                ingredients_parsed = ?,
                actives = ?,
                ingredient_source = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (raw_ings, parsed_ings, actives, method, p_id))
            conn.commit()

        return {
            "status": "resolved",
            "product_id": p_id,
            "method": method,
            "ingredients_raw": raw_ings,
            "ingredients_parsed": parsed_ings,
            "actives": actives,
            "title": title,
            "brand": brand
        }

    return {
        "status": "unresolved",
        "product_id": p_id,
        "method": None,
        "title": title,
        "brand": brand
    }

_thread_local = threading.local()
_all_thread_connections = []
_conns_lock = threading.Lock()

def get_thread_connection():
    if not hasattr(_thread_local, "conn") or _thread_local.conn is None:
        c = get_connection(DB_PATH)
        c.execute("PRAGMA busy_timeout = 60000;")
        _thread_local.conn = c
        with _conns_lock:
            _all_thread_connections.append(c)
    return _thread_local.conn

def close_all_thread_connections():
    with _conns_lock:
        for c in _all_thread_connections:
            try:
                c.close()
            except Exception:
                pass
        _all_thread_connections.clear()

def run_waterfall_pipeline(
    conn=None,
    limit: Optional[int] = None,
    revalidate_all: bool = False,
    langsearch_enabled: bool = True,
    dry_run: bool = False,
    max_workers: int = 6
) -> Dict[str, Any]:
    """
    Executes the waterfall resolution across all skincare products using a high-performance
    two-pass single-writer architecture:
    - Pass 1: Multi-threaded read execution of Tiers 1-5 with batched main-thread writes.
    - Pass 2: Cross-store cluster propagation (Tier 6) with batch executemany.
    """
    should_close = False
    if conn is None:
        conn = get_connection(DB_PATH)
        conn.execute("PRAGMA busy_timeout = 60000;")
        should_close = True

    try:
        cur = conn.cursor()
        historical_index = load_historical_scrapes_index()
        load_skinsafe_reference_index(conn=conn)
        from pipeline.whitelist_matcher import load_and_compile_whitelist
        load_and_compile_whitelist(conn=conn)

        # Target skincare products
        query = """
        SELECT id, title, brand, product_url, description_html, ingredients_raw, ingredient_source, canonical_product_id
        FROM products
        WHERE is_skincare = 1
        """
        if not revalidate_all:
            # Target missing or unverified legacy/curated
            query += """ AND (
                ingredients_raw IS NULL 
                OR TRIM(ingredients_raw) = '' 
                OR ingredient_source IS NULL 
                OR ingredient_source NOT IN (
                    'store_page_tab', 'historical_store_scrape', 'openbeauty_api', 
                    'skinsafe_high_match', 'langsearch_web_search', 'whitelist_description', 'cluster_propagation'
                )
            )"""
        query += " ORDER BY id ASC"
        if limit:
            query += f" LIMIT {int(limit)}"

        cur.execute(query)
        rows = [dict(r) for r in cur.fetchall()]

        total_to_process = len(rows)
        logger.info(f"--- Starting INCI Waterfall Resolution Pipeline ({total_to_process} products, {max_workers} workers) ---")

        stats = {
            "total_processed": total_to_process,
            "resolved": 0,
            "unresolved": 0,
            "by_method": {}
        }

        # --- PASS 1: Tiers 1-5 (Multi-threaded read, main-thread batched write) ---
        logger.info("--- PASS 1: Executing Tiers 1-5 (Store Tab, OpenBeauty, SkinSAFE High, LangSearch, Whitelist) ---")

        def worker_task(prod):
            t_conn = get_thread_connection()
            return execute_inci_waterfall_for_product(
                prod,
                conn=t_conn,
                historical_index=historical_index,
                langsearch_enabled=langsearch_enabled,
                allow_tier7=False,
                dry_run=dry_run,
                write_to_db=False
            )

        batch_updates = []
        BATCH_SIZE = 50
        processed_so_far = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(worker_task, row): row for row in rows}
            for fut in as_completed(futures):
                processed_so_far += 1
                try:
                    res = fut.result()
                    if res.get("status") == "resolved":
                        stats["resolved"] += 1
                        m = res["method"]
                        stats["by_method"][m] = stats["by_method"].get(m, 0) + 1
                        if not dry_run:
                            batch_updates.append((
                                res["ingredients_raw"],
                                res["ingredients_parsed"],
                                res["actives"],
                                res["method"],
                                res["product_id"]
                            ))
                    else:
                        stats["unresolved"] += 1
                except Exception as ex:
                    stats["unresolved"] += 1
                    logger.warning(f"Error in worker task: {ex}")

                # Flush updates in main thread
                if len(batch_updates) >= BATCH_SIZE and not dry_run:
                    cur.executemany("""
                    UPDATE products
                    SET ingredients_raw = ?,
                        ingredients_parsed = ?,
                        actives = ?,
                        ingredient_source = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """, batch_updates)
                    conn.commit()
                    batch_updates.clear()

                if processed_so_far % 100 == 0 or processed_so_far == total_to_process:
                    logger.info(f"Pass 1 Progress: [{processed_so_far}/{total_to_process}] Resolved: {stats['resolved']} | {stats['by_method']}")

        if batch_updates and not dry_run:
            cur.executemany("""
            UPDATE products
            SET ingredients_raw = ?,
                ingredients_parsed = ?,
                actives = ?,
                ingredient_source = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, batch_updates)
            conn.commit()
            batch_updates.clear()

        # Close all thread-local connections before Pass 2
        close_all_thread_connections()

        # --- PASS 2: Tier 6 Cluster Propagation ---
        logger.info("--- PASS 2: Executing Tier 6 Same-Product Cluster Propagation ---")
        cur.execute("""
        SELECT id, canonical_product_id, brand, title, ingredients_raw, ingredients_parsed, actives
        FROM products
        WHERE is_skincare = 1 AND ingredients_raw IS NOT NULL AND TRIM(ingredients_raw) != ''
          AND ingredient_source NOT IN ('cluster_propagation', 'unmatched');
        """)
        resolved_rows = cur.fetchall()
        canonical_map = {}
        brand_title_map = {}
        for r in resolved_rows:
            cid = r[1]
            b = (r[2] or "").strip().lower()
            t = (r[3] or "").strip()
            raw_i = r[4]
            pars_i = r[5]
            act_i = r[6]
            if not raw_i:
                continue
            if cid and cid not in canonical_map:
                canonical_map[cid] = (raw_i, pars_i, act_i, t)
            clean_t = re.sub(r'\b\d+\s*(?:ml|g|gm|oz|fl\s*oz)\b|[()\[\]\+]', ' ', t, flags=re.I)
            clean_t = re.sub(r'[^a-zA-Z0-9\s]', ' ', clean_t).strip().lower()
            if b and len(clean_t) >= 5 and (b, clean_t) not in brand_title_map:
                brand_title_map[(b, clean_t)] = (raw_i, pars_i, act_i)

        cur.execute("""
        SELECT id, title, brand, canonical_product_id
        FROM products
        WHERE is_skincare = 1 AND (ingredients_raw IS NULL OR TRIM(ingredients_raw) = '');
        """)
        still_empty = [dict(r) for r in cur.fetchall()]
        logger.info(f"Found {len(still_empty)} products still missing ingredients for cluster propagation...")

        propagated_updates = []
        for p in still_empty:
            matched = None
            cid = p.get("canonical_product_id")
            title = p.get("title") or ""
            target_v = set(get_vehicles(title))

            if cid and cid in canonical_map:
                cand_raw, cand_pars, cand_act, cand_title = canonical_map[cid]
                cand_v = set(get_vehicles(cand_title))
                if not (target_v and cand_v and target_v.isdisjoint(cand_v)):
                    matched = (cand_raw, cand_pars, cand_act)
            else:
                b = (p.get("brand") or "").strip().lower()
                clean_t = re.sub(r'\b\d+\s*(?:ml|g|gm|oz|fl\s*oz)\b|[()\[\]\+]', ' ', title, flags=re.I)
                clean_t = re.sub(r'[^a-zA-Z0-9\s]', ' ', clean_t).strip().lower()
                if b and len(clean_t) >= 5 and (b, clean_t) in brand_title_map:
                    matched = brand_title_map[(b, clean_t)]

            if matched:
                raw_ings, pars_ings, acts = matched
                raw_ings = sanitize_inci_list(raw_ings)
                if not pars_ings or not acts:
                    p_tokens, act_list = parse_and_normalize_ingredients(raw_ings)
                    pars_ings = json.dumps(p_tokens)
                    acts = json.dumps(act_list)
                elif isinstance(pars_ings, list):
                    pars_ings = json.dumps(pars_ings)
                elif isinstance(acts, list):
                    acts = json.dumps(acts)

                if not dry_run:
                    propagated_updates.append((
                        raw_ings,
                        pars_ings,
                        acts,
                        p["id"]
                    ))
                stats["resolved"] += 1
                stats["unresolved"] = max(0, stats["unresolved"] - 1)
                stats["by_method"]["cluster_propagation"] = stats["by_method"].get("cluster_propagation", 0) + 1

        if propagated_updates and not dry_run:
            cur.executemany("""
            UPDATE products
            SET ingredients_raw = ?,
                ingredients_parsed = ?,
                actives = ?,
                ingredient_source = 'cluster_propagation',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, propagated_updates)
            conn.commit()

        logger.info(f"Pass 2 Complete: Propagated {len(propagated_updates)} ingredients via cluster propagation.")

        # --- PASS 3: Tier 7 Pending Jev & Pending Gemini from Tier 3 ---
        logger.info("--- PASS 3: Executing Tier 7 Pending Jev & Pending Gemini Arbitration ---")
        cur.execute("""
        SELECT id, title, brand
        FROM products
        WHERE is_skincare = 1 AND (ingredients_raw IS NULL OR TRIM(ingredients_raw) = '');
        """)
        empty_after_tier6 = [dict(r) for r in cur.fetchall()]
        logger.info(f"Evaluating {len(empty_after_tier6)} products still empty after Tiers 1-6 for Tier 7 LLM queue...")

        tier7_updates = []
        for p in empty_after_tier6:
            pid = p["id"]
            brand = p.get("brand") or ""
            title = p.get("title") or ""
            _, tier3_cand = resolve_tier3_skinsafe_with_candidate(brand, title, conn=conn)
            if tier3_cand:
                cand_id = tier3_cand["id"]
                cand_name = tier3_cand["product_name"]
                conf = round(tier3_cand.get("confidence", 0.0), 3)
                if not dry_run:
                    tier7_updates.append((cand_id, cand_name, conf, pid))
                stats["resolved"] += 1
                stats["unresolved"] = max(0, stats["unresolved"] - 1)
                stats["by_method"]["tier7_pending_llm"] = stats["by_method"].get("tier7_pending_llm", 0) + 1

        if tier7_updates and not dry_run:
            cur.executemany("""
            UPDATE products
            SET inci_matched_id = ?,
                inci_matched_name = ?,
                inci_token_score = ?,
                gate4_jev_status = 'pending_jev',
                gate4_gemini_status = 'pending_gemini',
                gate4_cross_status = 'pending',
                ingredient_source = 'pending_tier7_match',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, tier7_updates)
            conn.commit()

        logger.info(f"Pass 3 Complete: Queued {len(tier7_updates)} products for Jev & Gemini semantic verification.")
        logger.info(f"--- INCI Waterfall Resolution Pipeline Complete ---")
        logger.info(f"Final Stats: Resolved {stats['resolved']}/{total_to_process} ({stats['resolved']/max(total_to_process, 1)*100:.1f}%) | Breakdown: {stats['by_method']}")
        return stats

    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_waterfall_pipeline(limit=100)
