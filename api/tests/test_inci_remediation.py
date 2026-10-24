"""
Comprehensive Regression Test Suite for SkinSync INCI Remediation Plan.
Verifies:
1. Composite slash preservation (Caprylic/Capric Triglyceride, Acrylates/C10-30 Alkyl Acrylate Crosspolymer).
2. Alternate label normalization (Aqua/Water/Eau -> water).
3. Completeness validation: Marketing callouts (partial) vs full formulas vs single-ingredient oils.
4. Truncation detection (ellipsis, abrupt conjunctions).
5. Hard identity gates (SPF numbers, potency %, Day vs Night, Fragrance-Free vs Scented, vehicle & lines).
6. Elimination of loose 0.50 token Jaccard auto-approval.
7. FormulationRepository atomic publication, versioning, and inci_audit_evidence logging.
8. Preservation of canonical products and audit history during clustering.
9. Zero cluster propagation in waterfall resolution.
10. Explicit entity type resolution in similarity router.
"""

import os
import sys
import json
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.inci_tokenizer import tokenize_inci_string, InciValidationResult
from pipeline.identity_gates import validate_identity_gates
from pipeline.formulation_service import FormulationRepository, FormulationCandidate
from pipeline.db_manager import init_db
from pipeline.product_linker import link_catalog_products
from pipeline.waterfall_inci_resolver import execute_inci_waterfall_for_product
from routers.similarity import _resolve_product_for_similarity


class TestInciTokenizer(unittest.TestCase):
    def test_composite_slash_preservation(self):
        raw = "Aqua/Water/Eau, Caprylic/Capric Triglyceride, Glycerin, Acrylates/C10-30 Alkyl Acrylate Crosspolymer, PEG/PPG-17/6 Copolymer, Phenoxyethanol"
        res = tokenize_inci_string(raw)
        self.assertIn("water", res.tokens)
        self.assertIn("caprylic/capric triglyceride", res.tokens)
        self.assertIn("acrylates/c10-30 alkyl acrylate crosspolymer", res.tokens)
        self.assertTrue(res.has_composite_slashes)
        self.assertEqual(res.completeness, "full")
        self.assertFalse(res.is_truncated)

    def test_marketing_callout_detection(self):
        callout = "Key Ingredients: 10% Niacinamide, 1% Zinc PCA"
        res = tokenize_inci_string(callout)
        self.assertEqual(res.completeness, "partial")
        self.assertIn("callout_header", res.validation_flags)

    def test_single_ingredient_oil_formula(self):
        raw = "100% Organic Cold-Pressed Rose Hip Seed Oil"
        title = "The Ordinary 100% Organic Cold-Pressed Rose Hip Seed Oil"
        res = tokenize_inci_string(raw, product_title=title)
        self.assertEqual(res.completeness, "full")
        self.assertTrue(res.validation_flags.get("single_ingredient_formulation"))

    def test_truncation_detection(self):
        raw = "Water, Glycerin, Dimethicone, Cetyl Alcohol, Phenoxyethanol..."
        res = tokenize_inci_string(raw)
        self.assertTrue(res.is_truncated)
        self.assertIn("truncation_pattern", res.validation_flags)


