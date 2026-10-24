import sqlite3
import unittest
from unittest.mock import patch
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.source_registry import get_source
from routers.workflows import discover_shopify,update_existing_shopify_offers
from security.admin import permission_for


class CatalogWorkflowSeparation(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id='sourcebeauty'")
        resolver=CanonicalIdentityResolver(self.conn)
        name='CeraVe Moisturizing Cream'
        self.cid=resolver.create_provisional(name,'CeraVe',classify_product_attributes(name,'CeraVe'),
            extract_identity(name,'CeraVe'),visible=True)
        self.conn.commit()

    def test_workflows_require_collection_permission(self):
        self.assertEqual(permission_for('/admin/workflows/products/discover','POST'),'collection')
        self.assertEqual(permission_for('/admin/workflows/offers/refresh','POST'),'collection')

    def test_product_discovery_dry_run_does_not_add_products(self):
        source={'id':444,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe',
                'handle':'foaming-cleanser','product_type':'Cleanser',
                'variants':[{'id':445,'price':'160','available':True}]}
        before=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        with patch('pipeline.harvester.fetch_shopify_page',return_value=[source]):
            report=discover_shopify(self.conn,'sourcebeauty',dry_run=True)
        self.assertEqual(report['fetched'],1)
        self.assertGreaterEqual(report['new_products_staged'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before)

    def test_product_discovery_stages_new_identity_without_running_inci(self):
        source={'id':444,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe',
                'handle':'foaming-cleanser','product_type':'Cleanser',
                'variants':[{'id':445,'price':'160','available':True}]}
        with patch('pipeline.harvester.fetch_shopify_page',return_value=[source]):
            report=discover_shopify(self.conn,'sourcebeauty',dry_run=False)
        self.assertGreaterEqual(report['new_products_staged'],1)
        staged=self.conn.execute("SELECT catalog_visible,identity_status,ingredients_raw FROM canonical_products WHERE canonical_title='CeraVe Foaming Cleanser'").fetchone()
        self.assertIsNotNone(staged)
        self.assertEqual(staged['catalog_visible'],0)
        self.assertIsNone(staged['ingredients_raw'])

    def test_offer_refresh_skips_unknown_and_updates_only_existing(self):
        self.conn.execute("""INSERT INTO product_offers(source_id,source_product_id,source_variant_id,canonical_product_id,
            product_url,title,price,compare_at_price,is_available,currency,sku)
            VALUES ('sourcebeauty','123','v1',?,'https://sourcebeauty.com/products/cream',
              'CeraVe Moisturizing Cream',100,110,1,'EGP','CREAM-50')""",(self.cid,))
        self.conn.commit()
        p={'id':123,'title':'CeraVe Moisturizing Cream','vendor':'CeraVe','variants':[
           {'id':'v1','price':'120','compare_at_price':'130','available':False,'sku':'CREAM-50'},
           {'id':'v2','price':'120','compare_at_price':'130','available':True}]}
        with patch('pipeline.harvester.fetch_shopify_page',return_value=[p]):
            preview=update_existing_shopify_offers(self.conn,'sourcebeauty',dry_run=True)
            self.assertEqual(preview['offers_updated'],1)
            self.assertEqual(self.conn.execute("SELECT price FROM product_offers WHERE source_variant_id='v1'").fetchone()[0],100)
            report=update_existing_shopify_offers(self.conn,'sourcebeauty',dry_run=False)
        self.assertEqual(report['offers_updated'],1)
        self.assertEqual(report['unknown_variants_skipped'],1)
        self.assertEqual(report['new_products_created'],0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_offers").fetchone()[0],1)
        row=self.conn.execute("SELECT price,is_available FROM product_offers WHERE source_variant_id='v1'").fetchone()
        self.assertEqual(tuple(row),(120,0))
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])

    def test_offer_refresh_cannot_move_changed_product_or_sku(self):
        self.conn.execute("""INSERT INTO product_offers(source_id,source_product_id,source_variant_id,canonical_product_id,
            product_url,title,price,compare_at_price,is_available,currency,sku)
            VALUES ('sourcebeauty','123','v1',?,'https://sourcebeauty.com/products/cream',
              'CeraVe Moisturizing Cream',100,110,1,'EGP','CREAM-50')""",(self.cid,))
        self.conn.commit()
        p={'id':123,'title':'CeraVe Moisturizing Lotion','vendor':'CeraVe','variants':[
           {'id':'v1','price':'120','available':False,'sku':'CREAM-50'}]}
        with patch('pipeline.harvester.fetch_shopify_page',return_value=[p]):
            report=update_existing_shopify_offers(self.conn,'sourcebeauty',dry_run=False)
        self.assertEqual(report['identity_mismatches_skipped'],1)
        self.assertEqual(self.conn.execute("SELECT price FROM product_offers WHERE source_variant_id='v1'").fetchone()[0],100)


if __name__=='__main__':
    unittest.main()
