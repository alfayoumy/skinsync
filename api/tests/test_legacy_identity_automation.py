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
        self.assertEqual(self.conn.execute("SELECT identity_status FROM canonical_products WHERE id=?",(self.cid,)).fetchone()[0],'verified')
        self.assertEqual(self.conn.execute("SELECT operation FROM canonical_identity_history WHERE canonical_product_id=?",(self.cid,)).fetchone()[0],'auto_verify')

    def test_different_formula_vehicle_is_never_automatically_verified(self):
        self.offer(1,self.name)
        self.offer(2,'CeraVe Moisturizing Lotion')
        result=reconcile_legacy_exact(self.conn,dry_run=False)
        self.assertEqual(result['auto_verified'],0)
        self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],'legacy')

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
