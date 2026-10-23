import urllib.request
import urllib.error
import json
import time
import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.db_manager import get_connection
from pipeline.inci_matcher import (
    extract_ingredients_from_html,
    parse_and_normalize_ingredients,
    match_with_inci_db
)
from pipeline.classifier import is_skincare_or_beauty, classify_product
try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

logger = logging.getLogger("skincare_pipeline.harvester")

STORES = [
    {
        "id": "sourcebeauty",
        "name": "Source Beauty",
        "domain": "sourcebeauty.com",
        "rate_limit_delay": 0.3
    },
    {
        "id": "feel22",
        "name": "Feel22 Egypt",
        "domain": "eg.feel22.com",
        "rate_limit_delay": 0.3
    },
    {
        "id": "bloompharmacy",
        "name": "Bloom Pharmacy",
        "domain": "www.bloompharmacy.com",
        "rate_limit_delay": 0.3
    },
    {
        "id": "drdawood",
        "name": "Dr Dawood Pharmacy",
        "domain": "drdawoodstore.com",
        "rate_limit_delay": 0.4
    },
    {
        "id": "bashrety",
        "name": "Bashrety",
        "domain": "bashrety.tv",
        "rate_limit_delay": 0.4
    },
    {
        "id": "rawafrican",
        "name": "Raw African",
        "domain": "rawafrican.net",
        "rate_limit_delay": 0.4
    }
]

def fetch_shopify_page(domain: str, page: int, retries: int = None) -> list:
    """
    Fetches a page of products from Shopify /products.json with retry logic.
    """
    if retries is None:
        retries = cfg.SHOPIFY_MAX_RETRIES
    url = f"https://{domain}/products.json?limit={cfg.SHOPIFY_PAGE_LIMIT}&page={page}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=cfg.SHOPIFY_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data.get("products", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_sec = (attempt + 1) * 3
                logger.warning(f"Rate limited by {domain}. Backing off for {wait_sec}s...")
                time.sleep(wait_sec)
            elif e.code in (404, 403):
                logger.error(f"HTTP {e.code} for {domain} page {page}: {e.reason}")
                return []
            else:
                time.sleep(1)
        except Exception as e:
            logger.warning(f"Fetch error for {domain} page {page} (attempt {attempt+1}): {e}")
            time.sleep(1.5)

    return []

