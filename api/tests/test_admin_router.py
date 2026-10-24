"""
Unit and Integration Tests for Admin & Telemetry Observatory Router:
1. System overview vitals and store counts (/admin/overview).
2. Filtered catalog listing with pagination (/admin/products).
3. In-depth product forensics and audit trail (/admin/products/{id}/telemetry).
"""
from admin_test_support import authenticate

import os
import sys
import unittest
from fastapi.testclient import TestClient

# Ensure repo/api is in sys.path without repo. prefixes
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app
from pipeline.db_manager import get_connection


class TestAdminObservatory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, headers=authenticate(cls))
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE canonical_product_id IS NOT NULL LIMIT 1;")
        row = cur.fetchone()
        cls.sample_product_id = row["id"] if row else 1
        conn.close()

    def test_admin_overview_endpoint(self):
        resp = self.client.get("/admin/overview")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("catalog_vitals", data)
        self.assertIn("stores", data)
        self.assertIn("recent_tasks", data)

        vitals = data["catalog_vitals"]
        self.assertGreater(vitals["total_store_listings"], 0)
        self.assertGreater(vitals["canonical_entities"], 0)
        self.assertIn("coverage_percent", vitals)

    def test_list_admin_products_pagination(self):
        resp = self.client.get("/admin/products?limit=10&offset=0")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total", data)
        self.assertIn("products", data)
        self.assertEqual(data["limit"], 10)
        self.assertLessEqual(len(data["products"]), 10)

    def test_list_admin_products_filter_search(self):
        resp = self.client.get("/admin/products?q=CeraVe&limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(data["total"], 0)
        for p in data["products"]:
            # Either title, brand, or url should match
            has_match = ("cerave" in p["title"].lower() or "cerave" in p["brand"].lower() or "cerave" in (p["product_url"] or "").lower())
            self.assertTrue(has_match)

    def test_product_telemetry_forensics(self):
        resp = self.client.get(f"/admin/products/{self.sample_product_id}/telemetry")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("product", data)
        self.assertIn("ingredients_forensics", data)
        self.assertIn("inci_matching_telemetry", data)
        self.assertIn("cross_store_linking", data)
        self.assertIn("similar_formulation_matches", data)

        self.assertEqual(data["product"]["id"], self.sample_product_id)
        self.assertIsInstance(data["similar_formulation_matches"], list)

    def test_product_telemetry_404(self):
        resp = self.client.get("/admin/products/9999999/telemetry")
        self.assertEqual(resp.status_code, 404)

    def test_get_all_apis_status_endpoint(self):
        resp = self.client.get("/admin/apis/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("summary", data)
        self.assertIn("gemini", data)
        self.assertIn("jev", data)
        self.assertIn("langsearch", data)

        # Gemini
        gemini = data["gemini"]
        self.assertIn("model", gemini)
        self.assertIn("limits", gemini)
        self.assertIn("queue_metrics", gemini)

        # Jev
        jev = data["jev"]
        self.assertIn("model", jev)
        self.assertIn("limits", jev)
        self.assertIn("queue_metrics", jev)

        # LangSearch
        ls = data["langsearch"]
        self.assertIn("is_daily_exhausted", ls)
        self.assertIn("tokens_used_today", ls)
        self.assertIn("daily_token_limit", ls)
        self.assertIn("resets_in_human", ls)
        self.assertIn("limits", ls)

    def test_langsearch_reset_quota_endpoint(self):
        # Reset quota
        resp = self.client.post("/admin/langsearch/reset-quota")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["tokens_used_today"], 0)
        self.assertFalse(data["is_daily_exhausted"])

        # Re-exhaust quota for test realism
        resp2 = self.client.post(
            "/admin/langsearch/set-quota-exhausted",
            json={"tokens_used": 10000000, "reason": "Test Exhaustion"}
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["is_daily_exhausted"])


if __name__ == "__main__":
    unittest.main()
