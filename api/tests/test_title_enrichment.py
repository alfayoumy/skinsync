"""
Unit and Integration Tests for Product Title Enrichment Engine:
1. Brand presence detection and normalization
2. Punctuation sanitization (preserving SPF ratings)
3. URL handle reconciliation and cosmetic claim extraction
4. Curated v10 catalog lookup and generic noun prefixing
5. Database migration execution and REST API endpoints (/products/enrich-titles)
"""
from admin_test_support import authenticate

import unittest
import os
import sys
import sqlite3
from fastapi.testclient import TestClient

# Ensure repo/api is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app
from pipeline.title_enricher import (
    is_brand_in_title,
    clean_trailing_punctuation,
    reconcile_title_from_handle,
    enrich_product_title,
    run_title_enrichment,
    is_noise_token
)
from pipeline.db_manager import get_connection


class TestTitleEnrichmentEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, headers=authenticate(cls))

    # =========================================================================
    # 1. BRAND DETECTION & NORMALIZATION TESTS
    # =========================================================================
    def test_is_brand_in_title(self):
        # Exact match
        self.assertTrue(is_brand_in_title("The Ordinary", "The Ordinary Niacinamide 10%"))
        # Normalized punctuation and hyphenation
        self.assertTrue(is_brand_in_title("Bio-Oil", "Bio Oil Skincare Oil 60ml"))
        self.assertTrue(is_brand_in_title("BIO OIL", "Bio-Oil Natural Skincare Oil"))
        self.assertTrue(is_brand_in_title("L'Oreal Paris", "Loreal Paris Revitalift Serum"))
        # Brand absent
        self.assertFalse(is_brand_in_title("Cerave", "Hydrating Facial Cleanser"))
        self.assertFalse(is_brand_in_title("", "Hydrating Facial Cleanser"))
        self.assertFalse(is_brand_in_title("Nuxe", ""))

    # =========================================================================
    # 2. PUNCTUATION & SPF PRESERVATION TESTS
    # =========================================================================
    def test_clean_trailing_punctuation(self):
        # Strips trailing ellipses and punctuation
        self.assertEqual(clean_trailing_punctuation("Revitalift Serum..."), "Revitalift Serum")
        self.assertEqual(clean_trailing_punctuation("Hydrating Cream - "), "Hydrating Cream")
        self.assertEqual(clean_trailing_punctuation("Moisturizer / "), "Moisturizer")
        self.assertEqual(clean_trailing_punctuation("Gentle Cleanser, "), "Gentle Cleanser")
        self.assertEqual(clean_trailing_punctuation("Eye Gel …"), "Eye Gel")

        # Crucial safeguard: preserves SPF sun protection ratings with '+'
        self.assertEqual(clean_trailing_punctuation("SVR Sun Secure Eau Solaire SPF50+"), "SVR Sun Secure Eau Solaire SPF50+")
        self.assertEqual(clean_trailing_punctuation("Anthelios UVMune 400 SPF50+"), "Anthelios UVMune 400 SPF50+")

    # =========================================================================
    # 3. URL HANDLE RECONCILIATION & COSMETIC ATTRIBUTES TESTS
    # =========================================================================
    def test_reconcile_title_from_handle_edge_cases(self):
        # Cleanly appends missing substantive qualifiers
        t1 = "Let's Soothe & Clean"
        h1 = "lets-soothe-clean-hydrating-face-cleanser"
        self.assertEqual(reconcile_title_from_handle(t1, h1, "Raw African"), "Let's Soothe & Clean - Hydrating Face Cleanser")

        t2 = "Hyaluronic Acid Hydra"
        h2 = "hyaluronic-acid-hydra-quenching-age-defying-face-serum"
        self.assertEqual(reconcile_title_from_handle(t2, h2, "Neuth France"), "Hyaluronic Acid Hydra - Quenching Age-Defying Face Serum")

        t3 = "Mustela® Stelatopia+"
        h3 = "mustela®-stelatopia®-emollient-balm-copy"
        self.assertEqual(reconcile_title_from_handle(t3, h3, "Mustela"), "Mustela® Stelatopia+ - Emollient Balm")

    def test_reconcile_cosmetic_claims_preservation(self):
        # Preserves aluminum-free, fragrance-free, oil-free cosmetic claims
        t1 = "GENTLE DEODORANT"
        h1 = "gentle_deodorant-aluminum-free"
        self.assertEqual(reconcile_title_from_handle(t1, h1, "URIAGE"), "GENTLE DEODORANT - Aluminum-Free")

        # Preserves Vitamin C single-letter active
        t2 = "ACTI-WHITE Correcting Serum"
        h2 = "acti-white-correcting-serum-vitamin-c"
        self.assertEqual(reconcile_title_from_handle(t2, h2, "Dermactive"), "ACTI-WHITE Correcting Serum - Vitamin C")

    def test_reconcile_filters_promotional_and_volume_noise(self):
        # Promotional campaign slugs must NOT be appended
        t1 = "NUXE REVE DE MIEL Hand & Nail Cream 50 ML"
        h1 = "nuxe-reve-de-miel-hand-nail-cream-50-ml-eid"
        self.assertEqual(reconcile_title_from_handle(t1, h1, "NUXE"), t1)

        # Standalone volume/size suffixes must NOT be appended to complete titles
        t2 = "Garnier Fast Bright Vitamin C Booster Serum"
        h2 = "garnier-fast-bright-vitamin-c-booster-serum-30ml"
        self.assertEqual(reconcile_title_from_handle(t2, h2, "Garnier"), t2)

        # Off discount slugs must NOT be appended
        t3 = "Vichy Mineral 89 Bundle"
        h3 = "vichy-min-ral-89-bundle-50-off"
        self.assertEqual(reconcile_title_from_handle(t3, h3, "Vichy"), t3)

    # =========================================================================
    # 4. FULL ENRICHMENT PIPELINE TESTS
    # =========================================================================
    def test_enrich_product_title_v10_lookup(self):
        mock_lookup = {
            "https://store.example.com/p1": "The Ordinary Niacinamide 10% + Zinc 1% - 30ml"
        }
        res, changed, method = enrich_product_title(
            title="Niacinamide 10%",
            url="https://store.example.com/p1",
            v10_lookup=mock_lookup
        )
        self.assertTrue(changed)
        self.assertEqual(method, "v10_curated")
        self.assertEqual(res, "The Ordinary Niacinamide 10% + Zinc 1% - 30ml")

    def test_enrich_product_title_brand_prefixing(self):
        # Bare generic noun
        res, changed, method = enrich_product_title(
            title="Lip Oil",
            brand="Glow'D"
        )
        self.assertTrue(changed)
        self.assertIn("brand_prefixed", method)
        self.assertEqual(res, "Glow'D Lip Oil")

        # Title already has brand
        res2, changed2, method2 = enrich_product_title(
            title="Glow'D Lip Oil",
            brand="Glow'D"
        )
        self.assertFalse(changed2)
        self.assertEqual(res2, "Glow'D Lip Oil")

    # =========================================================================
    # 5. DATABASE RUNNER & REST API TESTS
    # =========================================================================
    def test_run_title_enrichment_dry_run(self):
        res = run_title_enrichment(dry_run=True)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["dry_run"])
        self.assertGreater(res["total_products"], 0)
        self.assertIn("v10_curated", res["methods_breakdown"])
        self.assertIn("handle_reconciled", res["methods_breakdown"])
        self.assertIn("brand_prefixed", res["methods_breakdown"])

    def test_enrich_titles_api_endpoint_sync_dry_run(self):
        resp = self.client.post("/products/enrich-titles", json={
            "dry_run": True,
            "background": False
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["dry_run"])
        self.assertIn("total_products", data)

    def test_enrich_titles_api_endpoint_async(self):
        resp = self.client.post("/products/enrich-titles", json={
            "dry_run": True,
            "background": True
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "accepted")
        self.assertIn("task_id", data)
        self.assertIn("Monitor progress at /tasks/", data["message"])


if __name__ == "__main__":
    unittest.main()