class TestIdentityGates(unittest.TestCase):
    def test_spf_number_mismatch_rejected(self):
        passed, reason = validate_identity_gates(
            "CeraVe Hydrating Mineral Sunscreen SPF 30",
            "CeraVe Hydrating Mineral Sunscreen SPF 50"
        )
        self.assertFalse(passed)
        self.assertIn("SPF number mismatch", reason)

    def test_spf_vs_non_spf_rejected(self):
        passed, reason = validate_identity_gates(
            "La Roche-Posay Anthelios Sunscreen SPF 50",
            "La Roche-Posay Toleriane Ultra Soothing Cream"
        )
        self.assertFalse(passed)
        self.assertIn("SPF / UV protection conflict", reason)

    def test_circadian_day_vs_night_rejected(self):
        passed, reason = validate_identity_gates(
            "CeraVe AM Facial Moisturizing Lotion",
            "CeraVe PM Facial Moisturizing Lotion"
        )
        self.assertFalse(passed)
        self.assertIn("Circadian regimen conflict", reason)

    def test_fragrance_free_vs_scented_rejected(self):
        passed, reason = validate_identity_gates(
            "Neutrogena Hydro Boost Fragrance-Free Gel-Cream",
            "Neutrogena Hydro Boost Scented Water Gel"
        )
        self.assertFalse(passed)
        self.assertIn("Fragrance conflict", reason)

    def test_potency_percentage_mismatch_rejected(self):
        passed, reason = validate_identity_gates(
            "The Ordinary Niacinamide 10% + Zinc 1%",
            "The Ordinary Niacinamide 2%"
        )
        self.assertFalse(passed)
        self.assertIn("Potency mismatch", reason)

    def test_vehicle_rinse_vs_leave_on_rejected(self):
        passed, reason = validate_identity_gates(
            "CeraVe Hydrating Cleanser",
            "CeraVe Moisturizing Cream"
        )
        self.assertFalse(passed)
        self.assertIn("Vehicle conflict", reason)

    def test_dermatological_line_conflict_rejected(self):
        passed, reason = validate_identity_gates(
            "Bioderma Sebium Gel Moussant",
            "Bioderma Sensibio H2O Micellar Water"
        )
        self.assertFalse(passed)
        self.assertIn("Line conflict", reason)

    def test_loose_jaccard_rejected(self):
        # 0.50 token Jaccard must be rejected for formulation equivalence
        toks_a = ["water", "glycerin", "phenoxyethanol", "niacinamide"]
        toks_b = ["water", "glycerin", "phenoxyethanol", "salicylic acid"]
        passed, reason = validate_identity_gates("Title", "Title", tokens_a=toks_a, tokens_b=toks_b)
        self.assertFalse(passed)
        self.assertIn("Formulation token similarity too low", reason)


