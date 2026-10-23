import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.harvester import sync_all_stores, STORES
from pipeline.db_manager import get_connection
try:
    from pipeline.config import cfg
except ImportError:
    try:
        from repo.api.pipeline.config import cfg
    except ImportError:
        from config import cfg

logger = logging.getLogger("skincare_pipeline.daily_sync")

def run_daily_sync(max_pages_per_store: int = None):
    """
    Automated daily synchronization job.
    Fetches latest products, updates prices, and logs metrics.
    """
    if max_pages_per_store is None:
        max_pages_per_store = cfg.DEFAULT_MAX_PAGES_PER_STORE
    start_time = datetime.now()
    logger.info("==================================================")
    logger.info("Starting Daily Skincare Catalog Synchronization...")
    logger.info(f"Targeting {len(STORES)} Stores: {[s['name'] for s in STORES]}")
    logger.info("==================================================")

    results = sync_all_stores(max_pages_per_store=max_pages_per_store)

    total_fetched = sum(r["fetched"] for r in results)
    total_added = sum(r["added"] for r in results)
    total_updated = sum(r["updated"] for r in results)
    total_price_changes = sum(r["price_changes"] for r in results)
    duration = (datetime.now() - start_time).total_seconds()

    logger.info("==================================================")
    logger.info("Daily Synchronization Complete!")
    logger.info(f"Duration: {duration:.1f}s")
    logger.info(f"Total Fetched: {total_fetched} | Added: {total_added} | Updated: {total_updated} | Price Changes: {total_price_changes}")
    logger.info("==================================================")

    return {
        "status": "completed",
        "duration_seconds": round(duration, 1),
        "total_fetched": total_fetched,
        "total_added": total_added,
        "total_updated": total_updated,
        "total_price_changes": total_price_changes,
        "store_breakdown": results
    }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    import argparse
    parser = argparse.ArgumentParser(description="SkinSync Daily Multi-Store Catalog Synchronizer")
    parser.add_argument("--pages", type=int, default=cfg.DEFAULT_MAX_PAGES_PER_STORE, help=f"Max pages to crawl per store (default: {cfg.DEFAULT_MAX_PAGES_PER_STORE}, full catalog)")
    args = parser.parse_args()

    run_daily_sync(max_pages_per_store=args.pages)
