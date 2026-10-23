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

from dermo_data import INGREDIENT_DB, Comedogenicity, Irritancy, _get_ingredient_fuzzy
from pipeline.config import cfg
from pipeline.db_manager import load_synonyms_from_db, add_synonym_to_db
from pipeline.inci_matcher import (
    get_cross_encoder,
    score_cross_encoder,
    verify_match_with_gemini,
    clean_product_title,
    normalize_brand
)

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

@app.get("/")
def health_check():
    return {
        "status": "active",
        "database": "Production SQLite WAL",
        "products_indexed": len(df_cache),
        "ai_engine": "Jev 1.13 Free (OpenCode Zen) + Cosmetic Formulation Bioequivalence"
    }