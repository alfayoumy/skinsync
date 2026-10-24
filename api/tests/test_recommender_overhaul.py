"""
Unit tests for the overhauled Alternative Recommender Engine:
- Active Ingredients Priority (Non-Water Formulation Jaccard)
- Elimination of Brand Bias in Title Matching and Cross-Encoder
- Texture / Vehicle Compatibility Scoring (Cleansing Balms, Cleansing Oils, Serums)
- Category Filtering Modes ('same_category', 'cross_category', specific category)
- Complete Exclusion of Hair Care / Salon Products from Facial Skincare Alternatives
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.recommender import (
    compute_non_water_jaccard,
    compute_functional_title_sim,
    extract_vehicle,
    compute_vehicle_sim,
    get_recommendations,
    WATER_SOLVENTS
)
from pipeline.classifier import classify_and_tag


class TestRecommenderOverhaul(unittest.TestCase):

    def test_water_solvents_filtered_in_non_water_jaccard(self):
        """Ubiquitous water/aqua/eau solvents must not inflate formulation similarity."""
        formula_water_only = ['water', 'aqua', 'eau', 'purified water']
        formula_different = ['niacinamide', 'zinc pca']
        sim = compute_non_water_jaccard(formula_water_only, formula_different)
        self.assertEqual(sim, 0.0)

        # Two formulations that only share water but have distinct non-water ingredients
        f1 = ['aqua', 'glycerin', 'cetyl alcohol']
        f2 = ['aqua', 'squalane', 'ceramide np']
        sim_filtered = compute_non_water_jaccard(f1, f2)
        # Neither shares non-water ingredients ('glycerin', 'cetyl alcohol' vs 'squalane', 'ceramide np')
        self.assertEqual(sim_filtered, 0.0)

    def test_functional_title_sim_neutralizes_brand_bias(self):
        """Sharing the same brand must not inflate functional title similarity."""
        t1 = "&honey Cleansing Balm Clear 90g"
        t2 = "&honey Cleansing Oil Cleansing 150ml"
        # When brands are stripped, only functional words are compared
        sim = compute_functional_title_sim(t1, t2, brand1="&honey", brand2="&honey")
        # Cleansing balm vs Cleansing oil: only 'cleansing' is common, balm vs oil differ
        self.assertLess(sim, 0.6)

    def test_vehicle_extraction_and_compatibility(self):
        """Formulation vehicles (balm, oil, serum, cream) must be accurately detected and aligned."""
        self.assertEqual(extract_vehicle("Cutie Tribe Cleansing Balm"), "cleansing_balm")
        self.assertEqual(extract_vehicle("&honey Skin Care Cleansing Oil 180ml"), "cleansing_oil")
        self.assertEqual(extract_vehicle("L'Oreal Hyaluro Expert Serum 30ml"), "serum")
        self.assertEqual(extract_vehicle("CeraVe Moisturizing Cream 340g"), "cream")

        # Cleansing balm vs Cleansing balm is an exact match (1.0)
        self.assertEqual(compute_vehicle_sim("cleansing_balm", "cleansing_balm"), 1.0)
        # Cleansing balm vs Cleansing oil has partial compatibility (0.65)
        self.assertAlmostEqual(compute_vehicle_sim("cleansing_balm", "cleansing_oil"), 0.65)
        # Exact match beats cross-vehicle
        self.assertGreater(
            compute_vehicle_sim("cleansing_balm", "cleansing_balm"),
            compute_vehicle_sim("cleansing_balm", "cleansing_oil")
        )

    def test_hair_care_products_suppressed_from_skincare(self):
        """Hair care products (Keratin, Professionnel, Kerastase, Frizz) must be suppressed from skincare."""
        hair_titles = [
            ("L'Oreal Professionnel Serie Expert Keratin Alpha Sleek Serum", "L'Oreal Professionnel"),
            ("Kerastase Discipline Keratin Mask", "KERASTASE"),
            ("Zero Frizz Keratin Serum", "Zero Frizz"),
            ("Hairburst Elixir Thickening Spray 125ml", "Hairburst"),
            ("Bless Leave-In Cream with Argan Oil", "BLESS")
        ]
        for title, brand in hair_titles:
            cat, is_skincare = classify_and_tag(title, brand=brand)
            self.assertEqual(cat, "Hair Care", f"Failed for {title}")
            self.assertFalse(is_skincare, f"{title} should not be skincare")

    def test_cleansing_balm_recommendations_prioritizes_balms_over_brand(self):
        """Cleansing balm alternatives must rank other cleansing balms over a same-brand cleansing oil."""
        res = get_recommendations("&honey Cleansing Balm Clear 90g", skin_type="normal")
        self.assertIsNotNone(res)
        results = res.get("results", [])
        self.assertGreater(len(results), 0)

        # The #1 recommended product must be a cleansing balm, NOT &honey cleansing oil
        top_rec = results[0]
        self.assertIn("balm", top_rec["title"].lower())
        self.assertNotEqual(top_rec["title"].lower(), "&honey skin care cleansing oil 180ml")

    def test_hyaluro_expert_serum_recommendations_prioritizes_hyaluronic_acid(self):
        """L'Oreal Hyaluro Expert Serum alternatives must be Hyaluronic Acid facial serums, no hair products."""
        res = get_recommendations("loreal hyaluro expert serum 30ml.", skin_type="normal")
        self.assertIsNotNone(res)
        results = res.get("results", [])
        self.assertGreater(len(results), 0)

        for r in results:
            # Must NOT contain hair care words
            self.assertNotIn("keratin", r["title"].lower())
            self.assertNotIn("professionnel", r["title"].lower())
            self.assertNotIn("hair", r["title"].lower())
            # Must be in Serum category
            self.assertEqual(r["category"], "Serum")
            # Must share hyaluronic_acid active
            self.assertIn("hyaluronic_acid", r["shared_actives"])

    def test_cross_category_recommendations_mode(self):
        """Cross-category mode allows alternatives across categories sharing target active ingredients."""
        res = get_recommendations(
            "loreal hyaluro expert serum 30ml.",
            skin_type="normal",
            category_mode="cross_category"
        )
        self.assertIsNotNone(res)
        results = res.get("results", [])
        self.assertGreater(len(results), 0)

        # Cross category should include other skincare categories that have hyaluronic_acid
        categories = {r["category"] for r in results}
        self.assertTrue(len(categories) > 0)
        for r in results:
            self.assertIn("hyaluronic_acid", r["shared_actives"])

    def test_alternatives_deduplicates_same_canonical_product(self):
        """The same product must not appear more than once in alternatives across stores."""
        res_peach = get_recommendations("Anua Peach 70% Niacin Serum 30Ml", skin_type="normal")
        self.assertIsNotNone(res_peach)
        results = res_peach.get("results", [])
        self.assertGreater(len(results), 0)

        # 1. No duplicate canonical IDs or normalized titles
        seen_canonicals = set()
        for r in results:
            cid = r.get("canonical_product_id")
            if cid:
                self.assertNotIn(cid, seen_canonicals, f"Duplicate canonical product ID {cid} found in alternatives")
                seen_canonicals.add(cid)

        # 2. Target itself or its siblings must never be recommended to itself
        res_txa = get_recommendations("Anua Niacinamide 10%+Txa 4% Serum", skin_type="normal")
        self.assertIsNotNone(res_txa)
        txa_results = res_txa.get("results", [])
        self.assertGreater(len(txa_results), 0)

        target_canon = res_txa["source"].get("canonical_product_id")
        for r in txa_results:
            if target_canon and r.get("canonical_product_id"):
                self.assertNotEqual(r["canonical_product_id"], target_canon, "Target product appeared in its own alternatives")
            self.assertNotIn("txa 4%", r["title"].lower(), "Same TXA product variant appeared in its own alternatives")


if __name__ == '__main__':
    unittest.main()
