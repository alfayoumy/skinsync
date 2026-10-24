"""
Seed script for ingredient synonyms and aliases.
Populates the `ingredient_synonyms` database table with authoritative mappings.
"""

import os
import sys
import logging
from typing import List, Tuple, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection, DB_PATH

logger = logging.getLogger("skincare_pipeline.seed_synonyms")

DEFAULT_SYNONYMS: List[Tuple[str, str, str]] = [
    # Carriers / Solvents
    ('aqua', 'water', 'carrier'),
    ('eau', 'water', 'carrier'),
    ('purified water', 'water', 'carrier'),
    ('deionized water', 'water', 'carrier'),

    # Fragrances / Sensitizers
    ('parfum', 'fragrance', 'fragrance'),
    ('perfume', 'fragrance', 'fragrance'),
    ('fragrance (parfum)', 'fragrance', 'fragrance'),

    # Alcohols
    ('alcohol denat.', 'alcohol denat', 'alcohol'),
    ('alcohol denat', 'alcohol denat', 'alcohol'),
    ('denatured alcohol', 'alcohol denat', 'alcohol'),

    # Surfactants / Cleansers
    ('sodium lauryl sulfate', 'sls', 'surfactant'),
    ('sodium laureth sulfate', 'sles', 'surfactant'),

    # Oils & Emollients
    ('cocos nucifera (coconut) oil', 'coconut oil', 'oil'),
    ('cocos nucifera oil', 'coconut oil', 'oil'),
    ('coconut oil', 'coconut oil', 'oil'),
    ('theobroma cacao (cocoa) seed butter', 'cocoa butter', 'butter'),
    ('theobroma cacao seed butter', 'cocoa butter', 'butter'),
    ('cocoa butter', 'cocoa butter', 'butter'),
    ('butyrospermum parkii (shea) butter', 'shea butter', 'butter'),
    ('butyrospermum parkii butter', 'shea butter', 'butter'),
    ('shea butter', 'shea butter', 'butter'),
    ('simmondsia chinensis (jojoba) seed oil', 'jojoba oil', 'oil'),
    ('simmondsia chinensis seed oil', 'jojoba oil', 'oil'),
    ('jojoba oil', 'jojoba oil', 'oil'),
    ('tea tree oil', 'tea tree oil', 'oil'),
    ('tea tree', 'tea tree oil', 'oil'),
    ('melaleuca alternifolia', 'tea tree oil', 'oil'),
    ('melaleuca alternifolia (tea tree) leaf oil', 'tea tree oil', 'oil'),

    # Actives / Soothing
    ('aloe barbadensis', 'aloe vera', 'botanical'),
    ('aloe barbadensis leaf juice', 'aloe vera', 'botanical'),
    ('aloe vera', 'aloe vera', 'botanical'),
    ('centella asiatica', 'centella', 'botanical'),
    ('centella asiatica extract', 'centella', 'botanical'),
    ('cica', 'centella', 'botanical'),
    ('madecassoside', 'centella', 'botanical'),
    ('panthenol', 'panthenol', 'active'),
    ('d-panthenol', 'panthenol', 'active'),
    ('pro-vitamin b5', 'panthenol', 'active'),
    ('provitamin b5', 'panthenol', 'active'),
    ('vitamin b5', 'panthenol', 'active'),
    ('niacinamide', 'niacinamide', 'active'),
    ('nicotinamide', 'niacinamide', 'active'),
    ('vitamin b3', 'niacinamide', 'active'),
    ('ascorbic acid', 'l-ascorbic acid', 'active'),
    ('l-ascorbic acid', 'l-ascorbic acid', 'active'),
    ('vitamin c', 'l-ascorbic acid', 'active'),
    ('tocopherol', 'tocopherol', 'active'),
    ('vitamin e', 'tocopherol', 'active'),
    ('salicylic acid', 'salicylic acid', 'active'),
    ('bha', 'salicylic acid', 'active'),
    ('aha', 'glycolic acid', 'active'),
    ('glycolic acid', 'glycolic acid', 'active'),
    ('lactic acid', 'lactic acid', 'active'),
    ('sodium hyaluronate', 'hyaluronic acid', 'active'),
    ('hyaluronate', 'hyaluronic acid', 'active'),
    ('hyaluronic acid', 'hyaluronic acid', 'active'),
    ('retinol', 'retinol', 'active'),
    ('retinyl palmitate', 'retinyl palmitate', 'active')
]

def seed_synonyms(conn=None, db_path: str = DB_PATH, synonyms: Optional[List[Tuple[str, str, str]]] = None) -> int:
    """
    Seeds ingredient synonyms into the ingredient_synonyms table.
    Returns the number of synonyms seeded.
    """
    target_synonyms = synonyms if synonyms is not None else DEFAULT_SYNONYMS
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True

    count = 0
    try:
        cur = conn.cursor()
        for alias, canon, cat in target_synonyms:
            cur.execute("""
            INSERT INTO ingredient_synonyms (alias, canonical_name, category)
            VALUES (?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                canonical_name = excluded.canonical_name,
                category = excluded.category;
            """, (alias.lower().strip(), canon.strip(), cat.strip()))
            count += 1

        conn.commit()
        logger.info(f"Successfully seeded {count} ingredient synonyms into database.")
        return count
    except Exception as e:
        logger.error(f"Failed to seed ingredient synonyms: {e}")
        conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = seed_synonyms()
    print(f"Seeded {n} ingredient synonyms into database.")
