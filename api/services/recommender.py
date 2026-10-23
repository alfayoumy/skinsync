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

def compute_title_sim(t1: str, t2: str) -> float:
    """Computes token Jaccard similarity between two product titles excluding stop words."""
    c1 = clean_product_title(str(t1))
    c2 = clean_product_title(str(t2))
    w1 = set(re.findall(r'\w+', c1.lower()))
    w2 = set(re.findall(r'\w+', c2.lower()))
    stop = {'the', 'and', 'with', 'for', 'skin', 'in', 'of', 'face', 'cream', 'serum', 'lotion'}
    w1 = w1 - stop
    w2 = w2 - stop
    if not w1 or not w2:
        return 0.0
    return len(w1.intersection(w2)) / len(w1.union(w2))

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

def get_recommendations(target_title: str, skin_type: str = "normal") -> Optional[Dict[str, Any]]:
    """
    Finds the top cosmetic alternatives/dupes for a target product using SOTA hybrid scoring:
    - Standard category filtering
    - Formula similarity (weighted Jaccard + active overlap + title sim)
    - Clinical skin safety and tolerance adjustments
    - FastEmbed ONNX Cross-Encoder reranking
    - Pre-computed Gate 4 AI telemetry
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
    target_ings_str = ", ".join(target_ings[:8]) if target_ings else ""

    candidates = df[df['std_category'] == target_cat].copy()
    if len(candidates) < 6:
        candidates = df.copy()

    # Convert candidates to list of dicts for fast iteration
    candidate_records = candidates.to_dict('records')
    scored = []

    for row in candidate_records:
        if row['id'] == target_id:
            continue
        cand_ings = row['parsed_ingredients']
        cand_actives = row['all_actives']

        if target_actives and cand_actives:
            inter_act = len(target_actives.intersection(cand_actives))
            union_act = len(target_actives.union(cand_actives))
            actives_sim = (inter_act / union_act) if union_act > 0 else 0.0
        elif not target_actives and not cand_actives:
            actives_sim = 0.5
        else:
            actives_sim = 0.1

        inci_sim = compute_weighted_jaccard(target_ings, cand_ings) if (target_ings and cand_ings) else 0.0
        title_sim = compute_title_sim(target['title'], row['title'])

        if target_ings and cand_ings:
            formula_sim = (actives_sim * 0.50) + (inci_sim * 0.35) + (title_sim * 0.15)
        else:
            formula_sim = (actives_sim * 0.60) + (title_sim * 0.40)

        skin_safety, warnings = evaluate_skin_safety(cand_ings, cand_actives, skin_type)

        if skin_type == 'normal':
            final_match = 45.0 + (formula_sim * 53.0)
        else:
            final_match = 45.0 + (formula_sim * 39.0) + ((skin_safety / 100.0) * 14.5)

        final_match = float(round(min(98.5, max(30.0, final_match)), 1))

        scored.append({
            'id': int(row['id']),
            'title': str(row['title']),
            'brand': str(row['brand']),
            'price': float(row['price_low']),
            'match_score': float(final_match),
            'formula_sim': float(formula_sim),
            'image_url': str(row['image_url']) if pd.notna(row['image_url']) else None,
            'warnings': "; ".join(warnings) if warnings else None,
            'url': str(row['url']),
            'ingredients': row.get('ingredients') or "",
            'parsed_ingredients': cand_ings,
            'gate4_jev_status': row.get('gate4_jev_status'),
            'inci_jev_score': row.get('inci_jev_score'),
            'gate4_gemini_status': row.get('gate4_gemini_status'),
            'inci_gemini_score': row.get('inci_gemini_score'),
            'cross_score': None,
            'ai_verified': False,
            'ai_confidence': None,
            'ai_provider': None
        })

    # Take top 10 candidates for FastEmbed Cross-Encoder reranking
    scored = sorted(scored, key=lambda x: x['match_score'], reverse=True)[:10]

    if cfg.ENABLE_CROSS_ENCODER and scored:
        target_pair_text = f"{target['brand']} {target_clean_title}. Actives: {', '.join(sorted(target_actives)) if target_actives else 'N/A'}. Ingredients: {target_ings_str}"
        for cand in scored:
            cand_clean_title = clean_product_title(cand['title'])
            cand_ings_sub = ", ".join(cand['parsed_ingredients'][:8]) if cand['parsed_ingredients'] else ""
            cand_pair_text = f"{cand['brand']} {cand_clean_title}. Ingredients: {cand_ings_sub}"
            try:
                cross_score = score_cross_encoder(target_pair_text, cand_pair_text)
                cand['cross_score'] = float(round(cross_score, 4))
                # Calibrate match_score with cross_score: blend 75% formula + 25% cross-encoder
                cand['match_score'] = float(round((cand['match_score'] * 0.75) + (cross_score * 100 * 0.25), 1))
            except Exception as e:
                logger.warning(f"Cross-encoder scoring skipped: {e}")
                cand['cross_score'] = None
        scored = sorted(scored, key=lambda x: x['match_score'], reverse=True)

    # Take top 5 candidates
    scored = scored[:5]

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
            "title": c["title"],
            "brand": c["brand"],
            "price": c["price"],
            "match_score": c["match_score"],
            "cross_score": c.get("cross_score"),
            "ai_verified": c.get("ai_verified", False),
            "ai_confidence": c.get("ai_confidence"),
            "ai_provider": c.get("ai_provider"),
            "image_url": c["image_url"],
            "warnings": c["warnings"],
            "url": c["url"]
        })

    return {"source": source_prod, "results": clean_results}
