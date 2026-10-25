"""Identity Gemini requeue: archive obsolete answers and compare current saved evidence."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.db_manager import init_db
from pipeline.source_registry import get_source
from pipeline.source_adapters import ShopifyAdapter
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver, extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.aggregation_service import queue_review,save_observation
from pipeline.source_gemini_requeue import (
    preview_identity_requeue, reset_and_requeue_identity, CONFIRMATION,
)
from pipeline.product_identity_gemini import (
    gemini_queue_readiness, retire_unresearchable_queue, run_identity_gemini_queue,
    _batch_prompt,
)


class OpenConnection:
    def __init__(self, conn):
        self.conn=conn
    def close(self):
        pass
    def __getattr__(self, name):
        return getattr(self.conn,name)


class SourceGeminiRequeueTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'catalog.sqlite3'
        self.conn=sqlite3.connect(self.path)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        self.source=get_source(self.conn,'bashrety')
        candidate_title='SVR Sun Secure Water SPF50'
        self.cid=CanonicalIdentityResolver(self.conn).create_provisional(
            candidate_title,'SVR',
            classify_product_attributes(candidate_title,'SVR'),
            extract_identity(candidate_title,'SVR',self.source['market']))
        self.conn.execute("""INSERT OR IGNORE INTO stores(id,name,domain)
            VALUES('bashrety','Bashrety','bashrety.tv')""")
        self.conn.execute("""INSERT INTO products(store_id,store_product_id,title,
            brand,category,product_url,canonical_product_id)
            VALUES('bashrety','old-spf50',?,'SVR','Sunscreen',?,?)""",
            (candidate_title,'https://bashrety.tv/products/old-spf50',self.cid))
        raw={'id':937001,'title':'SVR Sun Secure Water','vendor':'SVR',
             'handle':'sun-water-937001','product_type':'Sun Protection',
             'body_html':'<p>Sun water for face and body</p>',
             'variants':[{'id':9370010,'title':'Default Title',
                          'price':'900.00','available':True,'sku':'SVR-WATER'}],
             'images':[{'src':'https://cdn.shopify.com/937001.png'}]}
        obs=ShopifyAdapter(self.source).parse(
            {'product':raw},'https://bashrety.tv/products/sun-water-937001')[0]
        self.oid=save_observation(self.conn,obs,
            extract_identity(obs.raw_title,obs.raw_brand,obs.market),
            classify_product_attributes(obs.raw_title,obs.raw_brand,
                obs.raw_category,obs.description))
        self.rid=queue_review(self.conn,'identity',obs.source_id,obs.source_product_id,
            None,{'identity':{'status':'ambiguous','method':'review_required',
                'candidates':[]},'observation_id':self.oid,
                'title':obs.raw_title,'raw_brand':obs.raw_brand,
                'source_url':obs.source_url})
        self.conn.execute("""UPDATE product_review_queue SET status='pending'
            WHERE id=?""",(self.rid,))
        self.conn.execute("""INSERT INTO identity_gemini_queue(
            review_id,evidence_hash,status,attempts,confidence,verdict,
            explanation,model_name,queued_by)
            SELECT id,evidence_hash,'needs_manual',1,0.8,'uncertain',
            'Old comparison list was empty.','old-model','legacy'
            FROM product_review_queue WHERE id=?""",(self.rid,))
        self.conn.commit()

    def test_preview_is_nondestructive_and_uses_fresh_competitor(self):
        before=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        old=self.conn.execute('SELECT explanation FROM identity_gemini_queue WHERE review_id=?',
                              (self.rid,)).fetchone()[0]
        plan=preview_identity_requeue(self.conn)
        self.assertEqual(plan['old_queue_count'],1)
        self.assertEqual(plan['requeueable_review_cases'],1,plan)
        self.assertTrue(plan['can_apply'],plan)
        self.assertEqual(plan['gemini_calls'],0)
        self.assertEqual(self.conn.execute('SELECT explanation FROM identity_gemini_queue WHERE review_id=?',
                           (self.rid,)).fetchone()[0],old)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before)

    def test_reset_archives_old_response_and_requeues_without_identity_changes(self):
        plan=preview_identity_requeue(self.conn)
        with patch.dict(os.environ,{'SKINSYNC_CATALOG_BACKUP_DIR':
                                    str(Path(self.folder.name)/'backups')}):
            outcome=reset_and_requeue_identity(self.conn,plan['fingerprint'],
                                                CONFIRMATION,db_path=self.path)
        self.assertEqual(outcome['new_identity_queue_count'],1)
        self.assertEqual(outcome['old_identity_queue_deleted'],1)
        self.assertEqual(outcome['provider_calls'],0)
        q=self.conn.execute('SELECT * FROM identity_gemini_queue WHERE review_id=?',
                            (self.rid,)).fetchone()
        self.assertEqual(q['status'],'queued')
        self.assertEqual(q['attempts'],0)
        self.assertIsNone(q['verdict'])
        self.assertIsNone(q['explanation'])
        p=json.loads(self.conn.execute('SELECT payload_json FROM identity_gemini_source_cases WHERE review_id=?',
                           (self.rid,)).fetchone()[0])
        self.assertEqual(p['source']['title'],'SVR Sun Secure Water')
        self.assertEqual(p['candidates'][0]['canonical_product_id'],self.cid)
        self.assertEqual(p['candidates'][0]['retailer_listings'][0]['retailer'],'bashrety')
        self.assertEqual(gemini_queue_readiness(self.conn)['queued_actionable'],1)
        self.assertEqual(retire_unresearchable_queue(self.conn),0)
        old=sqlite3.connect(outcome['backup_path'])
        try:
            self.assertEqual(old.execute(
                'SELECT verdict FROM identity_gemini_queue WHERE review_id=?',
                (self.rid,)).fetchone()[0],'uncertain')
        finally:old.close()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',
                           (self.rid,)).fetchone()[0],'pending')

    def test_gemini_suggestion_not_auto_linked_even_at_max_confidence(self):
        plan=preview_identity_requeue(self.conn)
        with patch.dict(os.environ,{'SKINSYNC_CATALOG_BACKUP_DIR':
                                    str(Path(self.folder.name)/'backups')}):
            reset_and_requeue_identity(self.conn,plan['fingerprint'],CONFIRMATION,db_path=self.path)
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES ('source-gemini-test','product_identity_gemini','running')""")
        self.conn.commit()
        calls=[]
        def mock_provider(cases):
            calls.append(_batch_prompt(cases))
            return {self.rid:{
                'verdict':'same_product','confidence':0.999,
                'matched_canonical_id':self.cid,'reason':'Listings look similar.',
                'model_name':'mock-model'}}
        with (
            patch('pipeline.product_identity_gemini.get_connection',
                  return_value=OpenConnection(self.conn)),
            patch('pipeline.product_identity_gemini.update_task_progress'),
            patch('pipeline.product_identity_gemini.complete_task'),
            patch('pipeline.product_identity_gemini.fail_task') as fail,
        ):
            result=run_identity_gemini_queue('source-gemini-test',provider=mock_provider)
        fail.assert_not_called()
        self.assertEqual(result['needs_manual'],1)
        self.assertIn('SVR Sun Secure Water SPF50',calls[0])
        self.assertEqual(self.conn.execute('SELECT suggested_canonical_id FROM identity_gemini_source_cases WHERE review_id=?',
                           (self.rid,)).fetchone()[0],self.cid)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',
                           (self.rid,)).fetchone()[0],'pending')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_identity_links WHERE status=\'accepted\'').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',
                           (self.cid,)).fetchone()[0],None)
        from pipeline.saved_gemini_decisions import candidate_rows
        self.assertEqual(len(candidate_rows(self.conn)),0,
            'Saved-decision application must not auto-apply source-only Gemini suggestions.')

    def test_running_worker_blocks_reset(self):
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES ('do-not-disturb','catalog_discovery','running')""")
        self.conn.commit()
        plan=preview_identity_requeue(self.conn)
        self.assertFalse(plan['can_apply'])
        with self.assertRaises(ValueError):
            reset_and_requeue_identity(self.conn,plan['fingerprint'],CONFIRMATION,db_path=self.path)
        self.assertEqual(self.conn.execute('SELECT verdict FROM identity_gemini_queue WHERE review_id=?',
                           (self.rid,)).fetchone()[0],'uncertain')

    def test_wrong_confirmation_cannot_remove_history(self):
        plan=preview_identity_requeue(self.conn)
        with self.assertRaises(ValueError):
            reset_and_requeue_identity(self.conn,plan['fingerprint'],'DELETE',db_path=self.path)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_queue').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
