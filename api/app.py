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
    'aloe_vera': ['aloe barbadensis', 'aloe vera']
}

SYNONYMS = {
    'aqua': 'water', 'eau': 'water', 'purified water': 'water', 'deionized water': 'water',
    'parfum': 'fragrance', 'perfume': 'fragrance', 'fragrance (parfum)': 'fragrance',
    'alcohol denat.': 'alcohol denat', 'alcohol denat': 'alcohol denat',
    'sodium lauryl sulfate': 'sls', 'sodium laureth sulfate': 'sles',
    'cocos nucifera (coconut) oil': 'coconut oil', 'coconut oil': 'coconut oil',
    'theobroma cacao (cocoa) seed butter': 'cocoa butter', 'cocoa butter': 'cocoa butter',
    'butyrospermum parkii (shea) butter': 'shea butter', 'shea butter': 'shea butter',
    'simmondsia chinensis (jojoba) seed oil': 'jojoba oil', 'jojoba oil': 'jojoba oil'
}

SKIN_BENEFITS = {
    'oily': {'salicylic_acid': 3.0, 'niacinamide': 2.5, 'zinc': 2.5, 'tea_tree': 2.0, 'centella': 1.5},
    'dry': {'hyaluronic_acid': 3.0, 'ceramides': 3.0, 'squalane': 2.5, 'vitamin_b5': 2.0, 'collagen': 1.5},
    'sensitive': {'centella': 3.0, 'vitamin_b5': 2.5, 'ceramides': 2.5, 'aloe_vera': 2.0}
}

SKIN_RISKS = {
    'oily': {
        'coconut oil': 4.0, 'cocoa butter': 3.5, 'isopropyl myristate': 5.0, 
        'isopropyl palmitate': 5.0, 'ethylhexyl palmitate': 4.0, 'wheat germ oil': 5.0
    },
    'dry': {
        'alcohol denat': 4.0, 'sls': 3.5, 'salicylic_acid': 1.5
    },
    'sensitive': {
        'fragrance': 4.0, 'alcohol denat': 4.0, 'sls': 3.5, 'aha': 2.5,
        'retinoid': 2.0, 'salicylic_acid': 2.0
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
    global df_cache
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
               ingredients_raw as ingredients, ingredients_parsed, actives, ingredient_source
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
def compute_weighted_jaccard(list_a, list_b):
    if not list_a or not list_b:
        return 0.0
    weights_a = {ing: 1.0 / np.sqrt(i + 1) for i, ing in enumerate(list_a)}
    weights_b = {ing: 1.0 / np.sqrt(i + 1) for i, ing in enumerate(list_b)}
    all_keys = set(weights_a.keys()).union(weights_b.keys())
    inter_w = sum(min(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    union_w = sum(max(weights_a.get(k, 0.0), weights_b.get(k, 0.0)) for k in all_keys)
    return (inter_w / union_w) if union_w > 0 else 0.0

def compute_title_sim(t1, t2):
    w1 = set(re.findall(r'\w+', str(t1).lower()))
    w2 = set(re.findall(r'\w+', str(t2).lower()))
    stop = {'the', 'and', 'with', 'for', 'skin', 'ml', 'g', 'in', 'of', 'face'}
    w1 = w1 - stop
    w2 = w2 - stop
    if not w1 or not w2: return 0.0
    return len(w1.intersection(w2)) / len(w1.union(w2))

def evaluate_skin_safety(parsed_ings, actives, skin_type):
    if skin_type not in SKIN_BENEFITS:
        return 75.0, []
    benefit_pts = sum(SKIN_BENEFITS[skin_type].get(act, 0.0) for act in actives)
    risk_pts = 0.0
    warnings = []
    risks = SKIN_RISKS[skin_type]
    for ing in parsed_ings:
        for r_name, r_val in risks.items():
            if r_name in ing:
                risk_pts += r_val
                warnings.append(f"Contains {r_name.title()} ({skin_type} skin flag)")
                break
    safety_score = 75.0 + min(25.0, benefit_pts * 4.0) - min(50.0, risk_pts * 10.0)
    safety_score = max(20.0, min(100.0, safety_score))
    return safety_score, list(set(warnings))

# ==============================================================================
# 4. JEV 1.13 FREE DECISION MODEL (OPENCODE ZEN)
# ==============================================================================
def call_jev_reranker(source_title, candidate_title, candidate_ings, skin_type):
    opencode_key = os.environ.get("OPENCODE_API_KEY")
    typesafe_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if opencode_key:
        api_key = opencode_key
        url = "https://opencode.ai/zen/v1/systemone"
        model_name = "jev-1.13-free"
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
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            answers = data.get("answers", {})
            dupe_info = answers.get("is_effective_dupe", {})
            if isinstance(dupe_info, dict) and "noul" in dupe_info:
                return float(dupe_info["noul"])
            elif isinstance(dupe_info, (int, float)):
                return float(dupe_info)
            return None
    except Exception as e:
        logger.warning(f"Jev API call skipped/timed out: {e}")
        return None

# ==============================================================================
# 5. CORE RECOMMENDATION ENGINE
# ==============================================================================
def get_recommendations(target_title: str, skin_type: str = "normal"):
    df = df_cache
    matches = df[df['title'].str.lower() == target_title.lower()]
    if matches.empty:
        matches = df[df['title'].str.contains(re.escape(target_title), case=False, na=False)]
    if matches.empty:
        return None

    target = matches.iloc[0]
    target_idx = target.name
    target_cat = target['std_category']
    target_ings = target['parsed_ingredients']
    target_actives = target['all_actives']

    candidates = df[df['std_category'] == target_cat].copy()
    if len(candidates) < 6:
        candidates = df.copy()

    scored = []
    for idx, row in candidates.iterrows():
        if idx == target_idx:
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
            'index': int(idx),
            'title': str(row['title']),
            'brand': str(row['brand']),
            'price': float(row['price_low']),
            'match_score': float(final_match),
            'image_url': str(row['image_url']) if pd.notna(row['image_url']) else None,
            'warnings': "; ".join(warnings) if warnings else None,
            'url': str(row['url'])
        })

    scored = sorted(scored, key=lambda x: x['match_score'], reverse=True)[:5]

    # Optional Jev AI calibration
    has_jev_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if has_jev_key:
        for cand in scored:
            row_cand = df.iloc[cand['index']]
            dupe_prob = call_jev_reranker(target['title'], cand['title'], row_cand['ingredients'], skin_type)
            if dupe_prob is not None:
                cand['ai_verified'] = True
                cand['ai_confidence'] = float(round(dupe_prob * 100, 1))
                cand['match_score'] = float(round((cand['match_score'] * 0.65) + (dupe_prob * 100 * 0.35), 1))
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

    return {"source": source_prod, "results": scored}

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

@app.get("/")
def health_check():
    return {
        "status": "active",
        "database": "Production SQLite WAL",
        "products_indexed": len(df_cache),
        "ai_engine": "Jev 1.13 Free (OpenCode Zen) + Cosmetic Formulation Bioequivalence"
    }