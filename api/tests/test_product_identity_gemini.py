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
        self.assertFalse(can_auto_resolve(self.conn,self.case(),self.answer(.89))[0])
        self.assertTrue(can_auto_resolve(self.conn,self.case(),self.answer(.90))[0])
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

    def test_single_source_does_not_qualify_for_automated_publication(self):
        self.conn.execute("DELETE FROM products WHERE id IN (10147,14335)")
        details=self.case()['details']
        details['offers']=details['offers'][:1]
        self.conn.execute('UPDATE product_review_queue SET details=? WHERE id=?',(json.dumps(details),self.review_id))
        ok,reason=can_auto_resolve(self.conn,self.case(),self.answer())
        self.assertFalse(ok)
        self.assertIn('listings',reason.lower())

    def test_unanchored_fresh_review_is_not_sent_to_gemini(self):
        from pipeline.product_identity_gemini import retire_unresearchable_queue
        rid=queue_review(self.conn,'identity','bashrety','unknown-canonical',None,
            {'title':'Some moisturiser','raw_brand':'CeraVe','identity':{'candidates':[]}})
        report=queue_cases(self.conn,queued_by='test')
        self.assertEqual(report['queued'],1)  # existing actionable case only
        self.assertGreaterEqual(report['skipped_unresearchable'],1)
        self.conn.execute("""INSERT INTO identity_gemini_queue(review_id,evidence_hash,status,queued_by)
            SELECT id,evidence_hash,'queued','previous_bug' FROM product_review_queue WHERE id=?""",
            (rid,))
        self.assertEqual(retire_unresearchable_queue(self.conn),1)
        row=self.conn.execute("""SELECT status,verdict,model_name,explanation
            FROM identity_gemini_queue WHERE review_id=?""",(rid,)).fetchone()
        self.assertEqual(row['status'],'needs_manual')
        self.assertIsNone(row['verdict'])
        self.assertIsNone(row['model_name'])
        self.assertIn('not called',row['explanation'])

    def test_fresh_provisional_multiretailer_can_be_guarded_gemini_verified(self):
        self.conn.execute("UPDATE canonical_products SET identity_status='provisional' WHERE id=?",
            (self.cid,))
        ok,why=can_auto_resolve(self.conn,self.case(),self.answer())
        self.assertTrue(ok,why)

    def test_readiness_uses_current_multi_source_evidence_and_pruning_is_zero_call(self):
        from pipeline.product_identity_gemini import gemini_queue_readiness
        rid=queue_review(self.conn,'identity','bashrety','missing',None,
                         {'title':'Anonymous moisturizer','raw_brand':'Unknown'})
        self.conn.execute("""INSERT INTO identity_gemini_queue
            (review_id,evidence_hash,status,queued_by)
            SELECT id,evidence_hash,'queued','historical_worker' FROM product_review_queue
            WHERE id=?""",(rid,))
        queue_cases(self.conn,review_ids=[self.review_id],queued_by='test')
        report=gemini_queue_readiness(self.conn)
        self.assertEqual(report['queued_actionable'],1)
        self.assertEqual(report['queued_without_comparable_evidence'],1)
        from pipeline.product_identity_gemini import retire_unresearchable_queue
        self.assertEqual(retire_unresearchable_queue(self.conn),1)
        refreshed=gemini_queue_readiness(self.conn)
        self.assertEqual(refreshed['queued_without_comparable_evidence'],0)
        self.assertEqual(refreshed['queued_actionable'],1)

    def test_multiple_cases_are_evaluated_in_one_gemini_request(self):
        for i in (2,3):
            details=self.case()['details']
            queue_review(self.conn,'identity','bashrety',str(i),self.cid,details)
        queued=queue_cases(self.conn,queued_by='test')
        self.assertEqual(queued['queued'],3)
        calls=[]
        def provider(cases):
            calls.append(len(cases))
            return {case['id']:self.answer(.70,'uncertain') for case in cases}
        proxy=NoCloseConnection(self.conn)
        with patch('pipeline.product_identity_gemini.get_connection',return_value=proxy), \
             patch('pipeline.product_identity_gemini.update_task_progress'), \
             patch('pipeline.product_identity_gemini.complete_task'):
            report=run_identity_gemini_queue('gemini-test',limit=3,batch_size=10,provider=provider)
        self.assertEqual(report['api_calls'],1)
        self.assertEqual(report['needs_manual'],3)
        self.assertEqual(calls,[3])

    def test_abandoned_processing_batch_is_requeued(self):
        queue_cases(self.conn,queued_by='test')
        self.conn.execute("""UPDATE identity_gemini_queue SET status='processing',task_id='crashed-task'
            WHERE review_id=?""",(self.review_id,))
        report=queue_cases(self.conn,queued_by='test')
        self.assertEqual(report['queued'],1)
        self.assertEqual(queue_counts(self.conn)['queued'],1)

    def test_remote_failure_keeps_queue_retryable(self):
        queue_cases(self.conn,queued_by='test')
        proxy=NoCloseConnection(self.conn)
        with patch('pipeline.product_identity_gemini.get_connection',return_value=proxy), \
             patch('pipeline.product_identity_gemini.fail_task') as fail:
            result=run_identity_gemini_queue('gemini-test',provider=lambda c:None)
        self.assertEqual(result['status'],'failed')
        fail.assert_called_once()
        self.assertEqual(queue_counts(self.conn)['queued'],1)

    def test_old_database_without_queue_table_still_lists_1164_pending_reviews(self):
        """Optional Gemini table migration must not hide the existing review backlog."""
        from routers.aggregation import reviews,gemini_identity_queue_status
        for i in range(1164):
            queue_review(self.conn,'identity','teststore',str(i+50000),self.cid,
                {'title':f'Legacy review {i}','canonical_ids':[self.cid],'offers':[]})
        self.conn.execute('DROP TABLE identity_gemini_queue')
        proxy=NoCloseConnection(self.conn)
        with patch('routers.aggregation.get_connection',return_value=proxy):
            result=reviews(view='manual',status='pending',limit=50,offset=0)
            self.assertEqual(result['total'],1165)
            self.assertEqual(len(result['reviews']),50)
            queue=gemini_identity_queue_status()
        self.assertEqual(queue['counts'],{})
        self.assertEqual(queue['pending_identity_cases'],1165)
        self.assertIsNotNone(self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='identity_gemini_queue'").fetchone())

    def test_malformed_legacy_review_evidence_does_not_hide_other_reviews(self):
        from routers.aggregation import reviews
        self.conn.execute("UPDATE product_review_queue SET details='not valid JSON' WHERE id=?",(self.review_id,))
        proxy=NoCloseConnection(self.conn)
        with patch('routers.aggregation.get_connection',return_value=proxy):
            result=reviews(status='pending',view='manual',limit=50,offset=0)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['reviews'][0]['details']['reason_code'],'unparseable_legacy_evidence')

    def test_admin_gemini_review_write_requires_identity_scope(self):
        self.assertEqual(permission_for('/admin/aggregation/reviews/gemini/run','POST'),'identity')


if __name__=='__main__':
    unittest.main()
