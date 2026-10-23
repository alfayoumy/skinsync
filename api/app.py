from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Union, Optional
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import os
import re
import json
import urllib.request
import urllib.error
import logging
import sqlite3
import sys

from dermo_data import (
    INGREDIENT_DB, Comedogenicity, Irritancy, _get_ingredient_fuzzy,
    evaluate_product_dermo, reload_clinical_db
)
from pipeline.config import cfg
from pipeline.db_manager import (
    load_synonyms_from_db, add_synonym_to_db,
    load_clinical_ingredients_from_db, add_clinical_ingredient_to_db,
    get_connection
)
from pipeline.inci_matcher import (
    get_cross_encoder,
    score_cross_encoder,
    verify_match_with_gemini,
    clean_product_title,
    normalize_brand,
    match_with_inci_db,
    parse_and_normalize_ingredients,
    extract_ingredients_from_html
)
from pipeline.safeguards import CosmeticCompatibilityValidator
from pipeline.harvester import refresh_catalog, STORES

logger = logging.getLogger("skincare_engine")
logging.basicConfig(level=logging.INFO)

# ==============================================================================
# 1. LOAD .ENV CONFIGURATION
# ==============================================================================
def load_env_file():
    """
    Loads environment variables from .env files.
    Supports python-dotenv if installed, with a zero-dependency fallback.
    """
    try:
        from dotenv import load_dotenv
        env_file = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file)
        load_dotenv()
    except ImportError:
        pass

    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
            except Exception as e:
                logger.warning(f"Failed to read .env file at {c}: {e}")

    if os.environ.get("OPENCODE_API_KEY"):
        key = os.environ.get("OPENCODE_API_KEY")
        logger.info(f"Loaded OPENCODE_API_KEY from environment ({key[:4]}...{key[-3:]})")
    elif os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY"):
        logger.info("Loaded JEV_API_KEY / TYPESAFE_API_KEY from environment.")

load_env_file()

