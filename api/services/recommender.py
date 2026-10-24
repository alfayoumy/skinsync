"""
Recommender Service: SOTA Hybrid Formulation Similarity, Clinical Skin Safety Scoring,
FastEmbed Cross-Encoder Reranking, and Jev 1.13 / Gemini Reranker Integration.
"""

import os
import re
import json
import urllib.request
import urllib.error
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, List, Tuple, Set

from pipeline.config import cfg
from pipeline.logging_config import get_logger
from pipeline.inci_matcher import clean_product_title, verify_match_with_gemini, score_cross_encoder
from dermo_data import INGREDIENT_DB, Comedogenicity, Irritancy, _get_ingredient_fuzzy
from services.catalog_service import get_catalog_df, SKIN_BENEFITS, SKIN_RISKS

logger = get_logger("skincare_engine.recommender")

def get_inci_concentration_weight(idx: int, ingredient_name: str) -> float:
    """
    3-Zone concentration weight reflecting FDA/EU cosmetic labeling regulations:
    - Zone 1 Carrier (i=0): Discount ubiquitous water/solvent (0.25) to avoid false similarity.
    - Zone 1 Descending (1 <= i <= 5): Exponential decay for primary ingredients > 1%.
    - Zone 2 Plateau (6 <= i <= 15): Flat plateau (0.30) for sub-1% threshold where ordering is arbitrary.
    - Zone 3 Micro-additives (i > 15): Baseline trace weight (0.10).
    """
    ing_lower = str(ingredient_name).lower().strip()
    if idx == 0:
        if ing_lower in {'water', 'aqua', 'eau', 'purified water', 'deionized water'}:
            return 0.25
        return 0.90
    if 1 <= idx <= 5:
        return float(np.exp(-0.22 * (idx - 1)) * 0.90)
    if 6 <= idx <= 15:
        return 0.30
    return 0.10

WATER_SOLVENTS = {
    'water', 'aqua', 'eau', 'purified water', 'deionized water',
    'aqua / water / eau', 'aqua (water)', 'water/aqua/eau',
    'aqua/water', 'eau/water', 'water (aqua)', 'water (aqua/eau)',
    'eau thermale', 'thermal water'
}

VEHICLE_PATTERNS: List[Tuple[str, str]] = [
    ('cleansing_balm', r'\b(cleansing\s*balm|cleaning\s*balm|cleanser\s*balm|balm\s*cleanser|baume\s*nettoyant)\b'),
    ('cleansing_oil', r'\b(cleansing\s*oil|oil\s*cleanser|huile\s*nettoyante)\b'),
    ('micellar', r'\b(micellar|eau\s*micellaire)\b'),
    ('foam_cleanser', r'\b(cleansing\s*foam|foaming\s*cleanser|foam\s*wash|foaming\s*gel|gel\s*moussant)\b'),
    ('gel_cleanser', r'\b(cleansing\s*gel|gel\s*cleanser|wash\s*gel|gel\s*wash)\b'),
    ('balm', r'\b(balm|baume)\b'),
    ('serum', r'\b(serum|ampoule|booster|concentrate|elixir|essence)\b'),
    ('gel_cream', r'\b(gel-cream|gel\s*cream|water\s*gel|hydro\s*gel)\b'),
    ('cream', r'\b(cream|creme|rich\s*cream|cold\s*cream|soothing\s*cream|butter)\b'),
    ('lotion', r'\b(lotion|emulsion|fluid|fluide|milk|lait)\b'),
    ('gel', r'\b(gel\b)\b'),
    ('oil', r'\b(face\s*oil|facial\s*oil|oil\b|huile)\b'),
    ('mask', r'\b(mask|masque|sheet\s*mask)\b'),
    ('scrub', r'\b(scrub|peeling|exfoliant|gommage)\b'),
    ('stick', r'\b(stick\b)\b'),
    ('spray', r'\b(mist|spray|vaporisateur)\b'),
    ('pad', r'\b(pads?|peel\s*pads?)\b')
]

