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
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except Exception:
    pass

from pipeline.db_manager import get_connection, DB_PATH
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg
from pipeline.inci_matcher import (
    parse_and_normalize_ingredients, is_valid_inci_list,
    query_open_beauty_facts, CosmeticCompatibilityValidator,
    clean_product_title
)
from pipeline.whitelist_matcher import extract_ingredients_via_whitelist
from pipeline.identity_gates import validate_identity_gates
from pipeline.formulation_service import FormulationRepository, FormulationCandidate
from pipeline.inci_tokenizer import tokenize_inci_string

logger = logging.getLogger("skincare_pipeline.waterfall_resolver")
formulation_repo = FormulationRepository(DB_PATH)

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
    was_truncated = tokenize_inci_string(s).is_truncated

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
    if was_truncated:
        s += ', ...'
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

    # Check dedicated description HTML if available
    desc_html = product.get("description_html")
    if desc_html:
        from pipeline.inci_matcher import extract_ingredients_from_html
        extracted = extract_ingredients_from_html(desc_html)
        if extracted and is_valid_inci_list(extracted):
            return {
                "ingredients_raw": extracted.strip(),
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
    words = clean_t.split()
    search_key = f"{brand.lower().strip()}_{' '.join(words).lower()}"

    with _OPENBEAUTY_LOCK:
        if search_key in _OPENBEAUTY_CACHE:
            return _OPENBEAUTY_CACHE[search_key]

    try:
        match = query_open_beauty_facts(brand, title)
        if match and match.get("ingredients") and is_valid_inci_list(match["ingredients"]):
            compatible, _ = validate_identity_gates(title, match.get("product_name") or "")
            if not compatible:
                return None
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
            cur.execute("SELECT id, product_name, brand, ingredients FROM inci_reference WHERE (source = 'skinsafe' OR source IS NULL) AND ingredients IS NOT NULL AND TRIM(ingredients) != '';")
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

        # 3. Comprehensive hard identity gates (SPF rating, potency %, day/night, fragrance, lines)
        is_compat, reason = validate_identity_gates(title, p_name, brand, b_name)
        if not is_compat:
            continue

        # 4. Cross-variant conflict check
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

        # 5. Token overlap
        words_c = clean_title_tokens(p_norm, b_name)
        overlap = len(words_p & words_c) / max(len(words_p | words_c), 1)

        if overlap > best_overlap:
            best_overlap = overlap
            best_cand = (c_id, p_name, b_name, ings, overlap)

    # High confidence threshold for Tier 3 auto-approval (0.80 Jaccard + hard identity gates)
    if best_cand and best_overlap >= 0.80:
        return {
            "ingredients_raw": best_cand[3].strip(),
            "inci_matched_id": best_cand[0],
            "inci_matched_name": best_cand[1],
            "confidence": best_cand[4],
            "method": "skinsafe_high_match"
        }, None

    # Ambiguity zone threshold for Tier 7 (Jev & Gemini fallback)
    if best_cand and best_overlap >= 0.30:
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

PREFERRED_SEARCH_DOMAINS = {
    "inkeedecoder.com", "www.inkeedecoder.com",
    "incidecoder.com", "www.incidecoder.com"
}

BLACKLISTED_SEARCH_DOMAINS = {
    "incibeauty.com", "www.incibeauty.com", "m.incibeauty.com",
    "fr.incibeauty.com", "es.incibeauty.com", "de.incibeauty.com", "it.incibeauty.com"
}

def is_blacklisted_search_url(url: str) -> bool:
    """Checks whether URL belongs to blacklisted or sabotaged cosmetic directories (e.g. incibeauty)."""
    if not url:
        return False
    u = url.lower()
    for d in BLACKLISTED_SEARCH_DOMAINS:
        if d in u:
            return True
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        if any(netloc == d or netloc.endswith("." + d) for d in BLACKLISTED_SEARCH_DOMAINS):
            return True
    except Exception:
        pass
    return False

def is_preferred_search_url(url: str) -> bool:
    """Checks whether URL belongs to preferred cosmetic decoder databases (e.g. inkeedecoder / incidecoder)."""
    if not url:
        return False
    u = url.lower()
    for d in PREFERRED_SEARCH_DOMAINS:
        if d in u:
            return True
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        if any(netloc == d or netloc.endswith("." + d) for d in PREFERRED_SEARCH_DOMAINS):
            return True
    except Exception:
        pass
    return False

def extract_inci_from_web_text(text: str) -> Optional[str]:
    """Extracts and validates a chemical INCI formulation block from webpage text."""
    if not text or len(text) < 40:
        return None

    # Immediate rejection if text contains incibeauty sabotage disclaimer or asterisk masking
    low_text = text.lower()
    if '***' in text:
        return None
    if any(phrase in low_text for phrase in [
        'deliberately hidden',
        'displayed in alphabetical order',
        'exact composition',
        'please use our application'
    ]):
        return None

    patterns = [
        # Explicit INKEEDecoder & INCI header pattern
        r'(?:ingredients\s+explained|full\s+ingredients?|inci\s+list|inci|active\s+ingredients?)\s*[:\-\n]\s*([A-Za-z0-9\s,;.\(\)\[\]\/\-\+%•]{30,2500})',
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

            candidate = re.sub(r'\s+', ' ', candidate).strip(',;:- \n\t')
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
    Prioritizes INKEEDecoder (inkeedecoder.com) and strictly blacklists incibeauty.com.
    Strictly rate limited to 5 RPS, 1,000,000 TPM, and 10,000,000 daily tokens.
    """
    from pipeline.rate_limiter import langsearch_limiter

    quota_status = langsearch_limiter.get_quota_status()
    if quota_status.get("is_daily_exhausted"):
        logger.debug(
            f"LangSearch skipped: Daily allowance ({quota_status['tokens_used_today']:,}/{quota_status['daily_token_limit']:,} tokens, "
            f"{quota_status['daily_used_percentage']}%) used. Resets in {quota_status['resets_in_human']} at 00:00 UTC."
        )
        return None

    if not brand or not title:
        return None

    if not api_key:
        api_key = os.getenv("LANGSEARCH_API_KEY") or cfg.LANGSEARCH_API_KEY
    if not api_key:
        return None

    clean_t = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ml|g|gm|oz|fl\s*oz)\b|[()\[\]\+]', ' ', title, flags=re.I).strip()
    cache_key = f"{brand.lower().strip()}_{clean_t.lower()}"
    with _LANGSEARCH_LOCK:
        if cache_key in _LANGSEARCH_CACHE:
            return _LANGSEARCH_CACHE[cache_key]

    # Acquire rate limit slot (enforcing 5 RPS and 1M TPM)
    acquired = langsearch_limiter.acquire(estimated_tokens=1500)
    if not acquired:
        return None

    query = f"{brand} {clean_t} inkeedecoder inci ingredients"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "query": query,
        "count": 7,
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
        with urllib.request.urlopen(req, timeout=cfg.LANGSEARCH_TIMEOUT_SECONDS) as resp:
            resp_bytes = resp.read()
            resp_headers = dict(resp.headers)
            data = json.loads(resp_bytes.decode("utf-8"))

            # Token tracking: extract usage object or estimate accurately
            usage = data.get("usage") or data.get("data", {}).get("usage") or {}
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)

            pages = data.get("data", {}).get("webPages", {}).get("value", [])
            if in_tok == 0 and out_tok == 0:
                in_tok = len(query.split()) * 2
                tot_chars = sum(len(p.get("text", "") or p.get("snippet", "")) for p in pages)
                out_tok = int(tot_chars / 4.0)

            langsearch_limiter.record_usage(input_tokens=in_tok, output_tokens=out_tok)

            # Filter out blacklisted domains (e.g. incibeauty.com) and sabotage content
            candidate_pages = []
            for p in pages:
                p_url = p.get("url") or ""
                p_text = p.get("text") or ""
                p_snippet = p.get("snippet") or ""

                if is_blacklisted_search_url(p_url):
                    logger.debug(f"LangSearch skipped blacklisted URL: {p_url}")
                    continue

                combined_lower = f"{p_text} {p_snippet}".lower()
                if '***' in p_text or '***' in p_snippet or any(phrase in combined_lower for phrase in [
                    'deliberately hidden',
                    'displayed in alphabetical order',
                    'exact composition',
                    'please use our application'
                ]):
                    logger.debug(f"LangSearch skipped sabotaged/masked formulation at {p_url}")
                    continue

                candidate_pages.append(p)

            # Prioritize INKEEDecoder / INCIDecoder pages at Rank 0
            candidate_pages.sort(key=lambda p: 0 if is_preferred_search_url(p.get("url") or "") else 1)

            for p in candidate_pages:
                p_name = (p.get("name") or "").lower()
                p_url = (p.get("url") or "").lower()
                p_snippet = (p.get("snippet") or "").lower()
                clean_b = re.sub(r'[^a-z0-9]', '', brand.lower())

                # Gate 1: Brand relevance - brand must appear in page name, url, or snippet
                if clean_b and clean_b not in re.sub(r'[^a-z0-9]', '', p_name) and clean_b not in re.sub(r'[^a-z0-9]', '', p_url) and clean_b not in re.sub(r'[^a-z0-9]', '', p_snippet):
                    continue

                compatible, _ = validate_identity_gates(title, p_name)
                if not compatible:
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

                    is_inkee = is_preferred_search_url(p.get("url") or "")
                    res = {
                        "ingredients_raw": sanitized.strip(),
                        "source_url": p.get("url"),
                        "is_inkeedecoder": is_inkee,
                        "method": "langsearch_web_search"
                    }
                    with _LANGSEARCH_LOCK:
                        _LANGSEARCH_CACHE[cache_key] = res
                    return res
    except urllib.error.HTTPError as http_err:
        err_body = ""
        try:
            err_body = http_err.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if http_err.code == 429:
            langsearch_limiter.record_429_exhaustion(response_body=err_body, headers=dict(http_err.headers))
        else:
            logger.warning(f"LangSearch HTTP {http_err.code} error: {http_err.reason} - {err_body[:200]}")
    except Exception as ex:
        err_str = str(ex).lower()
        if "429" in err_str:
            langsearch_limiter.record_429_exhaustion(response_body=str(ex))
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
                "confidence": 0.70,
                "method": "whitelist_description"
            }
    except Exception as ex:
        logger.debug(f"Whitelist extraction failed: {ex}")
    return None

# ==============================================================================
# TIER 6: SAME-PRODUCT CLUSTER PROPAGATION
# ==============================================================================

def resolve_tier6_cluster_propagation(product_id: int, canonical_id: Optional[int], brand: str, title: str, conn=None) -> Optional[Dict[str, Any]]:
    """Retired: canonical formulations cannot be inferred from cluster neighbors."""
    return None


def resolve_tier7_pending_llm(
    product_id: int,
    candidate: Dict[str, Any],
    conn=None,
    write_to_db: bool = True,
    dry_run: bool = False,
    queue_jev: bool = True,
    queue_gemini: bool = True
) -> Optional[Dict[str, Any]]:
    """
    Tier 7: Queues ambiguous SkinSAFE candidate from Tier 3 for Jev and/or Gemini verification
    when Tiers 4, 5, and 6 have all failed to find ingredients.
    Selectively sets gate4_jev_status='pending_jev' and gate4_gemini_status='pending_gemini'
    based on separate toggles.
    Leaves ingredients_raw as NULL until an LLM verifier (Jev or Gemini) approves it.
    """
    if not (queue_jev or queue_gemini):
        return None

    cand_id = candidate["id"]
    cand_name = candidate["product_name"]
    conf = round(candidate.get("confidence", 0.0), 3)

    jev_val = 'pending_jev' if queue_jev else None
    gemini_val = 'pending_gemini' if queue_gemini else None

    if not dry_run and conn and product_id and write_to_db:
        cur = conn.cursor()
        cur.execute("""
        UPDATE products
        SET inci_matched_id = ?,
            inci_matched_name = ?,
            inci_token_score = ?,
            gate4_jev_status = CASE WHEN ? IS NOT NULL THEN ? ELSE gate4_jev_status END,
            gate4_gemini_status = CASE WHEN ? IS NOT NULL THEN ? ELSE gate4_gemini_status END,
            gate4_cross_status = 'pending',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """, (cand_id, cand_name, conf, jev_val, jev_val, gemini_val, gemini_val, product_id))
        conn.commit()

    return {
        "status": "pending_llm_verification",
        "product_id": product_id,
        "method": "tier7_pending_llm",
        "inci_matched_id": cand_id,
        "inci_matched_name": cand_name,
        "confidence": conf,
        "candidate": candidate,
        "queue_jev": queue_jev,
        "queue_gemini": queue_gemini
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
    queue_jev: bool = True,
    queue_gemini: bool = True,
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

    # Tier 6 is deprecated & eliminated: Cross-store cluster propagation violates canonical product integrity.

    # Tier 7: Pending Jev & Pending Gemini from Tier 3 if Tiers 4, 5 all fail
    if not res and allow_tier7 and (queue_jev or queue_gemini) and tier3_candidate:
        return resolve_tier7_pending_llm(
            p_id, tier3_candidate, conn=conn, write_to_db=write_to_db, dry_run=dry_run,
            queue_jev=queue_jev, queue_gemini=queue_gemini
        )

    # If successfully resolved, parse actives and publish formulation
    if res and res.get("ingredients_raw"):
        raw_ings = sanitize_inci_list(res["ingredients_raw"]).strip()
        method = res["method"]

        # Parse normalized tokens and actives using chemical-aware tokenizer
        from pipeline.inci_tokenizer import tokenize_inci_string
        tok_res = tokenize_inci_string(raw_ings, product_title=title)
        p_tokens = tok_res.tokens
        act_list = tok_res.declared_actives
        parsed_ings = json.dumps(p_tokens)
        actives = json.dumps(act_list)

        source_url = res.get("source_url")
        conf_val = float(res.get("confidence", 0.85))
        is_partial = (method == "whitelist_description" or tok_res.completeness == "partial")
        completeness = "partial" if is_partial else tok_res.completeness

        published = None
        if not dry_run and conn and write_to_db:
            published = False
            # 1. Authoritative publication to canonical_products & inci_audit_evidence
            if canonical_id:
                cand_obj = FormulationCandidate(
                    canonical_product_id=canonical_id,
                    source_name=method,
                    raw_ingredients=raw_ings,
                    parsed_tokens=p_tokens,
                    actives=act_list,
                    confidence_score=conf_val,
                    evidence_url=source_url,
                    source_record_id=str(p_id) if p_id else None,
                    completeness=completeness,
                    validation_flags={"method": method, "is_truncated": tok_res.is_truncated, "origin_product_id": p_id},
                    inci_matched_id=res.get("inci_matched_id"),
                    inci_matched_name=res.get("inci_matched_name")
                )
                published = formulation_repo.publish_formulation(
                    candidate=cand_obj,
                    approval_reason=f"Resolved via {method} in waterfall pipeline.",
                    force=False,
                    conn=conn
                )

            # 2. Update offer-level telemetry in products table
            if p_id:
                cur = conn.cursor()
                cur.execute("""
                UPDATE products
                SET inci_matched_id = COALESCE(?, inci_matched_id),
                    inci_matched_name = COALESCE(?, inci_matched_name),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (res.get("inci_matched_id"), res.get("inci_matched_name"), p_id))
                conn.commit()

        return {
            "status": "candidate_only" if published is False else "resolved",
            "published": published,
            "product_id": p_id,
            "method": method,
            "ingredients_raw": raw_ings,
            "ingredients_parsed": parsed_ings,
            "actives": actives,
            "ingredient_source_url": source_url,
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

# ==============================================================================
# MULTI-TIER COMPARATOR & ADMIN RESOLUTION ARBITRATION
# ==============================================================================

def execute_all_tiers_for_product(
    product: Dict[str, Any],
    conn=None,
    historical_index: Optional[Dict[str, str]] = None,
    langsearch_enabled: bool = True,
    write_to_db: bool = True
) -> Dict[str, Any]:
    """
    Executes all 7 tiers of INCI ingredient resolution independently for a single product.
    Does not halt upon finding an earlier tier, allowing side-by-side comparison.
    Captures full metadata, normalized tokens, and clinical actives for each tier.
    Stores the comparative audit payload in products.tier_resolutions.
    """
    should_close = False
    if conn is None:
        conn = get_connection(DB_PATH)
        should_close = True

    try:
        if historical_index is None:
            historical_index = load_historical_scrapes_index()

        p_id = product.get("id")
        title = product.get("title") or ""
        brand = product.get("brand") or ""
        desc_html = product.get("description_html") or ""
        canonical_id = product.get("canonical_product_id")
        current_source = product.get("ingredient_source")
        current_raw = product.get("ingredients_raw")

        def _format_tier(
            tier_num: int,
            code: str,
            name: str,
            res: Optional[Dict[str, Any]],
            default_details: str = "",
            is_pending: bool = False,
            extra_meta: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]:
            meta = extra_meta or {}
            if res and res.get("ingredients_raw"):
                raw = sanitize_inci_list(res["ingredients_raw"]).strip()
                tokens, acts = parse_and_normalize_ingredients(raw)
                def_conf = 0.95 if tier_num == 1 else (0.90 if tier_num == 2 else (0.85 if tier_num == 4 else (0.60 if tier_num == 5 else 0.75)))
                conf_val = round(float(res.get("confidence", def_conf)), 3)
                return {
                    "tier": tier_num,
                    "code": code,
                    "name": name,
                    "status": "resolved",
                    "ingredients_raw": raw,
                    "parsed_tokens": tokens,
                    "parsed_count": len(tokens),
                    "actives": acts,
                    "actives_count": len(acts),
                    "confidence": conf_val,
                    "method": res.get("method", code),
                    "details": res.get("details") or default_details,
                    "metadata": {**meta, **{k: v for k, v in res.items() if k not in ("ingredients_raw", "ingredients_parsed", "actives", "parsed_tokens")}}
                }
            elif is_pending and res and res.get("candidate"):
                cand = res["candidate"]
                raw = (cand.get("ingredients") or "").strip()
                tokens, acts = parse_and_normalize_ingredients(raw) if raw else ([], [])
                return {
                    "tier": tier_num,
                    "code": code,
                    "name": name,
                    "status": "pending_llm",
                    "ingredients_raw": raw,
                    "parsed_tokens": tokens,
                    "parsed_count": len(tokens),
                    "actives": acts,
                    "actives_count": len(acts),
                    "confidence": round(float(cand.get("confidence", 0.0)), 3),
                    "method": "pending_tier7_match",
                    "candidate_id": cand.get("id"),
                    "candidate_name": cand.get("product_name"),
                    "details": f"Ambiguous SkinSAFE match '{cand.get('product_name')}' (Overlap: {cand.get('confidence', 0):.2f}). Queued for Jev & Gemini arbitration.",
                    "metadata": {**meta, "candidate_id": cand.get("id"), "candidate_name": cand.get("product_name")}
                }
            else:
                return {
                    "tier": tier_num,
                    "code": code,
                    "name": name,
                    "status": "skipped" if "disabled" in default_details.lower() else "not_found",
                    "ingredients_raw": None,
                    "parsed_tokens": [],
                    "parsed_count": 0,
                    "actives": [],
                    "actives_count": 0,
                    "confidence": 0.0,
                    "method": None,
                    "details": default_details,
                    "metadata": meta
                }

        # 1. Tier 1: Store Dedicated Tab / Historical Scrape
        res_t1 = resolve_tier1_store_tab(product, historical_index)
        t1 = _format_tier(
            1, "tier1", "Store Dedicated Tab / Historical Scrape",
            res_t1,
            default_details="Found in store dedicated tab / historical scrape (Bashrety Jan 10-12, 2026)" if res_t1 else "No dedicated ingredients tab in current store listing or historical scrape."
        )

        # 2. Tier 2: OpenBeauty API
        res_t2 = resolve_tier2_openbeauty(brand, title)
        t2 = _format_tier(
            2, "tier2", "OpenBeauty API",
            res_t2,
            default_details=f"Matched OpenBeauty product '{res_t2.get('matched_name')}'" if res_t2 else "No match found in OpenBeauty database."
        )

        # 3. Tier 3: SkinSAFE High-Confidence Match
        high_t3, cand_t3 = resolve_tier3_skinsafe_with_candidate(brand, title, conn=conn)
        t3 = _format_tier(
            3, "tier3", "SkinSAFE High-Confidence Direct Match",
            high_t3,
            default_details=f"High-confidence match: '{high_t3.get('inci_matched_name')}' (Score: {high_t3.get('confidence', 0):.2f})" if high_t3 else "No high-confidence SkinSAFE match (overlap < 0.50 or vehicle mismatch).",
            extra_meta={"candidate_id": high_t3.get("inci_matched_id")} if high_t3 else {}
        )

        # 4. Tier 4: LangSearch Web Search API
        from pipeline.rate_limiter import langsearch_limiter
        quota_status = langsearch_limiter.get_quota_status()

        if not langsearch_enabled:
            t4 = _format_tier(
                4, "tier4", "LangSearch Web Search INCI",
                None,
                default_details="LangSearch web search disabled."
            )
        elif quota_status.get("is_daily_exhausted"):
            t4 = _format_tier(
                4, "tier4", "LangSearch Web Search INCI",
                None,
                default_details=f"LangSearch skipped: Daily allowance ({quota_status['tokens_used_today']:,}/{quota_status['daily_token_limit']:,} tokens, {quota_status['daily_used_percentage']}%) used. Resets in {quota_status['resets_in_human']} at 00:00 UTC.",
                extra_meta={"quota_status": quota_status}
            )
        else:
            res_t4 = resolve_tier4_langsearch(brand, title)
            t4_source_url = res_t4.get("source_url") if res_t4 else None
            t4_is_inkee = res_t4.get("is_inkeedecoder", False) if res_t4 else False
            if res_t4:
                prefix = f"🌿 INKEEDecoder ({t4_source_url})" if t4_is_inkee else (t4_source_url or "web search")
                t4_details = f"Extracted authentic INCI list from {prefix}."
            else:
                t4_details = "No valid INCI list found in top search results."
            t4 = _format_tier(
                4, "tier4", "LangSearch Web Search INCI",
                res_t4,
                default_details=t4_details,
                extra_meta={"source_url": t4_source_url, "is_inkeedecoder": t4_is_inkee, "quota_status": quota_status}
            )

        # 5. Tier 5: Store Description Whitelist Extraction
        res_t5 = resolve_tier5_whitelist_description(desc_html, conn=conn)
        cnt5 = (res_t5.get("matched_count") or len(res_t5.get("matched_ingredients", []))) if res_t5 else 0
        t5 = _format_tier(
            5, "tier5", "Store Description Whitelist Extraction",
            res_t5,
            default_details=f"Extracted {cnt5} verified ingredients from description text." if res_t5 else "No verified whitelist ingredients found in store description."
        )

        # 6. Tier 6: Same-Product Cluster Propagation (Permanently Retired)
        t6 = {
            "tier": 6,
            "code": "tier6",
            "name": "Same-Product Cluster Propagation (Deprecated)",
            "status": "deprecated",
            "ingredients_raw": None,
            "parsed_tokens": [],
            "parsed_count": 0,
            "actives": [],
            "actives_count": 0,
            "confidence": 0.0,
            "method": "deprecated",
            "details": "Tier 6 Cluster Propagation is permanently retired to ensure 1 product = 1 authoritative canonical INCI.",
            "metadata": {"retired": True}
        }

        # 7. Tier 7: Pending Jev & Gemini Verification from Tier 3
        if cand_t3:
            t7 = _format_tier(
                7, "tier7", "Tier 3 Ambiguous Match (Pending LLM Arbitration)",
                {"candidate": cand_t3},
                is_pending=True,
                extra_meta={"candidate_id": cand_t3.get("id"), "candidate_name": cand_t3.get("product_name")}
            )
        else:
            t7 = _format_tier(
                7, "tier7", "Tier 3 Ambiguous Match (Pending LLM Arbitration)",
                None,
                default_details="No ambiguous SkinSAFE candidate in 0.20-0.49 overlap range."
            )

        # Determine currently active tier
        selected_tier = None
        if current_source in ("historical_store_scrape", "store_tab"):
            selected_tier = "tier1"
        elif current_source == "openbeauty_api":
            selected_tier = "tier2"
        elif current_source in ("skinsafe_high_match", "skinsafe_matched"):
            selected_tier = "tier3"
        elif current_source == "langsearch_web_search":
            selected_tier = "tier4"
        elif current_source in ("whitelist_description", "whitelist_extracted"):
            selected_tier = "tier5"
        elif current_source == "cluster_propagation":
            selected_tier = "tier6"
        elif current_source == "pending_tier7_match":
            selected_tier = "tier7"

        tiers_dict = {
            "tier1": t1,
            "tier2": t2,
            "tier3": t3,
            "tier4": t4,
            "tier5": t5,
            "tier6": t6,
            "tier7": t7
        }

        tier_resolutions = {
            "product_id": p_id,
            "executed_at": datetime.now().isoformat(),
            "selected_tier": selected_tier,
            "tiers": tiers_dict,
            "summary": {
                "total_tiers": 7,
                "resolved_count": sum(1 for t in tiers_dict.values() if t["status"] == "resolved"),
                "pending_count": sum(1 for t in tiers_dict.values() if t["status"] == "pending_llm"),
                "not_found_count": sum(1 for t in tiers_dict.values() if t["status"] == "not_found")
            }
        }

        if write_to_db and conn and p_id:
            cur = conn.cursor()
            cur.execute("""
            UPDATE products
            SET tier_resolutions = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (json.dumps(tier_resolutions), p_id))
            conn.commit()

        return tier_resolutions

    finally:
        if should_close:
            conn.close()


def apply_tier_resolution_for_product(
    product_id: int,
    tier_code: str,
    conn=None,
    custom_raw: Optional[str] = None
) -> Dict[str, Any]:
    """
    Applies a specific tier's outcome as the product's active ingredient formula.
    Publishes authoritative formulation to canonical_products via FormulationRepository.
    Updates offer telemetry and marks selected_tier in products.tier_resolutions.
    """
    if tier_code == "tier6":
        raise ValueError("Tier 6 Cluster Propagation is retired and cannot be applied.")

    should_close = False
    if conn is None:
        conn = get_connection(DB_PATH)
        should_close = True

    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, title, brand, description_html, canonical_product_id, ingredients_raw, ingredient_source, tier_resolutions
        FROM products WHERE id = ?;
        """, (product_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Product #{product_id} not found.")

        prod = dict(row)
        resolutions = None
        if prod.get("tier_resolutions"):
            try:
                resolutions = json.loads(prod["tier_resolutions"])
            except Exception:
                pass

        if not resolutions or "tiers" not in resolutions or tier_code not in resolutions.get("tiers", {}):
            resolutions = execute_all_tiers_for_product(prod, conn=conn, write_to_db=False)

        target_tier = resolutions.get("tiers", {}).get(tier_code)
        if not target_tier:
            raise ValueError(f"Tier '{tier_code}' not found in resolutions.")

        if tier_code == "tier7":
            cand_id = target_tier.get("candidate_id")
            cand_name = target_tier.get("candidate_name")
            cur.execute("""
            UPDATE products
            SET inci_matched_id = ?,
                inci_matched_name = ?,
                gate4_jev_status = 'pending_jev',
                gate4_gemini_status = 'pending_gemini',
                gate4_cross_status = 'pending',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """, (cand_id, cand_name, product_id))
        elif target_tier.get("status") == "resolved" or custom_raw:
            raw = (custom_raw or target_tier.get("ingredients_raw") or "").strip()
            validation = tokenize_inci_string(raw, product_title=prod.get("title"))
            tokens, acts = validation.tokens, validation.declared_actives
            method = target_tier.get("method") or "manual_override"
            inci_id = target_tier.get("metadata", {}).get("inci_matched_id") or target_tier.get("candidate_id")
            inci_name = target_tier.get("metadata", {}).get("inci_matched_name") or target_tier.get("candidate_name")
            source_url = target_tier.get("metadata", {}).get("source_url") or target_tier.get("source_url")

            # Publish authoritative canonical formulation via FormulationRepository
            canon_id = prod.get("canonical_product_id")
            if not canon_id:
                raise ValueError("Link this offer to a canonical product before applying a formulation.")
            if not raw or not tokens:
                raise ValueError("An ingredient formulation is required.")
            if canon_id:
                cand_obj = FormulationCandidate(
                    canonical_product_id=canon_id,
                    source_name=method,
                    raw_ingredients=raw,
                    parsed_tokens=tokens,
                    actives=acts,
                    confidence_score=float(target_tier.get("confidence") or 0.85),
                    evidence_url=source_url,
                    source_record_id=str(product_id),
                    completeness=validation.completeness if method != "whitelist_description" else "partial",
                    validation_flags={"applied_via": "admin_arbitration", "tier_code": tier_code, "origin_product_id": product_id},
                    inci_matched_id=inci_id,
                    inci_matched_name=inci_name
                )
                published = formulation_repo.publish_formulation(
                    candidate=cand_obj,
                    approval_reason=f"Applied {tier_code} via admin manual arbitration.",
                    force=True,
                    conn=conn
                )
                if not published:
                    raise ValueError("Candidate failed authoritative publication validation.")

        resolutions["selected_tier"] = tier_code
        resolutions["selected_at"] = datetime.now().isoformat()
        cur.execute("""
        UPDATE products
        SET tier_resolutions = ?
        WHERE id = ?;
        """, (json.dumps(resolutions), product_id))
        conn.commit()

        # Refresh in-memory catalog cache if available
        try:
            from services.catalog_service import load_catalog_from_db
            load_catalog_from_db()
        except Exception as e:
            logger.warning(f"Catalog refresh skipped: {e}")

        return {
            "status": "success",
            "product_id": product_id,
            "selected_tier": tier_code,
            "tier_resolutions": resolutions
        }
    finally:
        if should_close:
            conn.close()

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

def resolve_waterfall_for_canonical_product(
    canonical_prod: Dict[str, Any],
    conn=None,
    historical_index: Optional[Dict[str, str]] = None,
    langsearch_enabled: bool = True
) -> Tuple[Optional[FormulationCandidate], Optional[Dict[str, Any]]]:
    """
    Executes Tiers 1-5 for a canonical product:
    - Tier 1: Store dedicated tab and historical scrapes across linked retailer offers.
    - Tier 2: OpenBeauty API on canonical brand and title.
    - Tier 3: SkinSAFE High Match on canonical brand and title (with identity gates).
    - Tier 4: LangSearch Web Search API on canonical brand and title.
    - Tier 5: Store description whitelist extraction across linked offers (partial candidate).
    Returns (candidate_or_none, tier3_ambiguous_candidate_or_none).
    """
    if conn and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sources'").fetchone():
        from pipeline.source_formulations import source_candidate_for_waterfall
        candidate, source_status = source_candidate_for_waterfall(conn, canonical_prod["id"])
        if candidate is not None:
            return candidate, None
        if source_status == 'conflict':
            return None, None

    c_id = canonical_prod["id"]
    brand = canonical_prod.get("brand") or ""
    title = canonical_prod.get("canonical_title") or ""

    # Fetch linked retailer offers
    cur = conn.cursor() if conn else None
    offers = []
    if cur and c_id:
        cur.execute("""
        SELECT id, store_id, title, product_url, description_html, ingredients_raw, ingredient_source
        FROM products
        WHERE canonical_product_id = ?
        ORDER BY id ASC;
        """, (c_id,))
        offers = [dict(r) for r in cur.fetchall()]

    res = None
    source_offer_id = None

    # Tier 1: Check linked offers for dedicated store tab or historical scrapes
    for offer in offers:
        from pipeline.identity_trust import verified_link
        link = conn.execute('SELECT store_product_id FROM products WHERE id=?',(offer['id'],)).fetchone()
        if not link or not verified_link(conn,offer['store_id'],link[0] or str(offer['id']),c_id):
            continue
        t1_res = resolve_tier1_store_tab(offer, historical_index)
        if t1_res and t1_res.get("ingredients_raw"):
            res = t1_res
            source_offer_id = offer["id"]
            break

    # Tier 2: OpenBeauty API
    if not res:
        res = resolve_tier2_openbeauty(brand, title)

    # Tier 3: SkinSAFE High-Confidence Match (and ambiguous candidate for Tier 7)
    tier3_candidate = None
    if not res:
        res, tier3_candidate = resolve_tier3_skinsafe_with_candidate(brand, title, conn=conn)

    # Tier 4: LangSearch Web Search API
    if not res and langsearch_enabled:
        res = resolve_tier4_langsearch(brand, title)

    # Tier 5: Store Description Whitelisted Ingredients across linked offers
    if not res:
        for offer in offers:
            desc = offer.get("description_html")
            if desc:
                w_res = resolve_tier5_whitelist_description(desc, conn=conn)
                if w_res and w_res.get("ingredients_raw"):
                    res = w_res
                    source_offer_id = offer["id"]
                    break

    candidate = None
    if res and res.get("ingredients_raw"):
        raw_ings = sanitize_inci_list(res["ingredients_raw"]).strip()
        method = res["method"]
        tok_res = tokenize_inci_string(raw_ings, product_title=title)
        conf_val = float(res.get("confidence", 0.85))
        is_partial = (method == "whitelist_description" or tok_res.completeness == "partial")
        completeness = "partial" if is_partial else tok_res.completeness
        # Search/database hits are hypotheses until an exact source identity is verified.
        if method in ('openbeauty_api','skinsafe_high_match','langsearch_web_search'):
            completeness = 'unverified'

        candidate = FormulationCandidate(
            canonical_product_id=c_id,
            source_name=method,
            raw_ingredients=raw_ings,
            parsed_tokens=tok_res.tokens,
            actives=tok_res.declared_actives,
            confidence_score=conf_val,
            evidence_url=res.get("source_url"),
            source_record_id=str(source_offer_id) if source_offer_id else None,
            completeness=completeness,
            validation_flags={"method": method, "is_truncated": tok_res.is_truncated,
                              "origin_product_id": source_offer_id},
            inci_matched_id=res.get("inci_matched_id"),
            inci_matched_name=res.get("inci_matched_name")
        )

    return candidate, tier3_candidate

def run_waterfall_pipeline(
    conn=None,
    limit: Optional[int] = None,
    revalidate_all: bool = False,
    langsearch_enabled: bool = True,
    queue_jev: bool = True,
    queue_gemini: bool = True,
    dry_run: bool = False,
    max_workers: int = 6,
    progress_callback: Optional[Any] = None,
    cancel_event: Optional[threading.Event] = None
) -> Dict[str, Any]:
    """
    Executes waterfall INCI acquisition and matching across canonical skincare products.
    Authoritative formulations are published strictly to canonical_products and logged in
    inci_audit_evidence via FormulationRepository. Retailer offer rows are never mutated.
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

        from pipeline.identity_trust import pending_identity_review
        # Target canonical skincare products
        query = """
        SELECT id, canonical_title, brand, category, ingredients_raw, ingredient_source, formula_version, formula_hash
        FROM canonical_products
        WHERE is_skincare = 1
        """
        if not revalidate_all:
            query += """ AND (
                ingredients_raw IS NULL 
                OR TRIM(ingredients_raw) = '' 
                OR ingredient_source IS NULL 
                OR ingredient_source NOT IN (
                    'store_page_tab', 'historical_store_scrape', 'openbeauty_api', 
                    'skinsafe_high_match', 'langsearch_web_search', 'admin_override',
                    'curated_v10', 'jev_verified_skinsafe', 'gemini_verified_skinsafe'
                )
            )"""
        query += " ORDER BY id ASC"

        cur.execute(query)
        rows = [dict(r) for r in cur.fetchall()]
        from pipeline.waterfall_readiness import eligible_waterfall
        readiness = eligible_waterfall(conn,revalidate_all=revalidate_all,limit=limit)
        allowed=set(readiness['eligible_ids'])
        rows=[r for r in rows if r['id'] in allowed]

        total_to_process = len(rows)
        logger.info(f"--- Starting Canonical INCI Waterfall Pipeline ({total_to_process} canonical products, {max_workers} workers, LangSearch: {langsearch_enabled}, Jev: {queue_jev}, Gemini: {queue_gemini}) ---")

        stats = {
            "total_processed": total_to_process,
            "resolved": 0,
            "unresolved": 0,
            "by_method": {},
            "eligibility": {k:v for k,v in readiness.items() if k!='eligible_ids'}
        }

        if progress_callback:
            try:
                progress_callback(
                    stage="Initializing",
                    processed=0,
                    total=total_to_process,
                    stats=stats,
                    current_product="Preparing catalog batches...",
                    last_outcome=None
                )
            except Exception:
                pass

        # --- PASS 1: Tiers 1-5 across Canonical Entities (Multi-threaded read, main-thread publication) ---
        logger.info("--- PASS 1: Executing Tiers 1-5 per Canonical ID (Store Tab, OpenBeauty, SkinSAFE High, LangSearch, Whitelist) ---")

        db_file = conn.execute("PRAGMA database_list").fetchone()[2]
        # SQLite in-memory connections cannot be shared with worker threads.
        if not db_file:
            max_workers = 1

        def worker_task(can_prod):
            t_conn = get_connection(db_file)
            try:
                cand, t3_cand = resolve_waterfall_for_canonical_product(
                    can_prod, conn=t_conn, historical_index=historical_index,
                    langsearch_enabled=langsearch_enabled
                )
            finally:
                t_conn.close()
            return {
                "canonical_id": can_prod["id"],
                "title": can_prod["canonical_title"],
                "brand": can_prod["brand"],
                "candidate": cand,
                "tier3_cand": t3_cand
            }

        def process_item_result(item_res):
            cand = item_res.get("candidate")
            can_id = item_res.get("canonical_id")
            can_title = item_res.get("title")

            if cand and cand.completeness == "full":
                if not dry_run:
                    published = formulation_repo.publish_formulation(
                        candidate=cand,
                        approval_reason=f"Resolved via {cand.source_name} in bulk canonical waterfall.",
                        force=False,
                        conn=conn
                    )
                else:
                    # Preview the same conflict policy without writing audit records.
                    existing = formulation_repo.get_canonical_formulation(cand.canonical_product_id, conn=conn)
                    validation = tokenize_inci_string(cand.raw_ingredients, product_title=existing.get("canonical_title") if existing else None)
                    published = bool(existing and validation.completeness == "full" and not validation.is_truncated
                                     and not cand.validation_flags.get("is_truncated"))
                    if published and existing.get("ingredients_raw"):
                        old_tokens = existing.get("ingredients_parsed")
                        if not isinstance(old_tokens, list) or not old_tokens:
                            old_tokens = tokenize_inci_string(existing["ingredients_raw"]).tokens
                        old_hash = existing.get("formula_hash") or formulation_repo.compute_formula_hash(old_tokens)
                        published = old_hash == formulation_repo.compute_formula_hash(cand.parsed_tokens)

                item_res["status"] = "resolved" if published else "candidate_only"
                item_res["method"] = cand.source_name
                item_res["product_id"] = can_id
                if published:
                    stats["resolved"] += 1
                    m = cand.source_name
                    stats["by_method"][m] = stats["by_method"].get(m, 0) + 1
                else:
                    stats["unresolved"] += 1
            else:
                item_res["status"] = "candidate_only" if cand else "unresolved"
                item_res["method"] = cand.source_name if cand else None
                item_res["product_id"] = can_id
                if cand and not dry_run:
                    formulation_repo.record_candidate_evidence(
                        candidate=cand,
                        decision="candidate_evidence",
                        discrepancy_notes=f"Candidate from {cand.source_name} with completeness '{cand.completeness}' recorded as audit evidence.",
                        conn=conn
                    )
                stats["unresolved"] += 1

        processed_so_far = 0

        if max_workers <= 1:
            for row in rows:
                if cancel_event and cancel_event.is_set():
                    logger.info("Waterfall execution cancelled by user request during Pass 1.")
                    break
                processed_so_far += 1
                item_res = None
                try:
                    cand, t3_cand = resolve_waterfall_for_canonical_product(
                        row,
                        conn=conn,
                        historical_index=historical_index,
                        langsearch_enabled=langsearch_enabled
                    )
                    item_res = {
                        "canonical_id": row["id"],
                        "title": row["canonical_title"],
                        "brand": row["brand"],
                        "candidate": cand,
                        "tier3_cand": t3_cand
                    }
                    process_item_result(item_res)
                except Exception as ex:
                    stats["unresolved"] += 1
                    logger.warning(f"Error in canonical worker task: {ex}")

                if processed_so_far % 50 == 0 and not dry_run:
                    conn.commit()

                if progress_callback and (processed_so_far % 5 == 0 or processed_so_far == total_to_process):
                    try:
                        progress_callback(
                            stage="Pass 1: Tiers 1-5 (Store, OpenBeauty, SkinSAFE, LangSearch, Whitelist)",
                            processed=processed_so_far,
                            total=total_to_process,
                            stats=stats,
                            current_product=item_res.get("title") if item_res else "",
                            last_outcome=item_res
                        )
                    except Exception:
                        pass
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(worker_task, row): row for row in rows}
                for fut in as_completed(futures):
                    if cancel_event and cancel_event.is_set():
                        logger.info("Waterfall execution cancelled by user request during Pass 1.")
                        for f in futures:
                            f.cancel()
                        break

                    processed_so_far += 1
                    item_res = None
                    try:
                        item_res = fut.result()
                        process_item_result(item_res)
                    except Exception as ex:
                        stats["unresolved"] += 1
                        logger.warning(f"Error in canonical worker task: {ex}")

                    if processed_so_far % 50 == 0 and not dry_run:
                        conn.commit()

                    if progress_callback and (processed_so_far % 5 == 0 or processed_so_far == total_to_process):
                        try:
                            progress_callback(
                                stage="Pass 1: Tiers 1-5 (Store, OpenBeauty, SkinSAFE, LangSearch, Whitelist)",
                                processed=processed_so_far,
                                total=total_to_process,
                                stats=stats,
                                current_product=item_res.get("title") if item_res else "",
                                last_outcome=item_res
                            )
                        except Exception:
                            pass

                    if processed_so_far % 100 == 0 or processed_so_far == total_to_process:
                        logger.info(f"Pass 1 Progress: [{processed_so_far}/{total_to_process}] Resolved: {stats['resolved']} | {stats['by_method']}")

        if not dry_run:
            conn.commit()

        # Close all thread-local connections before Pass 2
        close_all_thread_connections()

        # --- PASS 2: Tier 6 Cluster Propagation (Permanently Retired) ---
        logger.info("Pass 2 (Tier 6 Cluster Propagation) retired: Authoritative formulations are mastered on canonical_products.")

        # --- PASS 3: Tier 7 Pending Jev & Pending Gemini from Tier 3 ---
        if (queue_jev or queue_gemini) and not (cancel_event and cancel_event.is_set()):
            logger.info(f"--- PASS 3: Executing Tier 7 LLM Queueing (Jev: {queue_jev}, Gemini: {queue_gemini}) ---")
            if progress_callback:
                try:
                    progress_callback(
                        stage="Pass 3: Tier 7 LLM Queueing",
                        processed=processed_so_far,
                        total=total_to_process,
                        stats=stats,
                        current_product="Evaluating ambiguous candidates for Jev & Gemini queues...",
                        last_outcome=None
                    )
                except Exception:
                    pass

            cur.execute("""
            SELECT id, canonical_title, brand
            FROM canonical_products
            WHERE is_skincare = 1 AND (ingredients_raw IS NULL OR TRIM(ingredients_raw) = '');
            """)
            all_empty = [dict(r) for r in cur.fetchall()]
            target_cids = set(r['id'] for r in rows)
            empty_canonical = [c for c in all_empty if c["id"] in target_cids]
            logger.info(f"Evaluating {len(empty_canonical)} target canonical products still empty for Tier 7 LLM queue...")

            can_updates = []
            offer_updates = []
            jev_val = 'pending_jev' if queue_jev else None
            gemini_val = 'pending_gemini' if queue_gemini else None

            for c in empty_canonical:
                if cancel_event and cancel_event.is_set():
                    break
                cid = c["id"]
                brand = c.get("brand") or ""
                title = c.get("canonical_title") or ""
                _, tier3_cand = resolve_tier3_skinsafe_with_candidate(brand, title, conn=conn)
                if tier3_cand:
                    cand_id = tier3_cand["id"]
                    cand_name = tier3_cand["product_name"]
                    if not dry_run:
                        can_updates.append((cand_id, cand_name, jev_val, jev_val, gemini_val, gemini_val, cid))
                        offer_updates.append((cand_id, cand_name, jev_val, jev_val, gemini_val, gemini_val, cid))
                    stats["queued"] = stats.get("queued", 0) + 1
                    stats["by_method"]["tier7_pending_llm"] = stats["by_method"].get("tier7_pending_llm", 0) + 1
                    if progress_callback:
                        try:
                            progress_callback(
                                stage="Pass 3: Tier 7 LLM Queueing",
                                processed=processed_so_far,
                                total=total_to_process,
                                stats=stats,
                                current_product=title,
                                last_outcome={"status": "pending_llm_verification", "method": "tier7_pending_llm", "canonical_id": cid, "title": title}
                            )
                        except Exception:
                            pass

            if can_updates and not dry_run:
                cur.executemany("""
                UPDATE canonical_products
                SET inci_matched_id = ?,
                    inci_matched_name = ?,
                    gate4_jev_status = CASE WHEN ? IS NOT NULL THEN ? ELSE gate4_jev_status END,
                    gate4_gemini_status = CASE WHEN ? IS NOT NULL THEN ? ELSE gate4_gemini_status END,
                    gate4_cross_status = 'pending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, can_updates)

                cur.executemany("""
                UPDATE products
                SET inci_matched_id = ?,
                    inci_matched_name = ?,
                    gate4_jev_status = CASE WHEN ? IS NOT NULL THEN ? ELSE gate4_jev_status END,
                    gate4_gemini_status = CASE WHEN ? IS NOT NULL THEN ? ELSE gate4_gemini_status END,
                    gate4_cross_status = 'pending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE canonical_product_id = ?;
                """, offer_updates)
                conn.commit()

            logger.info(f"Pass 3 Complete: Queued {len(can_updates)} canonical products for Jev & Gemini semantic verification.")
        elif not (queue_jev or queue_gemini):
            logger.info("Pass 3 Skipped: Both queue_jev and queue_gemini toggles are disabled.")

        if progress_callback:
            try:
                progress_callback(
                    stage="Complete",
                    processed=total_to_process,
                    total=total_to_process,
                    stats=stats,
                    current_product="Completed all passes.",
                    last_outcome=None
                )
            except Exception:
                pass

        logger.info(f"--- Canonical INCI Waterfall Resolution Complete ---")
        logger.info(f"Final Stats: Resolved {stats['resolved']}/{total_to_process} ({stats['resolved']/max(total_to_process, 1)*100:.1f}%) | Breakdown: {stats['by_method']}")
        return stats

    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_waterfall_pipeline(limit=100)