app = FastAPI(
    title="Egyptian Skincare AI Production Engine",
    description="Real-time multi-store scraper, INCI bioequivalence matcher, and Jev 1.13 reranker",
    version="3.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# 2. DATABASE LOADING & IN-MEMORY CACHE
# ==============================================================================
DB_PATH = os.path.join(os.path.dirname(__file__), "skincare.db")

KEY_ACTIVES = {
    'hyaluronic_acid': ['hyaluronic acid', 'sodium hyaluronate', 'hydrolyzed hyaluronic', 'hyaluronate'],
    'vitamin_b5': ['panthenol', 'd-panthenol', 'pro-vitamin b5', 'pantothenic acid'],
    'niacinamide': ['niacinamide', 'nicotinamide', 'vitamin b3'],
    'vitamin_c': ['ascorbic acid', 'l-ascorbic', 'ethyl ascorbic', 'magnesium ascorbyl', 'sodium ascorbyl', 'ascorbyl glucoside', 'vitamin c'],
    'retinoid': ['retinol', 'retinyl palmitate', 'retinal', 'hydroxypinacolone retinoate', 'adapalene', 'tretinoin', 'retinyl'],
    'salicylic_acid': ['salicylic acid', 'bha', 'betaine salicylate', 'willow bark'],
    'aha': ['glycolic acid', 'lactic acid', 'mandelic acid', 'malic acid', 'citric acid', 'tartaric acid'],
    'ceramides': ['ceramide np', 'ceramide ap', 'ceramide eop', 'ceramide 3', 'ceramide', 'phytosphingosine', 'sphingosine'],
    'centella': ['centella asiatica', 'madecassoside', 'asiaticoside', 'asiatic acid', 'madecassic acid', 'cica', 'gotu kola'],
    'peptides': ['peptide', 'matrixyl', 'copper tripeptide', 'palmitoyl', 'argireline', 'oligopeptide', 'acetyl hexapeptide'],
    'azelaic_acid': ['azelaic acid', 'potassium azeloyl diglycinate'],
    'arbutin': ['alpha-arbutin', 'arbutin', 'alpha arbutin', 'beta-arbutin'],
    'zinc': ['zinc pca', 'zinc oxide', 'zinc gluconate'],
    'squalane': ['squalane', 'squalene'],
    'tea_tree': ['tea tree', 'melaleuca alternifolia'],
    'collagen': ['hydrolyzed collagen', 'marine collagen', 'collagen'],
    'aloe_vera': ['aloe barbadensis', 'aloe vera'],
    'tranexamic_acid': ['tranexamic acid', 'txa', 'cetyl tranexamate'],
    'kojic_acid': ['kojic acid', 'kojic dipalmitate'],
    'bakuchiol': ['bakuchiol'],
    'urea': ['urea', 'hydroxyethyl urea'],
    'benzoyl_peroxide': ['benzoyl peroxide', 'bpo'],
    'sulfur': ['sulfur', 'colloidal sulfur'],
    'ectoin': ['ectoin'],
    'pha': ['gluconolactone', 'lactobionic acid', 'galactose', 'maltobionic acid'],
    'ferulic_acid': ['ferulic acid'],
    'licorice': ['glycyrrhiza glabra', 'licorice root', 'dipotassium glycyrrhizate', 'glabridin'],
    'snail_mucin': ['snail secretion filtrate', 'snail mucin'],
    'ferments': ['galactomyces', 'bifida ferment', 'saccharomyces', 'lactobacillus ferment']
}

# Database-backed ingredient synonyms (loaded from ingredient_synonyms table)
SYNONYMS = load_synonyms_from_db()

SKIN_BENEFITS = {
    'oily': {
        'salicylic_acid': 3.0, 'niacinamide': 2.5, 'zinc': 2.5, 'tea_tree': 2.0,
        'centella': 1.5, 'benzoyl_peroxide': 3.0, 'sulfur': 2.5, 'tranexamic_acid': 1.5
    },
    'dry': {
        'hyaluronic_acid': 3.0, 'ceramides': 3.0, 'squalane': 2.5, 'vitamin_b5': 2.0,
        'collagen': 1.5, 'urea': 3.0, 'ectoin': 2.5, 'snail_mucin': 2.0, 'ferments': 2.0
    },
    'sensitive': {
        'centella': 3.0, 'vitamin_b5': 2.5, 'ceramides': 2.5, 'aloe_vera': 2.0,
        'ectoin': 3.0, 'licorice': 2.5, 'snail_mucin': 2.0, 'pha': 1.5, 'bakuchiol': 1.5
    }
}

SKIN_RISKS = {
    'oily': {
        'coconut oil': 4.0, 'cocoa butter': 3.5, 'isopropyl myristate': 5.0, 
        'isopropyl palmitate': 5.0, 'ethylhexyl palmitate': 4.0, 'wheat germ oil': 5.0,
        'myristyl myristate': 5.0, 'isostearyl isostearate': 4.0
    },
    'dry': {
        'alcohol denat': 4.0, 'sls': 3.5, 'salicylic_acid': 1.5
    },
    'sensitive': {
        'fragrance': 4.0, 'alcohol denat': 4.0, 'sls': 3.5, 'aha': 2.5,
        'retinoid': 2.0, 'salicylic_acid': 2.0, 'benzoyl_peroxide': 2.5
    }
}

def parse_and_clean_ingredients(text):
    if not isinstance(text, str) or not text.strip():
        return [], set()
    raw_tokens = re.split(r'[,;•\n\r/]+', text)
    cleaned = []
    found_actives = set()
    for token in raw_tokens:
        token = token.strip().lower()
        if not token or len(token) < 2:
            continue
        token = re.sub(r'^\d+(\.\d+)?%\s*(pure\s*)?', '', token)
        token = token.rstrip('.').strip()
        for act_cat, keywords in KEY_ACTIVES.items():
            if any(k in token for k in keywords):
                found_actives.add(act_cat)
        canon = SYNONYMS.get(token, token)
        cleaned.append(canon)
    return cleaned, found_actives

def extract_actives_from_title(title):
    if not isinstance(title, str): return set()
    t = title.lower()
    found = set()
    for act_cat, keywords in KEY_ACTIVES.items():
        if any(k in t for k in keywords):
            found.add(act_cat)
    return found

# Global in-memory cache
df_cache = None

def load_catalog_from_db():
    global df_cache, SYNONYMS
    SYNONYMS = load_synonyms_from_db()
    if not os.path.exists(DB_PATH):
        logger.warning(f"Database not found at {DB_PATH}. Using fallback CSV.")
        csv_fallback = os.path.join(os.path.dirname(__file__), "master_product_database_v10_final.csv")
        df_cache = pd.read_csv(csv_fallback)
        df_cache['std_category'] = df_cache.get('std_category', 'Other')
    else:
        conn = sqlite3.connect(DB_PATH)
        df_cache = pd.read_sql_query("""
        SELECT id, store_id, title, brand, category as std_category, price as price_low,
               compare_at_price as price_high, is_available, image_url, product_url as url,
               ingredients_raw as ingredients, ingredients_parsed, actives, ingredient_source,
               gate4_jev_status, inci_jev_score, gate4_gemini_status, inci_gemini_score,
               gate4_cross_status, inci_cross_score
        FROM products;
        """, conn)
        conn.close()

    df_cache['title'] = df_cache['title'].fillna('').astype(str).str.strip()
    df_cache['brand'] = df_cache['brand'].fillna('Unknown').astype(str).str.strip()
    df_cache['std_category'] = df_cache['std_category'].fillna('Other').astype(str).str.strip()
    df_cache['url'] = df_cache['url'].fillna('').astype(str)
    df_cache['price_low'] = pd.to_numeric(df_cache['price_low'], errors='coerce').fillna(0.0)

    # Process parsed ingredients
    parsed_data = [parse_and_clean_ingredients(t) for t in df_cache['ingredients']]
    df_cache['parsed_ingredients'] = [p[0] for p in parsed_data]
    df_cache['actives'] = [p[1] for p in parsed_data]
    df_cache['title_actives'] = df_cache['title'].apply(extract_actives_from_title)
    df_cache['all_actives'] = [a.union(ta) for a, ta in zip(df_cache['actives'], df_cache['title_actives'])]
    logger.info(f"Loaded {len(df_cache)} products from SQLite database into real-time memory index.")

load_catalog_from_db()

# ==============================================================================
# 3. ADVANCED SIMILARITY & DERMATOLOGY RULES
# ==============================================================================
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

def compute_weighted_jaccard(list_a, list_b):
    if not list_a or not list_b:
        return 0.0
    weights_a = {ing: get_inci_concentration_weight(i, ing) for i, ing in enumerate(list_a)}
    weights_b = {ing: get_inci_concentration_weight(i, ing) for i, ing in enumerate(list_b)}
    all_keys = set(weights_a.keys()).union(weights_b.keys())
    inter_w = sum(min(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    union_w = sum(max(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    return (inter_w / union_w) if union_w > 0 else 0.0

def compute_title_sim(t1, t2):
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

def evaluate_skin_safety(parsed_ings, actives, skin_type):
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

# ==============================================================================
# 4. JEV 1.13 DECISION MODEL & GEMINI 3.5 FLASH-LITE FALLBACK
# ==============================================================================
def call_jev_reranker(source_title, candidate_title, candidate_ings, skin_type):
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

def call_gemini_reranker(source_title, candidate_title, candidate_ings, skin_type):
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

# ==============================================================================
# 5. CORE RECOMMENDATION ENGINE (SOTA HYBRID + CROSS-ENCODER)
# ==============================================================================
def get_recommendations(target_title: str, skin_type: str = "normal"):
    df = df_cache
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

    # Convert candidates to list of dicts for 10x-20x faster iteration (Flaw 5 fix)
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

    # Take top 10 candidates for FastEmbed Cross-Encoder reranking (Flaw 9 fix)
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

    # Pre-computed Gate 4 AI Telemetry & Local Cross-Encoder Verification (Non-blocking: <25ms SLA)
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

    # Clean up internal fields for clean API response
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

# ==============================================================================
# 6. FASTAPI ENDPOINTS & BACKGROUND AUTOMATION
# ==============================================================================
from simple_db import db

class OnboardingRequest(BaseModel):
    name: str
    email: str
    gender: str
    age_group: str
    likes: Union[List[str], str]
    dislikes: Union[List[str], str]

class ProductRequest(BaseModel):
    product_title: str
    user_skin_type: str = "normal"

@app.post("/onboarding")
def save_onboarding(data: OnboardingRequest):
    try:
        user_data = data.dict()
        email = user_data['email'].lower().strip()
        user_data['email'] = email
        existing = db.find_one("users", {"email": email})
        if existing:
            db.update("users", {"email": email}, user_data)
            return {"status": "updated", "user_id": existing['_id']}
        else:
            user_id = db.insert("users", user_data)
            return {"status": "created", "user_id": user_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user")
def get_user(email: str):
    user = db.find_one("users", {"email": email.lower().strip()})
    if user:
        return user
    raise HTTPException(status_code=404, detail="User not found")

@app.post("/recommend")
def recommend(request: ProductRequest):
    data = get_recommendations(request.product_title, request.user_skin_type)
    if not data:
        raise HTTPException(status_code=404, detail="Product not found")
    return {
        "user_skin": request.user_skin_type,
        "source_product": data['source'],
        "results": data['results']
    }

@app.get("/search")
def search_products(query: str):
    if not query or len(query.strip()) < 2:
        return []
    q = query.strip().lower()
    df = df_cache
    prefix_matches = df[df['title'].str.lower().str.startswith(q, na=False)]['title'].tolist()
    contains_matches = df[df['title'].str.contains(re.escape(q), case=False, na=False)]['title'].tolist()
    combined = []
    seen = set()
    for title in prefix_matches + contains_matches:
        if title not in seen:
            seen.add(title)
            combined.append(title)
        if len(combined) >= 8:
            break
    return combined

@app.get("/stats")
def catalog_stats():
    """
    Returns live database metrics, store product counts, and price change tracking.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM products;")
    total_products = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM price_history;")
    total_price_changes = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM inci_reference;")
    inci_records = cur.fetchone()[0]

    cur.execute("SELECT id, name, domain, total_products, last_sync_at FROM stores;")
    stores = [dict(zip(["id", "name", "domain", "total_products", "last_sync_at"], r)) for r in cur.fetchall()]

    cur.execute("""
    SELECT store_id, status, products_fetched, products_added, products_updated, price_changes, completed_at
    FROM sync_runs ORDER BY id DESC LIMIT 5;
    """)
    recent_syncs = [dict(zip(["store_id", "status", "fetched", "added", "updated", "price_changes", "completed_at"], r)) for r in cur.fetchall()]
    conn.close()

    return {
        "database": "SQLite (WAL Mode)",
        "total_active_products": total_products,
        "inci_knowledge_records": inci_records,
        "total_price_changes_logged": total_price_changes,
        "stores": stores,
        "recent_sync_runs": recent_syncs
    }

def background_sync_task():
    try:
        from pipeline.daily_sync import run_daily_sync
        logger.info("Executing background daily sync...")
        run_daily_sync(max_pages_per_store=50)
        load_catalog_from_db() # Refresh memory cache with new data
        logger.info("Background daily sync finished and memory cache reloaded.")
    except Exception as e:
        logger.error(f"Background sync error: {e}")

@app.post("/admin/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    """
    Triggers an automated background daily synchronization across all Shopify sources.
    Non-blocking. Returns immediate confirmation.
    """
    background_tasks.add_task(background_sync_task)
    return {
        "status": "accepted",
        "message": "Daily synchronization job dispatched to background worker. Check /stats for progress."
    }

def background_jev_task(limit: int):
    try:
        from pipeline.jev_worker import run_jev_verification_queue
        run_jev_verification_queue(limit=limit)
        load_catalog_from_db()
    except Exception as e:
        logger.error(f"Background Jev task error: {e}")

@app.post("/admin/verify/jev")
def trigger_jev_verification(background_tasks: BackgroundTasks, limit: int = 25):
    """Triggers asynchronous Gate 4 Jev verification queue."""
    background_tasks.add_task(background_jev_task, limit)
    return {
        "status": "accepted",
        "message": f"Gate 4 Jev verification job dispatched for up to {limit} products. Check /stats for updates."
    }

def background_gemini_task(limit: int, batch_size: int, all_pending: bool):
    try:
        from pipeline.gemini_worker import run_gemini_verification_queue
        run_gemini_verification_queue(limit=limit, batch_size=batch_size, all_pending=all_pending)
        load_catalog_from_db()
    except Exception as e:
        logger.error(f"Background Gemini task error: {e}")

@app.post("/admin/verify/gemini")
def trigger_gemini_verification(background_tasks: BackgroundTasks, limit: int = 50, batch_size: int = 10, all_pending: bool = False):
    """Triggers asynchronous Gate 4 Gemini batch verification queue."""
    background_tasks.add_task(background_gemini_task, limit, batch_size, all_pending)
    return {
        "status": "accepted",
        "message": f"Gate 4 Gemini batch verification job dispatched for up to {limit} products (batch size: {batch_size}). Check /stats for updates."
    }

class SynonymRequest(BaseModel):
    alias: str
    canonical_name: str
    category: Optional[str] = "general"

@app.get("/synonyms")
def list_synonyms():
    """Returns all active ingredient synonyms from the database."""
    return load_synonyms_from_db()

@app.post("/synonyms")
def create_or_update_synonym(req: SynonymRequest):
    """Dynamically adds or updates an ingredient synonym in the database and updates cache."""
    global SYNONYMS
    ok = add_synonym_to_db(req.alias, req.canonical_name, req.category)
    if ok:
        SYNONYMS = load_synonyms_from_db()
        try:
            from pipeline.inci_matcher import reload_synonyms
            reload_synonyms()
        except ImportError:
            pass
        try:
            from dermo_data import reload_dermo_lookup_cache
            reload_dermo_lookup_cache()
        except ImportError:
            pass
        return {
            "status": "success",
            "alias": req.alias.lower().strip(),
            "canonical_name": req.canonical_name.strip(),
            "category": req.category
        }
    raise HTTPException(status_code=500, detail="Failed to persist synonym in database.")

# ==============================================================================
# 7. SPECIALIZED OPERATIONAL ENDPOINTS
# ==============================================================================

# ------------------------------------------------------------------------------
# 7.1. CATALOG / PRODUCT REFRESH ENDPOINTS
# ------------------------------------------------------------------------------
class CatalogRefreshRequest(BaseModel):
    store_id: Optional[str] = None
    fields: Optional[List[str]] = None
    max_pages: Optional[int] = 50
    background: bool = True

class StoreRefreshRequest(BaseModel):
    fields: Optional[List[str]] = None
    max_pages: Optional[int] = 50
    background: bool = True

def _run_refresh_catalog_task(store_id: Optional[str], fields: Optional[List[str]], max_pages: int):
    try:
        res = refresh_catalog(store_id=store_id, fields=fields, max_pages=max_pages)
        load_catalog_from_db()
        return res
    except Exception as e:
        logger.error(f"Catalog refresh error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/products/refresh")
def refresh_products_endpoint(req: CatalogRefreshRequest, background_tasks: BackgroundTasks):
    """
    Refreshes catalog products across a specific source or all sources,
    selectively updating specific fields ('price', 'stock', 'image', 'ingredients', or 'all').
    """
    if req.background:
        background_tasks.add_task(_run_refresh_catalog_task, req.store_id, req.fields, req.max_pages or 50)
        return {
            "status": "accepted",
            "message": f"Catalog refresh dispatched to background worker (store: {req.store_id or 'all'}, fields: {req.fields or 'all'}). Check /stats for progress."
        }
    return _run_refresh_catalog_task(req.store_id, req.fields, req.max_pages or 50)

@app.post("/stores/{store_id}/refresh")
def refresh_store_endpoint(store_id: str, background_tasks: BackgroundTasks, req: Optional[StoreRefreshRequest] = None):
    """
    Refreshes products for a specific source ID across selected or all fields.
    """
    fields = req.fields if req else None
    max_pages = req.max_pages if req and req.max_pages else 50
    bg = req.background if req else True
    if bg:
        background_tasks.add_task(_run_refresh_catalog_task, store_id, fields, max_pages)
        return {
            "status": "accepted",
            "message": f"Store refresh dispatched for store '{store_id}' (fields: {fields or 'all'})."
        }
    return _run_refresh_catalog_task(store_id, fields, max_pages)

# ------------------------------------------------------------------------------
# 7.2. INCI FORMULATION MATCHING ENDPOINTS
# ------------------------------------------------------------------------------
class InciMatchRequest(BaseModel):
    brand: str
    title: str
    scoring_mode: str = "local"  # "local", "jev", "gemini", "all"
    allow_obf: bool = False

@app.post("/inci/match")
def match_inci_endpoint(req: InciMatchRequest):
    """
    Performs on-demand INCI formulation matching with granular scoring mode control
    ('local', 'jev', 'gemini', 'all'/'auto') and Open Beauty Facts fallback flag.
    """
    conn = get_connection()
    try:
        res = match_with_inci_db(
            brand=req.brand,
            title=req.title,
            conn=conn,
            allow_synchronous_jev=True,
            scoring_mode=req.scoring_mode,
            allow_obf=req.allow_obf
        )
        if not res:
            return {
                "matched": False,
                "brand": req.brand,
                "title": req.title,
                "scoring_mode": req.scoring_mode,
                "result": None
            }
        return {
            "matched": True,
            "brand": req.brand,
            "title": req.title,
            "scoring_mode": req.scoring_mode,
            "result": res
        }
    finally:
        conn.close()

class ProductInciMatchRequest(BaseModel):
    scoring_mode: str = "local"
    allow_obf: bool = False
    update_product: bool = True

@app.post("/products/{product_id}/match-inci")
def match_product_inci_endpoint(product_id: int, req: ProductInciMatchRequest):
    """
    Matches an existing product in the database against the INCI reference database and updates its telemetry.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, brand, title, ingredients_raw FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product ID {product_id} not found.")

        brand = row["brand"]
        title = row["title"]
        curr_raw = row["ingredients_raw"]

        match_res = match_with_inci_db(
            brand=brand,
            title=title,
            conn=conn,
            allow_synchronous_jev=True,
            scoring_mode=req.scoring_mode,
            allow_obf=req.allow_obf
        )

        if not match_res:
            return {
                "matched": False,
                "product_id": product_id,
                "brand": brand,
                "title": title,
                "scoring_mode": req.scoring_mode,
                "message": "No compatible INCI match found."
            }

        if req.update_product:
            update_sql = """
            UPDATE products
            SET inci_matched_id = ?,
                inci_matched_name = ?,
                inci_vector_score = ?,
                inci_token_score = ?,
                inci_hybrid_score = ?,
                inci_cross_score = ?,
                inci_jev_score = ?,
                inci_gemini_score = ?,
                gate1_quality_status = ?,
                gate2_safeguard_status = ?,
                gate3_active_status = ?,
                gate4_cross_status = ?,
                gate4_jev_status = ?,
                gate4_gemini_status = ?,
                inci_safeguard_log = ?,
                updated_at = CURRENT_TIMESTAMP
            """
            params = [
                match_res.get("matched_id"),
                match_res.get("matched_name"),
                match_res.get("vector_score"),
                match_res.get("token_score"),
                match_res.get("hybrid_score"),
                match_res.get("inci_cross_score"),
                match_res.get("inci_jev_score"),
                match_res.get("inci_gemini_score"),
                match_res.get("gate1_quality_status"),
                match_res.get("gate2_safeguard_status"),
                match_res.get("gate3_active_status"),
                match_res.get("gate4_cross_status"),
                match_res.get("gate4_jev_status"),
                match_res.get("gate4_gemini_status"),
                match_res.get("safeguard_log"),
            ]

            if (not curr_raw or len(curr_raw.strip()) < 10) and match_res.get("ingredients"):
                parsed, acts = parse_and_normalize_ingredients(match_res["ingredients"])
                update_sql += """,
                ingredients_raw = ?,
                ingredients_parsed = ?,
                actives = ?,
                ingredient_source = 'skinsafe_matched'
                """
                params.extend([match_res["ingredients"], json.dumps(parsed), json.dumps(acts)])

            update_sql += " WHERE id = ?;"
            params.append(product_id)
            cur.execute(update_sql, params)
            conn.commit()
            load_catalog_from_db()

        return {
            "matched": True,
            "product_id": product_id,
            "brand": brand,
            "title": title,
            "scoring_mode": req.scoring_mode,
            "updated": req.update_product,
            "match": match_res
        }
    finally:
        conn.close()

class BatchInciMatchRequest(BaseModel):
    product_ids: Optional[List[int]] = None
    store_id: Optional[str] = None
    limit: int = 25
    scoring_mode: str = "local"
    allow_obf: bool = False
    only_unmatched: bool = True
    background: bool = False

def _run_batch_inci_match(req: BatchInciMatchRequest):
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = "SELECT id, brand, title, ingredients_raw FROM products WHERE 1=1"
        params = []
        if req.product_ids:
            placeholders = ",".join("?" for _ in req.product_ids)
            query += f" AND id IN ({placeholders})"
            params.extend(req.product_ids)
        if req.store_id:
            query += " AND store_id = ?"
            params.append(req.store_id)
        if req.only_unmatched:
            query += " AND (inci_matched_id IS NULL OR gate4_jev_status IS NULL)"
        query += f" LIMIT {max(1, min(req.limit, 200))};"

        cur.execute(query, params)
        rows = cur.fetchall()

        results = []
        matched_count = 0
        for r in rows:
            pid = r["id"]
            brand = r["brand"]
            title = r["title"]
            curr_raw = r["ingredients_raw"]
            m_res = match_with_inci_db(brand, title, conn, allow_synchronous_jev=True, scoring_mode=req.scoring_mode, allow_obf=req.allow_obf)
            if m_res:
                matched_count += 1
                update_sql = """
                UPDATE products
                SET inci_matched_id = ?, inci_matched_name = ?, inci_vector_score = ?,
                    inci_token_score = ?, inci_hybrid_score = ?, inci_cross_score = ?,
                    inci_jev_score = ?, inci_gemini_score = ?, gate1_quality_status = ?,
                    gate2_safeguard_status = ?, gate3_active_status = ?, gate4_cross_status = ?,
                    gate4_jev_status = ?, gate4_gemini_status = ?, inci_safeguard_log = ?,
                    updated_at = CURRENT_TIMESTAMP
                """
                u_params = [
                    m_res.get("matched_id"), m_res.get("matched_name"), m_res.get("vector_score"),
                    m_res.get("token_score"), m_res.get("hybrid_score"), m_res.get("inci_cross_score"),
                    m_res.get("inci_jev_score"), m_res.get("inci_gemini_score"), m_res.get("gate1_quality_status"),
                    m_res.get("gate2_safeguard_status"), m_res.get("gate3_active_status"), m_res.get("gate4_cross_status"),
                    m_res.get("gate4_jev_status"), m_res.get("gate4_gemini_status"), m_res.get("safeguard_log")
                ]
                if (not curr_raw or len(curr_raw.strip()) < 10) and m_res.get("ingredients"):
                    parsed, acts = parse_and_normalize_ingredients(m_res["ingredients"])
                    update_sql += ", ingredients_raw = ?, ingredients_parsed = ?, actives = ?, ingredient_source = 'skinsafe_matched'"
                    u_params.extend([m_res["ingredients"], json.dumps(parsed), json.dumps(acts)])
                update_sql += " WHERE id = ?;"
                u_params.append(pid)
                cur.execute(update_sql, u_params)
                conn.commit()
                results.append({"id": pid, "title": title, "matched": True, "matched_name": m_res.get("matched_name")})
            else:
                results.append({"id": pid, "title": title, "matched": False})

        load_catalog_from_db()
        return {
            "status": "success",
            "total_processed": len(rows),
            "total_matched": matched_count,
            "scoring_mode": req.scoring_mode,
            "details": results
        }
    finally:
        conn.close()

@app.post("/inci/batch-match")
def batch_match_inci_endpoint(req: BatchInciMatchRequest, background_tasks: BackgroundTasks):
    """
    Runs batch INCI matching across selected products or catalog items.
    """
    if req.background:
        background_tasks.add_task(_run_batch_inci_match, req)
        return {
            "status": "accepted",
            "message": f"Batch INCI matching job dispatched to background worker (mode: {req.scoring_mode}, limit: {req.limit})."
        }
    return _run_batch_inci_match(req)

# ------------------------------------------------------------------------------
# 7.3. PRODUCTS INGREDIENTS FILL ENDPOINTS
# ------------------------------------------------------------------------------
class FillIngredientsRequest(BaseModel):
    source: str = "auto"  # "store", "inci", "auto"
    overwrite: bool = False

def _fill_single_product_ingredients(product_row, source: str, overwrite: bool, conn):
    cur = conn.cursor()
    curr_raw = product_row["ingredients_raw"]
    if curr_raw and len(curr_raw.strip()) >= 15 and not overwrite:
        return {
            "status": "skipped",
            "message": "Product already has ingredients. Set overwrite=True to replace.",
            "ingredients_raw": curr_raw
        }

    raw_text = None
    source_used = None

    # Step 1: Store HTML description
    if source in ("store", "auto"):
        desc = product_row["description_html"]
        if desc:
            extracted = extract_ingredients_from_html(desc)
            if extracted and len(extracted) >= 15:
                raw_text = extracted
                source_used = "store_description"

    # Step 2: INCI Reference DB
    if (not raw_text or len(raw_text) < 15) and source in ("inci", "auto"):
        if product_row["inci_matched_id"]:
            cur.execute("SELECT ingredients FROM inci_reference WHERE id = ?;", (product_row["inci_matched_id"],))
            i_row = cur.fetchone()
            if i_row and i_row["ingredients"]:
                raw_text = i_row["ingredients"]
                source_used = "skinsafe_matched"
        if not raw_text:
            m_res = match_with_inci_db(product_row["brand"], product_row["title"], conn, allow_synchronous_jev=False, scoring_mode="local")
            if m_res and m_res.get("ingredients"):
                raw_text = m_res["ingredients"]
                source_used = "skinsafe_matched"

    if not raw_text or len(raw_text.strip()) < 10:
        return {
            "status": "not_found",
            "message": f"No ingredients found using source mode '{source}'."
        }

    parsed, actives = parse_and_normalize_ingredients(raw_text)
    cur.execute("""
    UPDATE products
    SET ingredients_raw = ?,
        ingredients_parsed = ?,
        actives = ?,
        ingredient_source = ?,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ?;
    """, (raw_text, json.dumps(parsed), json.dumps(actives), source_used, product_row["id"]))
    conn.commit()

    return {
        "status": "success",
        "product_id": product_row["id"],
        "source_used": source_used,
        "ingredients_raw": raw_text,
        "parsed_count": len(parsed),
        "actives": actives
    }

@app.post("/products/{product_id}/fill-ingredients")
def fill_product_ingredients_endpoint(product_id: int, req: FillIngredientsRequest):
    """
    Fills a single product's ingredients from store description ('store'),
    INCI reference ('inci'), or fallback ('auto').
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, brand, title, description_html, ingredients_raw, inci_matched_id FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product ID {product_id} not found.")

        result = _fill_single_product_ingredients(row, req.source, req.overwrite, conn)
        if result["status"] == "success":
            load_catalog_from_db()
        return result
    finally:
        conn.close()

class BatchFillIngredientsRequest(BaseModel):
    product_ids: Optional[List[int]] = None
    store_id: Optional[str] = None
    source: str = "auto"
    only_empty: bool = True
    overwrite: bool = False
    limit: int = 50
    background: bool = False

def _run_batch_fill_ingredients(req: BatchFillIngredientsRequest):
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = "SELECT id, brand, title, description_html, ingredients_raw, inci_matched_id FROM products WHERE 1=1"
        params = []
        if req.product_ids:
            placeholders = ",".join("?" for _ in req.product_ids)
            query += f" AND id IN ({placeholders})"
            params.extend(req.product_ids)
        if req.store_id:
            query += " AND store_id = ?"
            params.append(req.store_id)
        if req.only_empty and not req.overwrite:
            query += " AND (ingredients_raw IS NULL OR LENGTH(TRIM(ingredients_raw)) < 10)"
        query += f" LIMIT {max(1, min(req.limit, 200))};"

        cur.execute(query, params)
        rows = cur.fetchall()

        results = []
        success_count = 0
        for r in rows:
            res = _fill_single_product_ingredients(r, req.source, req.overwrite, conn)
            if res.get("status") == "success":
                success_count += 1
            results.append({"id": r["id"], "title": r["title"], "status": res.get("status"), "source": res.get("source_used")})

        load_catalog_from_db()
        return {
            "status": "success",
            "processed": len(rows),
            "filled": success_count,
            "source": req.source,
            "details": results
        }
    finally:
        conn.close()

@app.post("/products/ingredients/batch-fill")
def batch_fill_ingredients_endpoint(req: BatchFillIngredientsRequest, background_tasks: BackgroundTasks):
    """
    Batch populates missing product ingredients from store description or INCI reference.
    """
    if req.background:
        background_tasks.add_task(_run_batch_fill_ingredients, req)
        return {
            "status": "accepted",
            "message": f"Batch ingredients fill job dispatched (source: {req.source}, limit: {req.limit})."
        }
    return _run_batch_fill_ingredients(req)

# ------------------------------------------------------------------------------
# 7.4. PRODUCT SIMILARITY SCORING ENDPOINT
# ------------------------------------------------------------------------------
class ProductItemPayload(BaseModel):
    id: Optional[int] = None
    title: Optional[str] = None
    brand: Optional[str] = None
    ingredients: Optional[Union[List[str], str]] = None

class SimilarityScoreRequest(BaseModel):
    product_id_a: Optional[int] = None
    product_id_b: Optional[int] = None
    product_a: Optional[ProductItemPayload] = None
    product_b: Optional[ProductItemPayload] = None
    skin_type: str = "normal"
    include_cross_encoder: bool = True

def _resolve_product_for_similarity(pid: Optional[int], payload: Optional[ProductItemPayload], conn):
    if pid is not None:
        cur = conn.cursor()
        cur.execute("SELECT id, title, brand, ingredients_raw, ingredients_parsed, actives FROM products WHERE id = ?;", (pid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product with ID {pid} not found in database.")
        raw_ings = row["ingredients_raw"] or ""
        parsed = json.loads(row["ingredients_parsed"]) if row["ingredients_parsed"] else []
        if not parsed and raw_ings:
            parsed, _ = parse_and_clean_ingredients(raw_ings)
        acts = set(json.loads(row["actives"])) if row["actives"] else set()
        if not acts:
            acts = extract_actives_from_title(row["title"])
            if raw_ings:
                _, p_acts = parse_and_clean_ingredients(raw_ings)
                acts = acts.union(p_acts)
        return {
            "id": row["id"],
            "title": row["title"],
            "brand": row["brand"],
            "parsed_ingredients": parsed,
            "actives": acts,
            "raw_ingredients": raw_ings
        }
    elif payload is not None:
        title = payload.title or ""
        brand = payload.brand or "Unknown"
        ings_input = payload.ingredients or []
        if isinstance(ings_input, str):
            parsed, acts = parse_and_clean_ingredients(ings_input)
            raw_ings = ings_input
        else:
            parsed = [str(x).strip().lower() for x in ings_input if str(x).strip()]
            raw_ings = ", ".join(parsed)
            _, acts = parse_and_clean_ingredients(raw_ings)
        t_acts = extract_actives_from_title(title)
        acts = acts.union(t_acts)
        return {
            "id": payload.id,
            "title": title,
            "brand": brand,
            "parsed_ingredients": parsed,
            "actives": acts,
            "raw_ingredients": raw_ings
        }
    else:
        raise HTTPException(status_code=400, detail="Must provide either product ID or product payload.")

@app.post("/similarity/score")
def score_similarity_endpoint(req: SimilarityScoreRequest):
    """
    Computes pairwise formulation similarity and cosmetic compatibility between two products.
    Accepts either database product IDs or custom product payloads.
    Evaluates:
    - 3-zone FDA/EU weighted Jaccard similarity
    - Active molecule overlap
    - Product title similarity
    - Cosmetic compatibility safeguards
    - FastEmbed cross-encoder score
    - Dermatological safety and tolerance breakdown
    """
    conn = get_connection()
    try:
        prod_a = _resolve_product_for_similarity(req.product_id_a, req.product_a, conn)
        prod_b = _resolve_product_for_similarity(req.product_id_b, req.product_b, conn)
    finally:
        conn.close()

    parsed_a = prod_a["parsed_ingredients"]
    parsed_b = prod_b["parsed_ingredients"]
    acts_a = prod_a["actives"]
    acts_b = prod_b["actives"]

    # 1. 3-Zone Weighted Jaccard
    inci_sim = compute_weighted_jaccard(parsed_a, parsed_b) if (parsed_a and parsed_b) else 0.0

    # 2. Actives Overlap
    if acts_a and acts_b:
        inter_act = len(acts_a.intersection(acts_b))
        union_act = len(acts_a.union(acts_b))
        actives_sim = (inter_act / union_act) if union_act > 0 else 0.0
    elif not acts_a and not acts_b:
        actives_sim = 0.5
    else:
        actives_sim = 0.1

    # 3. Title Sim
    title_sim = compute_title_sim(prod_a["title"], prod_b["title"])

    # 4. Formula Sim
    if parsed_a and parsed_b:
        formula_sim = (actives_sim * 0.50) + (inci_sim * 0.35) + (title_sim * 0.15)
    else:
        formula_sim = (actives_sim * 0.60) + (title_sim * 0.40)

    # 5. Cross-Encoder
    cross_score = None
    if req.include_cross_encoder and cfg.ENABLE_CROSS_ENCODER:
        try:
            cand_a_text = f"{prod_a['brand']} {clean_product_title(prod_a['title'])}. Actives: {', '.join(sorted(acts_a)) if acts_a else 'N/A'}. Ingredients: {', '.join(parsed_a[:8]) if parsed_a else 'N/A'}"
            cand_b_text = f"{prod_b['brand']} {clean_product_title(prod_b['title'])}. Ingredients: {', '.join(parsed_b[:8]) if parsed_b else 'N/A'}"
            cross_score = score_cross_encoder(cand_a_text, cand_b_text)
            cross_score = float(round(cross_score, 4))
        except Exception as e:
            logger.warning(f"Cross-encoder scoring error: {e}")

    # 6. Safeguards
    compat_ok, reason = CosmeticCompatibilityValidator.validate_pair(prod_a["title"], prod_b["title"])

    # 7. Skin Safety
    safety_a, warn_a = evaluate_skin_safety(parsed_a, acts_a, req.skin_type)
    safety_b, warn_b = evaluate_skin_safety(parsed_b, acts_b, req.skin_type)

    # 8. Calibrated Match Score
    if req.skin_type == "normal":
        base_match = 45.0 + (formula_sim * 53.0)
    else:
        mean_safety = (safety_a + safety_b) / 2.0
        base_match = 45.0 + (formula_sim * 39.0) + ((mean_safety / 100.0) * 14.5)

    if cross_score is not None:
        final_match = (base_match * 0.75) + (cross_score * 100 * 0.25)
    else:
        final_match = base_match

    final_match = float(round(min(99.0, max(15.0, final_match)), 1))

    return {
        "match_score": final_match,
        "formula_similarity": float(round(formula_sim, 4)),
        "inci_weighted_jaccard": float(round(inci_sim, 4)),
        "actives_overlap": float(round(actives_sim, 4)),
        "title_similarity": float(round(title_sim, 4)),
        "cross_encoder_score": cross_score,
        "safeguards": {
            "is_compatible": compat_ok,
            "notes": reason or "Direct compatibility passed"
        },
        "skin_type": req.skin_type,
        "product_a": {
            "id": prod_a["id"],
            "title": prod_a["title"],
            "brand": prod_a["brand"],
            "actives": sorted(list(acts_a)),
            "safety_score": safety_a,
            "warnings": warn_a
        },
        "product_b": {
            "id": prod_b["id"],
            "title": prod_b["title"],
            "brand": prod_b["brand"],
            "actives": sorted(list(acts_b)),
            "safety_score": safety_b,
            "warnings": warn_b
        }
    }

# ------------------------------------------------------------------------------
# 7.5. COMEDOGENICITY & IRRITANCY CLINICAL SCORING ENDPOINTS
# ------------------------------------------------------------------------------
class DermoScoreRequest(BaseModel):
    ingredients: Union[List[str], str]
    skin_type: str = "acne_prone"  # oily, dry, sensitive, acne_prone, normal

@app.post("/dermo/score")
def score_dermo_endpoint(req: DermoScoreRequest):
    """
    Evaluates clinical comedogenicity (acne risk 0-10) and irritancy risk (0-10)
    for a list or string of ingredients based on the database-backed clinical ingredient catalog.
    """
    return evaluate_product_dermo(req.ingredients, skin_type=req.skin_type)

@app.get("/products/{product_id}/dermo-score")
def score_product_dermo_endpoint(product_id: int, skin_type: str = "acne_prone"):
    """
    Retrieves a product from the database catalog and computes its full clinical dermatological score.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, brand, ingredients_raw, ingredients_parsed FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found.")

        ings = None
        if row["ingredients_parsed"]:
            try:
                ings = json.loads(row["ingredients_parsed"])
            except Exception:
                ings = None
        if not ings and row["ingredients_raw"]:
            ings = row["ingredients_raw"]
        if not ings:
            ings = []

        dermo_res = evaluate_product_dermo(ings, skin_type=skin_type)
        return {
            "product_id": row["id"],
            "title": row["title"],
            "brand": row["brand"],
            "ingredients_present": bool(ings),
            "evaluation": dermo_res
        }
    finally:
        conn.close()

@app.get("/dermo/ingredients")
def list_clinical_ingredients_endpoint():
    """
    Lists all clinical dermatological ingredients stored in the database.
    """
    return load_clinical_ingredients_from_db()

class ClinicalIngredientCreateRequest(BaseModel):
    inci: str
    common_name: Optional[str] = None
    functional_type: str = "active"
    comedogenicity: int = 0
    irritancy: int = 0
    tags: Optional[List[str]] = None
    evidence: Optional[str] = None
    contraindications: Optional[List[str]] = None
    notes: Optional[str] = None

@app.post("/dermo/ingredients")
def create_clinical_ingredient_endpoint(req: ClinicalIngredientCreateRequest):
    """
    Adds or updates a clinical ingredient in the database and hot-reloads the dermatological engine cache.
    """
    ok = add_clinical_ingredient_to_db(
        inci=req.inci,
        common_name=req.common_name,
        functional_type=req.functional_type,
        comedogenicity=req.comedogenicity,
        irritancy=req.irritancy,
        tags=req.tags,
        evidence=req.evidence,
        contraindications=req.contraindications,
        notes=req.notes
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save clinical ingredient to database.")
    reload_clinical_db()
    return {
        "status": "success",
        "message": f"Clinical ingredient '{req.inci}' persisted and clinical engine cache reloaded.",
        "ingredient": req.model_dump() if hasattr(req, "model_dump") else req.dict()
    }

@app.get("/")
def health_check():
    return {
        "status": "active",
        "database": "Production SQLite WAL",
        "products_indexed": len(df_cache),
        "ai_engine": "Jev 1.13 Free (OpenCode Zen) + Cosmetic Formulation Bioequivalence"
    }