"""
Automated Test Suite for SkinSync Authoritative Canonical Data Architecture.

Enforces the core architectural invariants:
1. One unique product = one authoritative INCI formulation in `canonical_products`.
2. Retailer listings (`products`) are strictly store offers contributing only retailer,
   availability, size, and pricing, and NEVER overwrite or propagate canonical ingredients.
3. Packaging size is an offer attribute (50ml, 177ml, 340g cluster to the same product),
   while genuinely differing formulations (SPF 30 vs SPF 50, fragrance-free vs scented)
   remain separate canonical products.
4. Price comparison is size-aware: groups offers by size, computes same-size deals,
   and computes normalized unit pricing (EGP/100ml for liquids, EGP/100g for solids)
   without ever assuming volume == mass (never 50g == 50ml).
5. Catalog service indexes exactly ONE row per canonical product.
6. Similarity resolver resolves authoritative canonical formulations.
7. INCI audit evidence table tracks independent verification sources and audit history.
"""

import unittest
import sqlite3
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.db_manager import (
    init_db,
    add_inci_audit_evidence,
    get_inci_audit_evidence
)
from pipeline.product_linker import (
    link_catalog_products,
    extract_size_details,
    parse_volume
)
from pipeline.harvester import sync_store
from services.price_service import (
    get_canonical_price_comparison,
    get_product_price_comparison
)
from services.catalog_service import load_catalog_from_db, get_catalog_df
from routers.similarity import _resolve_product_for_similarity


