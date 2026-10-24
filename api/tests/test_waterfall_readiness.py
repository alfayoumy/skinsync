import sqlite3
import unittest
from unittest.mock import patch
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver, extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.waterfall_readiness import eligible_waterfall, certify_cross_store
from pipeline.formulation_service import FormulationCandidate, FormulationRepository


class WaterfallReadinessTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False)
        self.addCleanup(self.conn.close)
        self.resolver=CanonicalIdentityResolver(self.conn)
        self.title='CeraVe Moisturizing Cream'
        self.cid=self.resolver.create_provisional(self.title,'CeraVe',
            classify_product_attributes(self.title,'CeraVe'),
            extract_identity(self.title,'CeraVe'),False)
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy',catalog_visible=1 WHERE id=?",(self.cid,))

    def test_zero_ready_reports_reason_without_provider_calls(self):
        from pipeline.waterfall_inci_resolver import run_waterfall_pipeline
        snapshot=eligible_waterfall(self.conn,True)
        self.assertEqual(snapshot['eligible'],0)
        self.assertEqual(snapshot['blocked_unverified'],1)
        with patch('pipeline.waterfall_inci_resolver.resolve_waterfall_for_canonical_product') as resolver:
            result=run_waterfall_pipeline(conn=self.conn,revalidate_all=True,max_workers=1,
                queue_jev=False,queue_gemini=False,langsearch_enabled=False)
        resolver.assert_not_called()
        self.assertEqual(result['eligibility']['blocked_unverified'],1)

    def test_historical_scraped_formula_is_not_a_trusted_match(self):
        self.conn.execute("""UPDATE canonical_products
            SET identity_status='verified',
                ingredients_raw='Water, Glycerin, Panthenol, Phenoxyethanol',
                ingredient_source='historical_store_scrape'
            WHERE id=?""", (self.cid,))
        self.assertEqual(eligible_waterfall(self.conn)['eligible_ids'], [self.cid])
        self.conn.execute("UPDATE canonical_products SET ingredient_source='store_page_tab' WHERE id=?", (self.cid,))
        self.assertEqual(eligible_waterfall(self.conn)['eligible'], 0)

    def test_two_agreeing_stores_can_be_explicitly_certified(self):
        for i,store in enumerate(('store_one','store_two'),1):
            self.conn.execute("INSERT INTO stores(id,name,domain) VALUES (?,?,?)",(store,store,store+'.test'))
            self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
                VALUES (?,?,?,?,?,'Moisturizer',?,?)""",
                (i,store,str(i),self.title,'CeraVe','https://'+store+'.test/p',self.cid))
        self.assertEqual(certify_cross_store(self.conn,'admin',apply=False)['certifiable'],1)
        self.assertEqual(eligible_waterfall(self.conn,True)['eligible'],0)
        cert=certify_cross_store(self.conn,'admin',apply=True)
        self.assertEqual(cert['processed'],1)
        self.assertEqual(eligible_waterfall(self.conn,True)['eligible'],1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_identity_links WHERE identity_status='identity_verified'").fetchone()[0],2)

    def test_conflicting_legacy_variants_cannot_be_certified(self):
        for i,(store,title) in enumerate((('first',self.title),('second','CeraVe Moisturizing Lotion')),1):
            self.conn.execute("INSERT INTO stores(id,name,domain) VALUES (?,?,?)",(store,store,store+'.test'))
            self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
                VALUES (?,?,?,?,?,'Moisturizer',?,?)""",
                (i,store,str(i),title,'CeraVe','https://'+store+'.test/p',self.cid))
        self.assertEqual(certify_cross_store(self.conn,'admin',apply=False)['certifiable'],0)

    def test_conflicting_complete_declarations_prevent_certification(self):
        raw1='Water, Glycerin, Panthenol, Phenoxyethanol'
        raw2='Water, Glycerin, Niacinamide, Phenoxyethanol'
        for i,store in enumerate(('first','second'),1):
            self.conn.execute("INSERT INTO stores(id,name,domain) VALUES (?,?,?)",(store,store,store+'.test'))
            self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,
                canonical_product_id,ingredients_raw,ingredient_source)
                VALUES (?,?,?,?,?,'Moisturizer',?,?,?,'store_page_tab')""",
                (i,store,str(i),self.title,'CeraVe','https://'+store+'.test/p',self.cid,raw1 if i==1 else raw2))
        self.assertEqual(certify_cross_store(self.conn,'admin')['certifiable'],0)

    def test_external_lookups_require_verified_observation(self):
        self.conn.execute("UPDATE canonical_products SET identity_status='verified' WHERE id=?",(self.cid,))
        raw='Water, Glycerin, Panthenol, Phenoxyethanol'
        for source in ('openbeauty_api','skinsafe_high_match','langsearch_web_search','skinsafe_matched'):
            candidate=FormulationCandidate(self.cid,source,raw,raw.split(', '),completeness='full')
            self.assertFalse(FormulationRepository().publish_formulation(candidate,'test',conn=self.conn))
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
