"""
Unit and Integration Tests for Cross-Source Product Linking & Price Comparison:
1. Volume normalization & entity key extraction (extract_volume_and_normalize).
2. In-memory / isolated canonical clustering and cross-store ingredient propagation.
3. Price comparison service (get_canonical_price_comparison, get_product_price_comparison, get_price_arbitrage_deals).
4. FastAPI endpoints (/products/{id}/price-comparison, /canonical-products/{id}/offers, /products/price-deals, /products/link-clusters).
"""

import os
import sys
import json
import sqlite3
import unittest
from fastapi.testclient import TestClient

# Ensure repo/api is in sys.path without repo. prefixes
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app
from pipeline.db_manager import get_connection
from pipeline.product_linker import (
    extract_volume_and_normalize,
    canonicalize_volume,
    init_linker_db,
    link_catalog_products
)
from services.price_service import (
    get_canonical_price_comparison,
    get_product_price_comparison,
    get_price_arbitrage_deals
)


class TestVolumeNormalization(unittest.TestCase):
    def test_volume_extraction_milliliters(self):
        norm_title, vol = extract_volume_and_normalize("CeraVe Moisturizing Cream 50ml", "CeraVe")
        self.assertEqual(vol, "50ml")
        self.assertIn("moisturizing", norm_title)
        self.assertIn("cream", norm_title)

    def test_volume_extraction_with_spaces_and_grams(self):
        norm_title, vol = extract_volume_and_normalize("La Roche-Posay Cicaplast Baume B5+ 100 g", "La Roche-Posay")
        self.assertEqual(vol, "100g")
        self.assertIn("cicaplast", norm_title)
        self.assertIn("baume", norm_title)
        self.assertIn("b5", norm_title)

    def test_volume_extraction_fluid_ounces(self):
        norm_title, vol = extract_volume_and_normalize("Cetaphil Gentle Skin Cleanser 16 fl oz", "Cetaphil")
        self.assertEqual(vol, "16oz")
        self.assertIn("cleanser", norm_title)
        self.assertIn("skin", norm_title)

    def test_no_volume_present(self):
        norm_title, vol = extract_volume_and_normalize("Bioderma Sensibio H2O Micellar Water", "Bioderma")
        self.assertEqual(vol, "")
        self.assertIn("sensibio", norm_title)
        self.assertIn("h2o", norm_title)
        self.assertIn("water", norm_title)

    def test_word_order_and_spelling_invariance(self):
        norm_a, vol_a = extract_volume_and_normalize("CeraVe PM Facial Moisturizing Lotion 52ml", "CeraVe")
        norm_b, vol_b = extract_volume_and_normalize("CeraVe Facial Lotion PM 52ml", "CeraVe")
        self.assertEqual(vol_a, vol_b)
        self.assertEqual(norm_a, norm_b)

    def test_drdawood_volume_formats(self):
        # Grams capitalization and abbreviations
        norm_title, vol = extract_volume_and_normalize("Cerave Moisturising Cream 177Gm", "Cerave")
        self.assertEqual(vol, "177g")
        self.assertEqual(canonicalize_volume(vol), "177ml")

        # Trailing period and milliliter capitalization
        norm_title, vol = extract_volume_and_normalize("Cerave Moisturising Lotion For Dry To Very Dry Skin 236Ml.", "Cerave")
        self.assertEqual(vol, "236ml")
        self.assertEqual(canonicalize_volume(vol), "236ml")

        # Invisible RTL unicode characters (e.g. \u200f)
        norm_title, vol = extract_volume_and_normalize("Cerave Oil Control Moisturizing Gel-Cream 52Ml\u200f", "Cerave")
        self.assertEqual(vol, "52ml")
        self.assertEqual(canonicalize_volume(vol), "52ml")

    def test_handle_volume_fallback(self):
        # When user-facing title omits size, extract from Shopify handle
        norm_title, vol = extract_volume_and_normalize(
            "CeraVe Moisturising Cream",
            "CeraVe",
            handle="cerave-moisturising-cream-177ml"
        )
        self.assertEqual(vol, "177ml")

    def test_canonicalize_volume_equivalences(self):
        # Grams and milliliters equivalence in cosmetic tubs
        self.assertEqual(canonicalize_volume("177g"), "177ml")
        self.assertEqual(canonicalize_volume("177gm"), "177ml")
        self.assertEqual(canonicalize_volume("6oz"), "177ml")
        self.assertEqual(canonicalize_volume("236g"), "236ml")
        self.assertEqual(canonicalize_volume("8oz"), "236ml")
        self.assertEqual(canonicalize_volume("340g"), "340ml")
        self.assertEqual(canonicalize_volume("12oz"), "340ml")
        self.assertEqual(canonicalize_volume("454g"), "473ml")
        self.assertEqual(canonicalize_volume("16oz"), "473ml")



