"""
Authoritative Seed Script for Ingredient Whitelist.
Extracts, validates, cleans, and categorizes verified cosmetic ingredients from:
1. inci_reference (53,000+ authentic SkinSAFE formulations, frequency >= 2)
2. dermo_data (Clinical dermatology database)
3. DEFAULT_CLINICAL_INGREDIENTS & DEFAULT_SYNONYMS
4. KEY_ACTIVES cosmetic actives catalog
"""

import os
import sys
import re
import sqlite3
import logging
from typing import Dict, Set, Tuple, List, Optional, Any
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import (
    get_connection, DB_PATH, DEFAULT_SYNONYMS, DEFAULT_CLINICAL_INGREDIENTS
)
from pipeline.inci_matcher import KEY_ACTIVES, ACTIVE_MARKERS
from dermo_data import INGREDIENT_DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("skincare_pipeline.seed_whitelist")

# Standalone words that MUST NEVER match or exist as a standalone ingredient on their own
# because they are ubiquitous common English words or non-specific descriptors
EXCLUDED_STANDALONE = {
    'oil', 'acid', 'water', 'extract', 'butter', 'wax', 'gum', 'juice', 'leaf', 
    'flower', 'root', 'fruit', 'seed', 'peel', 'bark', 'stem', 'gel', 'cream', 
    'drops', 'sun', 'spf', 'skin', 'face', 'body', 'daily', 'care', 'beauty', 
    'pure', 'natural', 'organic', 'active', 'free', 'clean', 'soft', 'smooth', 
    'bright', 'anti', 'complex', 'solution', 'formula', 'treatment', 'repair', 
    'mineral', 'gold', 'clear', 'light', 'dark', 'rose', 'tea', 'milk', 'sugar',
    'honey', 'salt', 'clay', 'mud', 'berry', 'orange', 'lemon', 'apple', 'grape',
    'iron', 'steel', 'cloth', 'cotton', 'bottle', 'tube', 'paper', 'towel',
    'redness', 'irritation', 'acne', 'inflammation', 'itchiness', 'dryness',
    'blemish', 'blemishes', 'spots', 'wrinkle', 'wrinkles', 'eczema', 'psoriasis',
    'and', 'the', 'for', 'with', 'from', 'contains', 'plus', 'item', 'details',
    'directions', 'features', 'benefits', 'routine', 'warning', 'caution',
    'application', 'revitaliz', 'results', 'suitable for', 'contour', 'years',
    'evening', 'morning', 'night', 'day', 'fresh', 'dermatologist', 'tested',
    'vegan', 'cruelty', 'paraben', 'fragrance', 'flavor', 'scent',
    'ingredients', 'alcohol-free', 'lock in moisture', 'plant', 'balm', 'birch',
    'micellar water', 'sunscreen', 'essence', 'vaseline', 'serum', 'lotion',
    'cleanser', 'toner', 'shampoo', 'conditioner', 'wash', 'pad', 'pads', 'mist'
}

ACTIVE_KEYWORDS = {
    'salicylic', 'glycolic', 'lactic', 'hyaluronic', 'ascorbic', 'azelaic',
    'mandelic', 'kojic', 'tranexamic', 'ferulic', 'lactobionic', 'gluconolactone',
    'niacinamide', 'panthenol', 'tocopherol', 'retinol', 'retinyl', 'retinal',
    'adapalene', 'biotin', 'peptide', 'matrixyl', 'palmitoyl', 'argireline',
    'copper tripeptide', 'centella', 'cica', 'madecassoside', 'asiaticoside',
    'ceramide', 'arbutin', 'bakuchiol', 'resveratrol', 'adenosine', 'ectoin',
    'caffeine', 'allantoin', 'squalane', 'urea', 'zinc pca', 'zinc oxide',
    'benzoyl peroxide', 'sulfur', 'licorice', 'glabridin', 'snail secretion',
    'galactomyces', 'bifida ferment'
}

