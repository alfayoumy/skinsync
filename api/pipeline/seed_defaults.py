"""
Master Seed Script for default database tables.
Coordinates seeding for stores, ingredient synonyms, and clinical ingredients.
"""

import os
import sys
import logging
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection, DB_PATH
from pipeline.seed_stores import seed_stores
from pipeline.seed_synonyms import seed_synonyms
from pipeline.seed_clinical import seed_clinical_ingredients

logger = logging.getLogger("skincare_pipeline.seed_defaults")

def seed_all_defaults(conn=None, db_path: str = DB_PATH) -> Dict[str, int]:
    """
    Seeds all default tables (stores, ingredient_synonyms, clinical_ingredients).
    Returns a dictionary of counts of seeded records.
    """
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True

    try:
        stores_count = seed_stores(conn=conn, db_path=db_path)
        synonyms_count = seed_synonyms(conn=conn, db_path=db_path)
        clinical_count = seed_clinical_ingredients(conn=conn, db_path=db_path)

        result = {
            "stores": stores_count,
            "synonyms": synonyms_count,
            "clinical_ingredients": clinical_count
        }
        logger.info(f"Default seeding complete: {result}")
        return result
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = seed_all_defaults()
    print(f"Master seed complete: {res}")
