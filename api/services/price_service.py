"""
Cross-Source Price Comparison and Deals Service.
Provides real-time price comparisons, best deals, and price arbitrage detection across stores.
"""

import sqlite3
from typing import Dict, List, Any, Optional
from pipeline.db_manager import get_connection
from pipeline.logging_config import get_logger

logger = get_logger("skincare_engine.price_service")


def get_canonical_price_comparison(canonical_id: int, conn=None) -> Optional[Dict[str, Any]]:
    """
    Returns full multi-store price comparison and store offers for a given canonical product ID.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    try:
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

        # Fetch all store listings for this canonical product
        cur.execute("""
        SELECT p.id as product_id, p.store_id, s.name as store_name, p.title as store_title,
               p.price, p.compare_at_price, p.is_available, p.product_url, p.image_url,
               p.ingredients_raw, p.ingredient_source
        FROM products p
        LEFT JOIN stores s ON s.id = p.store_id
        WHERE p.canonical_product_id = ?
        ORDER BY p.price ASC;
        """, (canonical_id,))
        listings = [dict(r) for r in cur.fetchall()]

        # Filter active available offers vs out of stock offers
        in_stock_offers = [o for o in listings if o["is_available"] and o["price"] > 0]
        active_offers = in_stock_offers if in_stock_offers else [o for o in listings if o["price"] > 0]

        best_deal = active_offers[0] if active_offers else (listings[0] if listings else None)
        prices = [o["price"] for o in active_offers]

        min_price = min(prices) if prices else canonical["min_price"]
        max_price = max(prices) if prices else canonical["max_price"]
        price_diff = round(max(0.0, max_price - min_price), 2)
        savings_pct = round((price_diff / max_price * 100.0), 1) if max_price > 0 else 0.0

        for o in listings:
            o["is_best_deal"] = (best_deal is not None and o["product_id"] == best_deal["product_id"])

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
            "offers": listings
        }
    finally:
        if close_on_exit:
            conn.close()


def get_product_price_comparison(product_id: int, conn=None) -> Optional[Dict[str, Any]]:
    """
    Given a store listing product ID, looks up its canonical entity and returns the price comparison.
    """
    close_on_exit = False
    if conn is None:
        conn = get_connection()
        close_on_exit = True

    try:
        cur = conn.cursor()
        cur.execute("SELECT canonical_product_id FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()

        if not row or not row["canonical_product_id"]:
            return None

        canonical_id = row["canonical_product_id"]
        comparison = get_canonical_price_comparison(canonical_id, conn=conn)
        if comparison:
            comparison["queried_product_id"] = product_id
        return comparison
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
        cur.execute("""
        SELECT id, canonical_title, brand, category, min_price, max_price,
               ROUND(max_price - min_price, 2) as diff,
               ROUND((max_price - min_price) / max_price * 100.0, 1) as savings_pct,
               store_count, primary_image_url
        FROM canonical_products
        WHERE store_count > 1
          AND is_skincare = 1
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
