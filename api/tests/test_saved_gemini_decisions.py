import json
import sqlite3
import unittest
from unittest.mock import patch
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.aggregation_service import queue_review
from pipeline.product_identity_gemini import queue_cases
from pipeline.saved_gemini_decisions import apply_saved_decisions
from pipeline.brand_registry import approve_brand_alias


class SavedGeminiDecisionTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)

    def make_case(self,name,brand,listings,verdict,confidence):
        approve_brand_alias(self.conn,brand,brand,'test')
        resolver=CanonicalIdentityResolver(self.conn)
        cid=resolver.create_provisional(name,brand,classify_product_attributes(name,brand),
            extract_identity(name,brand),visible=False)
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy' WHERE id=?",(cid,))
        details={'title':name,'brand':brand,'canonical_ids':[cid],'offers':[]}
        for i,(shop,title) in enumerate(listings):
            pid=10000+cid*100+i
            self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
                VALUES (?,?,?,?,?,'Treatment',?,?)""",
                (pid,shop,str(pid),title,brand,'https://'+shop+'.example/'+str(pid),cid))
            details['offers'].append({'store_id':shop,'product_id':pid,'title':title,'brand':brand})
        rid=queue_review(self.conn,'identity',listings[0][0],str(details['offers'][0]['product_id']),cid,details)
        queue_cases(self.conn,review_ids=[rid],queued_by='test')
        self.conn.execute("""UPDATE identity_gemini_queue
            SET status='needs_manual',verdict=?,confidence=?,explanation=?,
                model_name='test-model' WHERE review_id=?""",
            (verdict,confidence,'Stored Gemini reason | Old 0.97 cutoff rejected result.',rid))
        self.conn.commit()
        return cid,rid

    def test_replay_95_percent_same_product_without_model_calls(self):
        name='Avène Hyaluron Activ B3 Triple Correction Eye Care Cream'
        cid,rid=self.make_case(name,'Avène',[
            ('bashrety',name),
            ('feel22','Avene Hyaluron Activ B3 Triple Correction Eye Care Cream')
        ],'same_product',.95)
        with patch('pipeline.product_identity_gemini.call_gemini_identity_batch',side_effect=AssertionError('model call prohibited')):
            preview=apply_saved_decisions(self.conn,dry_run=True)
            self.assertEqual(preview['automatically_verified'],1)
            self.assertTrue(preview['no_gemini_calls'])
            self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],'legacy')
            applied=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(applied['automatically_verified'],1)
        self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],'verified')
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(rid,)).fetchone()[0],'confirmed')
        self.assertEqual(self.conn.execute('SELECT status FROM identity_gemini_queue WHERE review_id=?',(rid,)).fetchone()[0],'auto_resolved')
        self.assertEqual(apply_saved_decisions(self.conn,dry_run=False)['stored_decisions_checked'],0)

    def test_90_percent_same_product_can_apply_when_gates_pass(self):
        name='CeraVe Moisturizing Cream'
        cid,rid=self.make_case(name,'CeraVe',[
            ('sourcebeauty',name),('feel22',name)],'same_product',.90)
        applied=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(applied['automatically_verified'],1)

    def test_confident_different_product_splits_explicit_nuxe_variants(self):
        name='NUXE Huile Prodigieuse Dry Oil'
        cid,rid=self.make_case(name,'NUXE',[
            ('bashrety',name),
            ('feel22','NUXE Huile Prodigieuse Florale Dry Oil'),
            ('bloompharmacy','NUXE Huile Prodigieuse Golden Dry Oil')
        ],'different_product',.98)
        preview=apply_saved_decisions(self.conn,dry_run=True)
        self.assertEqual(preview['automatically_split'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
        done=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(done['automatically_split'],1)
        records=self.conn.execute("SELECT canonical_product_id FROM products WHERE title LIKE ?",('%Prodigieuse%',)).fetchall()
        self.assertEqual(len({r[0] for r in records}),3)
        new=self.conn.execute("SELECT identity_status,catalog_visible FROM canonical_products WHERE id!=?",(cid,)).fetchall()
        self.assertTrue(all(r[0]=='provisional' and r[1]==0 for r in new))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_identity_constraints').fetchone()[0],2)
        self.assertEqual(apply_saved_decisions(self.conn,dry_run=False)['stored_decisions_checked'],0)

    def test_confident_negative_without_safe_partition_stays_structural_review(self):
        name='NUXE Generic Body Oil'
        cid,rid=self.make_case(name,'NUXE',[
            ('store1','NUXE Generic Body Oil'),
            ('store2','NUXE Unknown Different Product')
        ],'different_product',1.0)
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['automatically_split'],0)
        self.assertEqual(result['still_needs_review'],1)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(rid,)).fetchone()[0],'pending')

    def test_stale_case_cannot_apply_saved_confidence(self):
        name='CeraVe Moisturizing Cream'
        cid,rid=self.make_case(name,'CeraVe',[('a',name),('b',name)],'same_product',.95)
        self.conn.execute("UPDATE product_review_queue SET evidence_hash='changed' WHERE id=?",(rid,))
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['automatically_verified'],0)
        self.assertEqual(result['still_needs_review'],1)


if __name__=='__main__':
    unittest.main()
