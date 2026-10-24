"""
Cross-Source Price Comparison and Deals Service.
Provides real-time price comparisons, best deals, and price arbitrage detection across stores.
"""

import sqlite3
from typing import Dict, List, Any, Optional
from pipeline.db_manager import get_connection
from pipeline.logging_config import get_logger

logger = get_logger("skincare_engine.price_service")


from collections import defaultdict


def get_canonical_price_comparison(canonical_id: int, conn=None) -> Optional[Dict[str, Any]]:
    """
    Returns full multi-store price comparison and store offers for a given canonical product ID.
    Enforces strict architectural separation:
    - Authoritative formulation comes exclusively from canonical_products.
    - Offers are grouped by package size for same-size price comparisons.
    - Computes normalized unit pricing (EGP / 100ml or EGP / 100g) strictly within compatible units.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_redirects'").fetchone():
            from pipeline.review_service import resolve_canonical_redirect
            canonical_id = resolve_canonical_redirect(conn, canonical_id)
        columns = {r[1] for r in conn.execute('PRAGMA table_info(canonical_products)')}
        if 'catalog_visible' in columns:
            visible = conn.execute('SELECT catalog_visible FROM canonical_products WHERE id=?',(canonical_id,)).fetchone()
            if visible and not visible[0]: return None
        cur = conn.cursor()
        cur.execute("""
        SELECT id, canonical_title, brand, category, ingredients_raw, ingredients_parsed,
               actives, ingredient_source, inci_matched_id, min_price, max_price,
               store_count, primary_image_url
        FROM canonical_products
        WHERE id = ?;
        """, (canonical_id,))
        canonical = cur.fetchone()

        if not canonical:
            return None

        canonical = dict(canonical)

        # Fetch all retailer store listings (offers) for this canonical product
        # Zero ingredient queries from products table
        cur.execute("""
        SELECT p.id as product_id, p.store_id, s.name as store_name, p.title as store_title,
               p.price, p.compare_at_price, p.is_available, p.product_url, p.image_url,
               p.size_raw, p.size_value, p.size_unit, p.normalized_size_ml, p.normalized_size_g,
               p.unit_price, p.unit_measure
        FROM products p
        LEFT JOIN stores s ON s.id = p.store_id
        WHERE p.canonical_product_id = ?
        ORDER BY p.price ASC;
        """, (canonical_id,))
        listings = [dict(r) for r in cur.fetchall()]
        has_variants = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_offers'").fetchone())
        for item in listings:
            item['offer_namespace'] = 'legacy'
            item['offer_id'] = item['product_id']
        if has_variants:
            variant_rows = [dict(r) for r in conn.execute("""SELECT o.id as offer_id,'variant' as offer_namespace,
                o.legacy_product_id as product_id,o.source_id as store_id,s.name as store_name,o.title as store_title,
                o.price,o.compare_at_price,o.is_available,o.product_url,o.image_url,o.source_product_id,o.source_variant_id,
                o.sku,o.gtin,o.pack_count,o.unit_size,o.unit_size_unit,o.total_quantity,
                CASE WHEN o.unit_size IS NULL THEN NULL WHEN o.pack_count>1 THEN printf('%d × %g %s',o.pack_count,o.unit_size,o.unit_size_unit)
                     ELSE printf('%g %s',o.unit_size,o.unit_size_unit) END as size_raw,
                o.unit_size as size_value,o.unit_size_unit as size_unit,o.normalized_size_ml,o.normalized_size_g,o.unit_price,o.unit_measure
                FROM product_offers o JOIN sources s ON s.id=o.source_id
                WHERE o.canonical_product_id=? AND s.provides_prices=1 AND (s.wave<=1 OR s.publish_enabled=1) AND o.currency='EGP'""", (canonical_id,))]
            represented = {r['product_id'] for r in variant_rows if r['product_id'] is not None}
            listings = [r for r in listings if r['product_id'] not in represented] + variant_rows
            listings.sort(key=lambda r:r['price'])
        def offer_key(item):
            return (item['offer_namespace'],item['offer_id']) if item else None


        # Filter active available offers vs out of stock offers
        in_stock_offers = [o for o in listings if o.get("is_available") and (o.get("price") or 0) > 0]
        active_offers = in_stock_offers if in_stock_offers else [o for o in listings if (o.get("price") or 0) > 0]

        best_deal = active_offers[0] if active_offers else (listings[0] if listings else None)
        prices = [o["price"] for o in active_offers]

        min_price = min(prices) if prices else canonical["min_price"]
        max_price = max(prices) if prices else canonical["max_price"]
        price_diff = round(max(0.0, max_price - min_price), 2)
        savings_pct = round((price_diff / max_price * 100.0), 1) if max_price > 0 else 0.0

        # 1. Group offers by package size
        offers_by_size = defaultdict(list)
        for o in listings:
            size_key = o.get("size_raw") or "Standard / Unspecified"
            offers_by_size[size_key].append(o)

        # 2. Compute same-size best deal and savings within each size group
        size_groups_formatted = []
        for size_key, group_offers in offers_by_size.items():
            active_grp = [o for o in group_offers if o.get("is_available") and (o.get("price") or 0) > 0]
            if not active_grp:
                active_grp = [o for o in group_offers if (o.get("price") or 0) > 0]
            grp_best = active_grp[0] if active_grp else (group_offers[0] if group_offers else None)
            grp_prices = [o["price"] for o in active_grp]
            grp_min = min(grp_prices) if grp_prices else 0.0
            grp_max = max(grp_prices) if grp_prices else 0.0
            grp_diff = round(max(0.0, grp_max - grp_min), 2)
            grp_savings = round((grp_diff / grp_max * 100.0), 1) if grp_max > 0 else 0.0

            for o in group_offers:
                o["is_best_size_deal"] = (grp_best is not None and offer_key(o) == offer_key(grp_best))

            size_groups_formatted.append({
                "size": size_key,
                "offer_count": len(group_offers),
                "min_price": grp_min,
                "max_price": grp_max,
                "price_difference_egp": grp_diff,
                "savings_percent": grp_savings,
                "best_deal": grp_best,
                "offers": group_offers
            })

        # 3. Size-aware unit pricing (EGP / 100ml for liquids, EGP / 100g for solids)
        # Compatible unit comparison only: NEVER mix volume and mass
        vol_offers = []
        wt_offers = []
        for o in active_offers:
            p = o.get("price") or 0.0
            ml = o.get("normalized_size_ml")
            g = o.get("normalized_size_g")

            if ml and ml > 0 and p > 0:
                if not o.get("unit_price") or o.get("unit_measure") != "EGP / 100ml":
                    o["unit_price"] = round(p / (ml / 100.0), 2)
                    o["unit_measure"] = "EGP / 100ml"
                vol_offers.append(o)
            elif g and g > 0 and p > 0:
                if not o.get("unit_price") or o.get("unit_measure") != "EGP / 100g":
                    o["unit_price"] = round(p / (g / 100.0), 2)
                    o["unit_measure"] = "EGP / 100g"
                wt_offers.append(o)

        best_unit_vol = min(vol_offers, key=lambda x: x["unit_price"]) if vol_offers else None
        best_unit_wt = min(wt_offers, key=lambda x: x["unit_price"]) if wt_offers else None

        for o in listings:
            o["is_best_deal"] = (best_deal is not None and offer_key(o) == offer_key(best_deal))
            is_best_unit = False
            if best_unit_vol and offer_key(o) == offer_key(best_unit_vol):
                is_best_unit = True
            if best_unit_wt and offer_key(o) == offer_key(best_unit_wt):
                is_best_unit = True
            o["is_best_unit_deal"] = is_best_unit

        return {
            "canonical_product_id": canonical["id"],
            "title": canonical["canonical_title"],
            "brand": canonical["brand"],
            "category": canonical["category"],
            "primary_image_url": canonical["primary_image_url"],
            "ingredient_source": canonical["ingredient_source"],
            "has_ingredients": bool(canonical["ingredients_raw"] and len(canonical["ingredients_raw"]) >= 15),
            "store_count": len(set(o["store_id"] for o in listings)),
            "listing_count": len(listings),
            "min_price": min_price,
            "max_price": max_price,
            "price_difference_egp": price_diff,
            "savings_percent": savings_pct,
            "best_deal": best_deal,
            "best_unit_deal_volume": best_unit_vol,
            "best_unit_deal_weight": best_unit_wt,
            "offers": listings,
            "offers_by_size": dict(offers_by_size),
            "size_groups": size_groups_formatted,
            "size_options": list(offers_by_size.keys())
        }
    finally:
        if close_on_exit:
            conn.close()


def get_product_price_comparison(product_id: int, conn=None) -> Optional[Dict[str, Any]]:
    """
    Given a store listing product ID or canonical product ID, resolves canonical entity and returns price comparison.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    try:
        cur = conn.cursor()
        # 1. Check if product_id is an offer listing in products
        cur.execute("SELECT canonical_product_id FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()

        if row and row["canonical_product_id"]:
            canonical_id = row["canonical_product_id"]
            comparison = get_canonical_price_comparison(canonical_id, conn=conn)
            if comparison:
                comparison["queried_product_id"] = product_id
            return comparison

        # 2. Check if product_id is already a canonical_products ID
        cur.execute("SELECT id FROM canonical_products WHERE id = ?;", (product_id,))
        if cur.fetchone():
            return get_canonical_price_comparison(product_id, conn=conn)

        return None
    finally:
        if close_on_exit:
            conn.close()


def get_price_arbitrage_deals(limit: int = 20, min_savings_egp: float = 20.0, conn=None) -> List[Dict[str, Any]]:
    """
    Finds products sold across multiple stores with the highest price divergence / consumer savings.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    try:
        cur = conn.cursor()
        columns = {r[1] for r in conn.execute('PRAGMA table_info(canonical_products)')}
        visibility = 'AND catalog_visible=1' if 'catalog_visible' in columns else ''
        cur.execute(f"""
        SELECT id, canonical_title, brand, category, min_price, max_price,
               ROUND(max_price - min_price, 2) as diff,
               ROUND((max_price - min_price) / max_price * 100.0, 1) as savings_pct,
               store_count, primary_image_url
        FROM canonical_products
        WHERE store_count > 1
          AND is_skincare = 1 {visibility}
          AND min_price > 0
          AND (max_price - min_price) >= ?
        ORDER BY diff DESC
        LIMIT ?;
        """, (min_savings_egp, limit))

        results = []
        for r in cur.fetchall():
            results.append({
                "canonical_product_id": r["id"],
                "title": r["canonical_title"],
                "brand": r["brand"],
                "category": r["category"],
                "min_price": r["min_price"],
                "max_price": r["max_price"],
                "savings_egp": r["diff"],
                "savings_percent": r["savings_pct"],
                "store_count": r["store_count"],
                "primary_image_url": r["primary_image_url"]
            })

        return results
    finally:
        if close_on_exit:
            conn.close()
