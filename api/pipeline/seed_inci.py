import os
import glob
import logging
import pandas as pd
import sqlite3
import sys

# Ensure api directory is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection

logger = logging.getLogger("skincare_pipeline.seed_inci")

def seed_skinsafe_data(db_path=None, skinsafe_dir=None):
    if not skinsafe_dir:
        # Check parent workspace
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data", "raw", "SkinSAFE"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "raw", "SkinSAFE"),
            "/mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/data/raw/SkinSAFE",
            "/mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/SkinSAFE",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "SkinSAFE"),
        ]
        for c in candidates:
            if os.path.exists(c):
                skinsafe_dir = c
                break
    
    files = glob.glob(os.path.join(skinsafe_dir, "*.csv")) if skinsafe_dir else []
    if not files:
        logger.warning(f"No SkinSAFE files found in {skinsafe_dir}")
        return 0

    conn = get_connection(db_path) if db_path else get_connection()
    cur = conn.cursor()
    
    # Check count
    cur.execute("SELECT COUNT(*) FROM inci_reference;")
    existing = cur.fetchone()[0]
    if existing > 1000:
        logger.info(f"INCI Reference table already seeded with {existing} products. Skipping seed.")
        conn.close()
        return existing

    logger.info(f"Seeding INCI Reference database from {len(files)} SkinSAFE files...")
    total_records = 0
    records = []

    for fpath in files:
        try:
            df = pd.read_csv(fpath)
            for _, row in df.iterrows():
                brand = str(row.get('brand', '')).strip()
                name = str(row.get('product_name', '')).strip()
                ings = str(row.get('ingredients', '')).strip()
                if brand and name and ings and ings.lower() != 'nan':
                    records.append((brand, name, ings, 'skinsafe'))
                    if len(records) >= 10000:
                        cur.executemany("""
                        INSERT INTO inci_reference (brand, product_name, ingredients, source)
                        VALUES (?, ?, ?, ?);
                        """, records)
                        total_records += len(records)
                        records = []
        except Exception as e:
            logger.warning(f"Error reading {fpath}: {e}")

    if records:
        cur.executemany("""
        INSERT INTO inci_reference (brand, product_name, ingredients, source)
        VALUES (?, ?, ?, ?);
        """, records)
        total_records += len(records)

    conn.commit()
    conn.close()
    logger.info(f"Successfully seeded {total_records} INCI reference products into database.")
    return total_records

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_skinsafe_data()
