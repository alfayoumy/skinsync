"""Cache freshness across committed CLI/worker writes, without API restarts."""
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from pipeline.db_manager import init_db
from pipeline.aggregation_service import process_source_observation
from pipeline.source_adapters.contracts import SourceProductObservation,SourceVariant
from services import catalog_service as catalog


class CatalogCacheRevisionTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.path=str(Path(folder.name)/'catalog.db')
        self.conn=sqlite3.connect(self.path);self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close);init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id='sourcebeauty'")
        obs=SourceProductObservation('sourcebeauty','retailer','cream','https://sourcebeauty.com/products/cream',
            'CeraVe Moisturizing Cream','CeraVe',variants=[SourceVariant('one',price=123,available=True)],market='EG')
        self.cid=process_source_observation(self.conn,obs)['results'][0]['canonical_product_id']
        self.conn.execute('UPDATE canonical_products SET catalog_visible=1 WHERE id=?',(self.cid,));self.conn.commit()
        old=(catalog.df_cache,catalog.SYNONYMS,catalog._catalog_revision)
        def restore():catalog.df_cache,catalog.SYNONYMS,catalog._catalog_revision=old
        self.addCleanup(restore)
        paths=patch.object(catalog,'DB_PATH',self.path);paths.start();self.addCleanup(paths.stop)
        synonyms=patch.object(catalog,'load_synonyms_from_db',return_value={});synonyms.start();self.addCleanup(synonyms.stop)
        catalog.df_cache=None;catalog._catalog_revision=None

    def test_committed_external_formula_changes_refresh_once_and_uncommitted_writes_are_not_visible(self):
        before=catalog.get_catalog_df()
        self.conn.execute("UPDATE canonical_products SET ingredients_raw='Water, Glycerin, Panthenol, Phenoxyethanol' WHERE id=?",(self.cid,))
        self.assertIs(catalog.get_catalog_df(),before)
        self.conn.commit()
        with patch.object(catalog,'load_catalog_from_db',wraps=catalog.load_catalog_from_db) as reload:
            after=catalog.get_catalog_df()
            self.assertIn('Panthenol',after.iloc[0]['ingredients'])
            self.assertIs(catalog.get_catalog_df(),after)
            self.assertEqual(reload.call_count,1)

    def test_queue_only_changes_do_not_rebuild_catalog_but_offer_stock_and_categories_do(self):
        before=catalog.get_catalog_df();self.assertEqual(int(before.iloc[0]['is_available']),1)
        self.conn.execute("INSERT INTO product_review_queue(case_key,kind,details,evidence_hash) VALUES('test','identity','{}','test')")
        self.conn.commit()
        self.assertIs(catalog.get_catalog_df(),before)
        self.conn.execute('UPDATE product_offers SET is_available=0 WHERE canonical_product_id=?',(self.cid,));self.conn.commit()
        self.assertEqual(int(catalog.get_catalog_df().iloc[0]['is_available']),0)
        self.conn.execute("UPDATE canonical_products SET category='Treatment' WHERE id=?",(self.cid,));self.conn.commit()
        self.assertEqual(catalog.get_catalog_df().iloc[0]['std_category'],'Treatment')

    def test_no_op_updates_do_not_invalidate_and_hiding_product_removes_it(self):
        before=catalog.get_catalog_df()
        self.conn.execute('UPDATE canonical_products SET category=category WHERE id=?',(self.cid,));self.conn.commit()
        self.assertIs(catalog.get_catalog_df(),before)
        self.conn.execute('UPDATE canonical_products SET catalog_visible=0 WHERE id=?',(self.cid,));self.conn.commit()
        self.assertTrue(catalog.get_catalog_df().empty)

    def test_revoked_source_link_removes_cached_offer_without_hiding_canonical(self):
        first=catalog.get_catalog_df()
        self.assertEqual(first.iloc[0]['price_low'],123)
        self.conn.execute("UPDATE product_identity_links SET status='rejected' WHERE canonical_product_id=?",(self.cid,))
        self.assertIs(catalog.get_catalog_df(),first)
        self.conn.commit()
        row=catalog.get_catalog_df().iloc[0]
        self.assertEqual(row['canonical_product_id'],self.cid)
        self.assertEqual(int(row['is_available']),0)
        self.assertEqual(row['price_low'],0)
        self.assertEqual(row['price_high'],0)
        self.assertEqual(row['url'],'')
        self.assertEqual(row['store_id'],'canonical')
        self.assertEqual(row['store_count'],0)
        self.conn.execute("UPDATE product_identity_links SET status='accepted' WHERE canonical_product_id=?",(self.cid,));self.conn.commit()
        self.assertEqual(catalog.get_catalog_df().iloc[0]['price_low'],123)

    def test_price_changes_refresh_from_trusted_offers_without_cached_aggregates(self):
        catalog.get_catalog_df()
        self.conn.execute('UPDATE product_offers SET price=333 WHERE canonical_product_id=?',(self.cid,));self.conn.commit()
        row=catalog.get_catalog_df().iloc[0]
        self.assertEqual(row['price_low'],333)
        self.assertEqual(row['min_price'],333)
        self.assertEqual(self.conn.execute('SELECT min_price FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],123)
        self.conn.execute("UPDATE product_offers SET currency='USD' WHERE canonical_product_id=?",(self.cid,));self.conn.commit()
        self.assertEqual(catalog.get_catalog_df().iloc[0]['price_low'],0)

    def test_changed_offer_source_identity_cannot_borrow_old_link(self):
        catalog.get_catalog_df()
        self.conn.execute("UPDATE product_offers SET source_product_id='different-product' WHERE canonical_product_id=?",(self.cid,));self.conn.commit()
        row=catalog.get_catalog_df().iloc[0]
        self.assertEqual(row['price_low'],0)
        self.assertEqual(row['url'],'')

    def test_old_offer_scopes_are_backfilled_from_exact_retained_variant(self):
        import json
        from pipeline.identity_scope import identity_link_product_id,backfill_offer_identity_scopes
        attrs={'shade':'rose'};scope=identity_link_product_id('cream',attrs)
        self.conn.execute("UPDATE product_identity_links SET status='rejected'")
        self.conn.execute("""INSERT INTO product_identity_links(source_id,source_product_id,canonical_product_id,status,method,evidence,identity_status)
            VALUES('sourcebeauty',?,?,'accepted','reviewed','{}','identity_verified')""",(scope,self.cid))
        payload=json.loads(self.conn.execute('SELECT attributes FROM source_product_variants').fetchone()[0]);payload['attributes']=attrs
        self.conn.execute('UPDATE source_product_variants SET attributes=?',(json.dumps(payload),))
        self.conn.execute('UPDATE product_offers SET identity_link_product_id=NULL');self.conn.commit()
        self.assertEqual(catalog.get_catalog_df().iloc[0]['price_low'],0)
        result=backfill_offer_identity_scopes(self.conn);self.conn.commit()
        self.assertEqual(result['scoped'],1)
        self.assertEqual(catalog.get_catalog_df().iloc[0]['price_low'],123)
        self.assertEqual(self.conn.execute('SELECT identity_link_product_id FROM product_offers').fetchone()[0],scope)


if __name__=='__main__':unittest.main()