def sync_store(store_cfg: dict, max_pages: int = None, conn = None, fields: list = None) -> dict:
    """
    Crawls and synchronizes a single Shopify store into the database.
    Optionally accepts a list of specific fields to refresh:
    e.g. ['price', 'stock', 'image', 'ingredients', 'title'] or 'all'.
    """
    if max_pages is None:
        max_pages = cfg.DEFAULT_MAX_PAGES_PER_STORE
    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    # Normalize fields filter
    clean_fields = None
    if fields:
        clean_fields = set()
        for f in fields:
            f_norm = str(f).lower().strip()
            if f_norm in ("all", "*"):
                clean_fields = None
                break
            if f_norm in ("price", "pricing"):
                clean_fields.add("price")
            elif f_norm in ("stock", "availability", "is_available"):
                clean_fields.add("stock")
            elif f_norm in ("image", "images", "image_url"):
                clean_fields.add("image")
            elif f_norm in ("ingredients", "ingredient", "raw_ingredients"):
                clean_fields.add("ingredients")
            elif f_norm in ("title", "category"):
                clean_fields.add("title")

    cur = conn.cursor()
    store_id = store_cfg["id"]
    domain = store_cfg["domain"]
    name = store_cfg["name"]
    delay = store_cfg.get("rate_limit_delay", cfg.SHOPIFY_DEFAULT_DELAY_SECONDS)

    logger.info(f"--- Starting Sync for Store: {name} ({domain}) | Fields: {list(clean_fields) if clean_fields else 'all'} ---")

    # Start Sync Run Log
    cur.execute("""
    INSERT INTO sync_runs (store_id, status, started_at)
    VALUES (?, 'running', CURRENT_TIMESTAMP);
    """, (store_id,))
    sync_run_id = cur.lastrowid
    conn.commit()

    total_fetched = 0
    total_added = 0
    total_updated = 0
    price_changes = 0
    error_msg = None

    try:
        for page in range(1, max_pages + 1):
            products = fetch_shopify_page(domain, page)
            if not products:
                logger.info(f"Finished crawling {name} at page {page-1} (Total products fetched: {total_fetched})")
                break

            total_fetched += len(products)

            for p in products:
                title = str(p.get("title", "")).strip()
                vendor = str(p.get("vendor", "")).strip() or "Unknown"
                prod_type = str(p.get("product_type", "")).strip()
                handle = p.get("handle", "")
                prod_id = str(p.get("id", ""))
                body_html = str(p.get("body_html", "")) or ""

                if not is_skincare_or_beauty(title, prod_type):
                    continue

                category = classify_product(title, prod_type)

                # Variant pricing
                variants = p.get("variants", [])
                if not variants:
                    continue
                v0 = variants[0]
                price = float(v0.get("price") or 0.0)
                compare_price = float(v0.get("compare_at_price") or 0.0)
                is_available = 1 if v0.get("available") else 0

                # Images
                images = p.get("images", [])
                image_url = images[0].get("src") if images else None

                # Construct clean canonical product URL
                product_url = f"https://{domain}/products/{handle}"

                # Extract ingredients from store HTML description if present
                raw_ings = extract_ingredients_from_html(body_html)
                ing_source = 'store_description' if raw_ings else None

                # Strategy A: Auto-enrich central inci_reference so other stores can reuse this formulation
                if raw_ings and len(raw_ings) >= cfg.MIN_STORE_ENRICH_LEN and vendor and vendor.lower() not in ('unknown', 'general'):
                    try:
                        cur.execute("SELECT id FROM inci_reference WHERE LOWER(brand) = LOWER(?) AND LOWER(product_name) = LOWER(?);", (vendor, title))
                        if not cur.fetchone():
                            cur.execute("INSERT INTO inci_reference (brand, product_name, ingredients, source) VALUES (?, ?, ?, 'store_verified');", (vendor, title, raw_ings))
                    except Exception:
                        pass

                parsed_ings, actives = parse_and_normalize_ingredients(raw_ings or "")

                # Check if product exists in DB
                cur.execute("SELECT id, price, ingredients_raw FROM products WHERE product_url = ?;", (product_url,))
                existing = cur.fetchone()

                if existing:
                    pid = existing["id"]
                    old_price = float(existing["price"])

                    # Detect Price Change if price is being updated
                    if clean_fields is None or "price" in clean_fields:
                        if abs(old_price - price) > cfg.PRICE_DIFFERENCE_THRESHOLD and price > 0:
                            cur.execute("""
                            INSERT INTO price_history (product_id, old_price, new_price)
                            VALUES (?, ?, ?);
                            """, (pid, old_price, price))
                            price_changes += 1

                    if clean_fields is None:
                        # Full update of all fields
                        if not existing["ingredients_raw"] and raw_ings:
                            cur.execute("""
                            UPDATE products
                            SET price = ?, compare_at_price = ?, is_available = ?, image_url = ?,
                                ingredients_raw = ?, ingredients_parsed = ?, actives = ?,
                                ingredient_source = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?;
                            """, (price, compare_price, is_available, image_url,
                                  raw_ings, json.dumps(parsed_ings), json.dumps(actives),
                                  ing_source, pid))
                        else:
                            cur.execute("""
                            UPDATE products
                            SET price = ?, compare_at_price = ?, is_available = ?, image_url = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?;
                            """, (price, compare_price, is_available, image_url, pid))
                    else:
                        # Granular field updates
                        upd_cols = []
                        upd_vals = []
                        if "price" in clean_fields:
                            upd_cols.extend(["price = ?", "compare_at_price = ?"])
                            upd_vals.extend([price, compare_price])
                        if "stock" in clean_fields:
                            upd_cols.append("is_available = ?")
                            upd_vals.append(is_available)
                        if "image" in clean_fields:
                            upd_cols.append("image_url = ?")
                            upd_vals.append(image_url)
                        if "title" in clean_fields:
                            upd_cols.extend(["title = ?", "category = ?"])
                            upd_vals.extend([title, category])
                        if "ingredients" in clean_fields and raw_ings:
                            upd_cols.extend(["ingredients_raw = ?", "ingredients_parsed = ?", "actives = ?", "ingredient_source = ?"])
                            upd_vals.extend([raw_ings, json.dumps(parsed_ings), json.dumps(actives), ing_source])

                        if upd_cols:
                            upd_cols.append("updated_at = CURRENT_TIMESTAMP")
                            upd_vals.append(pid)
                            sql = f"UPDATE products SET {', '.join(upd_cols)} WHERE id = ?;"
                            cur.execute(sql, tuple(upd_vals))

                    total_updated += 1
                else:
                    # Insert new product
                    cur.execute("""
                    INSERT INTO products (
                        store_id, store_product_id, handle, title, brand, category,
                        price, compare_at_price, is_available, image_url, product_url,
                        description_html, ingredients_raw, ingredients_parsed, actives, ingredient_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        store_id, prod_id, handle, title, vendor, category,
                        price, compare_price, is_available, image_url, product_url,
                        body_html, raw_ings, json.dumps(parsed_ings), json.dumps(actives), ing_source
                    ))
                    total_added += 1

            conn.commit()
            time.sleep(delay)

        # Update store stats
        cur.execute("SELECT COUNT(*) FROM products WHERE store_id = ?;", (store_id,))
        store_prod_cnt = cur.fetchone()[0]

        cur.execute("""
        UPDATE stores
        SET last_sync_at = CURRENT_TIMESTAMP, total_products = ?
        WHERE id = ?;
        """, (store_prod_cnt, store_id))

        # Close sync run log
        cur.execute("""
        UPDATE sync_runs
        SET status = 'success', products_fetched = ?, products_added = ?,
            products_updated = ?, price_changes = ?, completed_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """, (total_fetched, total_added, total_updated, price_changes, sync_run_id))

        conn.commit()
        logger.info(f"Sync complete for {name}: Fetched {total_fetched}, Added {total_added}, Updated {total_updated}, Price Changes: {price_changes}")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Sync failed for {name}: {e}")
        cur.execute("""
        UPDATE sync_runs
        SET status = 'failed', error_message = ?, completed_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """, (error_msg, sync_run_id))
        conn.commit()
    finally:
        if should_close:
            conn.close()

    return {
        "store": name,
        "status": "failed" if error_msg else "success",
        "fetched": total_fetched,
        "added": total_added,
        "updated": total_updated,
        "price_changes": price_changes,
        "error": error_msg
    }

def sync_all_stores(max_pages_per_store: int = None, fields: list = None) -> list:
    """
    Runs full synchronization for all configured stores.
    """
    if max_pages_per_store is None:
        max_pages_per_store = cfg.DEFAULT_MAX_PAGES_PER_STORE
    conn = get_connection()
    results = []
    for store_cfg in STORES:
        res = sync_store(store_cfg, max_pages=max_pages_per_store, conn=conn, fields=fields)
        results.append(res)
    conn.close()
    return results

def refresh_catalog(store_id: str = None, fields: list = None, max_pages: int = None) -> dict:
    """
    Refreshes catalog products across a specific store or all stores,
    selectively updating specific fields ('price', 'stock', 'image', 'ingredients', or 'all').
    """
    if max_pages is None:
        max_pages = cfg.DEFAULT_MAX_PAGES_PER_STORE

    target_stores = STORES
    if store_id and str(store_id).lower().strip() not in ("all", "*", ""):
        sid = str(store_id).lower().strip()
        target_stores = [s for s in STORES if s["id"] == sid]
        if not target_stores:
            return {
                "status": "error",
                "message": f"Store ID '{store_id}' not found. Available stores: {[s['id'] for s in STORES]}"
            }

    conn = get_connection()
    results = []
    total_fetched = 0
    total_updated = 0
    total_added = 0
    total_price_changes = 0

    for s_cfg in target_stores:
        res = sync_store(s_cfg, max_pages=max_pages, conn=conn, fields=fields)
        results.append(res)
        total_fetched += res.get("fetched", 0)
        total_updated += res.get("updated", 0)
        total_added += res.get("added", 0)
        total_price_changes += res.get("price_changes", 0)

    conn.close()
    return {
        "status": "success",
        "stores_processed": [s["name"] for s in target_stores],
        "fields_refreshed": fields or ["all"],
        "total_fetched": total_fetched,
        "total_updated": total_updated,
        "total_added": total_added,
        "price_changes": total_price_changes,
        "store_details": results
    }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing sync on Bashrety (fast test)...")
    test_cfg = next(s for s in STORES if s["id"] == "bashrety")
    res = sync_store(test_cfg, max_pages=2)
    print("Sync Result:", res)
