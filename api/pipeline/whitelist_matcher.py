"""
Authoritative Whitelist Lookup Extractor.
Scans product descriptions for verified cosmetic ingredients from the database whitelist.
STRICT EXECUTION PRECEDENCE:
1. Priority 1: Direct full INCI list from store description (store_description)
2. Priority 2: SkinSAFE DB / Open Beauty Facts API match (skinsafe_matched)
3. Priority 3 (Fallback): Whitelist description lookup (whitelist_extracted)
   -> ONLY executed for products with no ingredients after 1 & 2 fail.
"""

import re
import html
import logging
from typing import Optional, Dict, List, Any, Tuple, Set

try:
    from pipeline.db_manager import get_connection, load_whitelist_from_db, DB_PATH
except ImportError:
    from db_manager import get_connection, load_whitelist_from_db, DB_PATH

try:
    from pipeline.inci_matcher import parse_and_normalize_ingredients
except ImportError:
    from inci_matcher import parse_and_normalize_ingredients

try:
    from pipeline.logging_config import get_logger
    logger = get_logger("skincare_pipeline.whitelist_matcher")
except ImportError:
    logger = logging.getLogger("skincare_pipeline.whitelist_matcher")

# Thread-safe in-memory cache for compiled whitelist entries
_WHITELIST_CACHE: Optional[List[Dict[str, Any]]] = None

# Pure narrative marketing trigger words that should invalidate an ingredient match
# if the match is inside a narrative promotional phrase
NARRATIVE_IGNORE_CONTEXTS = [
    re.compile(r'free\s+of\s+([a-zA-Z\s]+)', re.IGNORECASE),
    re.compile(r'without\s+([a-zA-Z\s]+)', re.IGNORECASE),
    re.compile(r'0%\s+([a-zA-Z\s]+)', re.IGNORECASE),
    re.compile(r'no\s+([a-zA-Z\s]+)', re.IGNORECASE),
]

# Standalone words that must never match as an ingredient on their own
EXCLUDED_STANDALONE = {
    'oil', 'acid', 'water', 'extract', 'butter', 'wax', 'gum', 'juice', 'leaf', 
    'flower', 'root', 'fruit', 'seed', 'peel', 'bark', 'stem', 'gel', 'cream', 
    'drops', 'sun', 'spf', 'skin', 'face', 'body', 'daily', 'care', 'beauty', 
    'pure', 'natural', 'organic', 'active', 'free', 'clean', 'soft', 'smooth', 
    'bright', 'anti', 'complex', 'solution', 'formula', 'treatment', 'repair', 
    'mineral', 'gold', 'clear', 'light', 'dark', 'rose', 'tea', 'milk', 'sugar',
    'honey', 'salt', 'clay', 'mud', 'berry', 'orange', 'lemon', 'apple', 'grape',
    'iron', 'steel', 'cloth', 'cotton', 'bottle', 'tube', 'paper', 'towel',
    'and', 'the', 'for', 'with', 'from', 'contains', 'plus', 'item', 'details',
    'directions', 'features', 'benefits', 'routine', 'warning', 'caution',
    'application', 'revitaliz', 'results', 'suitable for', 'contour', 'years',
    'evening', 'morning', 'night', 'day', 'fresh', 'dermatologist', 'tested',
    'vegan', 'cruelty', 'paraben', 'fragrance', 'flavor', 'scent'
}

def load_and_compile_whitelist(conn=None, force_reload: bool = False) -> List[Dict[str, Any]]:
    """
    Loads active ingredients from database whitelist, sorted by length of name DESC,
    and pre-compiles regex patterns for high-performance substring boundary matching.
    """
    global _WHITELIST_CACHE
    if _WHITELIST_CACHE is not None and not force_reload:
        return _WHITELIST_CACHE

    raw_items = load_whitelist_from_db(conn=conn, active_only=True)
    compiled = []

    for item in raw_items:
        name = item.get("name", "").strip()
        if not name:
            continue
        # Exclude standalone generic English words
        if ' ' not in name and name.lower() in EXCLUDED_STANDALONE:
            continue
        
        # Word boundary pattern: require word boundary at start and end
        # Handle special characters (e.g. slashes, hyphens) safely
        escaped = re.escape(name)
        pattern = re.compile(r'(?<![a-zA-Z0-9])' + escaped + r'(?![a-zA-Z0-9])', re.IGNORECASE)
        
        compiled.append({
            "id": item.get("id"),
            "name": name,
            "canonical_name": item.get("canonical_name") or name,
            "category": item.get("category") or "general",
            "pattern": pattern,
            "length": len(name)
        })

    # Ensure strictly sorted by length descending (longest phrase matched first)
    compiled.sort(key=lambda x: x["length"], reverse=True)
    _WHITELIST_CACHE = compiled
    logger.info(f"Loaded and compiled {len(_WHITELIST_CACHE)} verified ingredients in whitelist cache.")
    return _WHITELIST_CACHE

