"""Unreviewed matching pointers must not become same-product price offers."""
import sqlite3
import unittest
from pipeline.db_manager import init_db
from services.price_service import get_canonical_price_comparison, get_product_price_comparison, get_price_arbitrage_deals


class PriceIdentityTrustTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close);init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES('sourcebeauty','Source Beauty','sourcebeauty.com')")
        self.cid=self.conn.execute("""INSERT INTO canonical_products(canonical_title,brand,category,normalized_key,
            is_skincare,identity_status,catalog_visible,min_price,max_price)
            VALUES('CeraVe Cream','CeraVe','Moisturizer','price-trust',1,'verified',1,1,9999)""").lastrowid
        self.ids=[]
        for pid,price in [('verified',200),('unverified',1)]:
            self.ids.append(self.conn.execute("""INSERT INTO products(store_id,store_product_id,title,brand,category,
                canonical_product_id,price,is_available,product_url) VALUES('sourcebeauty',?,'Cream','CeraVe','Moisturizer',?,?,1,?)""",
                (pid,self.cid,price,'https://sourcebeauty.com/products/'+pid)).lastrowid)
        self.conn.execute("""INSERT INTO product_identity_links(source_id,source_product_id,canonical_product_id,
            status,method,identity_status,evidence) VALUES('sourcebeauty','verified',?,'accepted','reviewed','identity_verified','{}')""",(self.cid,))

    def test_unverified_legacy_pointer_does_not_supply_offer_or_resolve_listing(self):
        result=get_canonical_price_comparison(self.cid,self.conn)
        self.assertEqual([r['product_id'] for r in result['offers']],[self.ids[0]])
        self.assertEqual(result['min_price'],200)
        self.assertIsNone(get_product_price_comparison(self.ids[1],self.conn))
        self.assertEqual(get_product_price_comparison(self.ids[0],self.conn)['min_price'],200)

    def test_unverified_variant_cannot_replace_verified_legacy_offer(self):
        self.conn.execute("""INSERT INTO product_offers(source_id,source_product_id,source_variant_id,
            canonical_product_id,legacy_product_id,title,price,currency,is_available,product_url)
            VALUES('sourcebeauty','wrong-product','small',?,?,'Wrong cream',1,'EGP',1,'https://sourcebeauty.com/products/wrong')""",(self.cid,self.ids[0]))
        result=get_canonical_price_comparison(self.cid,self.conn)
        self.assertEqual(result['listing_count'],1)
        self.assertEqual(result['offers'][0]['offer_namespace'],'legacy')
        self.assertEqual(result['min_price'],200)

    def test_revoked_links_do_not_leak_stale_canonical_price_aggregates(self):
        self.conn.execute("UPDATE product_identity_links SET status='rejected'")
        result=get_canonical_price_comparison(self.cid,self.conn)
        self.assertEqual(result['offers'],[])
        self.assertIsNone(result['best_deal'])
        self.assertEqual(result['min_price'],0)
        self.assertEqual(result['max_price'],0)

    def test_unverified_cheapest_listing_does_not_create_deal(self):
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES('feel22','Feel22','feel22.com')")
        self.conn.execute("""INSERT INTO products(store_id,store_product_id,title,brand,category,
            canonical_product_id,price,is_available,product_url)
            VALUES('feel22','candidate','Cream','CeraVe','Moisturizer',?,1,1,'https://feel22.com/products/cream')""",(self.cid,))
        self.assertEqual(get_price_arbitrage_deals(conn=self.conn),[])
        self.conn.execute("""INSERT INTO product_identity_links(source_id,source_product_id,canonical_product_id,
            status,method,identity_status,evidence) VALUES('feel22','candidate',?,'accepted','reviewed','identity_verified','{}')""",(self.cid,))
        deals=get_price_arbitrage_deals(conn=self.conn)
        self.assertEqual(len(deals),1)
        self.assertEqual(deals[0]['min_price'],1)
        self.assertEqual(deals[0]['max_price'],200)
        self.assertEqual(deals[0]['savings_egp'],199)
        self.conn.execute("UPDATE product_identity_links SET status='rejected' WHERE source_id='feel22'")
        self.assertEqual(get_price_arbitrage_deals(conn=self.conn),[])

    def test_exact_scoped_approval_supplies_offer_but_not_a_sibling_shade(self):
        from pipeline.identity_scope import identity_link_product_id
        self.conn.execute("""INSERT INTO canonical_products(canonical_title,brand,category,normalized_key,is_skincare,identity_status,catalog_visible)
            VALUES('CeraVe Other Shade','CeraVe','Moisturizer','other-scope',1,'verified',1)""")
        other=self.conn.execute("SELECT id FROM canonical_products WHERE normalized_key='other-scope'").fetchone()[0]
        for variant,price,target in [('rose',175,self.cid),('berry',1,other)]:
            scope=identity_link_product_id('shade-page',{'shade':variant})
            self.conn.execute("""INSERT INTO product_identity_links(source_id,source_product_id,canonical_product_id,
                status,method,identity_status,evidence) VALUES('sourcebeauty',?,?,'accepted','reviewed','identity_verified','{}')""",(scope,target))
            self.conn.execute("""INSERT INTO product_offers(source_id,source_product_id,source_variant_id,identity_link_product_id,
                canonical_product_id,title,price,currency,is_available,product_url)
                VALUES('sourcebeauty','shade-page',?,?,?,'Shade cream',?,'EGP',1,'https://sourcebeauty.com/products/shades')""",(variant,scope,self.cid,price))
        result=get_canonical_price_comparison(self.cid,self.conn)
        variants=[r for r in result['offers'] if r['offer_namespace']=='variant']
        self.assertEqual([r['source_variant_id'] for r in variants],['rose'])
        self.assertEqual(result['min_price'],175)
        self.assertNotIn(1,[r['price'] for r in result['offers']])
