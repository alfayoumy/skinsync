"""
Cosmetic Compatibility Safeguard & Rule-Based Validator.
Prevents impossible matches between skincare, haircare, and bodycare formulations
before they reach fuzzy matching or LLM verification.
"""

import re
import unicodedata
import logging
from typing import Tuple, Optional

logger = logging.getLogger("skincare_pipeline.safeguards")

class CosmeticCompatibilityValidator:
    """
    Deterministic rule-based firewall for cosmetic formulation compatibility.
    Catches physically, anatomically, and formulationally impossible matches.
    """

    @staticmethod
    def fix_mojibake(text: str) -> str:
        """Fixes common UTF-8 / Latin-1 encoding glitches (e.g. 'SÃ©bium' -> 'Sébium')."""
        if not text or not isinstance(text, str):
            return ""
        if "Ã" in text or "Â" in text:
            try:
                return text.encode("latin1").decode("utf-8")
            except Exception:
                pass
        return text

    @staticmethod
    def strip_accents(text: str) -> str:
        """Removes diacritical marks (e.g. 'crème' -> 'creme')."""
        return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")

    @classmethod
    def normalize(cls, text: str) -> str:
        text = cls.fix_mojibake(text)
        text = cls.strip_accents(text.lower())
        return " " + re.sub(r"[^a-z0-9%+\.]", " ", text) + " "

    @classmethod
    def validate_pair(
        cls,
        title_a: str,
        title_b: str,
        brand_a: Optional[str] = None,
        brand_b: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates whether title_a and title_b can physically and chemically describe
        the exact same product formulation.

        Returns:
            (is_compatible: bool, rejection_reason: Optional[str])
        """
        if not title_a or not title_b:
            return False, "Empty title provided"

        # -------------------------------------------------------------
        # 0. BRAND COMPATIBILITY SAFEGUARD
        # -------------------------------------------------------------
        if brand_a and brand_b:
            ba_clean = re.sub(r'[^a-z0-9]', '', cls.strip_accents(str(brand_a)).lower())
            bb_clean = re.sub(r'[^a-z0-9]', '', cls.strip_accents(str(brand_b)).lower())
            if ba_clean and bb_clean and ba_clean != bb_clean:
                # Disallow if neither is a close prefix or difference is significant
                if not (ba_clean.startswith(bb_clean) or bb_clean.startswith(ba_clean)) or abs(len(ba_clean) - len(bb_clean)) > 2:
                    return False, f"Impossible Brand: Store brand '{brand_a}' cannot match reference brand '{brand_b}'"

        a = cls.normalize(title_a)
        b = cls.normalize(title_b)

        # -------------------------------------------------------------
        # 0.1 DEODORANT & ANTIPERSPIRANT SAFEGUARD
        # -------------------------------------------------------------
        deo_kws = r"\b(deodorant|antiperspirant|anti-perspirant|roll-on|roll\s*on|sweat\s*control|transpirant|spirial|deo\b|deo-)\b"
        is_deo_a = bool(re.search(deo_kws, a))
        is_deo_b = bool(re.search(deo_kws, b))
        if is_deo_a != is_deo_b:
            return False, "Impossible Category: Deodorant/Antiperspirant cannot match Cream/Lotion/Skincare formulation"

        # -------------------------------------------------------------
        # 0.2 FRAGRANCE & PERFUMERY SAFEGUARD
        # -------------------------------------------------------------
        frag_kws = r"\b(eau\s*de\s*parfum|eau\s*de\s*toilette|edp\b|edt\b|perfume|parfum|cologne|body\s*splash|body\s*mist)\b"
        is_frag_a = bool(re.search(frag_kws, a))
        is_frag_b = bool(re.search(frag_kws, b))
        if is_frag_a != is_frag_b:
            return False, "Impossible Category: Perfume/Fragrance cannot match Skincare treatment"

        # -------------------------------------------------------------
        # 1. ANATOMICAL TARGET CONFLICTS (e.g. Hair Mask vs Face Mask)
        # -------------------------------------------------------------
        has_hair_a = bool(re.search(r"\b(hair|cheveux|capillaire|scalp|shampoo|shampooing|conditioner|apres-shampooing)\b", a))
        has_hair_b = bool(re.search(r"\b(hair|cheveux|capillaire|scalp|shampoo|shampooing|conditioner|apres-shampooing)\b", b))
        has_face_a = bool(re.search(r"\b(face|facial|visage)\b", a))
        has_face_b = bool(re.search(r"\b(face|facial|visage)\b", b))

        # Hair vs Face conflict (impossible case)
        if (has_hair_a and not has_hair_b and has_face_b) or (has_hair_b and not has_hair_a and has_face_a):
            return False, "Impossible Anatomy: Hair product cannot match Face product"

        # Lip vs Eye/Face conflict
        has_lip_a = bool(re.search(r"\b(lip|lips|levre|levres)\b", a))
        has_lip_b = bool(re.search(r"\b(lip|lips|levre|levres)\b", b))
        has_eye_a = bool(re.search(r"\b(eye|eyes|yeux|under-eye|eyelid|ocular|regard)\b", a))
        has_eye_b = bool(re.search(r"\b(eye|eyes|yeux|under-eye|eyelid|ocular|regard)\b", b))

        if has_lip_a != has_lip_b:
            if has_lip_a and (has_eye_b or has_face_b):
                return False, "Impossible Anatomy: Lip product cannot match Eye or Face product"
            if has_lip_b and (has_eye_a or has_face_a):
                return False, "Impossible Anatomy: Lip product cannot match Eye or Face product"

        # Eye vs Full Face/Body (Eye formulations have strict preservative/irritant limits)
        if has_eye_a != has_eye_b:
            return False, "Impossible Anatomy: Dedicated Eye contour product cannot match Full Face/Body product"

        # Body/Hand/Foot vs Dedicated Face
        has_body_a = bool(re.search(r"\b(body|corps|hand|hands|mains|foot|feet|pieds)\b", a))
        has_body_b = bool(re.search(r"\b(body|corps|hand|hands|mains|foot|feet|pieds)\b", b))
        if has_body_a != has_body_b:
            if has_body_a and (has_face_b or has_eye_b) and not has_body_b:
                return False, "Impossible Anatomy: Body/Hand/Foot product cannot match Face/Eye product"
            if has_body_b and (has_face_a or has_eye_a) and not has_body_a:
                return False, "Impossible Anatomy: Body/Hand/Foot product cannot match Face/Eye product"

        # -------------------------------------------------------------
        # 2. RINSE-OFF VS LEAVE-ON (VEHICLE / PHASE CONFLICT)
        # -------------------------------------------------------------
        rinse_kws = r"\b(cleanser|cleansing|wash|gel wash|foaming gel|foam|moussant|micellar|micellaire|soap|savon|scrub|gommage|rinse-off|shampoo|cleansing foam|cleansing gel)\b"
        leave_kws = r"\b(serum|ampoule|moisturizer|moisturiser|cream|creme|balm|baume|lotion|sleeping mask|leave-on|day cream|night cream|toner|toning lotion)\b"
        
        is_rinse_a = bool(re.search(rinse_kws, a))
        is_rinse_b = bool(re.search(rinse_kws, b))
        is_leave_a = bool(re.search(leave_kws, a))
        is_leave_b = bool(re.search(leave_kws, b))

        # Toner vs Cleanser
        has_toner_a = bool(re.search(r"\b(toner|tonique)\b", a))
        has_toner_b = bool(re.search(r"\b(toner|tonique)\b", b))
        if (has_toner_a and is_rinse_b and not has_toner_b) or (has_toner_b and is_rinse_a and not has_toner_a):
            return False, "Impossible Format: Leave-on Toner cannot match Rinse-off Cleanser"

        # Rinse-off vs Pure Leave-on
        if (is_rinse_a and not is_rinse_b and is_leave_b and not is_leave_a):
            return False, "Impossible Format: Rinse-off Cleanser cannot match Leave-on Treatment"
        if (is_rinse_b and not is_rinse_a and is_leave_a and not is_leave_b):
            return False, "Impossible Format: Rinse-off Cleanser cannot match Leave-on Treatment"

        # -------------------------------------------------------------
        # 3. TEXTURE & VEHICLE VISCOSITY
        # -------------------------------------------------------------
        is_serum_a = bool(re.search(r"\b(serum|ampoule|booster|elixir)\b", a))
        is_serum_b = bool(re.search(r"\b(serum|ampoule|booster|elixir)\b", b))
        is_cream_a = bool(re.search(r"\b(cream|creme|balm|baume|butter)\b", a))
        is_cream_b = bool(re.search(r"\b(cream|creme|balm|baume|butter)\b", b))

        if (is_serum_a and not is_serum_b and is_cream_b and not is_cream_a):
            return False, "Impossible Texture: Liquid Serum/Ampoule cannot match Emulsion Cream/Balm"
        if (is_serum_b and not is_serum_a and is_cream_a and not is_cream_b):
            return False, "Impossible Texture: Liquid Serum/Ampoule cannot match Emulsion Cream/Balm"

        # Fluid vs Cream
        is_fluid_a = bool(re.search(r"\b(fluid|fluide)\b", a))
        is_fluid_b = bool(re.search(r"\b(fluid|fluide)\b", b))
        if (is_fluid_a and not is_fluid_b and is_cream_b and not is_cream_a):
            return False, "Impossible Texture: Oil-Control Fluid cannot match Moisturising Cream"
        if (is_fluid_b and not is_fluid_a and is_cream_a and not is_cream_b):
            return False, "Impossible Texture: Oil-Control Fluid cannot match Moisturising Cream"

        # Oil-Free Gel vs Lipid Lotion
        is_gel_a = bool(re.search(r"\b(gel|gel-cream)\b", a))
        is_gel_b = bool(re.search(r"\b(gel|gel-cream)\b", b))
        is_lotion_a = bool(re.search(r"\b(lotion)\b", a))
        is_lotion_b = bool(re.search(r"\b(lotion)\b", b))
        if (is_gel_a and not is_gel_b and is_lotion_b and not is_lotion_a) or (is_gel_b and not is_gel_a and is_lotion_a and not is_lotion_b):
            return False, "Impossible Texture: Oil-Free Gel cannot match Lipid Lotion"

        # -------------------------------------------------------------
        # 4. SUN PROTECTION FACTOR (SPF)
        # -------------------------------------------------------------
        spf_kws = r"\b(spf\s*\d+|spf|sunscreen|sun\s*cream|sun\s*fluid|sun\s*screen|uvmune|pa\+{1,4})\b"
        has_spf_a = bool(re.search(spf_kws, a))
        has_spf_b = bool(re.search(spf_kws, b))
        if has_spf_a != has_spf_b:
            return False, "Impossible UV Protection: SPF Sunscreen cannot match Non-SPF Formulation"

        # -------------------------------------------------------------
        # 5. CIRCADIAN TIMING (DAY VS NIGHT)
        # -------------------------------------------------------------
        has_day_a = bool(re.search(r"\b(day|jour|am)\b", a))
        has_day_b = bool(re.search(r"\b(day|jour|am)\b", b))
        has_night_a = bool(re.search(r"\b(night|nuit|pm|overnight|sleeping)\b", a))
        has_night_b = bool(re.search(r"\b(night|nuit|pm|overnight|sleeping)\b", b))
        if (has_day_a and has_night_b) or (has_night_a and has_day_b):
            return False, "Impossible Regimen: Day Formulation cannot match Night Formulation"

        # -------------------------------------------------------------
        # 6. PIGMENTATION (TINTED VS UNTINTED)
        # -------------------------------------------------------------
        tint_kws = r"\b(tinted|teinte|bb\s*cream|cc\s*cream)\b"
        untint_kws = r"\b(untinted|invisible|translucent|clear)\b"
        has_tint_a = bool(re.search(tint_kws, a))
        has_tint_b = bool(re.search(tint_kws, b))
        has_untint_a = bool(re.search(untint_kws, a))
        has_untint_b = bool(re.search(untint_kws, b))
        if (has_tint_a and has_untint_b) or (has_untint_a and has_tint_b):
            return False, "Impossible Finish: Tinted Makeup/SPF cannot match Untinted/Invisible"

        # -------------------------------------------------------------
        # 7. DERMATOLOGICAL BRAND LINE MUTUAL EXCLUSIVITY
        # -------------------------------------------------------------
        # Bioderma
        bioderma_lines = ["sebium", "sensibio", "atoderm", "hydrabio", "pigmentbio", "cicabio"]
        for i, l1 in enumerate(bioderma_lines):
            for l2 in bioderma_lines[i+1:]:
                if (l1 in a and l2 in b) or (l2 in a and l1 in b):
                    return False, f"Impossible Line: Bioderma {l1.capitalize()} cannot match {l2.capitalize()}"

        # La Roche-Posay
        lrp_lines = ["effaclar", "toleriane", "lipikar", "cicaplast", "anthelios"]
        for i, l1 in enumerate(lrp_lines):
            for l2 in lrp_lines[i+1:]:
                if (l1 in a and l2 in b) or (l2 in a and l1 in b):
                    return False, f"Impossible Line: La Roche-Posay {l1.capitalize()} cannot match {l2.capitalize()}"

        # -------------------------------------------------------------
        # 8. ACTIVE CHEMICAL FAMILY & POTENCY
        # -------------------------------------------------------------
        if ("glycolic" in a and "niacinamide" in b and "glycolic" not in b) or ("glycolic" in b and "niacinamide" in a and "glycolic" not in a):
            return False, "Impossible Active: Exfoliating Glycolic Acid cannot match Niacinamide"

        # Explicit concentration percentages (e.g. 0.3% vs 0.1%, or 10% vs 15%)
        pct_a = re.findall(r"(\d+(?:\.\d+)?)\s*%", a)
        pct_b = re.findall(r"(\d+(?:\.\d+)?)\s*%", b)
        if pct_a and pct_b and pct_a[0] != pct_b[0]:
            return False, f"Impossible Potency: Active concentration {pct_a[0]}% cannot match {pct_b[0]}%"

        # Reedle shot levels: 50, 100, 300, 700
        reedle_a = re.search(r"reedle\s*shot\s*(50|100|300|700)", a)
        reedle_b = re.search(r"reedle\s*shot\s*(50|100|300|700)", b)
        if reedle_a and reedle_b and reedle_a.group(1) != reedle_b.group(1):
            return False, f"Impossible Potency: Reedle Shot {reedle_a.group(1)} cannot match {reedle_b.group(1)}"

        return True, None