def extract_vehicle(title: str) -> str:
    """Detects formulation vehicle/texture from title."""
    t = str(title).lower()
    for v_name, pat in VEHICLE_PATTERNS:
        if re.search(pat, t):
            return v_name
    return 'general'

def compute_vehicle_sim(v1: str, v2: str) -> float:
    """Computes formulation vehicle compatibility."""
    if v1 == v2 and v1 != 'general':
        return 1.0
    if (v1, v2) in {('cleansing_balm', 'cleansing_oil'), ('cleansing_oil', 'cleansing_balm')}:
        return 0.65
    if (v1, v2) in {('gel_cleanser', 'foam_cleanser'), ('foam_cleanser', 'gel_cleanser')}:
        return 0.75
    if (v1, v2) in {('cream', 'gel_cream'), ('gel_cream', 'cream'), ('cream', 'lotion'), ('lotion', 'cream')}:
        return 0.70
    if (v1, v2) in {('serum', 'lotion'), ('lotion', 'serum')}:
        return 0.50
    return 0.40 if (v1 == 'general' or v2 == 'general') else 0.20

def compute_weighted_jaccard(list_a: List[str], list_b: List[str]) -> float:
    """Computes weighted Jaccard similarity across two ordered ingredient lists."""
    if not list_a or not list_b:
        return 0.0
    weights_a = {ing: get_inci_concentration_weight(i, ing) for i, ing in enumerate(list_a)}
    weights_b = {ing: get_inci_concentration_weight(i, ing) for i, ing in enumerate(list_b)}
    all_keys = set(weights_a.keys()).union(weights_b.keys())
    inter_w = sum(min(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    union_w = sum(max(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    return (inter_w / union_w) if union_w > 0 else 0.0

def compute_non_water_jaccard(list_a: List[str], list_b: List[str]) -> float:
    """Computes weighted Jaccard similarity across non-water formulation ingredients."""
    if not list_a or not list_b:
        return 0.0
    filtered_a = [ing for ing in list_a if ing.lower().strip() not in WATER_SOLVENTS]
    filtered_b = [ing for ing in list_b if ing.lower().strip() not in WATER_SOLVENTS]
    if not filtered_a or not filtered_b:
        return 0.0
    weights_a = {ing: get_inci_concentration_weight(i, ing) for i, ing in enumerate(filtered_a)}
    weights_b = {ing: get_inci_concentration_weight(i, ing) for i, ing in enumerate(filtered_b)}
    all_keys = set(weights_a.keys()).union(weights_b.keys())
    inter_w = sum(min(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    union_w = sum(max(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    return (inter_w / union_w) if union_w > 0 else 0.0

def compute_functional_title_sim(t1: str, t2: str, brand1: str = "", brand2: str = "") -> float:
    """Computes functional token Jaccard similarity without brand bias."""
    c1 = clean_product_title(str(t1)).lower()
    c2 = clean_product_title(str(t2)).lower()
    if brand1:
        for b in brand1.lower().split():
            if len(b) > 2:
                c1 = re.sub(r'\b' + re.escape(b) + r'\b', ' ', c1)
    if brand2:
        for b in brand2.lower().split():
            if len(b) > 2:
                c2 = re.sub(r'\b' + re.escape(b) + r'\b', ' ', c2)
    w1 = set(re.findall(r'\w+', c1))
    w2 = set(re.findall(r'\w+', c2))
    stop = {'the', 'and', 'with', 'for', 'skin', 'in', 'of', 'face', 'ml', 'g', 'oz', 'mg'}
    w1 = w1 - stop
    w2 = w2 - stop
    if not w1 or not w2:
        return 0.0
    return len(w1.intersection(w2)) / len(w1.union(w2))

def compute_title_sim(t1: str, t2: str) -> float:
    """Computes token Jaccard similarity between two product titles excluding stop words."""
    return compute_functional_title_sim(t1, t2)

def evaluate_skin_safety(parsed_ings: List[str], actives: Set[str], skin_type: str) -> Tuple[float, List[str]]:
    """
    Dermatological safety evaluator integrating clinical metadata from dermo_data.py
    (comedogenicity 0-5, irritancy 0-5, contraindications) with position concentration weighting.
    """
    warnings = []
    comedogenic_load = 0.0
    irritancy_load = 0.0
    total_weight = 0.0

    for idx, ing in enumerate(parsed_ings):
        w = get_inci_concentration_weight(idx, ing)
        total_weight += w

        # Clinical DB lookup (fuzzy match against dermo_data.INGREDIENT_DB)
        entry = _get_ingredient_fuzzy(ing, INGREDIENT_DB)
        if entry:
            c_val = (
                entry.comedogenicity.value
                if isinstance(entry.comedogenicity, Comedogenicity) and entry.comedogenicity != Comedogenicity.VARIABLE
                else 2.0 if entry.comedogenicity == Comedogenicity.VARIABLE
                else int(entry.comedogenicity)
            )
            comedogenic_load += c_val * w

            i_val = int(entry.irritancy) if isinstance(entry.irritancy, Irritancy) else int(entry.irritancy or 0)
            irritancy_load += i_val * w

            if skin_type in ('oily', 'acne_prone') and c_val >= 3:
                name_display = entry.common_name or entry.inci
                warnings.append(f"Contains {name_display} (comedogenic rating {c_val}/5)")
            if skin_type == 'sensitive' and i_val >= 3:
                name_display = entry.common_name or entry.inci
                warnings.append(f"Contains {name_display} (irritant rating {i_val}/5)")
            if entry.contraindications:
                for c in entry.contraindications:
                    warnings.append(f"Caution: {c} precaution ({entry.common_name or entry.inci})")

    # Secondary classical skin risks check
    if skin_type in SKIN_RISKS:
        for ing in parsed_ings:
            for r_name, r_val in SKIN_RISKS[skin_type].items():
                if r_name in ing:
                    flag_msg = f"Contains {r_name.title()} ({skin_type} skin flag)"
                    if flag_msg not in warnings:
                        warnings.append(flag_msg)
                    break

    benefit_pts = 0.0
    if skin_type in SKIN_BENEFITS:
        benefit_pts = sum(SKIN_BENEFITS[skin_type].get(act, 0.0) for act in actives)

    norm_c = (comedogenic_load / total_weight) if total_weight > 0 else 0.0
    norm_i = (irritancy_load / total_weight) if total_weight > 0 else 0.0

    if skin_type in ('oily', 'acne_prone'):
        risk_penalty = (norm_c * 8.0) + (norm_i * 4.0)
    elif skin_type == 'sensitive':
        risk_penalty = (norm_i * 10.0) + (norm_c * 3.0)
    elif skin_type == 'dry':
        risk_penalty = (norm_i * 5.0)
    else:
        risk_penalty = (norm_c * 3.0) + (norm_i * 3.0)

    safety_score = 75.0 + min(25.0, benefit_pts * 4.0) - min(50.0, risk_penalty * 3.0)
    safety_score = max(20.0, min(100.0, float(safety_score)))

    unique_warnings = list(dict.fromkeys(warnings))
    return safety_score, unique_warnings

def call_jev_reranker(source_title: str, candidate_title: str, candidate_ings: str, skin_type: str) -> Optional[float]:
    """Queries Jev 1.13 decision model via OpenCode Zen or TypeSafe API for dupe equivalence scoring."""
    opencode_key = os.environ.get("OPENCODE_API_KEY")
    typesafe_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if opencode_key:
        api_key = opencode_key
        url = "https://opencode.ai/zen/v1/systemone"
        model_name = cfg.JEV_MODEL_NAME or "jev-1.13-free"
    elif typesafe_key:
        api_key = typesafe_key
        url = "https://api.typesafe.ai/v1/systemone"
        model_name = "jev-latest"
    else:
        return None

    payload = {
        "model": model_name,
        "state": f"Target Product: {source_title}. Candidate Dupe: {candidate_title}. Candidate Ingredients: {candidate_ings}. User Skin Type: {skin_type}.",
        "questions": {
            "is_effective_dupe": {
                "type": "noul",
                "instructions": "Is the candidate product an effective cosmetic dupe or functional alternative with equivalent active ingredients?"
            }
        }
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; SkincareAI/2.0)"
            }
        )
        with urllib.request.urlopen(req, timeout=cfg.JEV_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            answers = data.get("answers", {})
            dupe_info = answers.get("is_effective_dupe", {})
            if isinstance(dupe_info, dict) and "noul" in dupe_info:
                return float(dupe_info["noul"])
            elif isinstance(dupe_info, (int, float)):
                return float(dupe_info)
            return None
    except Exception as e:
        logger.warning(f"Jev API call skipped/failed: {e}")
        return None

def call_gemini_reranker(source_title: str, candidate_title: str, candidate_ings: str, skin_type: str) -> Optional[float]:
    """Queries Gemini 3.5 Flash-Lite fallback model for semantic dupe equivalence verification."""
    gemini_key = os.environ.get("GEMINI_API_KEY") or cfg.GEMINI_API_KEY
    if not gemini_key:
        return None
    try:
        res = verify_match_with_gemini(
            store_title=source_title,
            candidate_title=candidate_title
        )
        if res is not None:
            is_same, conf = res
            return float(conf) if is_same else float(conf * 0.3)
        return None
    except Exception as e:
        logger.warning(f"Gemini fallback reranker call failed: {e}")
        return None

def get_recommendations(
    target_title: str,
    skin_type: str = "normal",
    category_mode: str = "same_category",
    target_category: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Finds the top cosmetic alternatives/dupes for a target product using SOTA hybrid scoring:
    - Formulation function over brand: zero brand bias in title matching and cross-encoder
    - Category filtering modes: 'same_category' (default), 'cross_category', or specific target category
    - Active ingredient priority: candidates matching target's non-water key actives are heavily boosted,
      candidates lacking target actives are penalized
    - Non-water formulation Jaccard: ubiquitous water/aqua solvents are excluded
    - Texture/vehicle compatibility (e.g. cleansing balm vs cleansing balm)
    - FastEmbed ONNX Cross-Encoder reranking (unbiased, comparing texture, actives & non-water formulation)
    - Clinical skin safety and tolerance adjustments
    """
    df = get_catalog_df()
    matches = df[df['title'].str.lower() == target_title.lower()]
    if matches.empty:
        matches = df[df['title'].str.contains(re.escape(target_title), case=False, na=False)]
    if matches.empty:
        return None

    target = matches.iloc[0]
    target_id = target['id']
    target_cat = target['std_category']
    target_ings = target['parsed_ingredients']
    target_actives = target['all_actives']
    target_clean_title = clean_product_title(str(target['title']))
    target_vehicle = extract_vehicle(target['title'])
    target_non_water = [ing for ing in target_ings if ing.lower().strip() not in WATER_SOLVENTS]

    # Category candidate filtering
    if target_category and target_category != 'all':
        candidates = df[df['std_category'] == target_category].copy()
    elif category_mode == "cross_category":
        candidates = df.copy()
    else:  # same_category
        candidates = df[df['std_category'] == target_cat].copy()
        if len(candidates) < 6:
            candidates = df.copy()

    candidate_records = candidates.to_dict('records')
    scored = []

    target_canonical_id = target.get('canonical_product_id')
    if pd.isna(target_canonical_id):
        target_canonical_id = None
    target_brand = str(target.get('brand', '')).lower().strip()
    target_clean_title = clean_product_title(str(target['title'])).lower().strip()

    for row in candidate_records:
        if row['id'] == target_id:
            continue
        # Exclude sibling store listings of the same canonical product entity
        row_canon = row.get('canonical_product_id')
        if target_canonical_id and row_canon and row_canon == target_canonical_id:
            continue

        cand_brand = str(row.get('brand', '')).lower().strip()
        cand_clean_title = clean_product_title(str(row['title'])).lower().strip()

        # Exclude if it's the exact same product as target by brand + clean title
        if target_brand and cand_brand == target_brand and cand_clean_title == target_clean_title:
            continue

        cand_ings = row['parsed_ingredients']
        cand_actives = row['all_actives']
        cand_vehicle = extract_vehicle(row['title'])
        cand_cat = row['std_category']

        # Exclude same-product variants from the same brand (same brand, vehicle, and key active ingredients)
        if target_brand and cand_brand == target_brand and cand_vehicle == target_vehicle:
            shared_acts = target_actives.intersection(cand_actives) if target_actives else set()
            if target_actives and len(target_actives) >= 2 and shared_acts == target_actives == cand_actives:
                t_sim = compute_functional_title_sim(target['title'], row['title'], brand1=target['brand'], brand2=row['brand'])
                if t_sim >= 0.50:
                    continue

        # 1. Active Ingredients Matching (Extremely Important)
        shared_acts = target_actives.intersection(cand_actives) if target_actives else set()
        diff_cand_acts = cand_actives.difference(target_actives) if target_actives else cand_actives

        if target_actives:
            if len(shared_acts) > 0:
                active_recall = len(shared_acts) / len(target_actives)
                active_precision = len(shared_acts) / len(cand_actives) if cand_actives else 0.5
                actives_sim = (active_recall * 0.80) + (active_precision * 0.20)
            else:
                # Target has active ingredients (e.g. Hyaluronic Acid), but candidate has NONE of them!
                actives_sim = 0.0
        else:
            # Target has no explicit active keywords (e.g. basic cleansing balm, barrier cream)
            actives_sim = 0.5

        # 2. Non-Water Formulation Jaccard (excl. water/solvents)
        non_water_jaccard = compute_non_water_jaccard(target_ings, cand_ings) if (target_ings and cand_ings) else 0.0

        # 3. Vehicle / Texture Compatibility
        vehicle_sim = compute_vehicle_sim(target_vehicle, cand_vehicle)

        # 4. Brand-Independent Functional Title Similarity
        title_sim = compute_functional_title_sim(target['title'], row['title'], brand1=target['brand'], brand2=row['brand'])

        # 5. Hybrid Formulation Scoring
        if category_mode == "cross_category":
            if target_actives:
                if actives_sim == 0.0:
                    formula_sim = 0.05
                else:
                    formula_sim = (actives_sim * 0.65) + (non_water_jaccard * 0.25) + (title_sim * 0.10)
            else:
                formula_sim = (non_water_jaccard * 0.70) + (title_sim * 0.30)
        else:
            # same_category
            if target_actives:
                if actives_sim == 0.0:
                    formula_sim = (non_water_jaccard * 0.15) + (vehicle_sim * 0.10)
                else:
                    formula_sim = (actives_sim * 0.50) + (non_water_jaccard * 0.25) + (vehicle_sim * 0.20) + (title_sim * 0.05)
            else:
                formula_sim = (vehicle_sim * 0.45) + (non_water_jaccard * 0.40) + (title_sim * 0.15)

        skin_safety, warnings = evaluate_skin_safety(cand_ings, cand_actives, skin_type)

        if skin_type == 'normal':
            final_match = 45.0 + (formula_sim * 53.0)
        else:
            final_match = 45.0 + (formula_sim * 39.0) + ((skin_safety / 100.0) * 14.5)

        final_match = float(round(min(98.5, max(30.0, final_match)), 1))

        cand_non_water = [ing for ing in cand_ings if ing.lower().strip() not in WATER_SOLVENTS]

        scored.append({
            'id': int(row['id']),
            'canonical_product_id': int(row_canon) if (row_canon and pd.notna(row_canon)) else None,
            'title': str(row['title']),
            'brand': str(row['brand']),
            'category': cand_cat,
            'vehicle': cand_vehicle,
            'price': float(row['price_low']),
            'store_count': int(row.get('store_count', 1)) if pd.notna(row.get('store_count')) else 1,
            'min_price': float(row['min_price']) if (pd.notna(row.get('min_price')) and row.get('min_price')) else float(row['price_low']),
            'max_price': float(row['max_price']) if (pd.notna(row.get('max_price')) and row.get('max_price')) else float(row['price_low']),
            'match_score': float(final_match),
            'formula_sim': float(formula_sim),
            'image_url': str(row['image_url']) if pd.notna(row['image_url']) else None,
            'warnings': "; ".join(warnings) if warnings else None,
            'url': str(row['url']),
            'ingredients': row.get('ingredients') or "",
            'parsed_ingredients': cand_ings,
            'non_water_ingredients': cand_non_water,
            'shared_actives': sorted(list(shared_acts)),
            'differing_actives': sorted(list(diff_cand_acts)),
            'formula_similarity_percent': round(non_water_jaccard * 100.0, 1) if (target_ings and cand_ings) else None,
            'match_summary': f"Shares {len(shared_acts)} key active(s): {', '.join(sorted(list(shared_acts)))}" if shared_acts else f"Vehicle: {cand_vehicle.replace('_', ' ').title()}",
            'gate4_jev_status': row.get('gate4_jev_status'),
            'inci_jev_score': row.get('inci_jev_score'),
            'gate4_gemini_status': row.get('gate4_gemini_status'),
            'inci_gemini_score': row.get('inci_gemini_score'),
            'cross_score': None,
            'ai_verified': False,
            'ai_confidence': None,
            'ai_provider': None
        })

    # Deduplicate candidates across stores so each distinct product entity appears AT MOST ONCE.
    # The different store listings per product are shown in the Multi-Store Pricing tab.
    deduped_scored = []
    seen_canonical_ids = set()
    seen_brand_titles = set()

    # Sort descending by match_score and formulation quality so the best representative listing represents the product
    scored = sorted(scored, key=lambda x: (x['match_score'], len(x.get('parsed_ingredients') or []), x['price'] > 0), reverse=True)

    for cand in scored:
        canon_id = cand.get('canonical_product_id')
        b_clean = re.sub(r'[^a-z0-9]', '', str(cand.get('brand', '')).lower())
        t_clean = re.sub(r'[^a-z0-9]', '', clean_product_title(str(cand.get('title', ''))).lower())
        brand_title_key = f"{b_clean}_{t_clean}" if (b_clean and t_clean) else None

        # Sibling listing of an already included canonical product entity
        if canon_id is not None and canon_id in seen_canonical_ids:
            continue

        # Sibling listing by matching brand and clean title
        if brand_title_key is not None and brand_title_key in seen_brand_titles:
            continue

        if canon_id is not None:
            seen_canonical_ids.add(canon_id)
        if brand_title_key is not None:
            seen_brand_titles.add(brand_title_key)
        deduped_scored.append(cand)

    scored = deduped_scored

    # Take top 10 unique candidate products for FastEmbed Cross-Encoder reranking
    scored = scored[:10]

    if cfg.ENABLE_CROSS_ENCODER and scored:
        target_pair_text = f"Category: {target_cat}. Texture: {target_vehicle}. Actives: {', '.join(sorted(target_actives)) if target_actives else 'None'}. Formulation: {', '.join(target_non_water[:10])}"
        for cand in scored:
            cand_pair_text = f"Category: {cand['category']}. Texture: {cand['vehicle']}. Actives: {', '.join(sorted(cand['shared_actives'] + cand['differing_actives'])) if (cand['shared_actives'] or cand['differing_actives']) else 'None'}. Formulation: {', '.join(cand['non_water_ingredients'][:10])}"
            try:
                cross_score = score_cross_encoder(target_pair_text, cand_pair_text)
                cand['cross_score'] = float(round(cross_score, 4))
                # Calibrate match_score with cross_score: blend 75% formula + 25% cross-encoder
                cand['match_score'] = float(round((cand['match_score'] * 0.75) + (cross_score * 100 * 0.25), 1))
            except Exception as e:
                logger.warning(f"Cross-encoder scoring skipped: {e}")
                cand['cross_score'] = None
        scored = sorted(scored, key=lambda x: x['match_score'], reverse=True)

    # Take top 6 unique alternative products
    scored = scored[:6]

    # Pre-computed Gate 4 AI Telemetry & Local Cross-Encoder Verification
    for cand in scored:
        jev_st = cand.get('gate4_jev_status')
        jev_sc = cand.get('inci_jev_score')
        gem_st = cand.get('gate4_gemini_status')
        gem_sc = cand.get('inci_gemini_score')
        cross_sc = cand.get('cross_score')

        if jev_st == 'jev_approved' and jev_sc is not None:
            cand['ai_verified'] = True
            cand['ai_confidence'] = float(round(jev_sc * 100, 1))
            cand['ai_provider'] = "jev-1.13"
            cand['match_score'] = float(round((cand['match_score'] * 0.70) + (jev_sc * 100 * 0.30), 1))
        elif gem_st == 'gemini_approved' and gem_sc is not None:
            cand['ai_verified'] = True
            cand['ai_confidence'] = float(round(gem_sc * 100, 1))
            cand['ai_provider'] = "gemini-3.5-flash-lite"
            cand['match_score'] = float(round((cand['match_score'] * 0.70) + (gem_sc * 100 * 0.30), 1))
        elif cross_sc is not None and cross_sc >= cfg.CROSS_ENCODER_CONFIDENCE_THRESHOLD:
            cand['ai_verified'] = True
            cand['ai_confidence'] = float(round(cross_sc * 100, 1))
            cand['ai_provider'] = "local-cross-encoder"

    scored = sorted(scored, key=lambda x: x['match_score'], reverse=True)

    target_safety, target_warnings = evaluate_skin_safety(target_ings, target_actives, skin_type)
    source_prod = {
        "title": str(target['title']),
        "brand": str(target['brand']),
        "category": target_cat,
        "vehicle": target_vehicle,
        "price": float(target['price_low']),
        "match_score": 100.0,
        "image_url": str(target['image_url']) if pd.notna(target['image_url']) else None,
        "warnings": "; ".join(target_warnings) if target_warnings else None,
        "url": str(target['url'])
    }

    clean_results = []
    for c in scored:
        clean_results.append({
            "id": c["id"],
            "canonical_product_id": c.get("canonical_product_id"),
            "store_count": c.get("store_count", 1),
            "min_price": c.get("min_price"),
            "max_price": c.get("max_price"),
            "title": c["title"],
            "brand": c["brand"],
            "category": c["category"],
            "vehicle": c["vehicle"],
            "price": c["price"],
            "match_score": c["match_score"],
            "cross_score": c.get("cross_score"),
            "ai_verified": c.get("ai_verified", False),
            "ai_confidence": c.get("ai_confidence"),
            "ai_provider": c.get("ai_provider"),
            "image_url": c["image_url"],
            "warnings": c["warnings"],
            "url": c["url"],
            "shared_actives": c.get("shared_actives", []),
            "differing_actives": c.get("differing_actives", []),
            "formula_similarity_percent": c.get("formula_similarity_percent"),
            "match_summary": c.get("match_summary", "")
        })

    return {"source": source_prod, "results": clean_results}
