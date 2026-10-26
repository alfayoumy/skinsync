"""
SkinSync INCI Tokenizer & Formulation Completeness Validator
Provides unified, chemical-aware INCI parsing, composite slash preservation,
marketing callout vs. full formulation distinction, and truncation detection.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional, Any

try:
    from pipeline.db_manager import load_synonyms_from_db
    from pipeline.logging_config import get_logger
    logger = get_logger("skincare_pipeline.tokenizer")
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from pipeline.db_manager import load_synonyms_from_db
    logger = logging.getLogger("skincare_pipeline.tokenizer")

try:
    SYNONYMS: Dict[str, str] = load_synonyms_from_db()
except Exception:
    SYNONYMS = {}

KEY_ACTIVES = {
    'hyaluronic_acid': ['hyaluronic acid', 'sodium hyaluronate', 'hydrolyzed hyaluronic', 'hyaluronate', 'hayluronic'],
    'vitamin_b5': ['panthenol', 'd-panthenol', 'pro-vitamin b5'],
    'niacinamide': ['niacinamide', 'nicotinamide', 'vitamin b3'],
    'vitamin_c': ['ascorbic acid', 'l-ascorbic', 'ethyl ascorbic', 'magnesium ascorbyl', 'sodium ascorbyl', 'ascorbyl glucoside', 'vitamin c'],
    'retinoid': ['retinol', 'retinyl palmitate', 'retinal', 'hydroxypinacolone retinoate', 'adapalene'],
    'salicylic_acid': ['salicylic acid', 'bha', 'betaine salicylate', 'willow bark'],
    'aha': ['glycolic acid', 'lactic acid', 'mandelic acid', 'malic acid', 'citric acid'],
    'ceramides': ['ceramide np', 'ceramide ap', 'ceramide eop', 'ceramide 3', 'ceramide', 'phytosphingosine'],
    'centella': ['centella asiatica', 'madecassoside', 'asiaticoside', 'asiatic acid', 'madecassic acid', 'cica'],
    'peptides': ['peptide', 'matrixyl', 'copper tripeptide', 'palmitoyl', 'argireline', 'oligopeptide'],
    'azelaic_acid': ['azelaic acid', 'potassium azeloyl diglycinate'],
    'arbutin': ['alpha-arbutin', 'arbutin', 'alpha arbutin'],
    'zinc': ['zinc pca', 'zinc oxide', 'zinc gluconate'],
    'squalane': ['squalane', 'squalene'],
    'tea_tree': ['tea tree', 'melaleuca alternifolia'],
    'collagen': ['hydrolyzed collagen', 'marine collagen', 'collagen'],
    'aloe_vera': ['aloe barbadensis', 'aloe vera'],
    'tranexamic_acid': ['tranexamic acid', 'tranexamic'],
    'kojic_acid': ['kojic acid', 'kojic dipalmitate'],
    'bakuchiol': ['bakuchiol'],
    'urea': ['urea', 'hydroxyethyl urea'],
    'benzoyl_peroxide': ['benzoyl peroxide'],
    'sulfur': ['sulfur', 'colloidal sulfur'],
    'ectoin': ['ectoin'],
    'pha': ['gluconolactone', 'lactobionic acid', 'pha'],
    'ferulic_acid': ['ferulic acid', 'ferulic'],
    'licorice': ['glycyrrhiza glabra', 'licorice', 'dipotassium glycyrrhizate', 'glabridin'],
    'snail_mucin': ['snail secretion filtrate', 'snail mucin'],
    'ferment': ['galactomyces', 'bifida ferment', 'saccharomyces', 'ferment lysate']
}

# Common carrier / formulation base markers to distinguish complete formulations from active-only callouts
CARRIER_BASE_MARKERS = {
    'water', 'aqua', 'eau', 'glycerin', 'glycerol', 'butylene glycol', 'propylene glycol',
    'propanediol', 'dipropylene glycol', 'dimethicone', 'cyclopentasiloxane', 'cyclohexasiloxane',
    'mineral oil', 'paraffinum liquidum', 'petrolatum', 'cetyl alcohol', 'stearyl alcohol',
    'cetearyl alcohol', 'glyceryl stearate', 'stearic acid', 'caprylic/capric triglyceride',
    'isododecane', 'isohexadecane', 'squalane', 'jojoba oil', 'simmondsia chinensis seed oil',
    'sunflower seed oil', 'helianthus annuus seed oil', 'phenoxyethanol', 'ethylhexylglycerin'
}

# Known composite patterns where slash is an integral part of the INCI chemical nomenclature
COMPOSITE_SLASH_PATTERNS = [
    re.compile(r'(?i)\bcaprylic/capric(?:/succinic)?\s+(?:triglyceride|glycerides)\b'),
    re.compile(r'(?i)\bacrylates/c10-30\s+alkyl\s+acrylate\s+crosspolymer\b'),
    re.compile(r'(?i)\b(?:bis-)?peg/ppg-[0-9/]+(?:\s+[a-z0-9\-]+)?\b'),
    re.compile(r'(?i)\b[a-z0-9\-]+/[a-z0-9\-]+(?:\s+[a-z0-9\-]+)?\s+(?:copolymer|crosspolymer|polymer)\b'),
    re.compile(r'(?i)\bdimethicone/(?:vinyl\s+dimethicone|polyglycerin-3)\s+crosspolymer\b'),
    re.compile(r'(?i)\bhydroxyethyl\s+acrylate/sodium\s+acryloyldimethyl\s+taurate\s+copolymer\b'),
    re.compile(r'(?i)\bsodium\s+acrylate/sodium\s+acryloyldimethyl\s+taurate\s+copolymer\b'),
    re.compile(r'(?i)\bammonium\s+acryloyldimethyltaurate/vp\s+copolymer\b'),
    re.compile(r'(?i)\bvinyl\s+dimethicone/methicone\s+silsesquioxane\s+crosspolymer\b'),
    re.compile(r'(?i)\bpolysilicone-11/dimethicone\b'),
    re.compile(r'(?i)\bpeg-10/15\s+crosspolymer\b'),
    re.compile(r'(?i)\b[a-z0-9\-]+/[a-z0-9\-]+\s+triglyceride\b'),
]

# Alternate label patterns (e.g. multi-lingual or pigment codes) that map to a single canonical ingredient
ALIAS_SLASH_PATTERNS = [
    (re.compile(r'(?i)\b(?:aqua\s*/\s*water\s*/\s*eau|water\s*/\s*aqua\s*/\s*eau|aqua\s*/\s*water|water\s*/\s*aqua|aqua\s*/\s*eau|eau\s*/\s*aqua|water\s*/\s*eau)\b'), 'water'),
    (re.compile(r'(?i)\bci\s*77891\s*/\s*titanium\s*dioxide\b'), 'titanium dioxide'),
    (re.compile(r'(?i)\bci\s*77491\s*/\s*iron\s*oxides\b'), 'iron oxides'),
    (re.compile(r'(?i)\btocopherol\s*/\s*vitamin\s*e\b'), 'tocopherol'),
    (re.compile(r'(?i)\bascorbic\s*acid\s*/\s*vitamin\s*c\b'), 'ascorbic acid'),
    (re.compile(r'(?i)\bpanthenol\s*/\s*(?:pro-)?vitamin\s*b5\b'), 'panthenol'),
    (re.compile(r'(?i)\bniacinamide\s*/\s*vitamin\s*b3\b'), 'niacinamide'),
]


@dataclass
class InciValidationResult:
    raw_text: str
    tokens: List[str]
    declared_actives: List[str]
    completeness: str  # 'full', 'partial', 'unverified'
    is_truncated: bool
    has_composite_slashes: bool
    validation_flags: Dict[str, Any] = field(default_factory=dict)


def reload_synonyms() -> Dict[str, str]:
    global SYNONYMS
    try:
        SYNONYMS = load_synonyms_from_db()
    except Exception as e:
        logger.warning(f"Could not load synonyms from database: {e}")
    return SYNONYMS


def is_composite_slash(text: str) -> bool:
    """Checks whether the text matches an acknowledged composite INCI chemical."""
    for pat in COMPOSITE_SLASH_PATTERNS:
        if pat.search(text):
            return True
    return False


def normalize_alias_slashes(text: str) -> str:
    """Normalizes known multi-lingual / cosmetic alias slash combinations (e.g. Aqua/Water/Eau -> water)."""
    normalized = text
    for pat, replacement in ALIAS_SLASH_PATTERNS:
        normalized = pat.sub(replacement, normalized)
    return normalized


def tokenize_inci_string(raw_text: Optional[str], product_title: Optional[str] = None) -> InciValidationResult:
    """
    Parses a raw ingredient string into normalized tokens while preserving composite chemical slashes.
    Validates completeness and detects truncation.
    """
    if not raw_text or not isinstance(raw_text, str) or not raw_text.strip():
        return InciValidationResult(
            raw_text="",
            tokens=[],
            declared_actives=[],
            completeness="unverified",
            is_truncated=False,
            has_composite_slashes=False,
            validation_flags={"reason": "empty_input"}
        )

    text = raw_text.strip()
    validation_flags = {}

    # 1. Truncation Detection
    is_truncated = False
    truncation_re = re.compile(r'(\.{3,}|…|,\s*etc\.?|,\s*and\s+(?:more|other\s+ingredients)|,\s*\.\.\.)$', re.IGNORECASE)
    if truncation_re.search(text):
        is_truncated = True
        validation_flags["truncation_pattern"] = "trailing_ellipsis_or_etc"

    # Check for abrupt end (ends in conjunction or unclosed parenthesis)
    trailing_word = text.rstrip(' .,:;-').split()[-1].lower() if text.split() else ""
    if trailing_word in ('and', 'with', 'plus', 'including', 'containing'):
        is_truncated = True
        validation_flags["truncation_pattern"] = "trailing_conjunction"
    if text.count('(') > text.count(')'):
        is_truncated = True
        validation_flags["truncation_pattern"] = "unclosed_parenthesis"

    if re.search(r'(?:^|[,;])\s*(?:caprylyl|caprylic|cetearyl)\s*\.?$',text,re.I):
        is_truncated=True
        validation_flags['truncation_pattern']='unfinished_chemical_name'

    # 2. Check for marketing callout header
    callout_match = re.search(r'(?i)\b(key|active|hero|main|highlighted)\s+ingredients?\s*[:\-–]', text)
    is_callout_header = bool(callout_match)
    if is_callout_header:
        validation_flags["callout_header"] = callout_match.group(0)

    # 3. Pre-process known alias slash groups (Aqua/Water/Eau)
    preprocessed_text = normalize_alias_slashes(text)

    # 4. Split by primary delimiters: comma, semicolon, bullet, newline, or em-dash surrounded by spaces
    # Notice: WE DO NOT SPLIT ON SLASH (/)
    # Commas in botanical aliases or ppm values are not ingredient separators.
    raw_segments = []
    start = 0
    depth = 0
    for index, character in enumerate(preprocessed_text):
        if character in '([': depth += 1
        elif character in ')]': depth = max(0, depth - 1)
        elif character in ',;•●⦁*|\n\r' and depth == 0:
            raw_segments.append(preprocessed_text[start:index])
            start = index + 1
    raw_segments.append(preprocessed_text[start:])
    raw_segments = [part for segment in raw_segments for part in re.split(r'\s+[\-–—]\s+', segment)]

    tokens = []
    has_composite_slashes = False

    for segment in raw_segments:
        segment = segment.strip()
        if not segment:
            continue

        # Check if segment contains slashes
        if '/' in segment:
            # If it's an acknowledged composite INCI chemical, keep intact
            if is_composite_slash(segment):
                has_composite_slashes = True
                cleaned_token = _clean_token(segment)
                if cleaned_token:
                    tokens.append(cleaned_token)
            elif ' / ' in segment:
                # Explicit whitespace around slash indicates distinct ingredients (e.g. "Glycerin / Niacinamide")
                parts = [p.strip() for p in segment.split('/') if p.strip()]
                for p in parts:
                    cleaned = _clean_token(p)
                    if cleaned:
                        tokens.append(cleaned)
            else:
                # Slashes without surrounding whitespace (e.g. chemical nomenclature or polymers)
                # Only split if all parts are recognized cosmetic ingredients or synonyms
                parts = [p.strip() for p in segment.split('/') if p.strip()]
                both_known = len(parts) >= 2 and all(
                    _clean_token(p) in SYNONYMS.values() or _clean_token(p) in CARRIER_BASE_MARKERS
                    for p in parts
                )
                if both_known:
                    for p in parts:
                        cleaned = _clean_token(p)
                        if cleaned:
                            tokens.append(cleaned)
                else:
                    # Keep intact as single chemical entity to avoid fragment junk
                    cleaned_token = _clean_token(segment)
                    if cleaned_token:
                        tokens.append(cleaned_token)
        else:
            cleaned_token = _clean_token(segment)
            if cleaned_token:
                tokens.append(cleaned_token)

    # 5. Extract declared actives from parsed tokens
    declared_actives = set()
    for tok in tokens:
        for act_cat, keywords in KEY_ACTIVES.items():
            for k in keywords:
                if len(k) <= 3:
                    if re.search(r'\b' + re.escape(k) + r'\b', tok):
                        declared_actives.add(act_cat)
                        break
                else:
                    if k in tok:
                        declared_actives.add(act_cat)
                        break

    # 6. Single-ingredient formula exception
    # e.g. "100% Squalane", "100% Organic Cold-Pressed Rose Hip Seed Oil", "Pure Argan Oil"
    is_pure_single = False
    if product_title:
        title_lower = product_title.lower()
        if any(term in title_lower for term in ('100%', 'pure', 'cold-pressed', 'cold pressed', 'single-origin')):
            if len(tokens) == 1 or (len(tokens) <= 2 and any(t in ('tocopherol', 'vitamin e') for t in tokens[1:])):
                is_pure_single = True
                validation_flags["single_ingredient_formulation"] = True

    # 7. Completeness Determination
    completeness = "full"
    if is_truncated:
        # Visibly truncated lists must NEVER qualify as authoritative full formulations
        completeness = "partial"
        validation_flags["partial_reason"] = "truncated_ingredient_list"
    elif is_pure_single:
        completeness = "full"
    elif is_callout_header:
        completeness = "partial"
        validation_flags["partial_reason"] = "market_callout_header"
    elif len(tokens) < 4:
        # Check if list lacks any standard carrier / formulation base
        has_base = any(t in CARRIER_BASE_MARKERS or any(m in t for m in ('oil', 'water', 'extract', 'butter', 'wax')) for t in tokens)
        if not has_base:
            completeness = "partial"
            validation_flags["partial_reason"] = "fewer_than_4_tokens_no_base"
        elif len(tokens) <= 2:
            completeness = "partial"
            validation_flags["partial_reason"] = "short_callout_under_3_tokens"

    # A benefits summary or broken extraction is not a chemically identified
    # full declaration, even when it contains enough carrier ingredients.
    vague=re.search(r'\b(?:multivitamin\s+liposomal|vitamin\s+[ce]\s+liposom(?:al|e)|hyloronic\s+acid\s+liposomal)\b',raw_text,re.I)
    broken=re.search(r'(?:^|[,;])\s*(?:gum|caprylic/capric|triglyceride|swertia)\s*(?:[,;]|$)',raw_text,re.I)
    if vague or broken or re.search(r'\bshare\s*:\s*$',raw_text,re.I):
        completeness='partial'
        validation_flags['partial_reason']='non_specific_or_contaminated_declaration'

    # A dual-zone product has separate component formulas. Concatenating the
    # blue T-zone and white U-zone declarations would invent one ordered INCI.
    zones=set(re.findall(r'(?:^|[\n|])\s*([tu])[ -]?zone\s+(?:cream|gel)\b',raw_text,re.I))
    if len({zone.lower() for zone in zones})>1:
        completeness='partial'
        validation_flags['partial_reason']='multiple_component_formulas'

    # Drug labels can list inactive ingredients separately. A long carrier list
    # is still incomplete if the product's explicitly named drug active is absent.
    if product_title and re.search(r'\bbenzoyl\s+peroxide\b',product_title,re.I) and not any(
            re.search(r'\bbenzoyl\s+peroxide\b',t,re.I) for t in tokens):
        completeness='partial'
        validation_flags['partial_reason']='missing_named_drug_active'
        validation_flags['missing_named_active']='benzoyl peroxide'

    return InciValidationResult(
        raw_text=raw_text,
        tokens=tokens,
        declared_actives=sorted(list(declared_actives)),
        completeness=completeness,
        is_truncated=is_truncated,
        has_composite_slashes=has_composite_slashes,
        validation_flags=validation_flags
    )


def _clean_token(tok: str) -> Optional[str]:
    """Helper to clean a single token of percentages, footnotes, and whitespace."""
    tok = tok.strip().lower()
    if not tok or len(tok) < 2:
        return None
    # Strip leading percentages: "10% Niacinamide" -> "Niacinamide"
    tok = re.sub(r'^\d+(\.\d+)?%\s*(?:pure\s*)?', '', tok)
    # Strip trailing periods, asterisks, footnotes
    tok = re.sub(r'[\.\*]+$', '', tok).strip()
    if not tok or len(tok) < 2:
        return None
    # Map to canonical synonym if available
    return SYNONYMS.get(tok, tok)
