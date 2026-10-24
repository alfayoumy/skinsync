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
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.inci_tokenizer import tokenize_inci_string, InciValidationResult
from pipeline.identity_gates import validate_identity_gates
from pipeline.formulation_service import FormulationRepository, FormulationCandidate
from pipeline.db_manager import init_db
from pipeline.product_linker import link_catalog_products
from pipeline.waterfall_inci_resolver import (
    execute_inci_waterfall_for_product,
    run_waterfall_pipeline,
    apply_tier_resolution_for_product
)
from routers.similarity import _resolve_product_for_similarity
from fastapi import HTTPException


class TestSourceVariantIsolation(unittest.TestCase):
    def test_openbeauty_rejects_different_spf(self):
        import pipeline.waterfall_inci_resolver as resolver
        with patch.dict(resolver._OPENBEAUTY_CACHE, {}, clear=True), patch.object(
            resolver, "query_open_beauty_facts", return_value={
                "product_name": "Brand Sunscreen SPF 30",
                "ingredients": "Water, Glycerin, Zinc Oxide, Dimethicone, Phenoxyethanol"}):
            self.assertIsNone(resolver.resolve_tier2_openbeauty("Brand", "Brand Sunscreen SPF 50"))


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

    def test_sanitization_preserves_truncation_evidence(self):
        from pipeline.waterfall_inci_resolver import sanitize_inci_list
        raw = "Water, Glycerin, Panthenol, Niacinamide, Phenoxyethanol, ..."
        result = tokenize_inci_string(sanitize_inci_list(raw))
        self.assertTrue(result.is_truncated)
        self.assertEqual(result.completeness, "partial")

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
    def test_ambiguous_collision_raises_400(self):
        # ID 5 exists in both canonical_products and products
        with self.assertRaises(HTTPException) as ctx:
            _resolve_product_for_similarity(pid=5, payload=None, conn=self.conn, canonical_pid=None, entity_type=None)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Ambiguous identifier '5'", ctx.exception.detail)


