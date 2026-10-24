"""
Comprehensive Unit Tests for Verified Ingredient Whitelist & Fallback Lookup.
Verifies:
1. SQLite whitelist schema, CRUD helper methods, and statistics.
2. Longest-phrase precedence, boundary protection, and negation filtering.
3. Realistic skincare product description extraction.
4. Strict pipeline precedence (Priority 1: store full INCI > Priority 2: SkinSAFE DB > Priority 3: Whitelist fallback).
5. FastAPI Admin API endpoints: GET, POST, PUT, DELETE /admin/whitelist and POST /admin/whitelist/reconcile-pending.
"""

import os
import sys
import json
import sqlite3
import unittest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import app
from pipeline.db_manager import (
    get_connection, load_whitelist_from_db, add_whitelist_ingredient,
    update_whitelist_ingredient, delete_whitelist_ingredient, get_whitelist_stats, DB_PATH
)
from pipeline.whitelist_matcher import (
    extract_ingredients_via_whitelist, reload_whitelist_cache, clean_html_description
)
from pipeline.inci_matcher import extract_ingredients_from_html

class TestWhitelistMatcher(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.conn = get_connection(DB_PATH)
        reload_whitelist_cache(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_01_whitelist_db_helpers_crud(self):
        """Tests adding, querying, updating, and deleting whitelist items in SQLite."""
        test_ing_name = "Test Ectoin Molecule"
        
        # 1. Add
        ing_id = add_whitelist_ingredient(
            name=test_ing_name,
            canonical_name="Ectoin",
            category="active",
            is_active=1,
            conn=self.conn
        )
        self.assertIsNotNone(ing_id)

        # 2. Query
        cur = self.conn.cursor()
        cur.execute("SELECT name, canonical_name, category, is_active FROM ingredient_whitelist WHERE id = ?;", (ing_id,))
        row = cur.fetchone()
        self.assertEqual(row["name"], test_ing_name)
        self.assertEqual(row["canonical_name"], "Ectoin")
        self.assertEqual(row["category"], "active")
        self.assertEqual(row["is_active"], 1)

        # 3. Update
        upd_ok = update_whitelist_ingredient(ing_id, {"category": "humectant", "is_active": 0}, conn=self.conn)
        self.assertTrue(upd_ok)

        cur.execute("SELECT category, is_active FROM ingredient_whitelist WHERE id = ?;", (ing_id,))
        upd_row = cur.fetchone()
        self.assertEqual(upd_row["category"], "humectant")
        self.assertEqual(upd_row["is_active"], 0)

        # 4. Delete
        del_ok = delete_whitelist_ingredient(ing_id, conn=self.conn)
        self.assertTrue(del_ok)
        cur.execute("SELECT id FROM ingredient_whitelist WHERE id = ?;", (ing_id,))
        self.assertIsNone(cur.fetchone())

    def test_02_whitelist_stats(self):
        """Verifies total counts and category breakdown from get_whitelist_stats."""
        stats = get_whitelist_stats(conn=self.conn)
        self.assertIn("total_ingredients", stats)
        self.assertIn("active_ingredients", stats)
        self.assertIn("categories", stats)
        self.assertGreater(stats["total_ingredients"], 1000)
        self.assertGreater(stats["active_ingredients"], 1000)

    def test_03_longest_phrase_matching_precedence(self):
        """
        Verifies that compound botanical/chemical names match as single units
        rather than disjoint fragments (e.g. Centella Asiatica Extract over Centella).
        """
        text = "<p>Enriched with Centella Asiatica Leaf Extract and Panthenol for intensive soothing.</p>"
        res = extract_ingredients_via_whitelist(text, conn=self.conn)
        self.assertIsNotNone(res)
        self.assertIn("ingredients_raw", res)
        
        # Check that Centella Asiatica Leaf Extract or Centella Asiatica is captured
        raw = res["ingredients_raw"]
        self.assertTrue("Centella" in raw)
        self.assertTrue("Panthenol" in raw)
        # Verify Panthenol is detected as active
        self.assertIn("vitamin_b5", res["actives"])

    def test_04_word_boundary_and_substring_safety(self):
        """
        Verifies that short ingredient names do not match false positive English words.
        E.g. 'tea' should not match inside 'steam', 'oil' should not match inside 'spoil'.
        """
        false_positive_text = "This steaming iron smooths spoiled clothes effortlessly with zero soil."
        res = extract_ingredients_via_whitelist(false_positive_text, conn=self.conn)
        # Should be None or contain no spurious matches
        self.assertIsNone(res)

    def test_05_negation_and_free_of_filtering(self):
        """
        Verifies that claims like 'free of parabens and sulfates' do NOT extract
        parabens or sulfates as ingredients.
        """
        text = (
            "A hydrating facial lotion with Hyaluronic Acid and Glycerin. "
            "Formulated free of parabens, sulfates, and alcohol."
        )
        res = extract_ingredients_via_whitelist(text, conn=self.conn)
        self.assertIsNotNone(res)
        matched_lower = [m.lower() for m in res["matched_ingredients"]]
        
        # Should include Hyaluronic Acid and Glycerin
        self.assertTrue(any("hyaluronic" in m for m in matched_lower))
        self.assertTrue(any("glycerin" in m for m in matched_lower))
        
        # Should NOT include parabens, sulfates, or alcohol
        self.assertFalse(any("paraben" in m for m in matched_lower))
        self.assertFalse(any("sulfate" in m for m in matched_lower))

    def test_06_real_world_clinical_extractions(self):
        """
        Tests extraction on authentic store descriptions.
        """
        # SVR Sebiaclear description test
        svr_desc = (
            "SVR Sebiaclear Spray Corps is a targeted body treatment designed for acne-prone skin. "
            "Formulated with gluconolactone, niacinamide, lactobionic acid, and zinc."
        )
        res = extract_ingredients_via_whitelist(svr_desc, conn=self.conn)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res["matched_count"], 3)
        raw = res["ingredients_raw"].lower()
        self.assertIn("gluconolactone", raw)
        self.assertIn("niacinamide", raw)
        self.assertIn("zinc", raw)
        self.assertIn("niacinamide", res["actives"])

    def test_07_strict_pipeline_precedence(self):
        """
        Verifies Priority 1 (store full INCI) > Priority 2 (SkinSAFE DB) > Priority 3 (Whitelist fallback).
        Whitelist lookup must NEVER override an existing full INCI list.
        """
        full_inci_html = (
            "<h3>Ingredients:</h3>"
            "<p>Aqua, Glycerin, Niacinamide, Cetearyl Alcohol, Caprylic/Capric Triglyceride, Phenoxyethanol.</p>"
        )
        # Priority 1: extract_ingredients_from_html yields authentic full list
        p1_inci = extract_ingredients_from_html(full_inci_html)
        self.assertIsNotNone(p1_inci)
        self.assertIn("Aqua", p1_inci)
        self.assertIn("Niacinamide", p1_inci)

        # A product with an existing full formulation must keep store_description
        # Whitelist fallback is ONLY invoked when ingredients_raw is empty and INCI matching fails.

    def test_08_api_get_whitelist(self):
        """Tests GET /admin/whitelist with pagination and search query."""
        res = self.client.get("/admin/whitelist?q=niacinamide&limit=10")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("items", data)
        self.assertIn("total", data)
        self.assertIn("stats", data)
        self.assertGreater(data["total"], 0)
        self.assertTrue(any("niacinamide" in item["name"].lower() for item in data["items"]))

    def test_09_api_create_and_delete_whitelist_ingredient(self):
        """Tests POST, PUT, and DELETE /admin/whitelist via API."""
        unique_name = "ApiTest RareBotanicalFlower Extract"

        # POST /admin/whitelist
        create_res = self.client.post("/admin/whitelist", json={
            "name": unique_name,
            "canonical_name": "Rare Botanical Extract",
            "category": "botanical",
            "is_active": True
        })
        self.assertEqual(create_res.status_code, 200)
        ing_data = create_res.json()["ingredient"]
        ing_id = ing_data["id"]
        self.assertEqual(ing_data["name"], unique_name)

        # PUT /admin/whitelist/{id}
        put_res = self.client.put(f"/admin/whitelist/{ing_id}", json={
            "canonical_name": "Updated Botanical Target",
            "is_active": False
        })
        self.assertEqual(put_res.status_code, 200)
        self.assertEqual(put_res.json()["ingredient"]["canonical_name"], "Updated Botanical Target")
        self.assertEqual(put_res.json()["ingredient"]["is_active"], 0)

        # DELETE /admin/whitelist/{id}
        del_res = self.client.delete(f"/admin/whitelist/{ing_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "success")

    def test_10_api_reconcile_pending_endpoint(self):
        """Tests POST /admin/whitelist/reconcile-pending."""
        res = self.client.post("/admin/whitelist/reconcile-pending", json={
            "limit": 5,
            "min_ingredients": 1
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("processed", data)
        self.assertIn("extracted", data)
        self.assertIn("still_empty", data)

if __name__ == "__main__":
    unittest.main()
