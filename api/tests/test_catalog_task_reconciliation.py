"""Reconcile only stale terminal-stage task records; protect live and unknown jobs."""
import sqlite3
import tempfile
from pathlib import Path
import unittest

from pipeline.db_manager import init_db, get_connection
from pipeline.catalog_reset import _active_tasks, _control_schema
from pipeline.catalog_task_reconciliation import (
    CONFIRMATION, preview_stale_task_reconciliation, reconcile_stale_task_records,
)
from security.admin import permission_for


class StaleTaskReconciliationTests(unittest.TestCase):
    def setUp(self):
        root=tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.path=str(Path(root.name)/'reconcile.db')
        self.conn=get_connection(self.path)
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        _control_schema(self.conn)
        self.conn.execute("""INSERT INTO stores(id,name,domain)
            VALUES ('sourcebeauty','SourceBeauty','sourcebeauty.com')
            ON CONFLICT(id) DO NOTHING""")
        self.conn.execute("""INSERT INTO products(store_id,title,brand,category,product_url)
            VALUES ('sourcebeauty','Preserve Me','Brand','Serum','https://sourcebeauty.com/preserve')""")
        self.conn.commit()

    def insert_task(self,task_id,kind,stage=None,age='-12 hours',status='running'):
        self.conn.execute("""INSERT INTO pipeline_tasks
            (task_id,task_type,status,current_stage,updated_at)
            VALUES(?,?,?,?,datetime('now',?))""",(task_id,kind,status,stage,age))
        self.conn.commit()

    def test_exact_observed_legacy_patterns_are_eligible(self):
        fixtures=[
            ('test_api','api_cancel_test',None),
            ('inci_match','batch_match_inci','processed_item_1_of_1'),
            ('fill','batch_fill_ingredients','processed_item_1_of_1'),
            ('refresh','refresh_catalog','finished_store_bashrety'),
            ('source','refresh_store','finished_store_sourcebeauty'),
        ]
        for task_id,kind,stage in fixtures:
            self.insert_task(task_id,kind,stage)
        report=preview_stale_task_reconciliation(self.conn)
        self.assertTrue(report['can_reconcile'],report['errors'])
        self.assertEqual({t['task_id'] for t in report['candidates']},
                         {r[0] for r in fixtures})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],1)

    def test_stale_running_discovery_is_never_eligible(self):
        self.insert_task('scan','catalog_discovery','finished_sourcebeauty')
        report=preview_stale_task_reconciliation(self.conn)
        self.assertFalse(report['can_reconcile'])
        self.assertEqual(report['candidates'],[])
        self.assertEqual(report['ineligible_running'][0]['task_id'],'scan')

    def test_unknown_recent_and_partial_work_are_excluded(self):
        self.insert_task('unknown','special_export','finished_store_sourcebeauty')
        self.insert_task('partial','batch_match_inci','processed_item_1_of_5')
        self.insert_task('fresh','refresh_catalog','finished_store_bashrety',age='-1 hour')
        report=preview_stale_task_reconciliation(self.conn)
        self.assertEqual(report['candidates'],[])
        self.assertEqual(len(report['ineligible_running']),3)

    def test_strict_confirmation_fingerprint_and_live_lock(self):
        self.insert_task('old','refresh_catalog','finished_store_bashrety')
        plan=preview_stale_task_reconciliation(self.conn)
        with self.assertRaisesRegex(ValueError,'confirmation'):
            reconcile_stale_task_records(self.conn,plan['fingerprint'],'BAD','operator',True)
        with self.assertRaisesRegex(ValueError,'Confirm you inspected'):
            reconcile_stale_task_records(self.conn,plan['fingerprint'],CONFIRMATION,'operator',False)
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES('new-unknown','refresh_catalog','running')""")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError,'activity changed'):
            reconcile_stale_task_records(self.conn,plan['fingerprint'],CONFIRMATION,'operator',True)
        self.assertEqual(self.conn.execute("SELECT status FROM pipeline_tasks WHERE task_id='old'").fetchone()[0],'running')
        self.conn.execute('DELETE FROM pipeline_tasks WHERE task_id=?',('new-unknown',))
        self.conn.execute('INSERT INTO catalog_reset_control(singleton,active) VALUES(1,1)')
        self.conn.commit()
        locked=preview_stale_task_reconciliation(self.conn)
        self.assertFalse(locked['can_reconcile'])
        with self.assertRaisesRegex(ValueError,'maintenance'):
            reconcile_stale_task_records(self.conn,locked['fingerprint'],CONFIRMATION,'operator',True)

    def test_reconcile_is_audited_and_only_task_states_change(self):
        self.insert_task('old','batch_fill_ingredients','processed_item_1_of_1')
        self.insert_task('active','catalog_discovery','scan_page_35')
        first=preview_stale_task_reconciliation(self.conn)
        self.assertTrue(first['can_reconcile'])
        result=reconcile_stale_task_records(self.conn,first['fingerprint'],CONFIRMATION,
                                             'operations-reviewer',True)
        self.assertEqual(result['reconciled_count'],1)
        self.assertEqual(result['still_running_count'],1)
        self.assertEqual(result['catalog_rows_modified'],0)
        row=self.conn.execute("SELECT status,current_stage,error_message FROM pipeline_tasks WHERE task_id='old'").fetchone()
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['current_stage'],'processed_item_1_of_1')
        self.assertIn('orphaned task record',row['error_message'])
        self.assertEqual(self.conn.execute("SELECT status FROM pipeline_tasks WHERE task_id='active'").fetchone()[0],'running')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],1)
        audit=dict(self.conn.execute("""SELECT actor,task_id,old_snapshot_json FROM catalog_task_reconciliation_audit""").fetchone())
        self.assertEqual(audit['actor'],'operations-reviewer')
        self.assertEqual(audit['task_id'],'old')
        self.assertEqual(len(_active_tasks(self.conn,{'pipeline_tasks'})),1)
        self.assertFalse(preview_stale_task_reconciliation(self.conn)['can_reconcile'])

    def test_endpoint_permissions(self):
        self.assertEqual(permission_for('/admin/workflows/catalog/stale-tasks/preview','GET'),'read')
        self.assertEqual(permission_for('/admin/workflows/catalog/stale-tasks/reconcile','POST'),'operations')


if __name__=='__main__':
    unittest.main()
