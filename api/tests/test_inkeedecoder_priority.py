"""
Unit tests for INKEEDecoder Priority, INCIBEAUTY Blacklisting & Anti-Sabotage Guards,
and Verified Result Link Persistence in the Skincare Waterfall Architecture.
"""

import unittest
import sqlite3
import tempfile
import os
import json
from unittest.mock import patch, MagicMock

from pipeline.inci_matcher import is_valid_inci_list
from pipeline.waterfall_inci_resolver import (
    is_blacklisted_search_url,
    is_preferred_search_url,
    extract_inci_from_web_text,
    resolve_tier4_langsearch,
    execute_inci_waterfall_for_product,
    apply_tier_resolution_for_product,
    BLACKLISTED_SEARCH_DOMAINS,
    PREFERRED_SEARCH_DOMAINS
)


class TestInkeeDecoderAndInciBeautyGuards(unittest.TestCase):

    def test_blacklisted_domains_identification(self):
        """Tests that incibeauty URLs across subdomains and protocols are blacklisted."""
        blacklisted_urls = [
            "https://incibeauty.com/produit/3337871324683",
            "http://www.incibeauty.com/en/products/cerave",
            "https://m.incibeauty.com/produit/12345",
            "https://fr.incibeauty.com/produits/shampoing",
            "https://sub.domain.incibeauty.com/test"
        ]
        for url in blacklisted_urls:
            self.assertTrue(is_blacklisted_search_url(url), f"Failed to blacklist: {url}")

        allowed_urls = [
            "https://inkeedecoder.com/products/cerave-hydrating-cleanser",
            "https://incidecoder.com/products/the-ordinary-niacinamide",
            "https://www.sephora.com/product/cerave-cleanser",
            "https://www.ulta.com/p/hydrating-facial-cleanser"
        ]
        for url in allowed_urls:
            self.assertFalse(is_blacklisted_search_url(url), f"Incorrectly blacklisted: {url}")

    def test_preferred_domains_identification(self):
        """Tests that inkeedecoder and incidecoder URLs are identified as preferred."""
        preferred_urls = [
            "https://inkeedecoder.com/products/la-roche-posay-effaclar-duo",
            "https://www.inkeedecoder.com/products/cerave-moisturizing-cream",
            "https://incidecoder.com/products/bioderma-sensibio-h2o",
            "https://www.incidecoder.com/products/cosrx-snail-mucin"
        ]
        for url in preferred_urls:
            self.assertTrue(is_preferred_search_url(url), f"Failed to identify preferred: {url}")

        regular_urls = [
            "https://www.amazon.eg/dp/B0000",
            "https://bashrety.tv/products/cerave",
            "https://glamira.com/product"
        ]
        for url in regular_urls:
            self.assertFalse(is_preferred_search_url(url), f"Incorrectly marked preferred: {url}")

    def test_inci_beauty_sabotage_rejection_in_validator(self):
        """
        Tests that is_valid_inci_list rejects text with asterisks (*******)
        and typical incibeauty alphabetical reordering / masking disclaimers.
        """
        # Exact incibeauty disclaimer text
        sabotaged_inci = (
            "Aqua, Glycerin, Cetearyl Alcohol, Niacinamide, *******, Phenoxyethanol. "
            "(*) Ingredients are displayed in alphabetical order and some have been deliberately hidden (*******), "
            "to get the exact composition, please use our applications."
        )
        self.assertFalse(is_valid_inci_list(sabotaged_inci))

        # Asterisk masking without full disclaimer
        masked_inci = "Aqua, Glycerin, Butylene Glycol, *******, Tocopherol, Dimethicone"
        self.assertFalse(is_valid_inci_list(masked_inci))

        hidden_disclaimer = "Aqua, Glycerin, Stearic Acid, Cetyl Alcohol. Ingredients are deliberately hidden."
        self.assertFalse(is_valid_inci_list(hidden_disclaimer))

        alphabetical_disclaimer = "Aqua, Alcohol, Glycerin, Sodium Chloride. Ingredients displayed in alphabetical order."
        self.assertFalse(is_valid_inci_list(alphabetical_disclaimer))

        # Clean valid cosmetic INCI list should pass
        authentic_inci = "Aqua / Water / Eau, Glycerin, Cetearyl Alcohol, Peg-40 Stearate, Stearyl Alcohol, Potassium Phosphate, Ceramide Np, Phenoxyethanol"
        self.assertTrue(is_valid_inci_list(authentic_inci))

    def test_extract_inci_inkeedecoder_signature_format(self):
        """
        Tests that extract_inci_from_web_text successfully extracts INCI formulas
        from INKEEDecoder's signature 'ingredients explained: ...' format.
        """
        inkee_web_text = (
            "CeraVe Hydrating Facial Cleanser ingredients explained: "
            "Aqua / Water / Eau, Glycerin, Cetearyl Alcohol, Peg-40 Stearate, Stearyl Alcohol, "
            "Potassium Phosphate, Ceramide Np, Ceramide Ap, Ceramide Eop, Carbomer, Glyceryl Stearate, "
            "Behentrimonium Methosulfate, Sodium Lauroyl Lactylate, Sodium Hyaluronate, Cholesterol, "
            "Phenoxyethanol, Disodium Edta, Dipotassium Phosphate, Tocopherol, Phytosphingosine, "
            "Xanthan Gum, Cetyl Alcohol, Polysorbate 20, Ethylhexylglycerin. "
            "How to use: Wet skin with lukewarm water. Massage cleanser into skin."
        )
        extracted = extract_inci_from_web_text(inkee_web_text)
        self.assertIsNotNone(extracted)
        self.assertIn("Glycerin", extracted)
        self.assertIn("Ceramide Np", extracted)
        self.assertNotIn("How to use", extracted)

    def test_extract_inci_rejects_sabotaged_text(self):
        """Tests that extract_inci_from_web_text rejects pages with incibeauty disclaimers or asterisks."""
        sabotaged_page = (
            "Product Ingredients: Aqua, Glycerin, Niacinamide, *******, Alcohol, Zinc Pca. "
            "(*) Ingredients are displayed in alphabetical order and some have been deliberately hidden (*******)."
        )
        self.assertIsNone(extract_inci_from_web_text(sabotaged_page))

    def setUp(self):
        from pipeline.waterfall_inci_resolver import _LANGSEARCH_CACHE
        _LANGSEARCH_CACHE.clear()

    @patch("urllib.request.urlopen")
    @patch("pipeline.rate_limiter.langsearch_limiter.acquire", return_value=True)
    @patch("pipeline.rate_limiter.langsearch_limiter.get_quota_status")
    def test_langsearch_prioritizes_inkeedecoder_and_blacklists_incibeauty(self, mock_quota, mock_acquire, mock_urlopen):
        """
        Tests that resolve_tier4_langsearch:
        1. Discards results from incibeauty.com.
        2. Promotes inkeedecoder.com results to Rank 0 over general retailers.
        3. Returns source_url and is_inkeedecoder=True.
        """
        mock_quota.return_value = {
            "is_daily_exhausted": False,
            "tokens_used_today": 0,
            "daily_token_limit": 10000000,
            "daily_used_percentage": 0.0,
            "resets_in_human": "12h"
        }

        mock_response_data = {
            "data": {
                "webPages": {
                    "value": [
                        # Result 1: incibeauty (Blacklisted)
                        {
                            "name": "CeraVe Hydrating Cleanser - INCI Beauty",
                            "url": "https://incibeauty.com/produit/cerave-cleanser",
                            "snippet": "Ingredients: Aqua, Glycerin, *******. Displayed in alphabetical order.",
                            "text": "Ingredients: Aqua, Glycerin, *******. Some deliberately hidden."
                        },
                        # Result 2: General Retailer (Rank 1)
                        {
                            "name": "CeraVe Hydrating Cleanser at Retailer",
                            "url": "https://beautyretailer.com/cerave-hydrating-cleanser",
                            "snippet": "CeraVe cleanser ingredients",
                            "text": "Ingredients: Aqua / Water, Glycerin, Cetearyl Alcohol, Ceramide NP, Phenoxyethanol"
                        },
                        # Result 3: INKEEDecoder (Should be elevated to Rank 0!)
                        {
                            "name": "CeraVe Hydrating Cleanser ingredients explained",
                            "url": "https://inkeedecoder.com/products/cerave-hydrating-cleanser",
                            "snippet": "CeraVe Hydrating Cleanser ingredients explained: Aqua, Glycerin, Cetearyl Alcohol, Ceramide NP, Sodium Hyaluronate",
                            "text": "CeraVe Hydrating Cleanser ingredients explained: Aqua, Glycerin, Cetearyl Alcohol, Ceramide NP, Sodium Hyaluronate, Phenoxyethanol"
                        }
                    ]
                }
            }
        }

        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp_obj.headers = {}
        mock_resp_obj.__enter__.return_value = mock_resp_obj
        mock_urlopen.return_value = mock_resp_obj

        res = resolve_tier4_langsearch(brand="CeraVe", title="Hydrating Cleanser", api_key="dummy_key")

        self.assertIsNotNone(res)
        self.assertEqual(res["method"], "langsearch_web_search")
        # Assert INKEEDecoder was prioritized over beautyretailer.com
        self.assertEqual(res["source_url"], "https://inkeedecoder.com/products/cerave-hydrating-cleanser")
        self.assertTrue(res.get("is_inkeedecoder"))
        self.assertIn("Ceramide NP", res["ingredients_raw"])

    def test_database_persistence_of_ingredient_source_url(self):
        """
        Tests that ingredient_source_url is correctly persisted into the canonical table
        when resolving and applying Tier 4.
        """
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = temp_db.name
        temp_db.close()

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            from pipeline.db_manager import init_db
            init_db(conn=conn, seed_defaults=False)
            cur.execute("INSERT INTO stores (id,name,domain) VALUES ('10','Test Store','test.example')")
            cur.execute("INSERT INTO canonical_products (id,canonical_title,brand,category,normalized_key) VALUES (1,'Effaclar Duo Plus','La Roche-Posay','Treatment','effaclar-duo')")
            cur.execute("""INSERT INTO products (id,store_id,title,brand,category,product_url,description_html,canonical_product_id)
                VALUES (1,'10','Effaclar Duo Plus','La Roche-Posay','Treatment','https://test.example/p','Anti blemish treatment',1)""")
            conn.commit()

            # Mock apply_tier_resolution_for_product applying a Tier 4 outcome with a source URL
            tier4_resolutions = {
                "selected_tier": "tier4",
                "tiers": {
                    "tier4": {
                        "tier": 4,
                        "code": "tier4",
                        "name": "LangSearch Web Search INCI",
                        "status": "resolved",
                        "ingredients_raw": "Aqua / Water, Glycerin, Dimethicone, Isocetyl Stearate, Niacinamide, Salicylic Acid",
                        "parsed_tokens": ["aqua", "glycerin", "dimethicone", "isocetyl stearate", "niacinamide", "salicylic acid"],
                        "actives": ["niacinamide", "salicylic_acid"],
                        "method": "langsearch_web_search",
                        "confidence": 0.95,
                        "metadata": {
                            "source_url": "https://inkeedecoder.com/products/la-roche-posay-effaclar-duo",
                            "is_inkeedecoder": True
                        }
                    }
                }
            }
            cur.execute("UPDATE products SET tier_resolutions = ? WHERE id = 1;", (json.dumps(tier4_resolutions),))
            conn.commit()

            apply_tier_resolution_for_product(1, "tier4", conn=conn)

            # Verify persisted database row
            cur.execute("SELECT ingredient_source, ingredient_source_url, ingredients_raw FROM canonical_products WHERE id = 1;")
            row = cur.fetchone()

            self.assertEqual(row["ingredient_source"], "langsearch_web_search")
            self.assertEqual(row["ingredient_source_url"], "https://inkeedecoder.com/products/la-roche-posay-effaclar-duo")
            self.assertIn("Niacinamide", row["ingredients_raw"])
            conn.close()

        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


if __name__ == "__main__":
    unittest.main()
