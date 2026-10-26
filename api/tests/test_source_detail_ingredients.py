import json
import sqlite3
import unittest
from pipeline.db_manager import init_db
from pipeline.aggregation_service import process_source_observation
from pipeline.source_adapters.contracts import SourceProductObservation,SourceVariant
from pipeline.catalog_repair import verify_corroborated_identities
from pipeline.source_detail_ingredients import retain_detail_ingredients

RAW='Water, Glycerin, Panthenol, Sodium Hyaluronate, Phenoxyethanol'
URL='https://sourcebeauty.com/products/cream'


class SourceDetailIngredientTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close);init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id IN ('sourcebeauty','feel22')")
        for source,domain in [('sourcebeauty','sourcebeauty.com'),('feel22','eg.feel22.com')]:
            obs=SourceProductObservation(source,'retailer','cream','https://'+domain+'/products/cream',
                'CeraVe Moisturizing Cream 50ml','CeraVe',variants=[SourceVariant('50ml','50ml',price=123,available=True)],
                market='EG',raw_payload={'id':'cream','vendor':'CeraVe'})
            result=process_source_observation(self.conn,obs)['results'][0]
            self.cid=result['canonical_product_id']
            if source=='sourcebeauty':self.oid=result['observation_id']
        verify_corroborated_identities(self.conn)

    def page(self,brand='CeraVe',complete=True,raw=RAW):
        meta=json.dumps({'ok':True,'content_ok':True,'content_complete':complete,'url':URL})
        return {'url':URL,'text':'[meta] '+meta+'\n# CeraVe Moisturizing Cream 50ml\nBrand:'+brand+
            '\nEGP 9999.00\n### Description\nA daily cream.\n### Full Ingredients\n'+raw+'\n### Additional Information\n50ml'}

    def test_full_detail_declaration_publishes_without_matching_or_price_updates(self):
        offers=[tuple(r) for r in self.conn.execute('SELECT id,canonical_product_id,price,is_available FROM product_offers ORDER BY id')]
        links=[tuple(r) for r in self.conn.execute('SELECT * FROM product_identity_links ORDER BY id')]
        result=retain_detail_ingredients(self.conn,self.oid,self.page(),approve=True)
        self.assertEqual(result['status'],'published')
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],RAW)
        self.assertIsNone(self.conn.execute('SELECT ingredient_text FROM source_product_observations WHERE id=?',(self.oid,)).fetchone()[0])
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT id,canonical_product_id,price,is_available FROM product_offers ORDER BY id')],offers)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM product_identity_links ORDER BY id')],links)
        payload=json.loads(self.conn.execute('SELECT raw_payload FROM source_product_observations WHERE id=?',(result['observation_id'],)).fetchone()[0])
        self.assertEqual(payload['detail_ingredient_evidence']['text'],self.page()['text'])
        self.assertEqual(self.conn.execute('SELECT SUM(publish_enabled) FROM sources').fetchone()[0],0)

    def test_plain_metadata_and_delivery_text_end_the_ingredient_section(self):
        for boundary in ('Brand: CeraVe','Size: 50ml','Delivery Information','Payment Options','Returns: Read our policy'):
            with self.subTest(boundary=boundary):
                from pipeline.source_detail_ingredients import confirmed_detail_section
                from pipeline.saved_identity_rematch import _from_frozen_observation
                page=self.page()
                page['text']=page['text'].replace('\n### Additional Information\n50ml',
                    '\n'+boundary+'\nWe deliver daily, orders ship tomorrow, Cairo and Giza.')
                section=confirmed_detail_section(_from_frozen_observation(self.conn,self.oid,{}),page,self.conn)
                self.assertEqual(section.text,RAW)
                self.assertTrue(section.order_verified)

    def test_wrong_brand_incomplete_page_and_stale_observation_are_not_ingested(self):
        before=self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0]
        partial=self.page(complete=False);partial['text']=partial['text'].split('### Additional Information')[0]
        for page in [self.page(brand='Different Brand'),partial,{**self.page(),'url':URL+'-wrong'}]:
            with self.assertRaises(ValueError):retain_detail_ingredients(self.conn,self.oid,page,approve=True)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],before)
        retain_detail_ingredients(self.conn,self.oid,self.page())
        with self.assertRaisesRegex(ValueError,'changed'):retain_detail_ingredients(self.conn,self.oid,self.page())

    def test_duplicate_seo_heading_does_not_hide_the_actual_product_section(self):
        page=self.page();page['text']=page['text'].replace('\n# CeraVe Moisturizing Cream 50ml',
            '\n# CeraVe Moisturizing Cream 50ml\nPage summary\n# CeraVe Moisturizing Cream 50ml',1)
        self.assertEqual(retain_detail_ingredients(self.conn,self.oid,page,approve=True)['status'],'published')

    def test_conflicting_formula_is_evidence_only_and_preserves_current_formula(self):
        old='Water, Squalane, Glycerin, Phenoxyethanol'
        self.conn.execute('UPDATE canonical_products SET ingredients_raw=? WHERE id=?',(old,self.cid))
        result=retain_detail_ingredients(self.conn,self.oid,self.page(),approve=True)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],old)

    def test_current_non_skincare_retailer_type_blocks_ingredient_publication(self):
        self.conn.execute("UPDATE source_product_observations SET raw_category='Styling Cream' WHERE id=?",(self.oid,))
        with self.assertRaisesRegex(ValueError,'product type'):
            retain_detail_ingredients(self.conn,self.oid,self.page(),approve=True)

    def test_short_whats_in_it_active_summary_stays_partial_evidence(self):
        page=self.page(raw='Jojoba Oil, Vitamin E, Hyaluronic Acid, Collagen')
        page['text']=page['text'].replace('### Full Ingredients',"### What's in it")
        result=retain_detail_ingredients(self.conn,self.oid,page,approve=True)
        self.assertEqual(result['status'],'evidence_only')
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])
        evidence=self.conn.execute('SELECT completeness,order_verified FROM inci_audit_evidence WHERE id=?',(result['evidence_id'],)).fetchone()
        self.assertEqual(tuple(evidence),('partial',0))

    def test_partial_page_can_supply_an_explicitly_closed_full_declaration(self):
        result=retain_detail_ingredients(self.conn,self.oid,self.page(complete=False),approve=True)
        self.assertEqual(result['status'],'published')
        flags=json.loads(self.conn.execute('SELECT validation_flags FROM inci_audit_evidence WHERE id=?',(result['evidence_id'],)).fetchone()[0])
        self.assertFalse(flags['source_page_complete'])
        self.assertEqual(flags['evidence_scope'],'closed_product_declaration')

    def test_long_active_summary_does_not_become_full_merely_by_having_many_tokens(self):
        raw='Alpha Arbutin, Kojic Acid, Licorice Extract, Vitamin B3, Vitamin C, Collagen, Caviar Extract, Lactic Acid, Glycolic Acid, Hyaluronic Acid, Papaya Extract, Castor Oil, Retinol'
        page=self.page(raw=raw);page['text']=page['text'].replace('### Full Ingredients',"### What's in it")
        result=retain_detail_ingredients(self.conn,self.oid,page,approve=True)
        self.assertEqual(result['status'],'evidence_only')
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])

    def test_trailing_share_widget_is_not_an_ingredient_but_page_is_retained(self):
        page=self.page(raw=RAW+', Share:')
        result=retain_detail_ingredients(self.conn,self.oid,page,approve=True)
        self.assertEqual(result['status'],'published')
        raw=self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0]
        self.assertEqual(raw,RAW)
        payload=json.loads(self.conn.execute('SELECT raw_payload FROM source_product_observations WHERE id=?',(result['observation_id'],)).fetchone()[0])
        self.assertIn('Share:',payload['detail_ingredient_evidence']['text'])


if __name__=='__main__':unittest.main()
