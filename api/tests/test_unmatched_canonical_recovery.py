"""Previously unanchored new products must be created, not sent to empty reviews."""
import json
import sqlite3
import unittest

from pipeline.db_manager import init_db
from pipeline.source_registry import get_source
from pipeline.source_adapters import ShopifyAdapter
from pipeline.aggregation_service import (
    process_source_observation,queue_review,save_observation
)
from pipeline.brand_registry import resolve_brand
from pipeline.canonical_identity_resolver import extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.unmatched_canonical_recovery import (
    preview_unmatched_recovery,recover_unmatched_canonicals
)
from security.admin import permission_for


class AutoCreateUnmatchedCanonicals(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False)
        self.addCleanup(self.conn.close)
        self.sid='sourcebeauty'
        self.source=get_source(self.conn,self.sid)
        self.conn.execute("INSERT OR IGNORE INTO stores(id,name,domain) VALUES(?,?,?)",
                          (self.sid,'Source Beauty',self.source['domain']))
        self.conn.commit()

    def obs(self,pid=934567,title='Novalab Radiance Serum 30ml',brand='Novalab'):
        raw={'id':pid,'handle':'novalab-'+str(pid),'title':title,'vendor':brand,
             'product_type':'Serum','body_html':'<p>Topical serum.</p>',
             'variants':[{'id':pid*10,'title':'Default Title',
                          'price':'110.00','available':True,
                          'sku':'SKU-'+str(pid)}],
             'images':[{'src':'https://example.org/a.jpg'}]}
        return ShopifyAdapter(self.source).parse(
            {'product':raw},'https://sourcebeauty.com/products/novalab-'+str(pid))[0]

    def old_unanchored(self,obs,kind='identity',method='unknown_brand',status='ambiguous'):
        classification=classify_product_attributes(
            obs.raw_title,obs.raw_brand,obs.raw_category,obs.description)
        identity=extract_identity(obs.raw_title,obs.raw_brand,obs.market,obs.identity_attributes)
        oid=save_observation(self.conn,obs,identity,classification)
        decision={'status':status,'method':method,'candidates':[]}
        queue_review(self.conn,kind,self.sid,obs.source_product_id,None,{
            'observation_id':oid,'title':obs.raw_title,'raw_brand':obs.raw_brand,
            'identity':decision,'classification':classification.to_dict()})
        self.conn.execute("""INSERT INTO products(store_id,store_product_id,title,brand,category,product_url)
            VALUES(?,?,?,?,?,?)""",(self.sid,obs.source_product_id,obs.raw_title,
                                      obs.raw_brand,'Serum',obs.source_url))
        self.conn.commit()
        return oid

    def test_new_unknown_brand_gets_provisional_canonical_and_offers(self):
        result=process_source_observation(self.conn,self.obs())
        self.conn.commit()
        item=result['results'][0]
        self.assertEqual(item['status'],'provisional')
        cid=item['canonical_product_id']
        self.assertIsNotNone(cid)
        product=self.conn.execute(
            'SELECT identity_status,brand_id,is_skincare,ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()
        self.assertEqual(product['identity_status'],'provisional')
        self.assertEqual(product['is_skincare'],1)
        self.assertIsNone(product['ingredients_raw'])
        brand=self.conn.execute('SELECT status FROM brands WHERE id=?',(product['brand_id'],)).fetchone()[0]
        self.assertEqual(brand,'observed_unverified')
        self.assertFalse(resolve_brand('Novalab',self.conn)['verified'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers WHERE canonical_product_id=?',(cid,)).fetchone()[0],1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_review_queue WHERE kind='identity' AND status='pending'").fetchone()[0],0)

    def test_repeat_same_product_reuses_existing_canonical(self):
        first=process_source_observation(self.conn,self.obs())
        second=process_source_observation(self.conn,self.obs(pid=934568))
        self.assertEqual(first['results'][0]['canonical_product_id'],
                         second['results'][0]['canonical_product_id'])

    def test_missing_vendor_keeps_real_identity_exception(self):
        result=process_source_observation(self.conn,self.obs(934566,brand=''))
        self.assertEqual(result['results'][0]['status'],'review')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],0)

    def test_saved_review_recovers_without_source_refetch_or_gemini(self):
        oid=self.old_unanchored(self.obs())
        preview=preview_unmatched_recovery(self.conn)
        self.assertEqual(preview['eligible_observations'],1)
        self.assertTrue(preview['can_start'],preview)
        result=recover_unmatched_canonicals(self.conn,10)
        self.conn.commit()
        self.assertEqual(result['recovered_observations'],1,result)
        self.assertEqual(result['provider_calls'],0)
        cid=self.conn.execute('SELECT canonical_product_id FROM products WHERE store_product_id=?',
                              ('934567',)).fetchone()[0]
        self.assertIsNotNone(cid)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers WHERE legacy_product_id IS NOT NULL').fetchone()[0],1)
        case=self.conn.execute('SELECT status,decision FROM product_review_queue WHERE kind=?',('identity',)).fetchone()
        self.assertEqual(case['status'],'confirmed')
        self.assertEqual(json.loads(case['decision'])['canonical_ids'],[cid])
        self.assertEqual(preview_unmatched_recovery(self.conn)['eligible_observations'],0)
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()[0])

    def test_real_conflict_is_not_recovered(self):
        self.old_unanchored(self.obs(),method='gtin_identity_conflict')
        self.assertEqual(preview_unmatched_recovery(self.conn)['eligible_observations'],0)
        stats=recover_unmatched_canonicals(self.conn)
        self.assertEqual(stats['total_selected'],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],0)

    def test_category_uncertainty_does_not_block_canonical_creation(self):
        sample=self.obs(934570,title='Novalab Mystery Balm 30ml')
        # This title does not imply a specific approved skincare category;
        # its separate taxonomy uncertainty must remain visible.
        classification=classify_product_attributes(sample.raw_title,sample.raw_brand,
                        sample.raw_category,sample.description)
        self.assertEqual(classification.classification_status,'review')
        result=process_source_observation(self.conn,sample)
        self.assertEqual(result['results'][0]['status'],'provisional')
        cid=result['results'][0]['canonical_product_id']
        self.assertIsNotNone(cid)
        self.assertEqual(self.conn.execute("""SELECT COUNT(*) FROM product_review_queue
            WHERE kind='category' AND canonical_product_id=? AND status='pending'""",(cid,)).fetchone()[0],1)

    def test_brand_observed_alias_can_be_corrected_without_implicit_product_merge(self):
        from pipeline.brand_registry import approve_brand_alias
        first=process_source_observation(self.conn,self.obs())
        cid=first['results'][0]['canonical_product_id']
        approved=approve_brand_alias(self.conn,'Novalab','CeraVe','reviewer')
        self.assertIsNotNone(approved)
        self.assertEqual(resolve_brand('Novalab',self.conn)['name'],'CeraVe')
        self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',
            (cid,)).fetchone()[0],'provisional')

    def test_no_comparison_recovery_requires_identity_permission(self):
        self.assertEqual(permission_for('/admin/workflows/products/recover-unmatched','POST'),'identity')
        self.assertEqual(permission_for('/admin/workflows/products/recover-unmatched/preview','GET'),'read')


if __name__=='__main__':
    unittest.main()