class TestFormulationRepository(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(conn=self.conn, seed_defaults=False)
        self.repo = FormulationRepository()

        # Insert a test canonical product
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO canonical_products (canonical_title, brand, category, normalized_key)
        VALUES ('Test Moisturizing Cream', 'TestBrand', 'Moisturizer', 'testbrand_cream');
        """)
        self.can_id = cur.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_publish_formulation_creates_audit_evidence(self):
        cand = FormulationCandidate(
            canonical_product_id=self.can_id,
            source_name="skinsafe_verified",
            raw_ingredients="Water, Glycerin, Ceramide NP, Phenoxyethanol",
            parsed_tokens=["water", "glycerin", "ceramide np", "phenoxyethanol"],
            actives=["ceramides"],
            confidence_score=0.95
        )
        success = self.repo.publish_formulation(
            candidate=cand,
            approval_reason="Verified by dermatological reference",
            conn=self.conn
        )
        self.assertTrue(success)

        # Check canonical product updated
        form = self.repo.get_canonical_formulation(self.can_id, conn=self.conn)
        self.assertEqual(form["formula_version"], 1)
        self.assertEqual(form["ingredient_source"], "skinsafe_verified")
        self.assertIsNotNone(form["formula_hash"])

        # Check audit evidence row created
        history = self.repo.get_audit_history(self.can_id, conn=self.conn)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["decision"], "approved_authoritative")
        self.assertEqual(history[0]["source_name"], "skinsafe_verified")

    def test_partial_formulation_rejected_from_authoritative(self):
        cand = FormulationCandidate(
            canonical_product_id=self.can_id,
            source_name="whitelist_description",
            raw_ingredients="Niacinamide, Zinc PCA",
            parsed_tokens=["niacinamide", "zinc pca"],
            actives=["niacinamide", "zinc"],
            confidence_score=0.60,
            completeness="partial"
        )
        success = self.repo.publish_formulation(
            candidate=cand,
            approval_reason="Marketing callout extraction",
            force=False,
            conn=self.conn
        )
        self.assertFalse(success)

        # Canonical formulation must still be empty
        form = self.repo.get_canonical_formulation(self.can_id, conn=self.conn)
        self.assertIsNone(form["ingredients_raw"])

        # But candidate evidence was recorded
        history = self.repo.get_audit_history(self.can_id, conn=self.conn)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["decision"], "candidate_evidence")
        self.assertEqual(history[0]["completeness"], "partial")


class TestCanonicalDurabilityAndWaterfall(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(conn=self.conn, seed_defaults=False)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('store1', 'Store 1', 's1.com'), ('store2', 'Store 2', 's2.com');")

        # Canonical product with historical audit evidence
        cur.execute("""
        INSERT INTO canonical_products (id, canonical_title, brand, category, normalized_key, ingredients_raw)
        VALUES (99, 'Durable Cream', 'DurableBrand', 'Moisturizer', 'durablebrand_durable_cream', 'Water, Glycerin');
        """)
        cur.execute("""
        INSERT INTO inci_audit_evidence (canonical_product_id, source_name, decision)
        VALUES (99, 'historical_audit', 'approved_authoritative');
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_canonical_product_not_deleted_during_linking(self):
        # Insert a product belonging to a different cluster
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO products (store_id, title, brand, category, price, product_url)
        VALUES ('store1', 'Other Brand Wash 200ml', 'OtherBrand', 'Cleanser', 150.0, 'https://s1.com/p1');
        """)
        self.conn.commit()

        # Run product linking
        link_catalog_products(conn=self.conn)

        # Canonical #99 and its audit evidence must STILL exist!
        cur.execute("SELECT id FROM canonical_products WHERE id = 99;")
        self.assertIsNotNone(cur.fetchone())
        cur.execute("SELECT id FROM inci_audit_evidence WHERE canonical_product_id = 99;")
        self.assertIsNotNone(cur.fetchone())

    def test_waterfall_no_cluster_propagation(self):
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO products (id, store_id, title, brand, category, price, product_url, canonical_product_id)
        VALUES (101, 'store1', 'Unique Formulation Lotion', 'BrandX', 'Lotion', 200.0, 'https://s1.com/p101', 99);
        """)
        self.conn.commit()

        prod = dict(cur.execute("SELECT * FROM products WHERE id = 101;").fetchone())
        res = execute_inci_waterfall_for_product(prod, conn=self.conn, langsearch_enabled=False, allow_tier7=False)

        # Must not have cluster_propagation as method
        if res:
            self.assertNotEqual(res.get("method"), "cluster_propagation")


class TestSimilarityExplicitResolution(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(conn=self.conn, seed_defaults=False)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('store1', 'Store 1', 's1.com');")
        # Colliding ID = 5 in both tables
        cur.execute("""
        INSERT INTO canonical_products (id, canonical_title, brand, category, normalized_key, ingredients_raw)
        VALUES (5, 'Canonical Title 5', 'BrandA', 'Moisturizer', 'branda_5', 'Water, Glycerin, Ceramide NP');
        """)
        cur.execute("""
        INSERT INTO products (id, store_id, title, brand, category, price, product_url, ingredients_raw)
        VALUES (5, 'store1', 'Retailer Listing Title 5', 'BrandB', 'Cleanser', 100.0, 'https://s1.com/p5', 'Water, Sodium Laureth Sulfate');
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_explicit_canonical_vs_product_resolution(self):
        # Explicit canonical
        res_can = _resolve_product_for_similarity(
            pid=5, payload=None, conn=self.conn, canonical_pid=5
        )
        self.assertEqual(res_can["title"], "Canonical Title 5")
        self.assertTrue(res_can["is_authoritative_canonical"])

        # Explicit product offer
        res_prod = _resolve_product_for_similarity(
            pid=5, payload=None, conn=self.conn, entity_type="product"
        )
        self.assertEqual(res_prod["title"], "Retailer Listing Title 5")
        self.assertFalse(res_prod["is_authoritative_canonical"])


if __name__ == "__main__":
    unittest.main()
