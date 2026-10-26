"""
Integration and Unit Tests for Specialized API Operations:
1. Catalog & Store Refresh (/products/refresh, /stores/{store_id}/refresh)
2. INCI Formulation Matching (/inci/match, /products/{id}/match-inci, /inci/batch-match)
3. Products Ingredients Fill (/products/{id}/fill-ingredients, /products/ingredients/batch-fill)
4. Product Formulation Similarity Scoring (/similarity/score)
5. Clinical Comedogenicity & Irritancy Scoring (/dermo/score, /products/{id}/dermo-score, /dermo/ingredients)
"""
from admin_test_support import authenticate

import unittest
from unittest.mock import patch
import os
import sys
import json
from fastapi.testclient import TestClient

# Ensure repo/api is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import app
from pipeline.db_manager import get_connection

class TestApiOperations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, headers=authenticate(cls))
        # Verify DB connection and retrieve sample product ID
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, brand, title, ingredients_raw FROM products LIMIT 1;")
        row = cur.fetchone()
        if row:
            cls.sample_product_id = row["id"]
            cls.sample_brand = row["brand"]
            cls.sample_title = row["title"]
        else:
            cls.sample_product_id = 1
            cls.sample_brand = "The Ordinary"
            cls.sample_title = "Niacinamide 10% + Zinc 1%"
        conn.close()

    # =========================================================================
    # 1. CATALOG / STORE REFRESH TESTS
    # =========================================================================
    @patch("routers.catalog._run_refresh_catalog_task")
    def test_products_refresh_background(self, worker):
        payload = {
            "store_id": "bashrety",
            "fields": ["price", "stock"],
            "max_pages": 1,
            "background": True
        }
        resp = self.client.post("/products/refresh", json=payload)
        self.assertEqual(resp.status_code, 200)
        worker.assert_called_once()
        data = resp.json()
        self.assertEqual(data["status"], "accepted")
        self.assertIn("dispatched to background worker", data["message"])

    @patch("routers.catalog._run_refresh_catalog_task")
    def test_store_refresh_endpoint(self, worker):
        resp = self.client.post("/stores/sourcebeauty/refresh", json={
            "fields": ["image"],
            "max_pages": 1,
            "background": True
        })
        self.assertEqual(resp.status_code, 200)
        worker.assert_called_once()
        data = resp.json()
        self.assertEqual(data["status"], "accepted")
        self.assertIn("sourcebeauty", data["message"])

    # =========================================================================
    # 2. INCI MATCHING TESTS
    # =========================================================================
    def test_inci_match_local(self):
        payload = {
            "brand": "Bioderma",
            "title": "Bioderma Sensibio H2O Micellar Water",
            "scoring_mode": "local",
            "allow_obf": False
        }
        resp = self.client.post("/inci/match", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("matched", data)
        self.assertEqual(data["scoring_mode"], "local")
        self.assertEqual(data["brand"], "Bioderma")

    def test_product_inci_match_by_id(self):
        resp = self.client.post(f"/products/{self.sample_product_id}/match-inci", json={
            "scoring_mode": "local",
            "allow_obf": False,
            "update_product": True
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("matched", data)
        self.assertEqual(data["product_id"], self.sample_product_id)

    def test_product_inci_match_not_found(self):
        resp = self.client.post("/products/99999999/match-inci", json={
            "scoring_mode": "local"
        })
        self.assertEqual(resp.status_code, 404)

    def test_inci_batch_match(self):
        payload = {
            "product_ids": [self.sample_product_id],
            "scoring_mode": "local",
            "allow_obf": False,
            "only_unmatched": False,
            "background": False
        }
        resp = self.client.post("/inci/batch-match", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["total_processed"], 1)

    # =========================================================================
    # 3. PRODUCTS INGREDIENTS FILL TESTS
    # =========================================================================
    def test_fill_ingredients_single(self):
        resp = self.client.post(f"/products/{self.sample_product_id}/fill-ingredients", json={
            "source": "auto",
            "overwrite": False
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data)
        self.assertIn(data["status"], ["success", "skipped", "not_found", "candidate_only"])

    def test_fill_ingredients_not_found(self):
        resp = self.client.post("/products/99999999/fill-ingredients", json={
            "source": "auto"
        })
        self.assertEqual(resp.status_code, 404)

    def test_batch_fill_ingredients(self):
        payload = {
            "product_ids": [self.sample_product_id],
            "source": "auto",
            "only_empty": False,
            "overwrite": False,
            "limit": 1,
            "background": False
        }
        resp = self.client.post("/products/ingredients/batch-fill", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["processed"], 1)

    # =========================================================================
    # 4. PRODUCT SIMILARITY SCORING TESTS
    # =========================================================================
    def test_similarity_score_with_custom_payloads(self):
        payload = {
            "product_a": {
                "title": "Hyaluronic Acid 2% + B5",
                "brand": "The Ordinary",
                "ingredients": ["Water", "Sodium Hyaluronate", "Panthenol", "Phenoxyethanol"]
            },
            "product_b": {
                "title": "Hyalu B5 Serum",
                "brand": "La Roche-Posay",
                "ingredients": ["Aqua", "Sodium Hyaluronate", "Panthenol", "Glycerin", "Phenoxyethanol"]
            },
            "skin_type": "dry",
            "include_cross_encoder": True
        }
        resp = self.client.post("/similarity/score", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("match_score", data)
        self.assertIn("formula_similarity", data)
        self.assertIn("inci_weighted_jaccard", data)
        self.assertIn("actives_overlap", data)
        self.assertIn("safeguards", data)
        self.assertTrue(data["safeguards"]["is_compatible"])
        self.assertGreater(data["match_score"], 60.0)
        self.assertGreater(data["inci_weighted_jaccard"], 0.40)
        self.assertIn("hyaluronic_acid", data["product_a"]["actives"])
        self.assertIn("vitamin_b5", data["product_a"]["actives"])

    def test_similarity_score_with_incompatible_safeguards(self):
        payload = {
            "product_a": {
                "title": "Acne Purifying Cleanser (Rinse-off)",
                "brand": "Brand A",
                "ingredients": "Sodium Lauryl Sulfate, Salicylic Acid, Aqua"
            },
            "product_b": {
                "title": "Rich Night Barrier Balm (Leave-on)",
                "brand": "Brand B",
                "ingredients": "Shea Butter, Ceramide NP, Squalane"
            },
            "skin_type": "sensitive",
            "include_cross_encoder": False
        }
        resp = self.client.post("/similarity/score", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["safeguards"]["is_compatible"])
        self.assertTrue(
            "rinse-off" in data["safeguards"]["notes"].lower() or
            "impossible" in data["safeguards"]["notes"].lower()
        )

    # =========================================================================
    # 5. COMEDOGENICITY & IRRITANCY CLINICAL SCORING TESTS
    # =========================================================================
    def test_dermo_score_endpoint(self):
        payload = {
            "ingredients": [
                "Cocos Nucifera (Coconut) Oil",
                "Sodium Lauryl Sulfate",
                "Salicylic Acid",
                "Glycerin"
            ],
            "skin_type": "acne_prone"
        }
        resp = self.client.post("/dermo/score", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("acne_risk_score", data)
        self.assertIn("irritancy_risk_score", data)
        self.assertIn("overall_risk_score", data)
        self.assertFalse(data["is_suitable_for_skin_type"])  # Coconut oil flags pore-clogging
        self.assertGreater(len(data["comedogenic_ingredients"]), 0)
        self.assertGreater(len(data["irritating_ingredients"]), 0)

    def test_product_dermo_score_by_id(self):
        resp = self.client.get(f"/products/{self.sample_product_id}/dermo-score?skin_type=sensitive")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["product_id"], self.sample_product_id)
        self.assertIn("evaluation", data)
        self.assertIn("overall_risk_score", data["evaluation"])

    def test_product_dermo_score_not_found(self):
        resp = self.client.get("/products/99999999/dermo-score")
        self.assertEqual(resp.status_code, 404)

    def test_list_and_create_clinical_ingredients(self):
        # 1. List existing clinical ingredients
        resp = self.client.get("/dermo/ingredients")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 31)

        # 2. Add or update a clinical ingredient
        new_ing = {
            "inci": "Test Botanical Active",
            "common_name": "Test Plant Extract",
            "functional_type": "botanical",
            "comedogenicity": 1,
            "irritancy": 0,
            "tags": ["antioxidant", "soothing"],
            "evidence": "Clinical safety demonstrated in human patch testing.",
            "contraindications": [],
            "notes": "Safe for all skin types."
        }
        post_resp = self.client.post("/dermo/ingredients", json=new_ing)
        self.assertEqual(post_resp.status_code, 200)
        data = post_resp.json()
        self.assertEqual(data["status"], "success")

        # 3. Verify it was added to database and can be fetched
        get_resp = self.client.get("/dermo/ingredients")
        updated_items = get_resp.json()
        names = [x["inci"] for x in updated_items]
        self.assertIn("Test Botanical Active", names)

if __name__ == "__main__":
    unittest.main()