class TestCanonicalArchitectureInvariants(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()

        # Build schema using init_db on in-memory connection
        init_db(conn=self.conn, seed_defaults=False)

        # Seed test stores
        cur.executemany("""
        INSERT INTO stores (id, name, domain, platform)
        VALUES (?, ?, ?, 'shopify');
        """, [
            ("sourcebeauty", "Source Beauty", "sourcebeauty.com"),
            ("feel22", "Feel22 Egypt", "feel22egypt.com"),
            ("bloompharmacy", "Bloom Pharmacy", "bloompharmacy.com"),
            ("drdawood", "Dr. Dawood", "drdawood.com"),
        ])
        self.conn.execute('UPDATE sources SET enabled=1')
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_size_extraction_and_unit_price_normalization(self):
        """Tests that liquid volumes and solid weights are normalized to compatible units without mixing."""
        # 50 ml @ 350 EGP -> 700 EGP / 100ml
        size_50ml = extract_size_details("CeraVe Moisturizing Cream 50ml", price=350.0)
        self.assertEqual(size_50ml["size_raw"], "50 ml")
        self.assertEqual(size_50ml["size_value"], 50.0)
        self.assertEqual(size_50ml["size_unit"], "ml")
        self.assertEqual(size_50ml["normalized_size_ml"], 50.0)
        self.assertIsNone(size_50ml["normalized_size_g"])
        self.assertEqual(size_50ml["unit_price"], 700.0)
        self.assertEqual(size_50ml["unit_measure"], "EGP / 100ml")

        # 177 ml @ 690 EGP -> 389.83 EGP / 100ml
        size_177ml = extract_size_details("CeraVe Moisturizing Cream 177 ml", price=690.0)
        self.assertEqual(size_177ml["normalized_size_ml"], 177.0)
        self.assertIsNone(size_177ml["normalized_size_g"])
        self.assertEqual(size_177ml["unit_price"], 389.83)
        self.assertEqual(size_177ml["unit_measure"], "EGP / 100ml")

        # 340 g @ 950 EGP -> 279.41 EGP / 100g (NEVER converted to ml)
        size_340g = extract_size_details("CeraVe Moisturizing Cream 340g", price=950.0)
        self.assertEqual(size_340g["size_raw"], "340 g")
        self.assertEqual(size_340g["size_value"], 340.0)
        self.assertEqual(size_340g["size_unit"], "g")
        self.assertIsNone(size_340g["normalized_size_ml"])
        self.assertEqual(size_340g["normalized_size_g"], 340.0)
        self.assertEqual(size_340g["unit_price"], 279.41)
        self.assertEqual(size_340g["unit_measure"], "EGP / 100g")

        # 1 L (liquid liter) -> 1000 ml
        size_1l = extract_size_details("Bioderma Sensibio H2O Micellar Water 1L", price=800.0)
        self.assertEqual(size_1l["normalized_size_ml"], 1000.0)
        self.assertEqual(size_1l["unit_price"], 80.0)
        self.assertEqual(size_1l["unit_measure"], "EGP / 100ml")

    def test_packaging_size_variant_deduplication(self):
        """
        Packaging sizes (50ml, 177ml, 340g) of CeraVe Moisturizing Cream cluster into ONE canonical product,
        while different formulations (SPF 30 vs SPF 50, lotion vs cream, scented vs unscented) remain distinct.
        """
        cur = self.conn.cursor()

        # Insert multiple sizes and stores of CeraVe Moisturizing Cream
        cur.executemany("""
        INSERT INTO products (
            store_id, store_product_id, handle, title, brand, category,
            price, is_available, product_url, is_skincare, ingredients_raw
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
        """, [
            ("sourcebeauty", "p1", "cerave-cream-50ml", "CeraVe Moisturizing Cream 50ml", "CeraVe", "Moisturizer", 350.0, 1, "http://sb/c1", "Ceramides, Hyaluronic Acid"),
            ("feel22", "p2", "cerave-cream-177ml", "CeraVe Moisturizing Cream 177ml", "CeraVe", "Moisturizer", 690.0, 1, "http://f22/c2", None),
            ("bloompharmacy", "p3", "cerave-cream-50ml-bloom", "CeraVe Moisturizing Cream 50ml", "CeraVe", "Moisturizer", 330.0, 1, "http://bp/c3", None),
            ("drdawood", "p4", "cerave-cream-340g", "CeraVe Moisturizing Cream 340g", "CeraVe", "Moisturizer", 950.0, 1, "http://dd/c4", None),

            # Distinct formulation 1: Daily Moisturizing LOTION (different vehicle)
            ("sourcebeauty", "p5", "cerave-lotion-236ml", "CeraVe Daily Moisturizing Lotion 236ml", "CeraVe", "Moisturizer", 420.0, 1, "http://sb/l1", "Water, Glycerin, Caprylic Triglyceride"),

            # Distinct formulation 2: SPF 30 vs SPF 50 (different sun filter strength)
            ("feel22", "p6", "laroche-anthelios-spf30", "La Roche-Posay Anthelios SPF 30 50ml", "La Roche-Posay", "Sunscreen", 450.0, 1, "http://f22/s30", "Aqua, Octocrylene"),
            ("feel22", "p7", "laroche-anthelios-spf50", "La Roche-Posay Anthelios SPF 50 50ml", "La Roche-Posay", "Sunscreen", 520.0, 1, "http://f22/s50", "Aqua, Mexoryl SX, Mexoryl XL"),

            # Distinct formulation 3: Fragrance-Free vs Scented
            ("bloompharmacy", "p8", "bioderma-atoderm-creme-ff", "Bioderma Atoderm Creme Fragrance-Free 200ml", "Bioderma", "Moisturizer", 380.0, 1, "http://bp/b1", "Aqua, Mineral Oil, Glycerin"),
            ("bloompharmacy", "p9", "bioderma-atoderm-creme-scented", "Bioderma Atoderm Creme 200ml", "Bioderma", "Moisturizer", 380.0, 1, "http://bp/b2", "Aqua, Mineral Oil, Glycerin, Parfum"),
        ])
        self.conn.commit()

        # Run canonical linker
        res = link_catalog_products(conn=self.conn)

        # 1. CeraVe Moisturizing Cream across all 4 sizes and 4 stores must link to EXACTLY ONE canonical entity
        cur.execute("SELECT DISTINCT canonical_product_id FROM products WHERE id IN (1, 2, 3, 4);")
        cerave_cream_canonical_ids = cur.fetchall()
        self.assertEqual(len(cerave_cream_canonical_ids), 1, "All 4 sizes of CeraVe Moisturizing Cream must share a single canonical entity.")
        cerave_cream_id = cerave_cream_canonical_ids[0][0]

        # Check canonical aggregated metadata
        cur.execute("SELECT canonical_title, min_price, max_price, store_count FROM canonical_products WHERE id = ?;", (cerave_cream_id,))
        c_cream = dict(cur.fetchone())
        self.assertIn("Moisturizing Cream", c_cream["canonical_title"])
        self.assertEqual(c_cream["min_price"], 330.0)
        self.assertEqual(c_cream["max_price"], 950.0)
        self.assertEqual(c_cream["store_count"], 4)

        # 2. CeraVe Lotion must have a SEPARATE canonical entity from CeraVe Cream
        cur.execute("SELECT canonical_product_id FROM products WHERE id = 5;")
        lotion_canon_id = cur.fetchone()[0]
        self.assertNotEqual(lotion_canon_id, cerave_cream_id, "Lotion and Cream must NOT be merged.")

        # 3. SPF 30 and SPF 50 must be SEPARATE canonical entities
        cur.execute("SELECT canonical_product_id FROM products WHERE id = 6;")
        spf30_id = cur.fetchone()[0]
        cur.execute("SELECT canonical_product_id FROM products WHERE id = 7;")
        spf50_id = cur.fetchone()[0]
        self.assertNotEqual(spf30_id, spf50_id, "SPF 30 and SPF 50 are distinct formulations and must have separate canonical products.")

        # 4. Fragrance-free and Scented must be SEPARATE canonical entities
        cur.execute("SELECT canonical_product_id FROM products WHERE id = 8;")
        ff_id = cur.fetchone()[0]
        cur.execute("SELECT canonical_product_id FROM products WHERE id = 9;")
        scented_id = cur.fetchone()[0]
        self.assertNotEqual(ff_id, scented_id, "Fragrance-Free and Scented formulations must have separate canonical products.")

    def test_zero_ingredient_propagation_to_offers(self):
        """
        Retailer listings (offers) must NOT receive copied ingredients.
        Offers contribute only retailer, url, availability, size, and pricing.
        """
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO products (
            store_id, store_product_id, handle, title, brand, category,
            price, is_available, product_url, is_skincare, ingredients_raw
        ) VALUES
        ('sourcebeauty', 'o1', 'cerave-50ml', 'CeraVe Moisturizing Cream 50ml', 'CeraVe', 'Moisturizer', 350.0, 1, 'http://sb/c1', 1, 'Ceramides, Hyaluronic Acid, Glycerin'),
        ('bloompharmacy', 'o2', 'cerave-50ml-bp', 'CeraVe Moisturizing Cream 50ml', 'CeraVe', 'Moisturizer', 330.0, 1, 'http://bp/c1', 1, NULL);
        """)
        self.conn.commit()

        metrics = link_catalog_products(conn=self.conn)
        self.assertEqual(metrics["ingredients_propagated"], 0, "Zero ingredients should be propagated to retailer offer listings.")

        cur.execute("SELECT id, ingredients_raw, size_raw, unit_price FROM products WHERE id = 2;")
        offer_bp = dict(cur.fetchone())
        self.assertIsNone(offer_bp["ingredients_raw"], "Offer listing ingredients_raw must remain NULL, not overwritten.")
        self.assertEqual(offer_bp["size_raw"], "50 ml")
        self.assertEqual(offer_bp["unit_price"], 660.0)

    def test_size_aware_price_comparison_and_unit_pricing(self):
        """Tests that get_canonical_price_comparison produces same-size comparisons and normalized unit pricing."""
        cur = self.conn.cursor()
        cur.executemany("""
        INSERT INTO products (
            store_id, store_product_id, handle, title, brand, category,
            price, is_available, product_url, is_skincare, ingredients_raw
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
        """, [
            ("sourcebeauty", "p1", "cerave-50ml", "CeraVe Moisturizing Cream 50ml", "CeraVe", "Moisturizer", 350.0, 1, "http://sb/c1", "Ceramides, Hyaluronic Acid"),
            ("feel22", "p2", "cerave-177ml", "CeraVe Moisturizing Cream 177ml", "CeraVe", "Moisturizer", 690.0, 1, "http://f22/c2", None),
            ("bloompharmacy", "p3", "cerave-50ml-bp", "CeraVe Moisturizing Cream 50ml", "CeraVe", "Moisturizer", 330.0, 1, "http://bp/c3", None),
            ("drdawood", "p4", "cerave-340g", "CeraVe Moisturizing Cream 340g", "CeraVe", "Moisturizer", 950.0, 1, "http://dd/c4", None),
        ])
        self.conn.commit()

        link_catalog_products(conn=self.conn)

        cur.execute("SELECT DISTINCT canonical_product_id FROM products WHERE id = 1;")
        canonical_id = cur.fetchone()[0]

        # Price comparison fixture represents a reviewed, visible identity.
        self.conn.execute("UPDATE canonical_products SET catalog_visible=1,identity_status='verified' WHERE id=?",(canonical_id,))
        res = get_canonical_price_comparison(canonical_id, conn=self.conn)
        self.assertIsNotNone(res)
        self.assertEqual(res["canonical_product_id"], canonical_id)
        self.assertEqual(res["store_count"], 4)
        self.assertEqual(res["listing_count"], 4)

        # Same-size grouping breakdown
        self.assertIn("offers_by_size", res)
        offers_by_size = res["offers_by_size"]
        self.assertIn("50 ml", offers_by_size)
        self.assertIn("177 ml", offers_by_size)
        self.assertIn("340 g", offers_by_size)

        # 50 ml group: 2 offers (Bloom @ 330 EGP vs SourceBeauty @ 350 EGP)
        offers_50ml = offers_by_size["50 ml"]
        self.assertEqual(len(offers_50ml), 2)
        best_50ml = [o for o in offers_50ml if o["is_best_size_deal"]][0]
        self.assertEqual(best_50ml["store_id"], "bloompharmacy")
        self.assertEqual(best_50ml["price"], 330.0)

        # Normalized unit pricing: 177ml @ 690 EGP = 389.83 EGP/100ml vs 50ml @ 330 EGP = 660 EGP/100ml
        self.assertIsNotNone(res["best_unit_deal_volume"])
        self.assertEqual(res["best_unit_deal_volume"]["store_id"], "feel22")
        self.assertEqual(res["best_unit_deal_volume"]["unit_price"], 389.83)
        self.assertEqual(res["best_unit_deal_volume"]["unit_measure"], "EGP / 100ml")

        # 340g solid unit deal
        self.assertIsNotNone(res["best_unit_deal_weight"])
        self.assertEqual(res["best_unit_deal_weight"]["store_id"], "drdawood")
        self.assertEqual(res["best_unit_deal_weight"]["unit_price"], 279.41)
        self.assertEqual(res["best_unit_deal_weight"]["unit_measure"], "EGP / 100g")

        # Look up via retailer offer product ID resolves the exact same canonical price comparison
        lookup_from_offer = get_product_price_comparison(3, conn=self.conn)
        self.assertIsNotNone(lookup_from_offer)
        self.assertEqual(lookup_from_offer["canonical_product_id"], canonical_id)

    def test_harvester_zero_permission_to_canonical_ingredients(self):
        """
        Simulates harvester syncing an offer and verifies that canonical ingredients
        are completely protected and CANNOT be modified or overwritten by store scraping.
        """
        cur = self.conn.cursor()

        # Create authoritative canonical product
        cur.execute("""
        INSERT INTO canonical_products (
            canonical_title, brand, category, normalized_key,
            ingredients_raw, ingredients_parsed, actives, is_skincare, min_price, max_price
        ) VALUES (
            'CeraVe Moisturizing Cream', 'CeraVe', 'Moisturizer', 'cerave_moisturizing_cream',
            'Water, Glycerin, Cetearyl Alcohol, Caprylic Triglyceride, Ceramides, Hyaluronic Acid',
            '["water", "glycerin", "ceramides", "hyaluronic acid"]', '["ceramides", "hyaluronic_acid"]',
            1, 350.0, 350.0
        );
        """)
        canon_id = cur.lastrowid

        # Insert linked retailer offer
        cur.execute("""
        INSERT INTO products (
            store_id, store_product_id, handle, title, brand, category,
            price, is_available, product_url, canonical_product_id, is_skincare, ingredients_raw
        ) VALUES (
            'bloompharmacy', 'bp_101', 'cerave-moisturizing-cream',
            'CeraVe Moisturizing Cream 50ml', 'CeraVe', 'Moisturizer',
            350.0, 1, 'https://bloompharmacy.com/products/cerave-moisturizing-cream',
            ?, 1, NULL
        );
        """, (canon_id,))
        offer_id = cur.lastrowid
        self.conn.commit()

        # Simulate store sync updating the offer price to 390.0 EGP with retailer description
        mock_shopify_products = [{
            "id": "bp_101",
            "title": "CeraVe Moisturizing Cream 50ml",
            "vendor": "CeraVe",
            "product_type": "Moisturizer",
            "handle": "cerave-moisturizing-cream",
            "body_html": "<p>New batch description with partial ingredients: Aqua, Glycerin</p>",
            "variants": [{"price": "390.00", "compare_at_price": "420.00", "available": True}],
            "images": [{"src": "https://bloompharmacy.com/img.jpg"}]
        }]

        store_cfg = {
            "id": "bloompharmacy",
            "name": "Bloom Pharmacy",
            "domain": "bloompharmacy.com",
            "rate_limit_delay": 0.0
        }

        with patch("pipeline.harvester.fetch_shopify_page", return_value=mock_shopify_products):
            sync_res = sync_store(store_cfg, max_pages=1, conn=self.conn)

        self.assertEqual(sync_res["status"], "success")

        # 1. Verify offer price was updated in products table
        cur.execute("SELECT price, size_raw, unit_price FROM products WHERE id = ?;", (offer_id,))
        upd_offer = dict(cur.fetchone())
        self.assertEqual(upd_offer["price"], 390.0)
        self.assertEqual(upd_offer["size_raw"], "50 ml")
        self.assertEqual(upd_offer["unit_price"], 780.0)

        # 2. STRICT ARCHITECTURAL INVARIANT: Verify canonical ingredients were NOT touched!
        cur.execute("SELECT ingredients_raw, actives, min_price, max_price FROM canonical_products WHERE id = ?;", (canon_id,))
        canon_after = dict(cur.fetchone())
        self.assertEqual(
            canon_after["ingredients_raw"],
            'Water, Glycerin, Cetearyl Alcohol, Caprylic Triglyceride, Ceramides, Hyaluronic Acid',
            "Canonical authoritative ingredients must remain untouched by harvester!"
        )
        self.assertEqual(canon_after["actives"], '["ceramides", "hyaluronic_acid"]')
        self.assertEqual(canon_after["min_price"], 390.0)

    def test_similarity_resolver_checks_canonical_first(self):
        """Tests that _resolve_product_for_similarity always evaluates the authoritative canonical formulation."""
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO canonical_products (
            canonical_title, brand, category, normalized_key,
            ingredients_raw, ingredients_parsed, actives, is_skincare
        ) VALUES (
            'The Ordinary Niacinamide 10% + Zinc 1%', 'The Ordinary', 'Serum', 'the_ordinary_niacinamide_10_zinc_1',
            'Aqua (Water), Niacinamide, Pentylene Glycol, Zinc PCA, Dimethyl Isosorbide, Tamarindus Indica Seed Gum, Xanthan Gum, Isoceteth-20, Ethoxydiglycol, Phenoxyethanol, Chlorphenesin.',
            '["water", "niacinamide", "zinc pca"]', '["niacinamide", "zinc"]', 1
        );
        """)
        canon_id = cur.lastrowid

        # Linked retailer offer with no ingredients stored
        cur.execute("""
        INSERT INTO products (
            id, store_id, store_product_id, handle, title, brand, category,
            price, is_available, product_url, canonical_product_id, is_skincare, ingredients_raw
        ) VALUES (
            501, 'feel22', 'to_1', 'the-ordinary-niacinamide', 'The Ordinary Niacinamide 10% + Zinc 1% 30ml', 'The Ordinary', 'Serum',
            450.0, 1, 'https://feel22.com/to', ?, 1, NULL
        );
        """, (canon_id,))
        offer_id = 501
        self.conn.commit()

        # 1. Resolve directly by canonical ID
        res_canon = _resolve_product_for_similarity(canon_id, None, self.conn, entity_type="canonical")
        self.assertEqual(res_canon["id"], canon_id)
        self.assertTrue(res_canon["is_authoritative_canonical"])
        self.assertIn("niacinamide", res_canon["actives"])
        self.assertIn("zinc", res_canon["actives"])

        # 2. Resolve by retailer offer ID -> MUST resolve to the canonical authoritative formulation
        res_offer = _resolve_product_for_similarity(offer_id, None, self.conn, entity_type="product")
        self.assertEqual(res_offer["canonical_product_id"], canon_id)
        self.assertTrue(res_offer["is_authoritative_canonical"])
        self.assertIn("niacinamide", res_offer["actives"])
        self.assertIn("zinc", res_offer["actives"])

    def test_inci_audit_evidence_trail(self):
        """Tests logging and retrieval of independent verification sources in inci_audit_evidence."""
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO canonical_products (
            canonical_title, brand, category, normalized_key, ingredients_raw, is_skincare
        ) VALUES ('CeraVe Moisturizing Cream', 'CeraVe', 'Moisturizer', 'cerave_cream', 'Ceramides, Hyaluronic Acid', 1);
        """)
        canon_id = cur.lastrowid
        self.conn.commit()

        # Record audit evidence from SkinSAFE
        ev1_id = add_inci_audit_evidence(
            canonical_product_id=canon_id,
            source_name="skinsafe",
            evidence_url="https://skinsafeproducts.com/cerave-cream",
            raw_ingredients="Purified Water, Glycerin, Ceteareth-20, Caprylic/Capric Triglyceride, Ceramides",
            parsed_tokens=["water", "glycerin", "ceramides"],
            confidence_score=0.96,
            decision="approved_authoritative",
            discrepancy_notes="100% bioequivalent matches clinical database",
            audited_by="pipeline_tier3",
            conn=self.conn
        )
        self.assertIsNotNone(ev1_id)

        # Record candidate evidence from LangSearch / INKEEDecoder
        ev2_id = add_inci_audit_evidence(
            canonical_product_id=canon_id,
            source_name="langsearch_inkeedecoder",
            evidence_url="https://incidecoder.com/products/cerave-moisturizing-cream",
            raw_ingredients="Aqua / Water / Eau, Glycerin, Cetearyl Alcohol, Caprylic/Capric Triglyceride, Cetyl Alcohol, Ceteareth-20, Petrolatum, Potassium Phosphate, Ceramide NP",
            parsed_tokens=["aqua", "glycerin", "ceramide np"],
            confidence_score=0.94,
            decision="candidate_evidence",
            discrepancy_notes=None,
            audited_by="pipeline_tier4",
            conn=self.conn
        )
        self.assertIsNotNone(ev2_id)

        evidence_list = get_inci_audit_evidence(canon_id, conn=self.conn)
        self.assertEqual(len(evidence_list), 2)
        sources = {e["source_name"] for e in evidence_list}
        self.assertIn("skinsafe", sources)
        self.assertIn("langsearch_inkeedecoder", sources)


if __name__ == "__main__":
    unittest.main()
