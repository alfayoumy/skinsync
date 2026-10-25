"""A lost UI must not trap a discovery scan; weak matches are not decisions."""
import json
import sqlite3
import unittest
from unittest.mock import MagicMock,patch

from pipeline.db_manager import init_db
from pipeline.task_tracker import create_task
from pipeline.aggregation_service import queue_review
from pipeline.weak_identity_triage import (
    is_weak_identity_suggestion,preview_weak_suggestions,defer_weak_suggestions,
    CONFIRMATION
)
from routers.workflows import current_discovery_jobs, cancel_discovery
from security.admin import permission_for


class DurableDiscoveryAndTriageTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False)
        self.addCleanup(self.conn.close)
        self.conn.execute("""INSERT INTO canonical_products(canonical_title,brand,category,
            normalized_key,identity_status) VALUES
            ('SVR Sun Secure Water SPF50','SVR','Sunscreen','candidate-test','legacy')""")
        self.cid=self.conn.execute('SELECT MAX(id) FROM canonical_products').fetchone()[0]
        self.conn.execute("""INSERT INTO source_product_observations(
            source_id,source_product_id,source_url,raw_title,raw_brand,
            observed_at,payload_hash,raw_payload,identity_attributes,classification
        ) VALUES ('bashrety','obs-1',
            'https://bashrety.tv/products/svr-sun-secure-eau-solaire-sun-water-spf50',
            'SVR Sun Secure Eau Solaire SPF50+','SVR',CURRENT_TIMESTAMP,
            'fakehash','{}','{}','{}')""")
        self.oid=self.conn.execute('SELECT MAX(id) FROM source_product_observations').fetchone()[0]
        self.conn.commit()

    def evidence(self):
        return {'identity':{'candidates':[{
            'canonical_product_id':self.cid,'conflicts':[],'durable_link':False,
            'exact_name':False,'reviewed_link':False,'similarity':0.7018,
            'state':'unknown','strong_identifier':False,'unknown':['vehicle'],
            'verified_name':False}],
            'canonical_product_id':None,'details':{},'method':'review_required',
            'status':'ambiguous'},
            'images':[],
            'observation_id':self.oid,'raw_brand':'SVR',
            'source_url':'https://bashrety.tv/products/svr-sun-secure-eau-solaire-sun-water-spf50',
            'title':'SVR Sun Secure Eau Solaire SPF50+'}

    def test_reopen_discovers_running_job_without_any_frontend_task_id(self):
        wrapper=MagicMock(wraps=self.conn)
        wrapper.close=MagicMock()
        with patch('routers.workflows.get_connection',return_value=wrapper),             patch('pipeline.task_tracker.get_connection',return_value=wrapper):
            task=create_task('catalog_discovery',target_source='multiple',
                             total_items=6,auto_start=True,
                             exclusive_task_type=True)
            response=current_discovery_jobs()
            self.assertEqual(response['active_count'],1)
            self.assertEqual(response['jobs'][0]['task_id'],task)
            self.assertTrue(response['jobs'][0]['cancellable'])
            stopped=cancel_discovery(task)
            self.assertTrue(stopped['cancelled'])
            after=current_discovery_jobs()
            self.assertTrue(after['jobs'][0]['cancel_requested'])
            self.conn.execute("""UPDATE pipeline_tasks SET current_stage='worker_stopped'
                WHERE task_id=?""",(task,))
            self.conn.commit()
            self.assertEqual(current_discovery_jobs()['active_count'],0)

    def test_concurrent_scan_is_denied_before_second_task_insert(self):
        wrapper=MagicMock(wraps=self.conn)
        wrapper.close=MagicMock()
        with patch('pipeline.task_tracker.get_connection',return_value=wrapper):
            create_task('catalog_discovery',target_source='multiple',auto_start=True,
                        exclusive_task_type=True)
            with self.assertRaisesRegex(ValueError,'already active'):
                create_task('catalog_discovery',target_source='multiple',
                            auto_start=True,exclusive_task_type=True)
        self.assertEqual(self.conn.execute("""SELECT COUNT(*) FROM pipeline_tasks
            WHERE task_type='catalog_discovery'""").fetchone()[0],1)

    def test_weak_case_from_user_example_is_deferred_not_manual_or_merged(self):
        details=self.evidence()
        self.assertTrue(is_weak_identity_suggestion(details))
        rid=queue_review(self.conn,'identity','bashrety','obs-1',None,details)
        row=self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(rid,)).fetchone()
        self.assertEqual(row['status'],'deferred')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_identity_links').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)

    def test_prior_weak_pending_cases_can_be_deferred_without_any_identity_write(self):
        rid=queue_review(self.conn,'identity','bashrety','obs-1',None,self.evidence())
        self.conn.execute("UPDATE product_review_queue SET status='pending' WHERE id=?",(rid,))
        self.conn.commit()
        before=self.conn.total_changes
        plan=preview_weak_suggestions(self.conn)
        self.assertEqual(plan['eligible'],1)
        self.assertEqual(before,self.conn.total_changes)
        with self.assertRaisesRegex(ValueError,'Type DEFER'):
            defer_weak_suggestions(self.conn,plan['fingerprint'],'WRONG')
        result=defer_weak_suggestions(self.conn,plan['fingerprint'],CONFIRMATION)
        self.assertEqual(result['deferred'],1)
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(self.conn.execute("SELECT status FROM product_review_queue WHERE id=?",(rid,)).fetchone()[0],'deferred')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_identity_links").fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canonical_products").fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM source_product_observations").fetchone()[0],1)
        self.assertEqual(preview_weak_suggestions(self.conn)['eligible'],0)

    def test_actual_identifier_or_conflict_must_stay_manual(self):
        variants=[]
        payload=self.evidence()
        variants.append(dict(payload,identity={**payload['identity'],
                  'candidates':[{**payload['identity']['candidates'][0],
                  'strong_identifier':True}]}))
        variants.append(dict(payload,identity={**payload['identity'],
                  'candidates':[{**payload['identity']['candidates'][0],
                  'state':'conflict','conflicts':['SPF']}]}))
        variants.append(dict(payload,identity={**payload['identity'],
                  'candidates':[{**payload['identity']['candidates'][0],
                  'similarity':0.96}]}))
        for x in variants:
            self.assertFalse(is_weak_identity_suggestion(x))
        self.assertFalse(is_weak_identity_suggestion({}))

    def test_triage_rejects_active_gemini_and_source_mismatch(self):
        rid=queue_review(self.conn,'identity','bashrety','obs-1',None,self.evidence())
        self.conn.execute("UPDATE product_review_queue SET status='pending' WHERE id=?",(rid,))
        self.conn.execute("""INSERT INTO identity_gemini_queue(review_id,evidence_hash,status,
            attempts,queued_by) SELECT id,evidence_hash,'processing',1,'prior_worker'
            FROM product_review_queue WHERE id=?""",(rid,))
        self.conn.commit()
        self.assertEqual(preview_weak_suggestions(self.conn)['eligible'],0)

    def test_api_permissions_for_triage_and_active_discovery(self):
        self.assertEqual(permission_for('/admin/aggregation/reviews/weak-suggestions/defer','POST'),'identity')
        self.assertEqual(permission_for('/admin/workflows/products/discover-multiple/active','GET'),'read')


if __name__=='__main__':
    unittest.main()
