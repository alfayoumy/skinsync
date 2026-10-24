"""
Catalog Service: Manages database loading, ingredient normalization,
in-memory cache indexing, and clinical skin benefit/risk mappings.
"""

import os
import re
import json
import sqlite3
import pandas as pd
from typing import Dict, Set, List, Tuple, Optional, Any

from pipeline.db_manager import load_synonyms_from_db
from pipeline.logging_config import get_logger
from pipeline.inci_tokenizer import tokenize_inci_string


logger = get_logger("skincare_engine.catalog_service")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skincare.db")

KEY_ACTIVES = {
    'hyaluronic_acid': ['hyaluronic acid', 'sodium hyaluronate', 'hydrolyzed hyaluronic', 'hyaluronate', 'hyaluron', 'hyaluro', 'hyalu', 'h.a', 'ha'],
    'vitamin_b5': ['panthenol', 'd-panthenol', 'pro-vitamin b5', 'pantothenic acid', 'vitamin b5', 'b5'],
    'niacinamide': ['niacinamide', 'nicotinamide', 'vitamin b3', 'b3'],
    'vitamin_c': ['ascorbic acid', 'l-ascorbic', 'ethyl ascorbic', 'magnesium ascorbyl', 'sodium ascorbyl', 'ascorbyl glucoside', 'vitamin c', 'vit c', 'vit-c', 'ascorbic', 'ascorbyl'],
    'retinoid': ['retinol', 'retinyl palmitate', 'retinal', 'hydroxypinacolone retinoate', 'adapalene', 'tretinoin', 'retinyl', 'retinoid'],
    'salicylic_acid': ['salicylic acid', 'salicylic', 'bha', 'betaine salicylate', 'willow bark'],
    'aha': ['glycolic acid', 'glycolic', 'lactic acid', 'lactic', 'mandelic acid', 'mandelic', 'malic acid', 'citric acid', 'tartaric acid', 'aha'],
    'ceramides': ['ceramide np', 'ceramide ap', 'ceramide eop', 'ceramide 3', 'ceramide', 'ceramides', 'phytosphingosine', 'sphingosine'],
    'centella': ['centella asiatica', 'centella', 'madecassoside', 'asiaticoside', 'asiatic acid', 'madecassic acid', 'cica', 'gotu kola'],
    'peptides': ['peptide', 'peptides', 'matrixyl', 'copper tripeptide', 'palmitoyl', 'argireline', 'oligopeptide', 'acetyl hexapeptide'],
    'azelaic_acid': ['azelaic acid', 'azelaic', 'potassium azeloyl diglycinate'],
    'arbutin': ['alpha-arbutin', 'arbutin', 'alpha arbutin', 'beta-arbutin'],
    'zinc': ['zinc pca', 'zinc oxide', 'zinc gluconate', 'zinc'],
    'squalane': ['squalane', 'squalene'],
    'tea_tree': ['tea tree', 'melaleuca alternifolia'],
    'collagen': ['hydrolyzed collagen', 'marine collagen', 'collagen'],
    'aloe_vera': ['aloe barbadensis', 'aloe vera'],
    'tranexamic_acid': ['tranexamic acid', 'tranexamic', 'txa', 'cetyl tranexamate'],
    'kojic_acid': ['kojic acid', 'kojic dipalmitate'],
    'bakuchiol': ['bakuchiol'],
    'urea': ['urea', 'hydroxyethyl urea'],
    'benzoyl_peroxide': ['benzoyl peroxide', 'bpo'],
    'sulfur': ['sulfur', 'colloidal sulfur'],
    'ectoin': ['ectoin'],
    'pha': ['gluconolactone', 'lactobionic acid', 'galactose', 'maltobionic acid', 'pha'],
    'ferulic_acid': ['ferulic acid', 'ferulic'],
    'licorice': ['glycyrrhiza glabra', 'licorice root', 'dipotassium glycyrrhizate', 'glabridin', 'licorice'],
    'snail_mucin': ['snail secretion filtrate', 'snail mucin'],
    'ferments': ['galactomyces', 'bifida ferment', 'saccharomyces', 'lactobacillus ferment']
}

