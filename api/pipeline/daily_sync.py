import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.harvester import sync_all_stores, STORES
from pipeline.db_manager import get_connection
from pipeline.task_tracker import (
    create_task, complete_task, fail_task, log_task_event, update_task_progress
)
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

from pipeline.logging_config import setup_logging, get_logger
logger = get_logger("skincare_pipeline.daily_sync")

def run_daily_sync(max_pages_per_store: int = None, task_id: str = None):
    """
    Automated daily synchronization job.
    Fetches latest products, updates prices, logs metrics, and tracks task lifecycle in control tables.
    """
    if max_pages_per_store is None:
        max_pages_per_store = cfg.DEFAULT_MAX_PAGES_PER_STORE

    own_task = False
    if task_id is None:
        task_id = create_task(
            task_type="daily_sync",
            target_source="all",
            params={"max_pages": max_pages_per_store, "store_count": len(STORES)},
            auto_start=True
        )
        own_task = True

    start_time = datetime.now()
    logger.info("==================================================")
    logger.info("Starting Daily Skincare Catalog Synchronization...")
    logger.info(f"Targeting {len(STORES)} Stores: {[s['name'] for s in STORES]}")
    logger.info("==================================================")
    log_task_event(task_id, f"Starting Daily Sync for {len(STORES)} stores: {[s['name'] for s in STORES]}", level="INFO")

    try:
        results = sync_all_stores(max_pages_per_store=max_pages_per_store, task_id=task_id)

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

        if own_task:
            complete_task(
                task_id=task_id,
                metadata={
                    "total_fetched": total_fetched,
                    "total_added": total_added,
                    "total_updated": total_updated,
                    "total_price_changes": total_price_changes,
                    "duration_seconds": round(duration, 1)
                }
            )

        return {
            "status": "completed",
            "task_id": task_id,
            "duration_seconds": round(duration, 1),
            "total_fetched": total_fetched,
            "total_added": total_added,
            "total_updated": total_updated,
            "total_price_changes": total_price_changes,
            "store_breakdown": results
        }
    except Exception as e:
        if own_task:
            fail_task(task_id, str(e))
        raise

if __name__ == "__main__":
    setup_logging()
    import argparse
    parser = argparse.ArgumentParser(description="SkinSync Daily Multi-Store Catalog Synchronizer")
    parser.add_argument("--pages", type=int, default=cfg.DEFAULT_MAX_PAGES_PER_STORE, help=f"Max pages to crawl per store (default: {cfg.DEFAULT_MAX_PAGES_PER_STORE}, full catalog)")
    args = parser.parse_args()

    run_daily_sync(max_pages_per_store=args.pages)