MARKETING_VERBS_NOUNS = {
    'blemish', 'blemishes', 'wrinkle', 'wrinkles', 'complexion', 'condition',
    'leaving', 'protects', 'protect', 'soothe', 'soothes', 'soothing', 'hydrates',
    'hydrating', 'hydrate', 'glowing', 'glow', 'moisturizes', 'moisturizing',
    'nourishes', 'nourishing', 'appearance', 'promotes', 'promoting', 'targets',
    'formulated', 'enriched', 'infused', 'suitable', 'tested', 'visible', 'visibly',
    'pores', 'fine lines', 'dark circles', 'elasticity', 'firmness', 'texture',
    'sensorial', 'radiance', 'radiant', 'youthful', 'rejuvenat', 'revitaliz',
    'soften', 'smooths', 'smoothes', 'supple', 'flawless', 'luminos', 'anti-aging',
    'anti aging', 'age spots', 'dark spots', 'coverage', 'lips', 'hair types',
    'skin types', 'to help', 'to protect', 'to soothe', 'to hydrate', 'to provide',
    'our secret', 'clinically proven', 'gluten-free', 'cruelty-free', 'paraben-free',
    'morning savior', 'day after day', 'revolutionary', 'soft', 'feel', 'korean',
    'products', 'supplier', 'suppliers', 'confidence', 'applicator', 'outlining',
    'wear', 'swipe', 'transfer', 'smudge', 'waterproof', 'comforts skin',
    'non-irritat', 'non-comedogen', 'hypoallergen', 'dermatolog', 'tested', 'clinically',
    'uv filter', 'sun protect', 'broad spectrum', 'deep hydration', 'instant hydration',
    'skin barrier', 'barrier repair', 'plant extract', 'herbal extract', 'botanical extract',
    'fruit extract', 'this product', 'it is', 'contains', 'may contain', 'with a soft',
    'delivery enhancer', 'f.i.l.', 'pr-000', 'without causing'
}

