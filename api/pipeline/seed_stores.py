"""
Seed script for e-commerce stores and scraping checkpoints.
Populates the `stores` and `scrape_checkpoints` database tables.
"""

import os
import sys
import logging
from typing import List, Tuple, Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection, DB_PATH

logger = logging.getLogger("skincare_pipeline.seed_stores")

DEFAULT_STORES: List[Tuple[str, str, str, str]] = [
    ('sourcebeauty', 'Source Beauty', 'sourcebeauty.com', 'shopify'),
    ('feel22', 'Feel22 Egypt', 'eg.feel22.com', 'shopify'),
    ('bloompharmacy', 'Bloom Pharmacy', 'www.bloompharmacy.com', 'shopify'),
    ('drdawood', 'Dr Dawood Pharmacy', 'drdawoodstore.com', 'shopify'),
    ('bashrety', 'Bashrety', 'bashrety.tv', 'shopify'),
    ('rawafrican', 'Raw African', 'rawafrican.net', 'shopify')
]

def seed_stores(conn=None, db_path: str = DB_PATH, stores: Optional[List[Tuple[str, str, str, str]]] = None) -> int:
    """
    Seeds stores and default scrape checkpoints into the database.
    Returns the number of stores seeded.
    """
    target_stores = stores if stores is not None else DEFAULT_STORES
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True

    count = 0
    try:
        cur = conn.cursor()
        for s_id, s_name, s_dom, s_plat in target_stores:
            cur.execute("""
            INSERT INTO stores (id, name, domain, platform)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                domain = excluded.domain,
                platform = excluded.platform;
            """, (s_id, s_name, s_dom, s_plat))

            cur.execute("""
            INSERT OR IGNORE INTO scrape_checkpoints (store_id, status)
            VALUES (?, 'idle');
            """, (s_id,))
            count += 1

        conn.commit()
        logger.info(f"Successfully seeded {count} stores into database.")
        return count
    except Exception as e:
        logger.error(f"Failed to seed stores: {e}")
        conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = seed_stores()
    print(f"Seeded {n} stores into database.")
