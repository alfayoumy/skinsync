import pandas as pd
import json
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.inci_matcher import parse_and_normalize_ingredients

logger = logging.getLogger("skincare_pipeline.import_v10")

def import_v10_to_db(csv_path=None, db_path=None):
    if not csv_path:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "master_product_database_v10_final.csv")

    if not os.path.exists(csv_path):
        logger.error(f"CSV file not found at {csv_path}")
        return 0

    conn = get_connection(db_path) if db_path else get_connection()
    cur = conn.cursor()

    df = pd.read_csv(csv_path)
    logger.info(f"Importing {len(df)} products from {csv_path} into SQLite database...")

    imported = 0
    updated = 0

    for idx, row in df.iterrows():
        title = str(row.get('title', '')).strip()
        brand = str(row.get('brand', '')).strip() or "Unknown"
        category = str(row.get('std_category', '')).strip() or "Other"
        url = str(row.get('url', '')).strip()
        if not url:
            continue

        price = float(row.get('price_low') or 0.0)
        price_high = float(row.get('price_high') or price)
        raw_ings = str(row.get('ingredients', '')).strip()
        if raw_ings.lower() == 'nan':
            raw_ings = None

        image_url = str(row.get('image_url', '')).strip()
        if image_url.lower() == 'nan' or not image_url:
            image_url = None

        # Determine store_id
        store_id = "other"
        if "sourcebeauty.com" in url:
            store_id = "sourcebeauty"
        elif "eg.feel22.com" in url or "feel22.com" in url:
            store_id = "feel22"
        elif "bloompharmacy.com" in url:
            store_id = "bloompharmacy"
        elif "drdawood" in url:
            store_id = "drdawood"
        elif "bashrety" in url:
            store_id = "bashrety"

        handle = url.rstrip('/').split('/')[-1]

        parsed_ings, actives = parse_and_normalize_ingredients(raw_ings or "")

        # Check if already in DB
        cur.execute("SELECT id, price FROM products WHERE product_url = ?;", (url,))
        existing = cur.fetchone()

        if existing:
            # Update fields if missing
            cur.execute("""
            UPDATE products
            SET brand = ?, category = ?, ingredients_raw = COALESCE(ingredients_raw, ?),
                ingredients_parsed = COALESCE(ingredients_parsed, ?),
                actives = COALESCE(actives, ?),
                image_url = COALESCE(image_url, ?)
            WHERE id = ?;
            """, (brand, category, raw_ings, json.dumps(parsed_ings), json.dumps(actives), image_url, existing["id"]))
            updated += 1
        else:
            cur.execute("""
            INSERT INTO products (
                store_id, handle, title, brand, category, price, compare_at_price,
                is_available, image_url, product_url, ingredients_raw,
                ingredients_parsed, actives, ingredient_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?);
            """, (
                store_id, handle, title, brand, category, price, price_high,
                image_url, url, raw_ings, json.dumps(parsed_ings), json.dumps(actives),
                'v10_curated' if raw_ings else None
            ))
            imported += 1

    conn.commit()
    conn.close()
    logger.info(f"Import complete: {imported} new products inserted, {updated} updated.")
    return imported

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import_v10_to_db()