class TestBulkWaterfallCanonicalArchitecture(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(conn=self.conn, seed_defaults=False)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('store1', 'Store 1', 's1.com');")
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('store2', 'Store 2', 's2.com');")

        # Create canonical product with NULL ingredients
        cur.execute("""
        INSERT INTO canonical_products (id, canonical_title, brand, category, normalized_key, is_skincare, ingredients_raw)
        VALUES (200, 'Hydrating B5 Serum', 'BrandAlpha', 'Serum', 'brandalpha_b5_serum', 1, NULL);
        """)

        # Create two linked retailer offers with NULL ingredients
        cur.execute("""
        INSERT INTO products (id, store_id, title, brand, category, price, product_url, description_html, canonical_product_id, is_skincare, ingredients_raw)
        VALUES (201, 'store1', 'BrandAlpha Hydrating B5 Serum 30ml', 'BrandAlpha', 'Serum', 350.0, 'https://s1.com/b5',
                '<div><p>Ingredients:</p><p>Water, Glycerin, Panthenol, Sodium Hyaluronate, Phenoxyethanol.</p></div>', 200, 1, NULL);
        """)
        cur.execute("""
        INSERT INTO products (id, store_id, title, brand, category, price, product_url, description_html, canonical_product_id, is_skincare, ingredients_raw)
        VALUES (202, 'store2', 'BrandAlpha Hydrating B5 Serum 50ml', 'BrandAlpha', 'Serum', 550.0, 'https://s2.com/b5',
                '<div>Great serum for hydration!</div>', 200, 1, NULL);
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_bulk_waterfall_executes_and_publishes_per_canonical_id_without_mutating_offers(self):
        # Run bulk waterfall pipeline over the database
        stats = run_waterfall_pipeline(
            conn=self.conn,
            limit=10,
            revalidate_all=True,
            langsearch_enabled=False,
            queue_jev=False,
            queue_gemini=False,
            dry_run=False,
            max_workers=1
        )

        cur = self.conn.cursor()

        # 1. Authoritative canonical product MUST have the published formulation
        cur.execute("SELECT ingredients_raw, ingredients_parsed, actives, ingredient_source, formula_version, formula_hash FROM canonical_products WHERE id = 200;")
        can_row = cur.fetchone()
        self.assertIsNotNone(can_row["ingredients_raw"])
        self.assertIn("Glycerin", can_row["ingredients_raw"])
        self.assertIn("Panthenol", can_row["ingredients_raw"])
        self.assertEqual(can_row["formula_version"], 1)
        self.assertIsNotNone(can_row["formula_hash"])

        # 2. Audit evidence table MUST have an approved_authoritative record for canonical product 200
        cur.execute("SELECT decision, source_name, completeness FROM inci_audit_evidence WHERE canonical_product_id = 200;")
        ev_rows = cur.fetchall()
        self.assertTrue(any(r["decision"] == "approved_authoritative" for r in ev_rows))

        # 3. CRITICAL INVARIANT: Linked retailer offers (#201, #202) must NEVER have their ingredients mutated!
        cur.execute("SELECT id, ingredients_raw FROM products WHERE canonical_product_id = 200;")
        offer_rows = cur.fetchall()
        for off in offer_rows:
            self.assertIsNone(off["ingredients_raw"], f"Offer product #{off['id']} ingredients_raw was mutated by bulk waterfall!")

    def test_single_product_rejection_is_reported_as_candidate_only(self):
        product = dict(self.conn.execute("SELECT * FROM products WHERE id=201").fetchone())
        with patch("pipeline.waterfall_inci_resolver.resolve_tier1_store_tab", return_value={
                "ingredients_raw": "Water, Glycerin, Panthenol, Niacinamide, Phenoxyethanol, ...", "method": "store_page_tab"}):
            result = execute_inci_waterfall_for_product(product, conn=self.conn, historical_index={},
                langsearch_enabled=False, allow_tier7=False, write_to_db=True)
        self.assertEqual(result["status"], "candidate_only")
        self.assertFalse(result["published"])
        self.assertIsNone(self.conn.execute("SELECT ingredients_raw FROM canonical_products WHERE id=200").fetchone()[0])

    def test_approved_jev_sqlite_row_publishes_once_without_offer_write(self):
        from pipeline.jev_worker import run_jev_verification_queue
        from pipeline.config import cfg
        raw = "Water, Glycerin, Panthenol, Sodium Hyaluronate, Phenoxyethanol"
        self.conn.execute("INSERT INTO inci_reference (id, brand, product_name, ingredients) VALUES (10, 'BrandAlpha', 'Hydrating B5 Serum', ?)", (raw,))
        self.conn.execute("UPDATE products SET inci_matched_id=10, inci_matched_name='Hydrating B5 Serum', gate4_jev_status='pending_jev' WHERE id=201")
        self.conn.commit()
        connection = MagicMock(wraps=self.conn)
        connection.close = MagicMock()
        with patch("pipeline.jev_worker.get_connection", return_value=connection), \
             patch("pipeline.jev_worker.verify_match_with_jev", return_value=True), \
             patch("pipeline.jev_worker.score_cross_encoder", return_value=0.95), \
             patch("pipeline.jev_worker.cfg", replace(cfg, JEV_REQUEST_INTERVAL_SECONDS=0)):
            result = run_jev_verification_queue(limit=1)
        self.assertEqual(result["approved"], 1)
        self.assertEqual(self.conn.execute("SELECT ingredients_raw FROM canonical_products WHERE id=200").fetchone()[0], raw)
        self.assertIsNone(self.conn.execute("SELECT ingredients_raw FROM products WHERE id=201").fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inci_audit_evidence WHERE canonical_product_id=200").fetchone()[0], 1)

    def test_threaded_bulk_reads_the_supplied_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "catalog.db")
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            self.conn.backup(conn)
            try:
                result = run_waterfall_pipeline(conn=conn, revalidate_all=True,
                    langsearch_enabled=False, queue_jev=False, queue_gemini=False, max_workers=2)
                self.assertEqual(result["resolved"], 1)
                self.assertIsNotNone(conn.execute("SELECT formula_hash FROM canonical_products WHERE id=200").fetchone()[0])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM inci_audit_evidence WHERE canonical_product_id=200").fetchone()[0], 1)
            finally:
                conn.close()

    def test_dry_run_has_no_writes(self):
        before = self.conn.total_changes
        result = run_waterfall_pipeline(conn=self.conn, revalidate_all=True,
            langsearch_enabled=False, queue_jev=False, queue_gemini=False,
            dry_run=True, max_workers=2)
        self.assertEqual(result["resolved"], 1)
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inci_audit_evidence").fetchone()[0], 0)



class TestFormulationProtectionAndIntegrity(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(conn=self.conn, seed_defaults=False)
        self.repo = FormulationRepository()

        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO canonical_products (id, canonical_title, brand, category, normalized_key, is_skincare, ingredients_raw)
        VALUES (300, 'Original Authoritative Cream', 'CeraBrand', 'Cream', 'cerabrand_orig', 1, NULL);
        """)
        self.conn.commit()

        # Publish initial authoritative formulation v1
        cand_v1 = FormulationCandidate(
            canonical_product_id=300,
            source_name="curated_v10",
            raw_ingredients="Water, Glycerin, Ceramide NP, Ceramide AP, Phytosphingosine, Cholesterol",
            parsed_tokens=["water", "glycerin", "ceramide np", "ceramide ap", "phytosphingosine", "cholesterol"],
            actives=["ceramides"],
            confidence_score=0.98,
            completeness="full"
        )
        self.repo.publish_formulation(cand_v1, approval_reason="Initial authoritative formulation", conn=self.conn)

    def tearDown(self):
        self.conn.close()

    def test_block_unauthorized_replacement_of_existing_authoritative_formula(self):
        # A conflicting candidate from another source with DIFFERENT formula arrives
        cand_conflict = FormulationCandidate(
            canonical_product_id=300,
            source_name="openbeauty_api",
            raw_ingredients="Water, Alcohol Denat, Fragrance, Salicylic Acid, Glycerin",
            parsed_tokens=["water", "alcohol denat", "fragrance", "salicylic acid", "glycerin"],
            actives=["salicylic_acid"],
            confidence_score=0.90,
            completeness="full"
        )

        # Attempt publication with force=False
        success = self.repo.publish_formulation(
            candidate=cand_conflict,
            approval_reason="Conflicting candidate match",
            force=False,
            conn=self.conn
        )
        self.assertFalse(success, "Conflicting candidate incorrectly overwrote existing authoritative formulation without force=True!")

        cur = self.conn.cursor()
        cur.execute("SELECT ingredients_raw, formula_version FROM canonical_products WHERE id = 300;")
        row = cur.fetchone()
        self.assertEqual(row["formula_version"], 1)
        self.assertIn("Ceramide NP", row["ingredients_raw"])
        self.assertNotIn("Alcohol Denat", row["ingredients_raw"])

        # Must record conflict_discrepancy in audit evidence
        cur.execute("SELECT decision, discrepancy_notes FROM inci_audit_evidence WHERE canonical_product_id = 300 ORDER BY id DESC LIMIT 1;")
        ev = cur.fetchone()
        self.assertEqual(ev["decision"], "conflict_discrepancy")
        self.assertIn("Conflicting candidate formulation detected", ev["discrepancy_notes"])

        # With force=True, an authorized administrator can override
        forced_success = self.repo.publish_formulation(
            candidate=cand_conflict,
            approval_reason="Admin approved formulation reformulation",
            force=True,
            conn=self.conn
        )
        self.assertTrue(forced_success)
        cur.execute("SELECT ingredients_raw, formula_version FROM canonical_products WHERE id = 300;")
        row2 = cur.fetchone()
        self.assertEqual(row2["formula_version"], 2)
        self.assertIn("Salicylic Acid", row2["ingredients_raw"])

    def test_ordered_and_unordered_hash_order_invariance(self):
        tokens_a = ["water", "glycerin", "niacinamide", "phenoxyethanol"]
        tokens_b = ["water", "glycerin", "phenoxyethanol", "niacinamide"]

        ordered_a = FormulationRepository.compute_ordered_formula_hash(tokens_a)
        ordered_b = FormulationRepository.compute_ordered_formula_hash(tokens_b)
        self.assertNotEqual(ordered_a, ordered_b, "Ordered formula hash must reflect concentration sequence!")

        unordered_a = FormulationRepository.compute_unordered_formula_hash(tokens_a)
        unordered_b = FormulationRepository.compute_unordered_formula_hash(tokens_b)
        self.assertEqual(unordered_a, unordered_b, "Unordered formula hash must be invariant to ingredient permutations!")

    def test_truncated_candidate_cannot_publish(self):
        truncated_cand = FormulationCandidate(
            canonical_product_id=300,
            source_name="store_tab",
            raw_ingredients="Water, Glycerin, Ceramide NP, ...",
            parsed_tokens=["water", "glycerin", "ceramide np"],
            actives=["ceramides"],
            confidence_score=0.90,
            completeness="partial",
            validation_flags={"is_truncated": True}
        )
        success = self.repo.publish_formulation(
            candidate=truncated_cand,
            approval_reason="Truncated store tab",
            force=False,
            conn=self.conn
        )
        self.assertFalse(success, "Truncated formulation was published as authoritative!")

    def candidate(self, **changes):
        values = dict(canonical_product_id=300, source_name="store_page_tab",
            raw_ingredients="Water, Glycerin, Ceramide NP, Ceramide AP, Phytosphingosine, Cholesterol",
            parsed_tokens=["water", "glycerin", "ceramide np", "ceramide ap", "phytosphingosine", "cholesterol"],
            completeness="full")
        values.update(changes)
        return FormulationCandidate(**values)

    def test_reordering_is_a_conflict(self):
        candidate = self.candidate()
        candidate.parsed_tokens.reverse()
        self.assertFalse(self.repo.publish_formulation(candidate, "Reordered", conn=self.conn))
        self.assertEqual(self.repo.get_audit_history(300, conn=self.conn)[-1]["decision"], "conflict_discrepancy")

    def test_rerun_preserves_version_and_records_evidence(self):
        for _ in range(2):
            self.assertTrue(self.repo.publish_formulation(self.candidate(), "Repeat", conn=self.conn))
        self.assertEqual(self.repo.get_canonical_formulation(300, conn=self.conn)["formula_version"], 1)
        self.assertEqual(len(self.repo.get_audit_history(300, conn=self.conn)), 3)

    def test_force_does_not_bypass_validation(self):
        for changes in ({"completeness": "unverified"}, {"completeness": "partial"},
                        {"raw_ingredients": "Water, Glycerin, Ceramide NP, ..."},
                        {"validation_flags": {"identity_resolved": False}},
                        {"validation_flags": {"invalid_structure": True}},
                        {"raw_ingredients": "Water, Glycerin, *******, Panthenol, Phenoxyethanol"},
                        {"raw_ingredients": "Water (Aqua, Glycerin, Panthenol, Phenoxyethanol"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.repo.publish_formulation(self.candidate(**changes), "Correction", force=True, conn=self.conn))

    def test_evidence_does_not_commit_callers_work(self):
        self.conn.commit()
        self.conn.execute("UPDATE canonical_products SET brand='Pending' WHERE id=300")
        self.repo.record_candidate_evidence(self.candidate(), conn=self.conn)
        self.conn.rollback()
        self.assertEqual(self.repo.get_canonical_formulation(300, conn=self.conn)["brand"], "CeraBrand")
        self.assertEqual(len(self.repo.get_audit_history(300, conn=self.conn)), 1)

    def test_failed_audit_rolls_back_publication_only(self):
        self.conn.commit()
        self.conn.execute("UPDATE canonical_products SET brand='Pending' WHERE id=300")
        self.conn.execute("CREATE TEMP TRIGGER fail_audit BEFORE INSERT ON inci_audit_evidence BEGIN SELECT RAISE(ABORT, 'audit failure'); END")
        candidate = self.candidate(raw_ingredients="Water, Glycerin, Panthenol, Phenoxyethanol",
            parsed_tokens=["water", "glycerin", "panthenol", "phenoxyethanol"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.publish_formulation(candidate, "Correction", force=True, conn=self.conn)
        current = self.repo.get_canonical_formulation(300, conn=self.conn)
        self.assertEqual(current["formula_version"], 1)
        self.assertEqual(current["brand"], "Pending")
        self.assertIn("Ceramide NP", current["ingredients_raw"])



class TestSafeguardsAndRetiredTiers(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(conn=self.conn, seed_defaults=False)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO stores (id, name, domain) VALUES ('store1', 'Store 1', 's1.com');")
        cur.execute("""
        INSERT INTO products (id, store_id, title, brand, category, price, product_url, is_skincare)
        VALUES (401, 'store1', 'Safeguard Test Product', 'BrandX', 'Serum', 150.0, 'https://s1.com/p401', 1);
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_apply_tier6_cluster_propagation_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            apply_tier_resolution_for_product(product_id=401, tier_code="tier6", conn=self.conn)
        self.assertIn("Tier 6 Cluster Propagation is retired", str(ctx.exception))

    def test_product_linker_initializes_null_ingredients(self):
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO products (id, store_id, title, brand, category, price, product_url, ingredients_raw, ingredient_source, is_skincare)
        VALUES (402, 'store1', 'BrandZ Pure Hydrator 50ml', 'BrandZ', 'Moisturizer', 250.0, 'https://s1.com/p402',
                'Water, Glycerin, Niacinamide', 'skinsafe_matched', 1);
        """)
        self.conn.commit()

        link_catalog_products(conn=self.conn)

        # Check newly created canonical cluster
        cur.execute("SELECT ingredients_raw, ingredients_parsed, actives FROM canonical_products WHERE brand = 'BrandZ';")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["ingredients_raw"], "Linker incorrectly initialized canonical ingredients from offer listing!")


if __name__ == "__main__":
    unittest.main()
