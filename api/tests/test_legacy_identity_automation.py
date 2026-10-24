import sqlite3
import unittest
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.legacy_identity_reconcile import reconcile_legacy_exact
from pipeline.aggregation_service import queue_review
from security.admin import permission_for


class LegacyIdentityAutomation(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        r=CanonicalIdentityResolver(self.conn)
        self.name='CeraVe Moisturizing Cream'
        self.cid=r.create_provisional(self.name,'CeraVe',
            classify_product_attributes(self.name,'CeraVe'),extract_identity(self.name,'CeraVe'),visible=False)
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy',catalog_visible=1 WHERE id=?",(self.cid,))
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES ('sourcebeauty','Source Beauty','sourcebeauty.com')")
        self.conn.commit()

    def offer(self,id,title=None):
        self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,
            canonical_product_id) VALUES (?,'sourcebeauty',?,?, 'CeraVe','Moisturizer',?,?)""",
            (id,str(id),title or self.name,'https://sourcebeauty.com/products/'+str(id),self.cid))

    def test_identity_scope_is_required(self):
        self.assertEqual(permission_for('/admin/workflows/products/verify-existing','POST'),'identity')

    def test_single_unambiguous_source_does_not_require_human_approval(self):
        self.offer(1)
        before=self.conn.execute("SELECT identity_status FROM canonical_products WHERE id=?",(self.cid,)).fetchone()[0]
        preview=reconcile_legacy_exact(self.conn,dry_run=True)
        self.assertEqual(preview['safe_exact_matches_found'],1)
        self.assertEqual(self.conn.execute("SELECT identity_status FROM canonical_products WHERE id=?",(self.cid,)).fetchone()[0],before)
        result=reconcile_legacy_exact(self.conn,dry_run=False)
        self.assertEqual(result['auto_verified'],1)
        self.assertEqual(result['remaining_safe_matches'],0)
        self.assertEqual(reconcile_legacy_exact(self.conn,dry_run=True)['safe_exact_matches_found'],0)
        self.assertEqual(self.conn.execute("SELECT identity_status FROM canonical_products WHERE id=?",(self.cid,)).fetchone()[0],'verified')
        self.assertEqual(self.conn.execute("SELECT operation FROM canonical_identity_history WHERE canonical_product_id=?",(self.cid,)).fetchone()[0],'auto_verify')

    def test_different_formula_vehicle_is_never_automatically_verified(self):
        self.offer(1,self.name)
        self.offer(2,'CeraVe Moisturizing Lotion')
        result=reconcile_legacy_exact(self.conn,dry_run=False)
        self.assertEqual(result['auto_verified'],0)
        self.assertEqual(result['review_cases_prepared'],1)
        review=self.conn.execute("SELECT kind,status,details FROM product_review_queue WHERE canonical_product_id=?",(self.cid,)).fetchone()
        self.assertEqual((review['kind'],review['status']),('identity','pending'))
        self.assertIn('nonidentical_or_incomplete_variants',review['details'])
        self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],'legacy')

    def test_preview_and_apply_report_distinct_batches_not_repeated_first_500(self):
        self.offer(1)
        resolver=CanonicalIdentityResolver(self.conn)
        other_name='CeraVe Hydrating Cleanser'
        second=resolver.create_provisional(other_name,'CeraVe',
            classify_product_attributes(other_name,'CeraVe'),
            extract_identity(other_name,'CeraVe'),visible=False)
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy' WHERE id=?",(second,))
        self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,
            canonical_product_id) VALUES (2,'sourcebeauty','2',?,'CeraVe','Cleanser','https://sourcebeauty.com/products/2',?)""",
            (other_name,second))
        preview=reconcile_legacy_exact(self.conn,dry_run=True,max_verified=1)
        self.assertEqual(preview['safe_exact_matches_found'],2)
        self.assertEqual(preview['remaining_safe_matches'],2)
        first=reconcile_legacy_exact(self.conn,dry_run=False,max_verified=1)
        self.assertEqual(first['auto_verified'],1)
        self.assertEqual(first['remaining_safe_matches'],1)
        nxt=reconcile_legacy_exact(self.conn,dry_run=True,max_verified=1)
        self.assertEqual(nxt['safe_exact_matches_found'],1)
        last=reconcile_legacy_exact(self.conn,dry_run=False,max_verified=1)
        self.assertEqual(last['auto_verified'],1)
        self.assertEqual(reconcile_legacy_exact(self.conn)['safe_exact_matches_found'],0)

    def test_prepare_reviews_without_verifying_and_without_duplicates(self):
        self.offer(1,'CeraVe Moisturizing Lotion')
        first=reconcile_legacy_exact(self.conn,dry_run=False,max_verified=0)
        self.assertEqual(first['auto_verified'],0)
        self.assertEqual(first['review_cases_prepared'],1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE status='pending'").fetchone()[0],1)
        again=reconcile_legacy_exact(self.conn,dry_run=False,max_verified=0)
        self.assertEqual(again['review_cases_prepared'],0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_review_queue").fetchone()[0],1)

    def test_duplicate_canonical_name_and_pending_review_block_automatic_verification(self):
        self.offer(1)
        r=CanonicalIdentityResolver(self.conn)
        other=r.create_provisional(self.name,'CeraVe',classify_product_attributes(self.name,'CeraVe'),
            extract_identity(self.name,'CeraVe'),visible=False)
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy' WHERE id=?",(other,))
        self.assertEqual(reconcile_legacy_exact(self.conn)['safe_exact_matches_found'],0)
        self.conn.execute("UPDATE canonical_products SET canonical_title='CeraVe Something Else' WHERE id=?",(other,))
        queue_review(self.conn,'identity','sourcebeauty','1',self.cid,{'conflict':'variant mismatch'})
        self.assertEqual(reconcile_legacy_exact(self.conn)['safe_exact_matches_found'],0)


if __name__=='__main__':
    unittest.main()
