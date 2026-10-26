"""
SkinSync Product Identity & Formulation Matching Gates
Enforces deterministic hard identity gates overriding semantic vector/cross-encoder scores.
Prevents cross-variant mismatching (SPF numbers, active percentages, vehicle/line exclusivity, day vs night).
Eliminates loose 0.50 token Jaccard auto-approvals.
"""

import re
import unicodedata
import logging
from typing import Tuple, Optional, List, Set, Dict, Any

try:
    from pipeline.logging_config import get_logger
    logger = get_logger("skincare_pipeline.identity_gates")
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from pipeline.logging_config import get_logger
    logger = logging.getLogger("skincare_pipeline.identity_gates")


def strip_accents(text: str) -> str:
    """Removes diacritical marks (e.g. 'crème' -> 'creme')."""
    if not text:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize_for_gate(text: str) -> str:
    """Normalizes text for regex gate evaluation."""
    if not text:
        return ""
    text = strip_accents(text.lower())
    return " " + re.sub(r"[^a-z0-9%+\.]", " ", text) + " "


def extract_spf_number(normalized_text: str) -> Optional[int]:
    """Extracts explicit SPF integer rating (e.g. 'spf 30' -> 30, 'spf50+' -> 50)."""
    m = re.search(r'\bspf\s*(\d{2,3})\b', normalized_text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def extract_percentages(normalized_text: str) -> Set[str]:
    """Extracts all active concentration percentages (e.g. '10%', '0.3%')."""
    matches = re.findall(r'\b(\d+(?:\.\d+)?)\s*%', normalized_text)
    return set(matches)


def validate_identity_gates(
    title_a: str,
    title_b: str,
    brand_a: Optional[str] = None,
    brand_b: Optional[str] = None,
    tokens_a: Optional[List[str]] = None,
    tokens_b: Optional[List[str]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Evaluates hard identity gates between two product titles / formulations.
    Returns (True, None) if compatible, or (False, reason) if rejected by any gate.
    """
    if not title_a or not title_b:
        return False, "Empty product title provided"

    # -------------------------------------------------------------------------
    # GATE 1: BRAND COMPATIBILITY
    # -------------------------------------------------------------------------
    if brand_a and brand_b:
        from pipeline.brand_registry import resolve_brand
        # Use explicit curated equivalences, never a fuzzy prefix. The older
        # gate rejected PURITO Seoul / PURITO while accepting Brand / BrandX.
        resolved_a=resolve_brand(str(brand_a))
        resolved_b=resolve_brand(str(brand_b))
        b_clean_a = re.sub(r'[\W_]', '', strip_accents(resolved_a['name'] if resolved_a else str(brand_a)).casefold())
        b_clean_b = re.sub(r'[\W_]', '', strip_accents(resolved_b['name'] if resolved_b else str(brand_b)).casefold())
        if b_clean_a and b_clean_b and b_clean_a != b_clean_b:
            return False, f"Brand conflict: '{brand_a}' != '{brand_b}'"

    a = normalize_for_gate(title_a)
    b = normalize_for_gate(title_b)

    # Reviewed manufacturer distinction: Cotton Airy is a different formula
    # from Cotton Soft, not a translation or packaging alias. See the official
    # Airy product page and Korean Soft product_no=32 declarations.
    if brand_a and brand_b and b_clean_a==b_clean_b=='tocobo':
        soft_a=bool(re.search(r'\bcotton\s+soft\b',a))
        soft_b=bool(re.search(r'\bcotton\s+soft\b',b))
        airy_a=bool(re.search(r'\bcotton\s+airy\b',a))
        airy_b=bool(re.search(r'\bcotton\s+airy\b',b))
        if (soft_a and airy_b) or (airy_a and soft_b):
            return False,'Formulation line conflict: TOCOBO Cotton Airy and Cotton Soft are distinct formulas'

    # -------------------------------------------------------------------------
    # GATE 2: SUN PROTECTION FACTOR (SPF) & UV RATING
    # -------------------------------------------------------------------------
    spf_num_a = extract_spf_number(a)
    spf_num_b = extract_spf_number(b)

    # Both specify distinct SPF numbers -> must match exactly (e.g. SPF 30 vs SPF 50)
    if spf_num_a and spf_num_b and spf_num_a != spf_num_b:
        return False, f"SPF number mismatch: SPF {spf_num_a} != SPF {spf_num_b}"

    # SPF vs non-SPF conflict
    spf_kws = r"\b(spf\s*\d+|spf|sunscreen|sun\s*cream|sun\s*fluid|sun\s*screen|sun\s*stick|photoderm|uvmune|pa\+{1,4}|solar)\b"
    has_spf_a = bool(re.search(spf_kws, a))
    has_spf_b = bool(re.search(spf_kws, b))
    if has_spf_a != has_spf_b:
        return False, "SPF / UV protection conflict: Sunscreen formulation cannot match non-SPF product"

    # -------------------------------------------------------------------------
    # GATE 3: ACTIVE CONCENTRATION / POTENCY
    # -------------------------------------------------------------------------
    pct_a = extract_percentages(a)
    pct_b = extract_percentages(b)
    # If both specify percentages and have zero overlap, reject (e.g. 10% Niacinamide vs 2% Niacinamide)
    if pct_a and pct_b and pct_a.isdisjoint(pct_b):
        return False, f"Potency mismatch: Concentrations {sorted(list(pct_a))} != {sorted(list(pct_b))}"

    # Reedle shot levels
    reedle_a = re.search(r"reedle\s*shot\s*(50|100|300|700)", a)
    reedle_b = re.search(r"reedle\s*shot\s*(50|100|300|700)", b)
    if reedle_a and reedle_b and reedle_a.group(1) != reedle_b.group(1):
        return False, f"Reedle Shot intensity mismatch: {reedle_a.group(1)} != {reedle_b.group(1)}"

    # -------------------------------------------------------------------------
    # GATE 4: CIRCADIAN REGIMEN (DAY VS NIGHT)
    # -------------------------------------------------------------------------
    has_day_a = bool(re.search(r"\b(day|jour|am|matin)\b", a))
    has_day_b = bool(re.search(r"\b(day|jour|am|matin)\b", b))
    has_night_a = bool(re.search(r"\b(night|nuit|pm|overnight|sleeping|soir)\b", a))
    has_night_b = bool(re.search(r"\b(night|nuit|pm|overnight|sleeping|soir)\b", b))
    if ((has_day_a and not has_night_a and has_night_b and not has_day_b)
            or (has_night_a and not has_day_a and has_day_b and not has_night_b)):
        return False, "Circadian regimen conflict: Day/AM formulation cannot match Night/PM formulation"

    # -------------------------------------------------------------------------
    # GATE 5: FRAGRANCE & SCENT MODIFIERS
    # -------------------------------------------------------------------------
    ff_kws = r"\b(fragrance[- ]free|sans[- ]parfum|unscented|no\s*fragrance)\b"
    scented_kws = r"\b(fragrance|parfum|scented|perfumed)\b"
    is_ff_a = bool(re.search(ff_kws, a))
    is_ff_b = bool(re.search(ff_kws, b))
    is_scented_a = bool(re.search(scented_kws, a)) and not is_ff_a
    is_scented_b = bool(re.search(scented_kws, b)) and not is_ff_b

    if (is_ff_a and is_scented_b) or (is_ff_b and is_scented_a):
        return False, "Fragrance conflict: Fragrance-Free formulation cannot match Scented formulation"

    # -------------------------------------------------------------------------
    # GATE 6: FINISH (TINTED VS UNTINTED)
    # -------------------------------------------------------------------------
    tint_kws = r"\b(tinted|teinte|bb\s*cream|cc\s*cream)\b"
    untint_kws = r"\b(untinted|invisible|translucent|clear)\b"
    has_tint_a = bool(re.search(tint_kws, a))
    has_tint_b = bool(re.search(tint_kws, b))
    has_untint_a = bool(re.search(untint_kws, a))
    has_untint_b = bool(re.search(untint_kws, b))
    if (has_tint_a and has_untint_b) or (has_untint_a and has_tint_b):
        return False, "Finish conflict: Tinted cosmetic cannot match Untinted/Invisible formulation"

    # -------------------------------------------------------------------------
    # GATE 7: PRODUCT VEHICLE & PHYSICAL FORMAT
    # -------------------------------------------------------------------------
    rinse_kws = r"\b(cleanser|cleansing|wash|gel wash|foaming gel|foam|moussant|micellar|micellaire|soap|savon|scrub|gommage|rinse-off|shampoo)\b"
    leave_kws = r"\b(serum|ampoule|moisturizer|moisturiser|cream|creme|balm|baume|lotion|sleeping mask|leave-on|day cream|night cream|toner|essence)\b"
    is_rinse_a = bool(re.search(rinse_kws, a))
    is_rinse_b = bool(re.search(rinse_kws, b))
    is_leave_a = bool(re.search(leave_kws, a))
    is_leave_b = bool(re.search(leave_kws, b))

    if (is_rinse_a and not is_rinse_b and is_leave_b and not is_leave_a) or (is_rinse_b and not is_rinse_a and is_leave_a and not is_leave_b):
        return False, "Vehicle conflict: Rinse-off Cleanser cannot match Leave-on Treatment/Moisturizer"

    # Liquid serum vs thick cream/balm
    is_serum_a = bool(re.search(r"\b(serum|ampoule|booster)\b", a))
    is_serum_b = bool(re.search(r"\b(serum|ampoule|booster)\b", b))
    is_cream_a = bool(re.search(r"\b(cream|creme|balm|baume|butter)\b", a))
    is_cream_b = bool(re.search(r"\b(cream|creme|balm|baume|butter)\b", b))
    if (is_serum_a and not is_serum_b and is_cream_b and not is_cream_a) or (is_serum_b and not is_serum_a and is_cream_a and not is_cream_b):
        return False, "Vehicle conflict: Serum/Ampoule cannot match Emulsion Cream/Balm"

    # Dedicated Eye vs Full Face
    has_eye_a = bool(re.search(r"\b(eye|eyes|yeux|under-eye|eyelid|ocular|regard)\b", a))
    has_eye_b = bool(re.search(r"\b(eye|eyes|yeux|under-eye|eyelid|ocular|regard)\b", b))
    # A declared face-and-eye product is not dedicated to the eye contour.
    if re.search(r'\bface\s+(?:and\s+)?eyes?\b|\beyes?\s+(?:and\s+)?face\b',a):has_eye_a=False
    if re.search(r'\bface\s+(?:and\s+)?eyes?\b|\beyes?\s+(?:and\s+)?face\b',b):has_eye_b=False
    if has_eye_a != has_eye_b:
        return False, "Anatomy conflict: Dedicated Eye contour product cannot match Full Face/Body product"

    # Dedicated Lip vs Face
    has_lip_a = bool(re.search(r"\b(lip|lips|lipgloss|levre|levres)\b", a))
    has_lip_b = bool(re.search(r"\b(lip|lips|lipgloss|levre|levres)\b", b))
    if has_lip_a != has_lip_b:
        return False, "Anatomy conflict: Dedicated Lip product cannot match Face product"

    # -------------------------------------------------------------------------
    # GATE 8: DERMATOLOGICAL BRAND LINE EXCLUSIVITY
    # -------------------------------------------------------------------------
    bioderma_lines = ["sebium", "sensibio", "atoderm", "hydrabio", "pigmentbio", "cicabio"]
    for i, l1 in enumerate(bioderma_lines):
        for l2 in bioderma_lines[i+1:]:
            if (l1 in a and l2 in b) or (l2 in a and l1 in b):
                return False, f"Line conflict: Bioderma {l1.capitalize()} cannot match {l2.capitalize()}"

    lrp_lines = ["effaclar", "toleriane", "lipikar", "cicaplast", "anthelios"]
    for i, l1 in enumerate(lrp_lines):
        for l2 in lrp_lines[i+1:]:
            if (l1 in a and l2 in b) or (l2 in a and l1 in b):
                return False, f"Line conflict: La Roche-Posay {l1.capitalize()} cannot match {l2.capitalize()}"

    # -------------------------------------------------------------------------
    # GATE 9: TOKEN OVERLAP THRESHOLD (ELIMINATING LOOSE 0.50 JACCARD)
    # -------------------------------------------------------------------------
    if tokens_a and tokens_b:
        set_a = set(t.strip().lower() for t in tokens_a if t.strip())
        set_b = set(t.strip().lower() for t in tokens_b if t.strip())
        if set_a and set_b:
            intersection = len(set_a & set_b)
            union = len(set_a | set_b)
            jaccard = intersection / max(union, 1)
            # Two skincare products sharing 50% generic base (water, glycerin, phenoxyethanol)
            # are NOT identical formulations. A minimum 0.75 Jaccard is required to pass formulation equivalence.
            if jaccard < 0.75:
                return False, f"Formulation token similarity too low: Jaccard {jaccard:.2f} < 0.75 required for formulation equivalence"

    return True, None