def reload_whitelist_cache(conn=None) -> int:
    """Forces cache reload from database."""
    items = load_and_compile_whitelist(conn=conn, force_reload=True)
    return len(items)

def clean_html_description(body_html: str) -> str:
    """Strips HTML formatting, handles line breaks, and unescapes entities."""
    if not body_html or not isinstance(body_html, str):
        return ""
    text = re.sub(r'<br\s*/?>', '\n', body_html, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|li|h\d|tr)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    # Normalize spaces
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def _is_negated_or_free_from(text: str, start: int, end: int) -> bool:
    """
    Checks if an ingredient occurrence is preceded by 'free from', 'without', '0%', 'no'.
    E.g., 'paraben-free', 'free of sulfates', 'without alcohol'.
    """
    # Look back up to 40 characters before the match
    window_start = max(0, start - 40)
    window = text[window_start:start].lower()
    
    if any(neg in window for neg in [
        'free from', 'free of', '-free', ' without ', ' 0% ', ' no ', 'not contain'
    ]):
        return True
    
    # Check immediate suffix for '-free'
    suffix = text[end:end+10].lower()
    if suffix.startswith('-free') or suffix.startswith(' free'):
        return True
        
    return False

def extract_ingredients_via_whitelist(
    text_or_html: str, 
    conn=None, 
    min_ingredients: int = 1
) -> Optional[Dict[str, Any]]:
    """
    Extracts verified cosmetic ingredients from product description text/HTML
    using the authoritative SQLite ingredient whitelist.
    
    Returns None if:
    - Input text is empty or too short (< 20 chars)
    - Fewer than min_ingredients verified ingredients were matched
    - Only generic/negated terms were found
    """
    if not text_or_html or not isinstance(text_or_html, str) or len(text_or_html.strip()) < 20:
        return None

    clean_text = clean_html_description(text_or_html)
    if len(clean_text) < 15:
        return None

    whitelist = load_and_compile_whitelist(conn=conn)
    if not whitelist:
        return None

    # Track occupied spans (start, end) to prevent substring collisions
    # (e.g. 'Centella Asiatica Extract' prevents 'Centella Asiatica' or 'Extract' from matching again)
    occupied_spans: List[Tuple[int, int]] = []
    matches: List[Tuple[int, str, str]] = [] # (start_index, canonical_name, category)

    for item in whitelist:
        pat = item["pattern"]
        canon = item["canonical_name"]
        cat = item["category"]

        for m in pat.finditer(clean_text):
            m_start, m_end = m.start(), m.end()

            # Check overlap with any existing occupied span
            overlaps = False
            for (o_start, o_end) in occupied_spans:
                if not (m_end <= o_start or m_start >= o_end):
                    overlaps = True
                    break

            if overlaps:
                continue

            # Check if this occurrence is a negation ('free of sulfates', 'oil-free')
            if _is_negated_or_free_from(clean_text, m_start, m_end):
                continue

            # Mark span as occupied
            occupied_spans.append((m_start, m_end))
            matches.append((m_start, canon, cat))

    if not matches:
        return None

    # Sort matches by appearance in the text (natural formulation flow)
    matches.sort(key=lambda x: x[0])

    # Deduplicate while preserving order of first mention
    seen_lower = set()
    deduped_ingredients: List[str] = []
    category_map: Dict[str, str] = {}

    for _, canon_name, cat in matches:
        low = canon_name.lower().strip()
        if low not in seen_lower:
            seen_lower.add(low)
            deduped_ingredients.append(canon_name)
            category_map[canon_name] = cat

    if len(deduped_ingredients) < min_ingredients:
        return None

    # Construct clean raw INCI string
    raw_ingredients = ", ".join(deduped_ingredients)

    # Parse and detect actives using standard pipeline normalizer
    parsed_tokens, actives = parse_and_normalize_ingredients(raw_ingredients)

    return {
        "ingredients_raw": raw_ingredients,
        "ingredients_parsed": parsed_tokens,
        "actives": actives,
        "ingredient_source": "whitelist_extracted",
        "matched_count": len(deduped_ingredients),
        "matched_ingredients": deduped_ingredients,
        "matched_categories": category_map
    }
