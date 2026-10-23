# dermo_data.py
"""
Production-grade ingredient metadata for dermatology-aware apps.

Design goals:
- Dermatologically accurate core fields (INCI, common name, type, comedogenicity 0-5, irritancy 0-5)
- Evidence notes with references when relevant
- Validation utilities and simple risk-scoring helpers
- Intentionally conservative: when data are variable in literature, represented as "variable" or None
- No medical advice logic; only ingredient-level risk metadata (use alongside clinician decision logic)

Citations (most load-bearing references used in assertions below):
- Coconut oil comedogenicity and common guidance on pore-clogging. :contentReference[oaicite:0]{index=0}
- Isopropyl myristate / isopropyl palmitate: widely flagged as highly comedogenic in classic tables (risk depends on concentration). :contentReference[oaicite:1]{index=1}
- Sodium lauryl sulfate (SLS) is a proven irritant in patch/wash testing (irritancy evidence). :contentReference[oaicite:2]{index=2}
- Fragrance is a common cause of allergic contact dermatitis; fragrance components (limonene, linalool) are frequent sensitizers. :contentReference[oaicite:3]{index=3}
- Topical and systemic retinoids: pregnancy precautions and teratogenicity for systemic agents; topical retinoids are generally advised against in pregnancy. :contentReference[oaicite:4]{index=4}
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import IntEnum
from typing import Dict, List, Optional, Union
import logging

logger = logging.getLogger(__name__)


class Comedogenicity(IntEnum):
    """Comedogenic scale 0-5 (0 = non-comedogenic / 5 = highly comedogenic)."""
    ZERO = 0
    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    VARIABLE = -1  # use when evidence is mixed / concentration-dependent


class Irritancy(IntEnum):
    """Irritancy scale 0-5 (0 = non-irritant / 5 = strong irritant)."""
    ZERO = 0
    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5


@dataclass
class Ingredient:
    inci: str  # canonical INCI name (key)
    common_name: Optional[str]
    functional_type: str  # e.g., 'oil', 'active', 'humectant', 'preservative'
    comedogenicity: Union[Comedogenicity, int]  # 0-5 or Comedogenicity.VARIABLE (-1)
    irritancy: Irritancy
    tags: List[str]
    evidence: Optional[str] = None  # short evidence note
    contraindications: List[str] = None  # clinical contraindications (e.g., 'pregnancy' for retinoids)
    notes: Optional[str] = None  # free text for formulation caveats, concentration sensitivity

    def to_dict(self) -> Dict:
        d = asdict(self)
        # convert enums to ints for JSON friendliness
        if isinstance(self.comedogenicity, Comedogenicity):
            d["comedogenicity"] = int(self.comedogenicity) if self.comedogenicity != Comedogenicity.VARIABLE else "variable"
        if isinstance(self.irritancy, Irritancy):
            d["irritancy"] = int(self.irritancy)
        return d


from rapidfuzz import process, fuzz, utils

# --- Helper utilities --------------------------------------------------------

try:
    from pipeline.db_manager import load_synonyms_from_db, load_clinical_ingredients_from_db
except ImportError:
    try:
        from db_manager import load_synonyms_from_db, load_clinical_ingredients_from_db
    except ImportError:
        def load_synonyms_from_db():
            return {}
        def load_clinical_ingredients_from_db():
            return []

# Cache for fuzzy results and normalized lookup
_FUZZY_CACHE = {}
_LOOKUP_CACHE = {}

def reload_dermo_lookup_cache():
    """Clears cached lookup tables so database updates take effect immediately."""
    global _FUZZY_CACHE, _LOOKUP_CACHE
    _FUZZY_CACHE.clear()
    _LOOKUP_CACHE.clear()

def _build_lookup(db: Dict[str, Ingredient]) -> Dict[str, Ingredient]:
    import re
    lookup = {}
    for k, v in db.items():
        k_lower = k.lower().strip()
        lookup[k_lower] = v
        lookup[k_lower.rstrip('.')] = v
        if v.inci:
            inci_lower = v.inci.lower().strip()
            lookup[inci_lower] = v
            lookup[inci_lower.rstrip('.')] = v
        if v.common_name:
            cn_lower = v.common_name.lower().strip()
            lookup[cn_lower] = v
            lookup[cn_lower.rstrip('.')] = v
            c = re.sub(r'\(.*?\)', '', cn_lower).strip()
            if c:
                lookup[c] = v
                lookup[c.rstrip('.')] = v

    db_synonyms = load_synonyms_from_db()
    for alias, target in db_synonyms.items():
        a = alias.lower().strip()
        t = target.lower().strip()
        if target in db:
            lookup[a] = db[target]
        elif t in lookup:
            lookup[a] = lookup[t]
        elif a in lookup and t not in lookup:
            lookup[t] = lookup[a]

    return lookup

def _get_ingredient_fuzzy(unci_name: str, db: Dict[str, Ingredient]) -> Optional[Ingredient]:
    """
    Finds an ingredient using exact match, normalized alias/common-name match,
    and fuzzy matching with threshold 85.
    """
    if not unci_name or not isinstance(unci_name, str):
        return None
    raw_name = unci_name.strip()
    if not raw_name:
        return None

    # 1. Exact match on raw DB key
    if raw_name in db:
        return db[raw_name]

    clean_key = raw_name.lower().rstrip('.')

    # 2. Check normalized alias/common-name table
    db_id = id(db)
    if db_id not in _LOOKUP_CACHE:
        _LOOKUP_CACHE[db_id] = _build_lookup(db)
    lookup = _LOOKUP_CACHE[db_id]

    if clean_key in lookup:
        return lookup[clean_key]

    # 3. Check fuzzy cache
    if clean_key in _FUZZY_CACHE:
        match_key = _FUZZY_CACHE[clean_key]
        return lookup.get(match_key) if match_key else None

    # 4. Fuzzy match against normalized lookup keys
    result = process.extractOne(clean_key, lookup.keys(), scorer=fuzz.token_sort_ratio, processor=utils.default_process)
    if result:
        match_key, score, _ = result
        if score >= 85:
            _FUZZY_CACHE[clean_key] = match_key
            return lookup[match_key]

    _FUZZY_CACHE[clean_key] = None
    return None

def _validate_rating(value: Union[Comedogenicity, int, str]) -> Union[Comedogenicity, int]:
    if value == "variable" or value == Comedogenicity.VARIABLE:
        return Comedogenicity.VARIABLE
    try:
        iv = int(value)
    except Exception:
        raise ValueError(f"Invalid comedogenicity value: {value}")
    if iv < 0 or iv > 5:
        raise ValueError("Comedogenicity must be 0-5 or 'variable'")
    return Comedogenicity(iv)


def _validate_irritancy(value: Union[Irritancy, int]) -> Irritancy:
    try:
        iv = int(value)
    except Exception:
        raise ValueError(f"Invalid irritancy value: {value}")
    if iv < 0 or iv > 5:
        raise ValueError("Irritancy must be 0-5")
    return Irritancy(iv)


def validate_db(db: Dict[str, Ingredient]) -> None:
    """Run simple validation checks on INGREDIENT_DB; raises ValueError on failure."""
    for k, v in db.items():
        if not isinstance(k, str):
            raise ValueError("Ingredient key must be INCI string")
        if not isinstance(v, Ingredient):
            raise ValueError(f"Ingredient {k} must be an Ingredient dataclass instance")
        # validate ratings
        _ = _validate_rating(v.comedogenicity)
        _ = _validate_irritancy(v.irritancy)
    logger.debug("INGREDIENT_DB validation passed.")


def acne_risk_score(ingredients: List[str], db: Dict[str, Ingredient]) -> float:
    """
    Simple aggregate acne-risk score for a formula:
    - weights comedogenicity and irritancy; returns a 0-10 scale (higher = more risky for acne-prone skin)
    - This is a heuristic, not a clinical decision tool.
    """
    total = 0.0
    count = 0
    for inci in ingredients:
        entry = _get_ingredient_fuzzy(inci, db)
        if not entry:
            continue
        # treat 'variable' as neutral-to-mild risk (1.5)
        c_val = (entry.comedogenicity.value if isinstance(entry.comedogenicity, Comedogenicity) and entry.comedogenicity != Comedogenicity.VARIABLE
                 else 1.5 if entry.comedogenicity == Comedogenicity.VARIABLE else int(entry.comedogenicity))
        i_val = int(entry.irritancy)
        # basic weighting: comedogenicity * 0.6 + irritancy * 0.4
        total += (0.6 * c_val + 0.4 * i_val)
        count += 1
    return (total / count) if count else 0.0


def recommend_for_skin_type(ingredients: List[str], db: Dict[str, Ingredient], skin_type: str) -> Dict:
    """
    Returns:
      - overall risk score
      - list of flagged ingredients with reasons
    skin_type: one of 'oily', 'acne_prone', 'sensitive', 'dry', 'normal'
    """
    flagged = []
    for inci in ingredients:
        entry = _get_ingredient_fuzzy(inci, db)
        if not entry:
            continue
        if skin_type in ("oily", "acne_prone"):
            if (isinstance(entry.comedogenicity, Comedogenicity) and entry.comedogenicity >= Comedogenicity.THREE) or entry.comedogenicity == Comedogenicity.VARIABLE:
                flagged.append((inci, "comedogenic_risk"))
        if skin_type == "sensitive":
            if entry.irritancy >= Irritancy.THREE or "fragrance" in entry.tags or "essential_oil" in entry.tags:
                flagged.append((inci, "irritancy_risk"))
        if skin_type == "dry":
            # flag harsh detergents / alcohol denat if present
            if entry.inci in ("alcohol_denat", "sodium_lauryl_sulfate"):
                flagged.append((inci, "drying_agent"))
    return {"risk_score": acne_risk_score(ingredients, db), "flagged": flagged}


# --- Ingredient database (INCI -> Ingredient) --------------------------------
# Keep this authoritative, minimal, and conservative. Use "variable" when literature
# shows concentration-dependent or fraction-dependent behavior.

STATIC_INGREDIENT_DB: Dict[str, Ingredient] = {
    # ACNE TRIGGERS / high comedogenic potential (concentration-dependent)
    "Cocos Nucifera (Coconut) Oil": Ingredient(
        inci="Cocos Nucifera (Coconut) Oil",
        common_name="Coconut oil",
        functional_type="oil",
        comedogenicity=Comedogenicity.FOUR,
        irritancy=Irritancy.ONE,
        tags=["moisturizer", "occlusive"],
        evidence="Widely reported high comedogenic rating; tends to clog pores in acne-prone skin at leave-on concentrations. See dermatology literature and formulation tables. :contentReference[oaicite:5]{index=5}",
        notes="Effective emollient for dry skin; avoid in acne-prone/oily skin when used as primary leave-on oil."
    ),
    "Theobroma Cacao (Cocoa) Seed Butter": Ingredient(
        inci="Theobroma Cacao (Cocoa) Seed Butter",
        common_name="Cocoa butter",
        functional_type="butter",
        comedogenicity=Comedogenicity.FOUR,
        irritancy=Irritancy.ONE,
        tags=["moisturizer"],
        notes="Rich occlusive; useful for very dry skin but can be pore-clogging in acne-prone skin."
    ),
    "Isopropyl Myristate": Ingredient(
        inci="Isopropyl Myristate",
        common_name="Isopropyl myristate",
        functional_type="emollient",
        comedogenicity=Comedogenicity.FIVE,
        irritancy=Irritancy.TWO,
        tags=["texture", "emollient"],
        evidence="Classic comedogenicity tables flag IPM/IPMa as high risk, particularly in leave-on formulations and at elevated concentrations. :contentReference[oaicite:6]{index=6}",
        notes="Often used to increase spreadability; avoid in acne-prone formulations or keep at low concentrations in rinse-off."
    ),
    "Isopropyl Palmitate": Ingredient(
        inci="Isopropyl Palmitate",
        common_name="Isopropyl palmitate",
        functional_type="emollient",
        comedogenicity=Comedogenicity.FOUR,
        irritancy=Irritancy.ONE,
        tags=["texture"]
    ),
    "Ethylhexyl Palmitate": Ingredient(
        inci="Ethylhexyl Palmitate",
        common_name="Ethylhexyl palmitate",
        functional_type="emollient",
        comedogenicity=Comedogenicity.FOUR,
        irritancy=Irritancy.ONE,
        tags=["texture"]
    ),

    # SURFACTANTS / CLEANSERS
    "Sodium Laureth Sulfate": Ingredient(
        inci="Sodium Laureth Sulfate",
        common_name="SLES",
        functional_type="surfactant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.TWO,
        tags=["cleanser"],
        notes="Less irritating than SLS for many users, but still can be drying/irritating depending on formulation."
    ),
    "Sodium Lauryl Sulfate": Ingredient(
        inci="Sodium Lauryl Sulfate",
        common_name="SLS",
        functional_type="surfactant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.FIVE,
        tags=["cleanser", "irritant"],
        evidence="SLS is a well-documented irritant in patch/wash models and occupational exposure studies. Avoid in sensitive-skin-targeted cleansers. :contentReference[oaicite:7]{index=7}",
        notes="Primarily an irritant; not a comedogen per se."
    ),

    # SENSITIZERS / FRAGRANCE / ESSENTIAL OILS
    "Alcohol Denat.": Ingredient(
        inci="Alcohol Denat.",
        common_name="Denatured alcohol",
        functional_type="solvent",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.THREE,
        tags=["drying"],
        evidence="Denatured alcohol can be drying and can exacerbate barrier dysfunction in sensitive/dry skin. Formulations and co-ingredients modify the clinical effect. :contentReference[oaicite:8]{index=8}",
        notes="Short-term texture benefits; avoid as primary active in dry/sensitive-skin leave-on products."
    ),
    "Parfum": Ingredient(
        inci="Parfum",
        common_name="Fragrance (generic)",
        functional_type="fragrance",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.FOUR,
        tags=["fragrance"],
        evidence="Fragrance is a leading cause of allergic contact dermatitis from cosmetics; many fragrance components (limonene, linalool) are sensitizers. :contentReference[oaicite:9]{index=9}",
        notes="Use fragrance-free labeling for sensitive-skin products."
    ),
    "Menthol": Ingredient(
        inci="Menthol",
        common_name="Menthol",
        functional_type="sensitizer",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.FIVE,
        tags=["cooling", "sensitizer"],
        notes="Common irritant and sensitizer in leave-on products."
    ),
    "Melaleuca Alternifolia (Tea Tree) Leaf Oil": Ingredient(
        inci="Melaleuca Alternifolia (Tea Tree) Leaf Oil",
        common_name="Tea tree oil",
        functional_type="essential_oil",
        comedogenicity=Comedogenicity.ONE,
        irritancy=Irritancy.TWO,
        tags=["antimicrobial", "acne_fighting"],
        notes="Has antimicrobial properties but can be irritating/sensitizing to some users; concentration-dependent."
    ),

    # HEROES / ACTIVES
    "Niacinamide": Ingredient(
        inci="Niacinamide",
        common_name="Niacinamide (Vitamin B3)",
        functional_type="active",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ONE,
        tags=["brightening", "acne_fighting", "barrier_repair"],
        evidence="Clinical testing shows good tolerability up to commonly used concentrations; widely used for barrier support and hyperpigmentation. :contentReference[oaicite:10]{index=10}",
        notes="Generally well tolerated; rare sensitivity reported at higher concentrations."
    ),
    "Salicylic Acid": Ingredient(
        inci="Salicylic Acid",
        common_name="Salicylic acid (BHA)",
        functional_type="exfoliant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.TWO,
        tags=["acne_fighting", "bha"],
        evidence="Proven efficacy for acne; formulations may cause dryness/irritation depending on pH and vehicle. :contentReference[oaicite:11]{index=11}"
    ),
    "Glycolic Acid": Ingredient(
        inci="Glycolic Acid",
        common_name="Glycolic acid (AHA)",
        functional_type="exfoliant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.THREE,
        tags=["anti_aging", "aha"],
        notes="Irritancy depends on concentration/pH; leave-on high-strength AHAs can be sensitizing."
    ),
    "Retinol": Ingredient(
        inci="Retinol",
        common_name="Retinol (vitamin A derivative)",
        functional_type="retinoid",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.FOUR,
        tags=["anti_aging", "acne_fighting"],
        contraindications=["pregnancy"],
        evidence="Topical retinoids increase skin turnover and can irritate; systemic retinoids are teratogenic and topical retinoids are generally advised against in pregnancy. :contentReference[oaicite:12]{index=12}",
        notes="Introduce slowly; use sunscreen due to photosensitivity."
    ),
    "L-Ascorbic Acid": Ingredient(
        inci="Ascorbic Acid",
        common_name="L-ascorbic acid (Vitamin C)",
        functional_type="active",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.THREE,
        tags=["brightening", "antioxidant"],
        notes="Potent antioxidant but acidic and can irritate sensitive skin; stability issues require appropriate formulation."
    ),
    "Sodium Ascorbyl Phosphate": Ingredient(
        inci="Sodium Ascorbyl Phosphate",
        common_name="Sodium ascorbyl phosphate (SAP)",
        functional_type="active",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ONE,
        tags=["brightening", "vitamin_c"],
        evidence="SAP is a more stable, gentler vitamin C derivative often used for sensitive or acne-prone skin. :contentReference[oaicite:13]{index=13}",
        notes="Less irritating than L-ascorbic acid in many formulations."
    ),

    # HYDRATORS & SOOTHERS
    "Sodium Hyaluronate": Ingredient(
        inci="Sodium Hyaluronate",
        common_name="Sodium hyaluronate",
        functional_type="humectant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["hydration"]
    ),
    "Hyaluronic Acid": Ingredient(
        inci="Hyaluronic Acid",
        common_name="Hyaluronic acid",
        functional_type="humectant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["hydration"]
    ),
    "Glycerin": Ingredient(
        inci="Glycerin",
        common_name="Glycerin",
        functional_type="humectant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["hydration"]
    ),
    "Ceramide NP": Ingredient(
        inci="Ceramide NP",
        common_name="Ceramide NP",
        functional_type="lipid",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["barrier_repair"]
    ),
    "Panthenol": Ingredient(
        inci="Panthenol",
        common_name="Panthenol (pro-vitamin B5)",
        functional_type="humectant",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["soothing"]
    ),
    "Allantoin": Ingredient(
        inci="Allantoin",
        common_name="Allantoin",
        functional_type="soother",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["soothing"]
    ),
    "Centella Asiatica Extract": Ingredient(
        inci="Centella Asiatica Extract",
        common_name="Centella asiatica",
        functional_type="extract",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["soothing"]
    ),
    "Aloe Barbadensis Leaf Juice": Ingredient(
        inci="Aloe Barbadensis Leaf Juice",
        common_name="Aloe vera",
        functional_type="extract",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ZERO,
        tags=["soothing"]
    ),

    # OILS
    "Squalane": Ingredient(
        inci="Squalane",
        common_name="Squalane (plant/synthetic)",
        functional_type="oil",
        comedogenicity=Comedogenicity.ONE,
        irritancy=Irritancy.ZERO,
        tags=["moisturizer"],
        notes="Generally low comedogenic risk and well tolerated in many skin types."
    ),
    "Simmondsia Chinensis (Jojoba) Seed Oil": Ingredient(
        inci="Simmondsia Chinensis (Jojoba) Seed Oil",
        common_name="Jojoba oil",
        functional_type="oil",
        comedogenicity=Comedogenicity.TWO,
        irritancy=Irritancy.ZERO,
        tags=["moisturizer"]
    ),
    "Butyrospermum Parkii (Shea) Butter": Ingredient(
        inci="Butyrospermum Parkii (Shea) Butter",
        common_name="Shea butter",
        functional_type="butter",
        comedogenicity=Comedogenicity.TWO,
        irritancy=Irritancy.ZERO,
        tags=["moisturizer"],
        notes="Useful for dry skin; may be heavier for oily/acne-prone skin depending on formula."
    ),

    # PRESERVATIVES & STABILIZERS
    "Phenoxyethanol": Ingredient(
        inci="Phenoxyethanol",
        common_name="Phenoxyethanol",
        functional_type="preservative",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.ONE,
        tags=["preservative"]
    ),
    "Parabens": Ingredient(
        inci="Parabens",
        common_name="Parabens (methylparaben, propylparaben etc.)",
        functional_type="preservative",
        comedogenicity=Comedogenicity.ZERO,
        irritancy=Irritancy.TWO,
        tags=["preservative"]
    ),
    "Tocopherol": Ingredient(
        inci="Tocopherol",
        common_name="Vitamin E (tocopherol)",
        functional_type="antioxidant",
        comedogenicity=Comedogenicity.TWO,
        irritancy=Irritancy.ONE,
        tags=["vitamin_e"]
    ),
}

def load_clinical_db() -> Dict[str, Ingredient]:
    """Loads authoritative clinical ingredient metadata from SQLite with static fallback."""
    db = {}
    rows = load_clinical_ingredients_from_db()
    if rows:
        for r in rows:
            inci = r["inci"]
            c_val = r["comedogenicity"]
            i_val = r["irritancy"]
            try:
                comedo = Comedogenicity(c_val)
            except ValueError:
                comedo = Comedogenicity.VARIABLE if c_val == -1 else Comedogenicity.ZERO
            try:
                irrit = Irritancy(i_val)
            except ValueError:
                irrit = Irritancy.ZERO
            db[inci] = Ingredient(
                inci=inci,
                common_name=r["common_name"],
                functional_type=r["functional_type"],
                comedogenicity=comedo,
                irritancy=irrit,
                tags=r["tags"] or [],
                evidence=r["evidence"],
                contraindications=r["contraindications"] or [],
                notes=r["notes"]
            )
        return db
    return STATIC_INGREDIENT_DB

INGREDIENT_DB: Dict[str, Ingredient] = load_clinical_db()

def reload_clinical_db():
    """Reloads clinical ingredients from SQLite and clears lookup caches."""
    global INGREDIENT_DB
    INGREDIENT_DB = load_clinical_db()
    reload_dermo_lookup_cache()
    return INGREDIENT_DB

def evaluate_product_dermo(ingredients: List[str], skin_type: str = "normal", db: Dict[str, Ingredient] = None) -> Dict[str, Any]:
    """
    Evaluates complete dermatological profile:
    - Comedogenicity (acne risk score 0.0 - 10.0)
    - Irritancy (irritancy risk score 0.0 - 10.0)
    - Detailed list of flagged pore-clogging and irritating ingredients with clinical citations
    - Skin-type specific warnings (oily, acne_prone, sensitive, dry)
    """
    if db is None:
        db = INGREDIENT_DB

    comedogenic_items = []
    irritating_items = []
    skin_flagged = []
    contraindications = []
    total_comedo = 0.0
    total_irrit = 0.0
    count = 0

    st_clean = (skin_type or "normal").lower().strip()

    for raw_inci in ingredients:
        entry = _get_ingredient_fuzzy(raw_inci, db)
        if not entry:
            continue

        count += 1
        c_val = (entry.comedogenicity.value if isinstance(entry.comedogenicity, Comedogenicity) and entry.comedogenicity != Comedogenicity.VARIABLE
                 else 1.5 if entry.comedogenicity == Comedogenicity.VARIABLE else int(entry.comedogenicity))
        i_val = int(entry.irritancy)

        total_comedo += c_val
        total_irrit += i_val

        # Comedogenic check (>= 2 or variable)
        if c_val >= 2.0 or entry.comedogenicity == Comedogenicity.VARIABLE:
            comedogenic_items.append({
                "ingredient": raw_inci,
                "canonical_inci": entry.inci,
                "common_name": entry.common_name,
                "comedogenicity": c_val,
                "evidence": entry.evidence,
                "notes": entry.notes
            })

        # Irritancy check (>= 2)
        if i_val >= 2:
            irritating_items.append({
                "ingredient": raw_inci,
                "canonical_inci": entry.inci,
                "common_name": entry.common_name,
                "irritancy": i_val,
                "evidence": entry.evidence,
                "notes": entry.notes
            })

        # Skin-type specific flagging
        if st_clean in ("oily", "acne_prone"):
            if c_val >= 3.0 or entry.comedogenicity == Comedogenicity.VARIABLE:
                skin_flagged.append({
                    "ingredient": raw_inci,
                    "reason": "comedogenic_pore_clogging_risk",
                    "severity": "high" if c_val >= 4.0 else "moderate",
                    "detail": entry.notes or entry.evidence
                })
        if st_clean == "sensitive":
            if i_val >= 3 or "fragrance" in entry.tags or "essential_oil" in entry.tags:
                skin_flagged.append({
                    "ingredient": raw_inci,
                    "reason": "irritancy_or_sensitizer_risk",
                    "severity": "high" if i_val >= 4 else "moderate",
                    "detail": entry.notes or entry.evidence
                })
        if st_clean == "dry":
            if entry.inci in ("Alcohol Denat.", "Sodium Lauryl Sulfate") or "drying" in entry.tags:
                skin_flagged.append({
                    "ingredient": raw_inci,
                    "reason": "barrier_drying_agent",
                    "severity": "moderate",
                    "detail": entry.notes or entry.evidence
                })

        if entry.contraindications:
            for c in entry.contraindications:
                contraindications.append({"ingredient": entry.inci, "contraindication": c, "notes": entry.notes})

    acne_score = round((total_comedo / count) * 2.0, 2) if count else 0.0 # scale 0-5 to 0-10
    irrit_score = round((total_irrit / count) * 2.0, 2) if count else 0.0 # scale 0-5 to 0-10
    overall_score = round((0.6 * acne_score + 0.4 * irrit_score), 2)

    is_suitable = len(skin_flagged) == 0 and overall_score < 6.0

    return {
        "skin_type": st_clean,
        "acne_risk_score": acne_score,         # 0.0 to 10.0
        "irritancy_risk_score": irrit_score,   # 0.0 to 10.0
        "overall_risk_score": overall_score,   # 0.0 to 10.0
        "is_suitable_for_skin_type": is_suitable,
        "total_clinical_matches": count,
        "comedogenic_ingredients": comedogenic_items,
        "irritating_ingredients": irritating_items,
        "flagged_for_skin_type": skin_flagged,
        "contraindications": contraindications
    }

# Validate on import
try:
    validate_db(INGREDIENT_DB)
except Exception as exc:
    logger.exception("INGREDIENT_DB failed validation on import: %s", exc)
    raise
