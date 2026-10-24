"""
Comprehensive Whitelist and Catalog Sanitizer:
1. Purges marketing claims, clinical adjectives, batch codes, sentence fragments, and multi-ingredient period mergers from ingredient_whitelist.
2. Cleanses products.ingredients_raw and resets products that only had non-ingredient marketing claims.
3. Supports --dry-run and live commit modes.
"""

import sys
import os
import re
import argparse
import sqlite3
import json
from typing import Set, List, Dict, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.db_manager import get_connection
from pipeline.inci_matcher import parse_and_normalize_ingredients
from services.catalog_service import extract_actives_from_title

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "skincare.db")

# Strict patterns of items that are NOT single valid chemical/botanical ingredients
BOGUS_CLAIM_PATTERNS = [
    # Clinical and marketing claims
    r'\bnon[- ]irritat\w*',
    r'\bnon[- ]comedogen\w*',
    r'\bhypo[- ]?allergen\w*',
    r'\bdermatolog\w*',
    r'\bclinically\b',
    r'\btested\b',
    r'\buv filter\w*',
    r'\bsun protect\w*',
    r'\bbroad spectrum\b',
    r'\banti[- ]aging\b',
    r'\bcruelty[- ]free\b',
    r'\bparaben[- ]free\b',
    r'\bfragrance[- ]free\b',
    r'\balcohol[- ]free\b',
    r'\bgluten[- ]free\b',
    r'\bgmo[- ]free\b',
    r'\bgmo free\b',
    r'\bfor sensitive skin\b',
    r'\bsensitive skin\b',
    r'\ball skin types\b',
    r'\bdeep hydration\b',
    r'\binstant hydration\b',
    r'\bskin barrier\b',
    r'\bbarrier repair\b',
    r'\bnatural plant extracts?\b',
    r'\bherbal extracts?\b',
    r'\bplant extracts?\b',
    r'\bbotanical extracts?\b',
    r'\bthis product\b',
    r'\bit is\b',
    r'\bcontains\b',
    r'\bmay contain\b',
    r'\bwith a soft\b',
    # Sentence boundaries or multi-ingredient mergers separated by period
    r'\.\s+[A-Za-z]',
    # Batch codes and formulation registers
    r'\bf\.?i\.?l\.?\b',
    r'\bcode f\.?i\.?l\b',
    r'\bpr-\d{5,}\b',
    r'\bf#\d+',
    # Corrupt encoding artifacts
    r'ã', r'Â', r'™', r'®'
]

EXACT_BOGUS_NAMES = {
    'non-irritating', 'non-comedogenic', 'non comedogenic', 'non irritating',
    'hypoallergenic', 'hypo-allergenic', 'dermatologist tested', 'clinically proven',
    'anti-aging', 'anti aging', 'cruelty-free', 'cruelty free', 'paraben-free',
    'paraben free', 'fragrance-free', 'fragrance free', 'alcohol-free', 'alcohol free',
    'uv filters', 'uv filters spf 30', 'uv filters nanosized titanium dioxide',
    'sun protection', 'all skin types', 'natural plant extracts', 'plant extracts',
    'herbal extracts', 'botanical extracts', 'fruit extracts',
    'a soft', 'soft', 'feel', 'applicator', 'wear'
}

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BOGUS_CLAIM_PATTERNS]

# Valid exceptions to preserve (e.g. botanical varieties and official external D&C colorants)
VALID_EXCEPTIONS = {
    'st. john\'s wort', 'st. john’s wort', 'st. johns wort', 'st. paul\'s wort', 'st. paul’s wort',
    'carthamus tinctorius l. oil', 'cannabis sativa l. hemp', 'cinnamomum camphora l. leaf oil',
    'cinnamomum camphora l. ho leaf oil', 'cymbopogon martinii var. motia leaf oil',
    'cymbopogon martini v. motia leaf oil', 'foeniculum vulgare var. dulce seed oil',
    'citrus sinensis var. sanguina oil', 'citrus sinensis var. sanguina blood orange oil',
    'lavandula x intermedia var. grosso flower water',
    'ext. violet 2', 'ext. d&c violet 2', 'ext. d&c violet no. 2', 'ext. yellow 7'
}


def is_bogus_ingredient(name: str) -> bool:
    """Returns True if the ingredient name matches non-chemical marketing claims or corrupt tokens."""
    low = name.strip().lower()
    if low in EXACT_BOGUS_NAMES:
        return True
    
    # Check valid exceptions
    for exc in VALID_EXCEPTIONS:
        if exc in low:
            # If it's an exact or clean botanical extension, keep it
            if not any(stop in low for stop in ['non-comedogenic', 'it is', 'contains', 'may contain', 'this product']):
                return False

    for cp in COMPILED_PATTERNS:
        if cp.search(name):
            return True
    return False


