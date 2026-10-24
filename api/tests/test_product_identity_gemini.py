import json
import sqlite3
import unittest
from unittest.mock import patch
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.aggregation_service import queue_review
from pipeline.product_identity_gemini import (
    queue_cases,queue_counts,can_auto_resolve,run_identity_gemini_queue,equivalent_brand
)
from security.admin import permission_for


class NoCloseConnection:
    def __init__(self,conn):self.conn=conn
    def close(self):pass
    def __getattr__(self,name):return getattr(self.conn,name)


class GeminiIdentityReviewTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        self.canonical_title='Avène Hyaluron Activ B3 Triple Correction Eye Care Cream'
        resolver=CanonicalIdentityResolver(self.conn)
        self.cid=resolver.create_provisional(self.canonical_title,'Avène',
            classify_product_attributes(self.canonical_title,'Avène'),
            extract_identity(self.canonical_title,'Avène'),visible=False)
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy' WHERE id=?",(self.cid,))
        self.listings=[
          (14,'bashrety','Eau Thermale Avene','Avène Hyaluron Activ B3  Triple correction eye cream'),
          (10147,'feel22','Avene','Avene Hyaluron Activ B3 Triple Correction Eye Care Cream'),
          (14335,'bloompharmacy','Avene','Avene Hyaluron Activ B3 Triple Action Eye Cream - 15ml')
        ]
        self.offer()
        self.conn.execute("INSERT INTO pipeline_tasks(task_id,task_type,status) VALUES ('gemini-test','product_identity_gemini','running')")
        self.conn.commit()

    def offer(self):
        for id,store,brand,title in self.listings:
            self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
                VALUES (?,?,?,?,?,'Eye Skincare',?,?)""",
                (id,store,str(id),title,brand,'https://'+store+'.example/'+str(id),self.cid))
        details={'origin':'legacy_identity_scan_v1','brand':'Avène','title':self.canonical_title,
            'canonical_ids':[self.cid],
            'offers':[{'product_id':id,'store_id':store,'brand':brand,'title':title}
                      for id,store,brand,title in self.listings]}
        self.review_id=queue_review(self.conn,'identity','bashrety','14',self.cid,details)

    def answer(self,confidence=.99,verdict='same_product'):
        return {'verdict':verdict,'confidence':confidence,'reason':'Same Hyaluron Activ B3 eye formula across retailer naming.','model_name':'test-mock'}

    def case(self):
        r=dict(self.conn.execute('SELECT * FROM product_review_queue WHERE id=?',(self.review_id,)).fetchone())
        r['details']=json.loads(r['details'])
        return r

    def test_curated_avene_brand_names_are_equivalent(self):
        self.assertTrue(equivalent_brand(self.conn,'Eau Thermale Avene','Avène'))
        self.assertFalse(equivalent_brand(self.conn,'Avène','CeraVe'))

    def test_multiretailer_renaming_is_eligible_for_gemini_approval(self):
        ok,reason=can_auto_resolve(self.conn,self.case(),self.answer())
        self.assertTrue(ok,reason)

    def test_strict_threshold_prevents_model_only_approval(self):
        self.assertFalse(can_auto_resolve(self.conn,self.case(),self.answer(.90))[0])
        self.assertFalse(can_auto_resolve(self.conn,self.case(),self.answer(.99,'uncertain'))[0])

    def test_model_cannot_approve_a_conflicting_sunscreen_or_formula(self):
        title='Avène Hyaluron Activ B3 Triple Correction Eye Care SPF50 Cream'
        self.conn.execute("UPDATE products SET title=? WHERE id=14",(title,))
        # Snapshot is changed to reflect what the model evaluated, so guard must
        # reject by deterministic SPF gate rather than only stale snapshot.
        current=self.case()['details']
        current['offers'][0]['title']=title
        self.conn.execute('UPDATE product_review_queue SET details=? WHERE id=?',
                          (json.dumps(current),self.review_id))
        self.assertFalse(can_auto_resolve(self.conn,self.case(),self.answer())[0])

    def test_queue_does_not_duplicate_case_when_repeated(self):
        self.assertEqual(queue_cases(self.conn,queued_by='test')['queued'],1)
        self.assertEqual(queue_cases(self.conn,queued_by='test')['queued'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_queue').fetchone()[0],1)

    def test_batched_worker_auto_resolves_and_preserves_inci(self):
        queue_cases(self.conn,queued_by='test')
        proxy=NoCloseConnection(self.conn)
        calls=[]
        def fake_provider(cases):
            calls.append(len(cases))
            return {case['id']:self.answer() for case in cases}
        with patch('pipeline.product_identity_gemini.get_connection',return_value=proxy), \
             patch('pipeline.product_identity_gemini.update_task_progress'), \
             patch('pipeline.product_identity_gemini.complete_task'), \
             patch('pipeline.product_identity_gemini.fail_task') as fail:
            result=run_identity_gemini_queue('gemini-test',limit=500,batch_size=10,provider=fake_provider)
        fail.assert_not_called()
        self.assertEqual(result['auto_resolved'],1)
        self.assertEqual(result['api_calls'],1)
        self.assertEqual(calls,[1])
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(self.review_id,)).fetchone()[0],'confirmed')
        self.assertEqual(self.conn.execute('SELECT status FROM identity_gemini_queue WHERE review_id=?',(self.review_id,)).fetchone()[0],'auto_resolved')
        row=self.conn.execute('SELECT identity_status,ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()
        self.assertEqual(row['identity_status'],'verified')
        self.assertIsNone(row['ingredients_raw'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canonical_identity_history WHERE operation='gemini_identity_verify'").fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_identity_links WHERE canonical_product_id=? AND identity_status='identity_verified'",(self.cid,)).fetchone()[0],3)

    def test_uncertain_result_stays_in_manual_review_without_changing_identity(self):
        queue_cases(self.conn,queued_by='test')
        proxy=NoCloseConnection(self.conn)
        with patch('pipeline.product_identity_gemini.get_connection',return_value=proxy), \
             patch('pipeline.product_identity_gemini.update_task_progress'), \
             patch('pipeline.product_identity_gemini.complete_task'):
            run_identity_gemini_queue('gemini-test',provider=lambda c:{self.review_id:self.answer(.80)})
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(self.review_id,)).fetchone()[0],'pending')
        self.assertEqual(self.conn.execute('SELECT status FROM identity_gemini_queue WHERE review_id=?',(self.review_id,)).fetchone()[0],'needs_manual')
        self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],'legacy')

    def test_remote_failure_keeps_queue_retryable(self):
        queue_cases(self.conn,queued_by='test')
        proxy=NoCloseConnection(self.conn)
        with patch('pipeline.product_identity_gemini.get_connection',return_value=proxy), \
             patch('pipeline.product_identity_gemini.fail_task') as fail:
            result=run_identity_gemini_queue('gemini-test',provider=lambda c:None)
        self.assertEqual(result['status'],'failed')
        fail.assert_called_once()
        self.assertEqual(queue_counts(self.conn)['queued'],1)

    def test_admin_gemini_review_write_requires_identity_scope(self):
        self.assertEqual(permission_for('/admin/aggregation/reviews/gemini/run','POST'),'identity')


if __name__=='__main__':
    unittest.main()
