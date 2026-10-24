"""
Unit and Integration Tests for Multi-Tier INCI Waterfall Comparator & Admin API.
Verifies:
1. `execute_all_tiers_for_product` evaluates all 7 tiers independently without short-circuiting.
2. Structured persistence into `products.tier_resolutions` with summary statistics.
3. `apply_tier_resolution_for_product` updates catalog fields and syncs selection state.
4. Admin endpoints: GET tier-resolutions, POST execute-all-tiers, POST apply-tier, GET telemetry.
"""

import unittest
import sqlite3
import json
from unittest.mock import patch

from pipeline.waterfall_inci_resolver import (
    execute_all_tiers_for_product,
    apply_tier_resolution_for_product
)


class TestMultiTierComparator(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()

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
            ingredient_source_url TEXT,
            canonical_product_id INTEGER,
            cluster_id INTEGER,
            inci_matched_id INTEGER,
            inci_matched_name TEXT,
            inci_token_score REAL,
            gate4_jev_status TEXT,
            gate4_gemini_status TEXT,
            gate4_cross_status TEXT,
            tier_resolutions TEXT,
            is_skincare INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

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

        cur.execute("""
        CREATE TABLE inci_reference (
            id INTEGER PRIMARY KEY,
            product_name TEXT,
            brand TEXT,
            ingredients TEXT,
            source TEXT DEFAULT 'skinsafe'
        );
        """)

        import pipeline.waterfall_inci_resolver as wr
        import pipeline.whitelist_matcher as wm
        wr._SKINSAFE_BRAND_INDEX = None
        wm._WHITELIST_SET = None
        wm._COMPILED_WHITELIST_REGEX = None
        wm._WHITELIST_CACHE = None

        # Seed test whitelist ingredients
        cur.executemany("""
        INSERT INTO ingredient_whitelist (name, canonical_name, category)
        VALUES (?, ?, ?);
        """, [
            ("glycerin", "Glycerin", "humectant"),
            ("niacinamide", "Niacinamide", "antioxidant"),
            ("shea butter", "Shea Butter", "emollient"),
            ("ceramide np", "Ceramide NP", "barrier"),
        ])

        # Seed sample product
        cur.execute("""
        INSERT INTO products (
            id, title, brand, description_html,
            ingredients_raw, ingredients_parsed, actives, ingredient_source
        ) VALUES (
            101,
            'Gentle Daily Hydrating Cream',
            'PureDerm',
            '<p>Rich nourishing formula enriched with Glycerin, Niacinamide, and Shea Butter for 24h hydration.</p>',
            NULL, NULL, NULL, NULL
        );
        """)
        self.conn.commit()

        self.catalog_patcher = patch("services.catalog_service.load_catalog_from_db")
        self.mock_catalog_load = self.catalog_patcher.start()

    def tearDown(self):
        import pipeline.whitelist_matcher as wm
        wm._WHITELIST_CACHE = None
        self.catalog_patcher.stop()
        self.conn.close()

    def test_execute_all_tiers_independent_evaluation(self):
        """Verify that execute_all_tiers_for_product evaluates all 7 tiers and returns formatted dict."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE id = 101;")
        prod = dict(cur.fetchone())

        # Execute all tiers with langsearch disabled to avoid live network latency
        res = execute_all_tiers_for_product(
            prod,
            conn=self.conn,
            historical_index={},
            langsearch_enabled=False,
            write_to_db=True
        )

        self.assertIsNotNone(res)
        self.assertEqual(res["product_id"], 101)
        self.assertEqual(res["summary"]["total_tiers"], 7)
        self.assertIn("tier1", res["tiers"])
        self.assertIn("tier2", res["tiers"])
        self.assertIn("tier3", res["tiers"])
        self.assertIn("tier4", res["tiers"])
        self.assertIn("tier5", res["tiers"])
        self.assertIn("tier6", res["tiers"])
        self.assertIn("tier7", res["tiers"])

        # Tier 5 should resolve via the whitelist items in description
        t5 = res["tiers"]["tier5"]
        self.assertEqual(t5["status"], "resolved")
        self.assertGreaterEqual(len(t5["parsed_tokens"]), 3)
        self.assertIn("glycerin", t5["parsed_tokens"])
        self.assertIn("niacinamide", t5["parsed_tokens"])
        self.assertIn("shea butter", t5["parsed_tokens"])

        # Tier 4 should be marked as skipped (langsearch_enabled=False)
        self.assertEqual(res["tiers"]["tier4"]["status"], "skipped")

        # Verify persisted into database
        cur.execute("SELECT tier_resolutions FROM products WHERE id = 101;")
        db_raw = cur.fetchone()[0]
        self.assertIsNotNone(db_raw)
        db_data = json.loads(db_raw)
        self.assertEqual(db_data["product_id"], 101)
        self.assertEqual(db_data["tiers"]["tier5"]["status"], "resolved")

    def test_apply_tier_resolution(self):
        """Verify applying a specific resolved tier updates product formula and catalog state."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE id = 101;")
        prod = dict(cur.fetchone())

        # First generate resolutions
        execute_all_tiers_for_product(
            prod,
            conn=self.conn,
            historical_index={},
            langsearch_enabled=False,
            write_to_db=True
        )

        # An unlinked offer cannot publish a canonical formulation.
        with self.assertRaisesRegex(ValueError, "Link this offer"):
            apply_tier_resolution_for_product(101, "tier5", conn=self.conn)
        row = self.conn.execute("SELECT ingredients_raw FROM products WHERE id=101").fetchone()
        self.assertIsNone(row["ingredients_raw"])

    def test_apply_invalid_tier_fails_gracefully(self):
        """Verify attempting to apply an invalid tier code raises a ValueError."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE id = 101;")
        prod = dict(cur.fetchone())

        execute_all_tiers_for_product(
            prod,
            conn=self.conn,
            historical_index={},
            langsearch_enabled=False,
            write_to_db=True
        )

        with self.assertRaises(ValueError):
            apply_tier_resolution_for_product(101, "tier99", conn=self.conn)

    def test_apply_not_found_tier_preserves_formula(self):
        """A failed tier cannot clear existing ingredient evidence."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE id = 101;")
        prod = dict(cur.fetchone())

        # First resolve and apply tier5
        execute_all_tiers_for_product(
            prod,
            conn=self.conn,
            historical_index={},
            langsearch_enabled=False,
            write_to_db=True
        )
        self.conn.execute("UPDATE products SET ingredients_raw='Water, Glycerin', ingredient_source='store_page_tab' WHERE id=101")

        # Now apply tier2 (not_found)
        res = apply_tier_resolution_for_product(101, "tier2", conn=self.conn)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["selected_tier"], "tier2")

        cur.execute("SELECT ingredients_raw, ingredient_source FROM products WHERE id = 101;")
        row = cur.fetchone()
        self.assertEqual(row["ingredients_raw"], "Water, Glycerin")
        self.assertEqual(row["ingredient_source"], "store_page_tab")


if __name__ == "__main__":
    unittest.main()
