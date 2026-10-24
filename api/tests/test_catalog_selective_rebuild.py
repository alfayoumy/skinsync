"""Selective offline reconstruction, source provenance, backup and rollback tests."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pipeline.db_manager import init_db, get_connection
from pipeline.aggregation_service import process_source_observation
from pipeline.source_registry import get_source
from pipeline.source_adapters import ShopifyAdapter
from pipeline.catalog_selective_rebuild import (
    SELECTIVE_CONFIRMATION, preview_selective_rebuild,
    start_selective_rebuild, run_selective_rebuild, selective_rebuild_status,
)
from security.admin import permission_for


class SelectiveRebuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'catalog.db')
        self.conn = get_connection(self.path)
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn, seed_defaults=False)
        for sid in ('sourcebeauty','feel22','bloompharmacy'):
            self.conn.execute('UPDATE sources SET enabled=1 WHERE id=?', (sid,))
            self.conn.execute('INSERT OR IGNORE INTO stores(id,name,domain) VALUES(?,?,?)',
                              (sid,sid,get_source(self.conn,sid)['domain']))
        self.conn.commit()
        self._observe('sourcebeauty',1,'CeraVe Moisturizing Cream 50ml')
        self._observe('feel22',2,'CeraVe Foaming Cleanser 200ml')
        # Historical data must vanish; its INCI must not seed the rebuilt products.
        self.conn.execute("""INSERT INTO canonical_products(
            canonical_title,brand,category,normalized_key,ingredients_raw,ingredient_source)
            VALUES('Untrusted Old Item','Brand Old','Cream','old-canonical',
                   'Old ingredient, other ingredient','historical_store_scrape')""")
        self.conn.execute("""INSERT INTO products(
            store_id,title,brand,category,product_url,ingredients_raw,ingredient_source)
            VALUES('sourcebeauty','Untrusted Old Item','Brand Old','Cream',
                   'https://sourcebeauty.com/legacy-product',
                   'Fake ingredient list','historical_store_scrape')""")
        self.conn.execute("""INSERT INTO product_review_queue(case_key,kind,details,evidence_hash)
            VALUES('old-review','identity','{}','old-hash')""")
        rid=self.conn.execute("SELECT id FROM product_review_queue WHERE case_key='old-review'").fetchone()[0]
        self.conn.execute("""INSERT INTO identity_gemini_queue(
            review_id,evidence_hash,queued_by,status,verdict)
            VALUES(?,'old-hash','tester','needs_manual','same_product')""",(rid,))
        self.conn.execute("""INSERT INTO pipeline_tasks(
            task_id,task_type,status,params,metadata,started_at,completed_at)
            VALUES('completed-source-scan','catalog_discovery','completed',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (json.dumps({'source_ids':['sourcebeauty','feel22'],'dry_run':False}),
             json.dumps({'dry_run':False,'sources':[
                {'source_id':'sourcebeauty','status':'completed','catalog_exhausted':True,'fetched':1},
                {'source_id':'feel22','status':'completed','catalog_exhausted':True,'fetched':1},
             ]})))
        self.conn.commit()

    def _observe(self,sid,pid,title):
        source=get_source(self.conn,sid)
        payload={
            'id':pid,'handle':f'product-{pid}','title':title,'vendor':'CeraVe',
            'product_type':'Moisturizer','body_html':'<p>Hydrates skin.</p>',
            'variants':[{'id':pid*10,'title':'Default Title','price':'150.00',
                         'available':True, 'sku':f'SKU-{pid}'}],
            'images':[{'src':'https://example.org/img.jpg'}]
        }
        obs=ShopifyAdapter(source).parse({'product':payload})[0]
        process_source_observation(self.conn,obs)
        self.conn.commit()

    def test_preview_requires_completed_saved_full_scan(self):
        plan=preview_selective_rebuild(self.conn)
        self.assertTrue(plan['can_rebuild'],plan['errors'])
        self.assertEqual(plan['observations_to_replay'],2)
        self.assertEqual(plan['source_ids'],['sourcebeauty','feel22'])
        self.assertGreaterEqual(plan['legacy_products_to_purge'],1)
        self.conn.execute("""UPDATE pipeline_tasks SET params=? WHERE task_id='completed-source-scan'""",
            (json.dumps({'source_ids':['sourcebeauty','feel22'],'dry_run':True}),))
        self.conn.commit()
        plan=preview_selective_rebuild(self.conn)
        self.assertFalse(plan['can_rebuild'])
        self.assertTrue(any('preview-only' in e for e in plan['errors']))

    def test_active_scan_blocks_without_removing_rows(self):
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES('other-task','catalog_discovery','running')""")
        self.conn.commit()
        plan=preview_selective_rebuild(self.conn,'completed-source-scan')
        self.assertFalse(plan['can_rebuild'])
        with self.assertRaises(ValueError):
            start_selective_rebuild(self.conn,plan['fingerprint'],SELECTIVE_CONFIRMATION,
                                    'tester',plan['discovery_task_id'])
        self.assertGreater(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0],0)

    def test_incomplete_or_unpersisted_source_blocks(self):
        self.conn.execute("UPDATE pipeline_tasks SET metadata=? WHERE task_id='completed-source-scan'",
            (json.dumps({'dry_run':False,'sources':[
                {'source_id':'sourcebeauty','status':'completed','catalog_exhausted':True,'fetched':1},
                {'source_id':'feel22','status':'completed','catalog_exhausted':False,'fetched':1}]}),))
        self.conn.commit()
        plan=preview_selective_rebuild(self.conn)
        self.assertFalse(plan['can_rebuild'])
        self.assertTrue(any('end' in e for e in plan['errors']))

    def test_success_purges_historical_but_replays_without_network(self):
        original_ids={row[0] for row in self.conn.execute('SELECT id FROM source_product_observations')}
        preview=preview_selective_rebuild(self.conn)
        job=start_selective_rebuild(self.conn,preview['fingerprint'],SELECTIVE_CONFIRMATION,
                                    'tester',preview['discovery_task_id'])
        self.assertEqual(job['status'],'accepted')
        self.assertRaisesRegex(ValueError,'reset in progress',
            __import__('pipeline.catalog_reset',fromlist=['guard_catalog_writes']).guard_catalog_writes,self.conn)
        run_selective_rebuild(job['job_id'],self.path)
        final=selective_rebuild_status(self.conn,job['job_id'])
        self.assertEqual(final['status'],'completed',final['error_message'])
        self.assertTrue(Path(final['backup_path']).is_file())
        self.assertEqual(final['result']['replayed'],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0],0)
        self.assertGreater(self.conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_queue').fetchone()[0],0)
        self.assertIsNone(self.conn.execute('SELECT id FROM canonical_products WHERE normalized_key=?',('old-canonical',)).fetchone())
        self.assertEqual({row[0] for row in self.conn.execute('SELECT id FROM source_product_observations')},original_ids)
        self.assertFalse(list(self.conn.execute('PRAGMA foreign_key_check')))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canonical_products WHERE ingredient_source='historical_store_scrape'").fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inci_audit_evidence').fetchone()[0],0)
        saved=sqlite3.connect(final['backup_path'])
        try:
            self.assertEqual(saved.execute("SELECT COUNT(*) FROM products").fetchone()[0],1)
            self.assertEqual(saved.execute("SELECT verdict FROM identity_gemini_queue").fetchone()[0],'same_product')
        finally:saved.close()

    def test_corrupt_payload_replay_rolls_back_everything(self):
        # Corruption keeps the outer observation ID/hash unchanged; the replay
        # itself catches the mismatch and must roll back the previous catalog.
        plan=preview_selective_rebuild(self.conn)
        job=start_selective_rebuild(self.conn,plan['fingerprint'],SELECTIVE_CONFIRMATION,
                                    'tester',plan['discovery_task_id'])
        self.conn.execute("""UPDATE source_product_observations SET raw_payload='{}'
            WHERE source_id='feel22'""")
        self.conn.commit()
        original_products=self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        original_canonicals=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        run_selective_rebuild(job['job_id'],self.path)
        status=selective_rebuild_status(self.conn,job['job_id'])
        self.assertEqual(status['status'],'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0],original_products)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],original_canonicals)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_queue').fetchone()[0],1)

    def test_scope(self):
        self.assertEqual(permission_for('/admin/workflows/catalog/selective-rebuild','POST'),'operations')
        self.assertEqual(permission_for('/admin/workflows/catalog/selective-rebuild/preview','GET'),'read')


if __name__ == '__main__':
    unittest.main()