class TestIsolatedClusteringAndPropagation(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()

        # Minimal schema needed for linking test
        cur.execute("""
        CREATE TABLE stores (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            domain TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'shopify'
        );
        """)
        cur.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            title TEXT NOT NULL,
            brand TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0.0,
            compare_at_price REAL DEFAULT 0.0,
            is_available INTEGER NOT NULL DEFAULT 1,
            image_url TEXT,
            product_url TEXT NOT NULL UNIQUE,
            description_html TEXT,
            ingredients_raw TEXT,
            ingredients_parsed TEXT,
            actives TEXT,
            ingredient_source TEXT,
            inci_matched_id INTEGER,
            gate4_gemini_status TEXT,
            canonical_product_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Insert stores
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('store_a', 'Store A', 'a.com');")
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('store_b', 'Store B', 'b.com');")

        # Insert 2 matching products: store_a has ingredients, store_b does not
        cur.execute("""
        INSERT INTO products (
            id, store_id, title, brand, category, price, is_available, product_url,
            ingredients_raw, ingredients_parsed, actives, ingredient_source
        ) VALUES (
            1, 'store_a', 'Vichy Mineral 89 Booster 50ml', 'Vichy', 'Serum', 650.0, 1, 'https://a.com/v89',
            'Aqua, Peg/Ppg/Polybutylene Glycol-8/5/3 Glycerin, Glycerin, Butylene Glycol, Sodium Hyaluronate',
            '["Aqua", "Glycerin", "Sodium Hyaluronate"]',
            '["Sodium Hyaluronate"]',
            'store_description'
        );
        """)
        cur.execute("""
        INSERT INTO products (
            id, store_id, title, brand, category, price, is_available, product_url,
            ingredients_raw, ingredients_parsed, actives, ingredient_source
        ) VALUES (
            2, 'store_b', 'Vichy Mineral 89 Booster 50 ml', 'Vichy', 'Serum', 580.0, 1, 'https://b.com/v89',
            NULL, NULL, NULL, NULL
        );
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_clustering_and_ingredient_propagation(self):
        # Run linking on in-memory connection
        metrics = link_catalog_products(conn=self.conn)

        self.assertEqual(metrics["canonical_entities"], 1)
        self.assertEqual(metrics["newly_created_canonical"], 1)
        self.assertEqual(metrics["multi_store_clusters"], 1)
        self.assertEqual(metrics["ingredients_propagated"], 1)

        cur = self.conn.cursor()
        # Verify both products have canonical_product_id set
        cur.execute("SELECT id, canonical_product_id, ingredients_raw, ingredient_source FROM products ORDER BY id ASC;")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[0]["canonical_product_id"])
        self.assertEqual(rows[0]["canonical_product_id"], rows[1]["canonical_product_id"])

        # Verify Store B received the propagated ingredients
        self.assertIsNotNone(rows[1]["ingredients_raw"])
        self.assertIn("Sodium Hyaluronate", rows[1]["ingredients_raw"])
        self.assertEqual(rows[1]["ingredient_source"], "cross_store_linked")

        # Verify Canonical product aggregates price correctly
        cur.execute("SELECT * FROM canonical_products WHERE id = ?;", (rows[0]["canonical_product_id"],))
        canon = dict(cur.fetchone())
        self.assertEqual(canon["min_price"], 580.0)
        self.assertEqual(canon["max_price"], 650.0)
        self.assertEqual(canon["store_count"], 2)


class TestDrDawoodMultiStoreClustering(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE stores (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            domain TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'shopify'
        );
        """)
        cur.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            handle TEXT,
            title TEXT NOT NULL,
            brand TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0.0,
            compare_at_price REAL DEFAULT 0.0,
            is_available INTEGER NOT NULL DEFAULT 1,
            image_url TEXT,
            product_url TEXT NOT NULL UNIQUE,
            description_html TEXT,
            ingredients_raw TEXT,
            ingredients_parsed TEXT,
            actives TEXT,
            ingredient_source TEXT,
            inci_matched_id INTEGER,
            gate4_gemini_status TEXT,
            canonical_product_id INTEGER,
            is_skincare INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('drdawood', 'Dr. Dawood', 'drdawood.com');")
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('feel22', 'Feel22', 'feel22.com');")
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('bloompharmacy', 'Bloom Pharmacy', 'bloompharmacy.com');")
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('sourcebeauty', 'Source Beauty', 'sourcebeauty.com');")

        # Insert CeraVe cream items with varying size notations & un-sized listings
        cur.execute("""
        INSERT INTO products (id, store_id, handle, title, brand, category, price, product_url)
        VALUES
        (101, 'drdawood', 'cerave-moisturising-cream-177gm', 'Cerave Moisturising Cream 177Gm', 'Cerave', 'Moisturizer', 550.0, 'https://drdawood.com/c177'),
        (102, 'feel22', 'cerave-moisturising-cream-177ml', 'CeraVe Moisturising Cream', 'CeraVe', 'Moisturizer', 577.0, 'https://feel22.com/c177'),
        (103, 'bloompharmacy', 'cerave-moisturising-cream', 'Cerave Moisturising Cream', 'Cerave', 'Moisturizer', 517.0, 'https://bloom.com/c'),
        (104, 'drdawood', 'cera-ve-cream-340gm', 'Cerave Moisturising Cream 340Gm', 'Cerave', 'Moisturizer', 830.0, 'https://drdawood.com/c340'),
        (105, 'drdawood', 'cera-ve-cream-454gm', 'Cerave Moisturising Cream 454Gm', 'Cerave', 'Moisturizer', 1060.0, 'https://drdawood.com/c454');
        """)

        # Insert CeraVe lotion items (4 stores all aligning to 236ml, while 473ml stays separate)
        cur.execute("""
        INSERT INTO products (id, store_id, handle, title, brand, category, price, product_url)
        VALUES
        (201, 'drdawood', 'cera-ve-moisturizing-lotion-236ml', 'Cerave Moisturising Lotion For Dry To Very Dry Skin 236Ml.', 'Cerave', 'Lotion', 640.0, 'https://drdawood.com/l236'),
        (202, 'feel22', 'cerave-daily-moisturizing-lotion', 'CeraVe Daily Moisturizing Lotion', 'CeraVe', 'Lotion', 669.0, 'https://feel22.com/lotion'),
        (203, 'bloompharmacy', 'cerave-moisturizing-lotion', 'Cerave Moisturizing Lotion', 'Cerave', 'Lotion', 601.6, 'https://bloom.com/lotion'),
        (204, 'sourcebeauty', 'cerave-daily-moisturizing-lotion', 'Cerave Daily Moisturizing Lotion', 'Cerave', 'Lotion', 640.0, 'https://sourcebeauty.com/lotion'),
        (205, 'drdawood', 'cera-ve-moisturizing-lotion-473ml', 'Cerave Moisturising Lotion For Dry To Very Dry Skin 473Ml.', 'Cerave', 'Lotion', 1030.0, 'https://drdawood.com/l473');
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_drdawood_cross_store_clustering_and_variant_separation(self):
        metrics = link_catalog_products(conn=self.conn)
        cur = self.conn.cursor()

        # 1. Verify CeraVe Cream across all sizes (177g, 340g, 454g) and stores unify into ONE product entity
        cur.execute("SELECT canonical_product_id FROM products WHERE id IN (101, 102, 103, 104, 105);")
        c_cream_ids = {r[0] for r in cur.fetchall()}
        self.assertEqual(len(c_cream_ids), 1, "All CeraVe cream size variants and store listings must share the same canonical ID")

        cur.execute("SELECT store_count, min_price, max_price FROM canonical_products WHERE id = ?;", (list(c_cream_ids)[0],))
        canon_cream = cur.fetchone()
        self.assertEqual(canon_cream["store_count"], 3)
        self.assertEqual(canon_cream["min_price"], 517.0)
        self.assertEqual(canon_cream["max_price"], 1060.0)

        # 2. Verify CeraVe Lotion across all sizes (236ml, 473ml) and stores unify into ONE product entity
        cur.execute("SELECT canonical_product_id FROM products WHERE id IN (201, 202, 203, 204, 205);")
        c_lotion_ids = {r[0] for r in cur.fetchall()}
        self.assertEqual(len(c_lotion_ids), 1, "All CeraVe lotion size variants and store listings must link together")

        cur.execute("SELECT store_count, min_price, max_price FROM canonical_products WHERE id = ?;", (list(c_lotion_ids)[0],))
        canon_lotion = cur.fetchone()
        self.assertEqual(canon_lotion["store_count"], 4)
        self.assertEqual(canon_lotion["min_price"], 601.6)
        self.assertEqual(canon_lotion["max_price"], 1030.0)

        # 3. Verify Cream and Lotion remain distinct product entities
        self.assertNotEqual(list(c_cream_ids)[0], list(c_lotion_ids)[0])



class TestPriceComparisonService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn = get_connection()
        cur = conn.cursor()
        # Pick a multi-store canonical product
        cur.execute("SELECT id FROM canonical_products WHERE store_count > 1 LIMIT 1;")
        row = cur.fetchone()
        cls.multi_store_canonical_id = row["id"] if row else None

        # Pick a product linked to this canonical product
        if cls.multi_store_canonical_id:
            cur.execute("SELECT id FROM products WHERE canonical_product_id = ? LIMIT 1;", (cls.multi_store_canonical_id,))
            p_row = cur.fetchone()
            cls.sample_product_id = p_row["id"] if p_row else None
        else:
            cls.sample_product_id = None
        conn.close()

    def test_get_canonical_price_comparison_valid(self):
        if not self.multi_store_canonical_id:
            self.skipTest("No multi-store canonical cluster in DB")

        res = get_canonical_price_comparison(self.multi_store_canonical_id)
        self.assertIsNotNone(res)
        self.assertEqual(res["canonical_product_id"], self.multi_store_canonical_id)
        self.assertGreaterEqual(res["store_count"], 2)
        self.assertGreaterEqual(res["max_price"], res["min_price"])
        self.assertIn("offers", res)
        self.assertGreaterEqual(len(res["offers"]), 2)
        self.assertIn("best_deal", res)
        self.assertTrue(any(o["is_best_deal"] for o in res["offers"]))

    def test_get_canonical_price_comparison_not_found(self):
        res = get_canonical_price_comparison(9999999)
        self.assertIsNone(res)

    def test_get_product_price_comparison_valid(self):
        if not self.sample_product_id:
            self.skipTest("No sample linked product found in DB")

        res = get_product_price_comparison(self.sample_product_id)
        self.assertIsNotNone(res)
        self.assertEqual(res["queried_product_id"], self.sample_product_id)
        self.assertEqual(res["canonical_product_id"], self.multi_store_canonical_id)
        self.assertIn("offers", res)

    def test_get_product_price_comparison_not_found(self):
        res = get_product_price_comparison(9999999)
        self.assertIsNone(res)

    def test_get_price_arbitrage_deals(self):
        deals = get_price_arbitrage_deals(limit=10, min_savings_egp=10.0)
        self.assertIsInstance(deals, list)
        if deals:
            first = deals[0]
            self.assertIn("canonical_product_id", first)
            self.assertIn("savings_egp", first)
            self.assertGreaterEqual(first["savings_egp"], 10.0)
            self.assertGreater(first["store_count"], 1)
            # Ensure descending ordering by savings
            for i in range(len(deals) - 1):
                self.assertGreaterEqual(deals[i]["savings_egp"], deals[i+1]["savings_egp"])


class TestPriceComparisonApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM canonical_products WHERE store_count > 1 LIMIT 1;")
        row = cur.fetchone()
        cls.canonical_id = row["id"] if row else 1

        cur.execute("SELECT id FROM products WHERE canonical_product_id = ? LIMIT 1;", (cls.canonical_id,))
        p_row = cur.fetchone()
        cls.product_id = p_row["id"] if p_row else 1
        conn.close()

    def test_product_price_comparison_endpoint(self):
        resp = self.client.get(f"/products/{self.product_id}/price-comparison")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["queried_product_id"], self.product_id)
        self.assertIn("min_price", data)
        self.assertIn("max_price", data)
        self.assertIn("offers", data)

    def test_product_price_comparison_404(self):
        resp = self.client.get("/products/9999999/price-comparison")
        self.assertEqual(resp.status_code, 404)

    def test_canonical_offers_endpoint(self):
        resp = self.client.get(f"/canonical-products/{self.canonical_id}/offers")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["canonical_product_id"], self.canonical_id)
        self.assertIn("offers", data)

    def test_canonical_offers_404(self):
        resp = self.client.get("/canonical-products/9999999/offers")
        self.assertEqual(resp.status_code, 404)

    def test_price_deals_endpoint(self):
        resp = self.client.get("/products/price-deals?limit=5&min_savings=25.0")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("count", data)
        self.assertIn("deals", data)
        self.assertLessEqual(data["count"], 5)
        if data["deals"]:
            for d in data["deals"]:
                self.assertGreaterEqual(d["savings_egp"], 25.0)


if __name__ == "__main__":
    unittest.main()
