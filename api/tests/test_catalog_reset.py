"""Reset safety: live-job blocking, FK-safe purge, intact backup and references."""
import sqlite3
import tempfile
from pathlib import Path
import unittest
from pipeline.db_manager import init_db, get_connection
from pipeline.catalog_reset import (
    preview_catalog_reset, execute_catalog_reset, guard_catalog_writes, CONFIRMATION)
from security.admin import permission_for


class CatalogResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'catalog.sqlite3'
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        init_db(conn=self.conn, seed_defaults=False)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES ('test','Test','test.example')")
        self.conn.execute("""INSERT INTO products(store_id,title,brand,category,product_url)
            VALUES ('test','Example Lotion','Example','Moisturizer','https://test.example/p')""")
        self.conn.execute("""INSERT INTO canonical_products(canonical_title,brand,category,normalized_key)
            VALUES ('Example Lotion','Example','Moisturizer','example-lotion')""")
        self.conn.execute("""INSERT INTO product_offers(source_id,source_product_id,source_variant_id,
            canonical_product_id,product_url,title)
            VALUES ('sourcebeauty','one','variant',1,'https://test.example/p','Example Lotion')""")
        self.conn.execute("""INSERT INTO product_review_queue(case_key,kind,details,evidence_hash)
            VALUES ('case-1','identity','{}','hash-1')""")
        self.conn.execute("""INSERT INTO identity_gemini_queue(review_id,evidence_hash,queued_by,verdict,status)
            VALUES (1,'hash-1','tester','same_product','needs_manual')""")
        self.conn.commit()

    def test_preview_only_preserves_data(self):
        before = self.conn.total_changes
        report = preview_catalog_reset(self.conn)
        self.assertTrue(report['can_reset'])
        self.assertGreater(report['records_to_delete'], 0)
        self.assertEqual(before, self.conn.total_changes)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0], 1)

    def test_active_job_blocks_reset_and_no_backup(self):
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES ('running-1','catalog_discovery','running')""")
        self.conn.commit()
        report = preview_catalog_reset(self.conn)
        self.assertFalse(report['can_reset'])
        with self.assertRaisesRegex(ValueError, 'Active jobs'):
            execute_catalog_reset(self.conn, report['fingerprint'], CONFIRMATION, 'tester')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0], 1)

    def test_idle_pending_tasks_do_not_block_reset(self):
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES('pending-review','manual_review','pending')""")
        self.conn.commit()
        plan=preview_catalog_reset(self.conn)
        self.assertTrue(plan['can_reset'])
        self.assertEqual(plan['active_jobs'],[])

    def test_cancelled_worker_must_finish_current_page(self):
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status,updated_at)
            VALUES ('cancelled-1','catalog_discovery','cancelled',CURRENT_TIMESTAMP)""")
        self.conn.commit()
        self.assertFalse(preview_catalog_reset(self.conn)['can_reset'])
        self.conn.execute("""UPDATE pipeline_tasks SET current_stage='worker_stopped'
            WHERE task_id='cancelled-1'""")
        self.conn.commit()
        self.assertTrue(preview_catalog_reset(self.conn)['can_reset'])

    def test_confirmation_and_snapshot_fingerprint(self):
        report = preview_catalog_reset(self.conn)
        with self.assertRaisesRegex(ValueError, 'Type RESET'):
            execute_catalog_reset(self.conn, report['fingerprint'], 'RESET', 'tester')
        self.conn.execute("""INSERT INTO products(store_id,title,brand,category,product_url)
            VALUES ('test','Another Lotion','Example','Moisturizer','https://test.example/another')""")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            execute_catalog_reset(self.conn, report['fingerprint'], CONFIRMATION, 'tester')

    def test_backup_first_reset_and_preserve_references(self):
        report = preview_catalog_reset(self.conn)
        preserved = dict(report['preserved_tables'])
        result = execute_catalog_reset(self.conn, report['fingerprint'], CONFIRMATION, 'tester')
        self.assertEqual(result['status'], 'completed')
        for table in ('products','canonical_products','product_offers','product_review_queue',
                      'identity_gemini_queue','pipeline_tasks','source_product_observations'):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        self.assertFalse(list(self.conn.execute('PRAGMA foreign_key_check')))
        for table, count in preserved.items():
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0],count)
        saved = sqlite3.connect(result['backup_path'])
        try:
            self.assertEqual(saved.execute('SELECT COUNT(*) FROM products').fetchone()[0],1)
            self.assertEqual(saved.execute('SELECT verdict FROM identity_gemini_queue').fetchone()[0],'same_product')
            self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:
            saved.close()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM catalog_reset_history').fetchone()[0],1)
        guard_catalog_writes(self.conn)

    def test_immutable_evaluation_triggers_restored(self):
        self.conn.execute("""INSERT INTO source_evaluations(source_id,samples,baseline,created_by,policy_hash)
            VALUES ('sourcebeauty','{}','{}','test','hash')""")
        self.conn.commit()
        plan = preview_catalog_reset(self.conn)
        execute_catalog_reset(self.conn, plan['fingerprint'], CONFIRMATION, 'tester')
        self.conn.execute("""INSERT INTO source_evaluations(source_id,samples,baseline,created_by,policy_hash)
            VALUES ('sourcebeauty','{}','{}','test','hash')""")
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
            self.conn.execute('DELETE FROM source_evaluations')
        self.conn.rollback()

    def test_reset_requires_operations_scope(self):
        self.assertEqual(permission_for('/admin/workflows/catalog/reset','POST'),'operations')
        self.assertEqual(permission_for('/admin/workflows/catalog/reset/preview','GET'),'read')


if __name__ == '__main__':
    unittest.main()