# Database-backed ingredient synonyms (loaded from ingredient_synonyms table)
SYNONYMS: Dict[str, str] = {}

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

def parse_and_clean_ingredients(text: Optional[str]) -> Tuple[List[str], Set[str]]:
    """
    Parses comma/semicolon/bullet-separated raw ingredient text using chemical-aware inci_tokenizer,
    cleans percentages and noise, maps aliases to canonical names, and extracts active categories.
    """
    if not isinstance(text, str) or not text.strip():
        return [], set()
    res = tokenize_inci_string(text)
    return res.tokens, set(res.declared_actives)

def extract_actives_from_title(title: Optional[str]) -> Set[str]:
    """Extracts known active categories present in the product title with boundary safety."""
    if not isinstance(title, str):
        return set()
    t = title.lower()
    found = set()
    for act_cat, keywords in KEY_ACTIVES.items():
        for k in keywords:
            if len(k) <= 3:
                if re.search(r'\b' + re.escape(k) + r'\b', t):
                    found.add(act_cat)
                    break
            else:
                if k in t:
                    found.add(act_cat)
                    break
    return found

# Global in-memory cache
df_cache: Optional[pd.DataFrame] = None

def get_catalog_df() -> pd.DataFrame:
    """Returns the in-memory pandas DataFrame cache of products."""
    global df_cache
    if df_cache is None:
        load_catalog_from_db()
    return df_cache

def set_catalog_df(df: pd.DataFrame):
    """Sets the in-memory catalog DataFrame."""
    global df_cache
    df_cache = df

def get_synonyms() -> Dict[str, str]:
    """Returns the active ingredient synonym mappings."""
    global SYNONYMS
    return SYNONYMS

def set_synonyms(synonyms: Dict[str, str]):
    """Updates the active ingredient synonym mappings."""
    global SYNONYMS
    SYNONYMS = synonyms

def load_catalog_from_db():
    """
    Reloads all products and synonyms from SQLite into the in-memory cache,
    pre-calculating parsed ingredients, actives, and title actives.
    """
    global df_cache, SYNONYMS
    SYNONYMS = load_synonyms_from_db()

    if not os.path.exists(DB_PATH):
        logger.warning(f"Database not found at {DB_PATH}. Using fallback CSV.")
        csv_fallback = os.path.join(os.path.dirname(os.path.dirname(__file__)), "master_product_database_v10_final.csv")
        df_cache = pd.read_csv(csv_fallback)
        df_cache['std_category'] = df_cache.get('std_category', 'Other')
    else:
        conn = sqlite3.connect(DB_PATH)
        # Authoritative Canonical Index: Exactly ONE record per unique product formulation.
        # Retailer listings are store offers and are NOT duplicated in search or recommendation indices.
        df_cache = pd.read_sql_query("""
        SELECT c.id,
               c.id as canonical_product_id,
               c.canonical_title as title,
               c.brand,
               c.category as std_category,
               c.min_price as price_low,
               c.max_price as price_high,
               COALESCE(c.primary_image_url, (SELECT p.image_url FROM products p WHERE p.canonical_product_id = c.id AND p.image_url IS NOT NULL LIMIT 1)) as image_url,
               COALESCE((SELECT p.product_url FROM products p WHERE p.canonical_product_id = c.id AND p.product_url IS NOT NULL LIMIT 1), '') as url,
               COALESCE((SELECT p.store_id FROM products p WHERE p.canonical_product_id = c.id AND p.store_id IS NOT NULL LIMIT 1), 'canonical') as store_id,
               COALESCE((SELECT MAX(p.is_available) FROM products p WHERE p.canonical_product_id = c.id), 1) as is_available,
               c.ingredients_raw as ingredients,
               c.ingredients_parsed,
               c.actives,
               c.ingredient_source,
               c.gate4_jev_status,
               c.gate4_gemini_status,
               c.gate4_cross_status,
               COALESCE(c.store_count, 1) as store_count,
               c.min_price,
               c.max_price,
               c.is_skincare
        FROM canonical_products c
        WHERE c.is_skincare = 1;
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

# Initialize in-memory cache upon module import
load_catalog_from_db()
