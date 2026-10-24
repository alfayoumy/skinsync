"""Prevent historical scraped ingredient strings from entering trusted INCI."""
import unittest
from pipeline.formulation_service import FormulationCandidate, FormulationRepository
from pipeline.waterfall_inci_resolver import resolve_tier1_store_tab

RAW='Water, Glycerin, Niacinamide, Caprylic/Capric Triglyceride, Cetearyl Alcohol, Phenoxyethanol'

class HistoricalInciDisabledTests(unittest.TestCase):
    def test_historical_rows_are_not_tier1(self):
        product={'title':'Example Cream 50ml','product_url':'https://example.org/cream',
                 'ingredients_raw':RAW,'ingredient_source':'historical_store_scrape'}
        self.assertIsNone(resolve_tier1_store_tab(product))

    def test_verified_current_store_is_tier1(self):
        result=resolve_tier1_store_tab({'ingredients_raw':RAW,'ingredient_source':'store_page_tab'})
        self.assertIsNotNone(result)
        self.assertEqual(result['method'],'store_page_tab')

    def test_untagged_legacy_html_is_not_tier1(self):
        self.assertIsNone(resolve_tier1_store_tab({'description_html':'<h3>Ingredients</h3>'+RAW}))

    def test_publisher_blocks_old_csv(self):
        c=FormulationCandidate(123,'historical_store_scrape',RAW,['water','glycerin'])
        self.assertFalse(FormulationRepository().publish_formulation(c,'old CSV'))
        c.source_name='source:retailer'
        c.validation_flags={'method':'historical_store_scrape'}
        self.assertFalse(FormulationRepository().publish_formulation(c,'old CSV'))

if __name__=='__main__':
    unittest.main()