def clean_token(token: str) -> Optional[str]:
    """Cleans a single ingredient token from trailing noise and symbols, strictly excluding marketing verbs."""
    if not token or not isinstance(token, str):
        return None
    # Strip leading 'and ', 'or ', '& ', 'with ', 'plus ', 'contains '
    t = re.sub(r'^(?:and|or|with|plus|contains|&)\s+', '', token, flags=re.IGNORECASE)
    t = re.sub(r'^[•\-\*\d\.\s/%\+:#]+', '', t)
    t = re.sub(r'[•\-\*\.\s/%\+:#]+$', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = t.strip('\"\'()[]{}.,;:')
    if not t:
        return None
    if len(t) < 3 or len(t) > 60:
        return None
    low = t.lower()

    # Reject corrupt characters or embedded asterisks
    if any(c in t for c in ['ã', 'Â', '™', '®', '*']):
        return None

    # Reject sentence boundaries or multi-ingredient mergers separated by period
    if re.search(r'\.\s+[A-Za-z]', t):
        return None
    
    # Reject narrative sentences and marketing claims
    if any(m in low for m in MARKETING_VERBS_NOUNS):
        return None

    if any(stop in low for stop in [
        'how to use', 'directions', 'apply daily', 'warning', 'precautions', 
        'suitable for', 'is moisturized', 'years', 'clinically proven'
    ]):
        return None

    # Reject conversational starters
    tokens = low.split()
    if not tokens:
        return None
    if tokens[0] in {'the', 'a', 'an', 'to', 'with', 'for', 'from', 'in', 'on', 'so', 'you', 'if', 'is', 'it', 'offers', 'provides', 'made'}:
        return None

    # Check if multiple English function words exist (e.g. 'to help condition the lips')
    func_words = {'the', 'a', 'an', 'to', 'your', 'our', 'it', 'its', 'their', 'this', 'that', 'with'}
    if sum(1 for w in tokens if w in func_words) >= 2:
        return None

    # If single word, must not be in excluded standalone list and must be >= 4 chars
    # Exception: common 3-char chemical acronyms (BHA, AHA, PHA, PCA, SLS, BHT, PEG)
    if ' ' not in t:
        if low in EXCLUDED_STANDALONE:
            return None
        if len(t) < 4 and low not in {'bha', 'aha', 'pha', 'pca', 'sls', 'bht', 'peg'}:
            return None
    else:
        # Multi-word: check that it doesn't consist solely of excluded words
        words = low.split()
        if all(w in EXCLUDED_STANDALONE for w in words):
            return None
    return t

def categorize_ingredient(name: str) -> str:
    """Classifies an ingredient into its primary functional cosmetic category."""
    low = name.lower()
    if any(k in low for k in ACTIVE_KEYWORDS):
        return "active"
    if any(k in low for k in [
        'extract', 'leaf', 'flower', 'root', 'seed', 'juice', 'bark', 
        'fruit', 'stem', 'peel', 'oil', 'butter', 'hydrosol', 'botanical'
    ]):
        return "botanical"
    if any(k in low for k in [
        'triglyceride', 'stearate', 'palmitate', 'myristate', 'dimethicone', 
        'wax', 'silicone', 'emollient', 'caprylate', 'oleate', 'isostearate'
    ]):
        return "emollient"
    if any(k in low for k in [
        'glycerin', 'glycol', 'propanediol', 'sorbitol', 'hyaluronate', 
        'humectant', 'xylitol', 'panthenol'
    ]):
        return "humectant"
    if any(k in low for k in [
        'phenoxyethanol', 'paraben', 'benzoate', 'sorbate', 'edta', 
        'preservative', 'benzyl alcohol', 'dehydroacetate'
    ]):
        return "preservative"
    if any(k in low for k in [
        'sulfate', 'glucoside', 'betaine', 'laurate', 'peg-', 'polysorbate', 
        'surfactant', 'cleanser'
    ]):
        return "surfactant"
    return "general"

def clean_paren_and_slash(p: str) -> List[str]:
    """Cleans unbalanced parentheses/brackets and extracts separated slash synonyms."""
    if not p:
        return []
    p = p.strip()
    # Strip unclosed trailing parentheses / brackets from comma splits
    if '(' in p and ')' not in p:
        p = re.sub(r'\(.*$', '', p).strip()
    if '[' in p and ']' not in p:
        p = re.sub(r'\[.*$', '', p).strip()
    tokens = []
    if ' / ' in p:
        for sub in p.split(' / '):
            c = clean_token(sub)
            if c:
                tokens.append(c)
    c_full = clean_token(p)
    if c_full:
        tokens.append(c_full)
    return tokens

def seed_ingredient_whitelist(conn=None, min_frequency: int = 1) -> Dict[str, Any]:
    """
    Seeds the ingredient_whitelist table with all verified ingredients from the entire SkinSAFE dataset,
    clinical dermatology databases, official INCI references, and active catalogs.
    """
    should_close = False
    if conn is None:
        conn = get_connection(DB_PATH)
        should_close = True

    try:
        cur = conn.cursor()
        logger.info("--- Starting Whitelist Seeding from Entire SkinSAFE Dataset ---")

        token_counter = Counter()

        # 1. Harvest candidates from inci_reference database (verified SkinSAFE formulations)
        cur.execute("SELECT ingredients FROM inci_reference WHERE ingredients IS NOT NULL AND source = 'skinsafe';")
        for (ings,) in cur.fetchall():
            if not ings:
                continue
            for p in re.split(r'[,;\n•]+|\.\s+', ings):
                for cleaned in clean_paren_and_slash(p):
                    m = re.search(r'\(([^)]+)\)', cleaned)
                    if m:
                        inner = clean_token(m.group(1))
                        outer = clean_token(re.sub(r'\([^)]+\)', ' ', cleaned))
                        if inner:
                            token_counter[inner.title()] += 1
                        if outer:
                            token_counter[outer.title()] += 1
                    base_clean = clean_token(re.sub(r'[()\[\]]', '', cleaned))
                    if base_clean:
                        token_counter[base_clean.title()] += 1

        # 2. Harvest directly from raw SkinSAFE CSV directory if present
        skinsafe_candidates = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "raw", "SkinSAFE"),
            os.path.join(os.getcwd(), "data", "raw", "SkinSAFE"),
            "/mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/data/raw/SkinSAFE"
        ]
        skinsafe_dir = next((d for d in skinsafe_candidates if os.path.isdir(d)), None)
        if skinsafe_dir:
            import glob, csv
            csv_files = glob.glob(os.path.join(skinsafe_dir, "*.csv"))
            logger.info(f"Scanning {len(csv_files)} raw SkinSAFE CSV files from {skinsafe_dir}...")
            for cf in csv_files:
                try:
                    with open(cf, 'r', encoding='utf-8', errors='ignore') as fp:
                        reader = csv.DictReader(fp)
                        for row in reader:
                            ings = row.get('ingredients') or ''
                            if not ings:
                                continue
                            for p in re.split(r'[,;\n•]+|\.\s+', ings):
                                for cleaned in clean_paren_and_slash(p):
                                    m = re.search(r'\(([^)]+)\)', cleaned)
                                    if m:
                                        inner = clean_token(m.group(1))
                                        outer = clean_token(re.sub(r'\([^)]+\)', ' ', cleaned))
                                        if inner:
                                            token_counter[inner.title()] += 1
                                        if outer:
                                            token_counter[outer.title()] += 1
                                    base_clean = clean_token(re.sub(r'[()\[\]]', '', cleaned))
                                    if base_clean:
                                        token_counter[base_clean.title()] += 1
                except Exception as ex:
                    logger.warning(f"Could not read SkinSAFE file {cf}: {ex}")

        logger.info(f"Extracted {len(token_counter)} distinct candidate tokens from entire SkinSAFE dataset.")

        # 3. Add curated inputs from clinical DB & synonyms
        whitelist_items: Dict[str, Dict[str, str]] = {}

        # From SkinSAFE formulations with frequency >= min_frequency
        for tok, count in token_counter.items():
            if count >= min_frequency:
                cat = categorize_ingredient(tok)
                whitelist_items[tok.lower()] = {
                    "name": tok,
                    "canonical_name": tok,
                    "category": cat
                }

        # From clinical database (dermo_data INGREDIENT_DB)
        for inci_key, ing_obj in INGREDIENT_DB.items():
            clean_inci = clean_token(ing_obj.inci)
            if clean_inci:
                whitelist_items[clean_inci.lower()] = {
                    "name": clean_inci,
                    "canonical_name": ing_obj.common_name or clean_inci,
                    "category": "active" if ing_obj.functional_type in ("active", "exfoliant", "retinoid") else ing_obj.functional_type or "general"
                }
            if ing_obj.common_name:
                clean_common = clean_token(ing_obj.common_name)
                if clean_common and clean_common.lower() not in EXCLUDED_STANDALONE:
                    whitelist_items[clean_common.lower()] = {
                        "name": clean_common,
                        "canonical_name": ing_obj.inci,
                        "category": "active"
                    }

        # From DEFAULT_CLINICAL_INGREDIENTS in db_manager
        for row in DEFAULT_CLINICAL_INGREDIENTS:
            c_inci, c_common, c_ftype = row[0], row[1], row[2]
            c1 = clean_token(c_inci)
            if c1:
                whitelist_items[c1.lower()] = {
                    "name": c1,
                    "canonical_name": c_common or c1,
                    "category": "active" if c_ftype in ("active", "exfoliant", "retinoid") else c_ftype or "general"
                }
            c2 = clean_token(c_common)
            if c2 and c2.lower() not in EXCLUDED_STANDALONE:
                whitelist_items[c2.lower()] = {
                    "name": c2,
                    "canonical_name": c_inci,
                    "category": "active"
                }

        # From KEY_ACTIVES and synonyms
        for act_cat, syn_list in KEY_ACTIVES.items():
            for s in syn_list:
                cs = clean_token(s)
                if cs and cs.lower() not in EXCLUDED_STANDALONE:
                    whitelist_items[cs.lower()] = {
                        "name": cs.title(),
                        "canonical_name": cs.title(),
                        "category": "active"
                    }

        for alias, canon, cat in DEFAULT_SYNONYMS:
            ca = clean_token(alias)
            cc = clean_token(canon)
            if ca and ca.lower() not in EXCLUDED_STANDALONE:
                # Only use alias canonical name if it is a generic carrier, alcohol, or fragrance
                canon_target = cc.title() if (cc and cat in ('carrier', 'alcohol', 'fragrance')) else ca.title()
                whitelist_items[ca.lower()] = {
                    "name": ca.title(),
                    "canonical_name": canon_target,
                    "category": cat or "general"
                }

        logger.info(f"Compiled {len(whitelist_items)} total verified cosmetic ingredients for whitelist.")

        # 4. Bulk insert into SQLite ingredient_whitelist preserving any inactive overrides
        cur.execute("SELECT name FROM ingredient_whitelist WHERE is_active = 0;")
        disabled_names = set(r[0].lower() for r in cur.fetchall())

        cur.execute("DELETE FROM ingredient_whitelist;")
        rows_to_insert = [
            (item["name"], item["canonical_name"], item["category"], 0 if item["name"].lower() in disabled_names else 1)
            for item in whitelist_items.values()
        ]

        cur.executemany("""
        INSERT INTO ingredient_whitelist (name, canonical_name, category, is_active)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            canonical_name = excluded.canonical_name,
            category = excluded.category,
            is_active = excluded.is_active;
        """, rows_to_insert)

        conn.commit()

        # 5. Reload in-memory matcher cache
        try:
            from pipeline.whitelist_matcher import reload_whitelist_cache
            reload_whitelist_cache(conn)
        except Exception as ex:
            logger.warning(f"Could not reload whitelist cache: {ex}")

        # 6. Fetch category summary
        cur.execute("SELECT COUNT(*) FROM ingredient_whitelist;")
        total_seeded = cur.fetchone()[0]

        cur.execute("SELECT category, COUNT(*) FROM ingredient_whitelist GROUP BY category ORDER BY COUNT(*) DESC;")
        breakdown = {r[0]: r[1] for r in cur.fetchall()}

        logger.info(f"--- Whitelist Seeding Completed Successfully ---")
        logger.info(f"Total Ingredients in Whitelist: {total_seeded}")
        for cat, cnt in breakdown.items():
            logger.info(f"  {cat}: {cnt}")

        return {
            "total_seeded": total_seeded,
            "category_breakdown": breakdown
        }

    except Exception as e:
        logger.error(f"Error seeding whitelist: {e}", exc_info=True)
        conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    seed_ingredient_whitelist()
