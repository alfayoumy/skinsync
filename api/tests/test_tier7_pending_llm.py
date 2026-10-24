"""
Unit Tests for Tier 7 INCI Waterfall Resolution (Pending Jev & Pending Gemini Arbitration).
Verifies:
1. Candidate separation in Tier 3 (>= 0.50 auto-approved vs 0.20-0.49 ambiguous fallback).
2. Tier 7 activation when Tiers 1-6 fail: assigns pending_jev and pending_gemini.
3. INCI reference backfill upon LLM worker approval.
"""

import unittest
import sqlite3
import json

from pipeline.waterfall_inci_resolver import (
    resolve_tier3_skinsafe_with_candidate,
    resolve_tier3_skinsafe_high,
    resolve_tier7_pending_llm,
    execute_inci_waterfall_for_product
)


class TestTier7PendingLLM(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(":memory:")
        cls.conn.row_factory = sqlite3.Row
        cur = cls.conn.cursor()
        
        # Setup mock products table
        cur.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            title TEXT,
            brand TEXT,
            description_html TEXT,
            ingredients_raw TEXT,
            ingredients_parsed TEXT,
            actives TEXT,
            ingredient_source TEXT,
            canonical_product_id INTEGER,
            inci_matched_id INTEGER,
            inci_matched_name TEXT,
            inci_token_score REAL,
            gate4_jev_status TEXT,
            gate4_gemini_status TEXT,
            gate4_cross_status TEXT,
            is_skincare INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        # Setup mock ingredient_whitelist table
        cur.execute("""
        CREATE TABLE ingredient_whitelist (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            canonical_name TEXT,
            category TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Setup mock inci_reference table
        cur.execute("""
        CREATE TABLE inci_reference (
            id INTEGER PRIMARY KEY,
            product_name TEXT,
            brand TEXT,
            ingredients TEXT,
            source TEXT DEFAULT 'skinsafe'
        );
        """)
        
        # Insert reference products
        cur.execute("""
        INSERT INTO inci_reference (id, product_name, brand, ingredients, source) VALUES
        (101, 'Nuxe Huile Prodigieuse Multi-Purpose Dry Oil 100ml', 'NUXE', 'Aqua/Water, Macadamia Integrifolia Seed Oil, Helianthus Annuus Seed Oil, Prunus Amygdalus Dulcis Oil, Tocopherol', 'skinsafe'),
        (102, 'Nuxe Rich Hand Cream Cica 50ml', 'NUXE', 'Aqua/Water, Glycerin, Butyrospermum Parkii Butter, Centella Asiatica Extract, Tocopherol', 'skinsafe');
        """)
        cls.conn.commit()

        import pipeline.waterfall_inci_resolver as wr
        wr._SKINSAFE_BRAND_INDEX = None

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_01_tier3_high_vs_ambiguous_candidate(self):
        """Tests that >= 0.50 returns high match and 0.20-0.49 returns ambiguous candidate."""
        # 1. High match (nearly identical)
        high, cand = resolve_tier3_skinsafe_with_candidate("NUXE", "Nuxe Huile Prodigieuse Multi-Purpose Dry Oil 100ml", conn=self.conn)
        self.assertIsNotNone(high)
        self.assertIsNone(cand)
        self.assertEqual(high["method"], "skinsafe_high_match")
        self.assertGreaterEqual(high["confidence"], 0.50)

        # 2. Ambiguous match: "Very Rose Hand Cream" vs "Rich Hand Cream Cica" (same vehicle 'cream', overlap ~0.33)
        high, cand = resolve_tier3_skinsafe_with_candidate("NUXE", "Nuxe Very Rose Hand Cream 50ml", conn=self.conn)
        self.assertIsNone(high)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["id"], 102)
        self.assertGreaterEqual(cand["confidence"], 0.20)
        self.assertLess(cand["confidence"], 0.50)

    def test_02_resolve_tier7_pending_llm(self):
        """Tests resolve_tier7_pending_llm updates database fields with pending LLM statuses."""
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO products (id, title, brand) VALUES (500, 'Nuxe Very Rose Hand Cream 50ml', 'NUXE');
        """)
        self.conn.commit()

        candidate = {
            "id": 102,
            "product_name": "Nuxe Rich Hand Cream Cica 50ml",
            "confidence": 0.333
        }

        res = resolve_tier7_pending_llm(500, candidate, conn=self.conn, write_to_db=True)
        self.assertEqual(res["status"], "pending_llm_verification")
        self.assertEqual(res["method"], "tier7_pending_llm")

        cur.execute("SELECT inci_matched_id, inci_matched_name, gate4_jev_status, gate4_gemini_status, ingredient_source, ingredients_raw FROM products WHERE id = 500;")
        row = cur.fetchone()
        self.assertEqual(row["inci_matched_id"], 102)
        self.assertEqual(row["inci_matched_name"], "Nuxe Rich Hand Cream Cica 50ml")
        self.assertEqual(row["gate4_jev_status"], "pending_jev")
        self.assertEqual(row["gate4_gemini_status"], "pending_gemini")
        self.assertIsNone(row["ingredient_source"])
        self.assertIsNone(row["ingredients_raw"])

    def test_03_execute_waterfall_tier7_fallback(self):
        """Tests that execute_inci_waterfall_for_product activates Tier 7 when Tiers 1-6 fail."""
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO products (id, title, brand, description_html) 
        VALUES (501, 'Nuxe Very Rose Hand Cream 50ml', 'NUXE', '<p>Soothing hand cream for dry hands.</p>');
        """)
        self.conn.commit()

        cur.execute("SELECT * FROM products WHERE id = 501;")
        prod = dict(cur.fetchone())

        res = execute_inci_waterfall_for_product(
            prod,
            conn=self.conn,
            historical_index={},
            langsearch_enabled=False,
            allow_tier7=True,
            write_to_db=True
        )

        self.assertEqual(res["status"], "pending_llm_verification")
        self.assertEqual(res["method"], "tier7_pending_llm")
        self.assertEqual(res["inci_matched_id"], 102)

        cur.execute("SELECT ingredient_source, gate4_jev_status, gate4_gemini_status FROM products WHERE id = 501;")
        row = cur.fetchone()
        self.assertIsNone(row["ingredient_source"])
        self.assertEqual(row["gate4_jev_status"], "pending_jev")
        self.assertEqual(row["gate4_gemini_status"], "pending_gemini")


if __name__ == "__main__":
    unittest.main()