def run_sanitization(dry_run: bool = True):
    conn = get_connection(DB_PATH)
    cur = conn.cursor()

    print(f"=== Whitelist & Catalog Sanitizer (Dry Run: {dry_run}) ===")
    
    # Step 1: Find all bogus ingredients in ingredient_whitelist
    cur.execute("SELECT id, name FROM ingredient_whitelist;")
    all_whitelist = cur.fetchall()

    bogus_whitelist_items = []
    for row_id, name in all_whitelist:
        if is_bogus_ingredient(name):
            bogus_whitelist_items.append((row_id, name))

    print(f"Found {len(bogus_whitelist_items)} bogus ingredients in whitelist.")
    bogus_names_set = {name.lower().strip() for _, name in bogus_whitelist_items}

    # Step 2: Scan products table for these bogus ingredients in ingredients_raw
    cur.execute("""
    SELECT id, title, brand, ingredients_raw, ingredient_source
    FROM products
    WHERE is_skincare = 1 AND ingredients_raw IS NOT NULL AND TRIM(ingredients_raw) != '';
    """)
    products_with_ings = cur.fetchall()

    affected_products = []
    for pid, title, brand, raw_ings, source in products_with_ings:
        tokens = [t.strip() for t in raw_ings.split(',') if t.strip()]
        cleaned_tokens = []
        has_bogus = False
        for t in tokens:
            t_low = t.lower()
            if t_low in bogus_names_set or is_bogus_ingredient(t):
                has_bogus = True
            else:
                cleaned_tokens.append(t)

        if has_bogus:
            affected_products.append({
                "id": pid,
                "title": title,
                "brand": brand,
                "source": source,
                "original_raw": raw_ings,
                "cleaned_tokens": cleaned_tokens
            })

    print(f"Found {len(affected_products)} products affected by bogus ingredients in ingredients_raw.")

    reset_to_null = [p for p in affected_products if len(p["cleaned_tokens"]) == 0]
    partially_cleaned = [p for p in affected_products if len(p["cleaned_tokens"]) > 0]
    print(f"  - Reset completely to NULL (all tokens were marketing claims): {len(reset_to_null)}")
    print(f"  - Cleaned & preserved remaining authentic ingredients: {len(partially_cleaned)}")

    print("\nSample items reset to NULL:")
    for p in reset_to_null[:10]:
        print(f"  #{p['id']} [{p['brand']}] {p['title'][:40]} | Was: '{p['original_raw']}'")

    print("\nSample items partially cleaned:")
    for p in partially_cleaned[:5]:
        new_raw = ", ".join(p["cleaned_tokens"])
        print(f"  #{p['id']} [{p['brand']}] {p['title'][:40]}")
        print(f"    Before: '{p['original_raw']}'")
        print(f"    After:  '{new_raw}'")

    if dry_run:
        print("\n[DRY RUN] No changes were written to database. Run without --dry-run to commit.")
        return

    # Step 3: Delete bogus items from ingredient_whitelist
    print(f"\nDeleting {len(bogus_whitelist_items)} bogus ingredients from ingredient_whitelist...")
    bogus_ids = [row_id for row_id, _ in bogus_whitelist_items]
    cur.executemany("DELETE FROM ingredient_whitelist WHERE id = ?;", [(i,) for i in bogus_ids])
    conn.commit()

    # Step 4: Update affected products
    print("Updating affected products in database...")
    for p in reset_to_null:
        cur.execute("""
        UPDATE products
        SET ingredients_raw = NULL,
            ingredients_parsed = NULL,
            actives = NULL,
            ingredient_source = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """, (p["id"],))

    for p in partially_cleaned:
        new_raw = ", ".join(p["cleaned_tokens"])
        parsed_ings, acts = parse_and_normalize_ingredients(new_raw)
        title_acts = extract_actives_from_title(p["title"])
        all_acts = sorted(list(set(acts).union(title_acts)))
        cur.execute("""
        UPDATE products
        SET ingredients_raw = ?,
            ingredients_parsed = ?,
            actives = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """, (new_raw, json.dumps(parsed_ings), json.dumps(all_acts), p["id"]))

    conn.commit()
    conn.close()
    print("Sanitization completed successfully and committed to database.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Commit changes to database")
    args = parser.parse_args()

    run_sanitization(dry_run=not args.commit)
