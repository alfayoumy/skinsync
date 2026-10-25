"""Rematching must show actual blockers, not restored idle task records."""
import sqlite3
import unittest
from unittest.mock import patch

from pipeline.db_manager import init_db
from pipeline.saved_identity_rematch import (
    rematch_execution_blockers,preview_saved_rematch,
)
from pipeline.catalog_reset import _control_schema


class RematchExecutionBlockersTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False)
        self.addCleanup(self.conn.close)
        self.conn.commit()

    def task(self,task_id,kind,status,stage=None,when="datetime('now')",started_at=None):
        # Explicitly set started_at; pending + started_at NULL is an idle queue record.
        stamp=None if when is None else self.conn.execute('SELECT '+when).fetchone()[0]
        self.conn.execute("""INSERT INTO pipeline_tasks
          (task_id,task_type,status,current_stage,started_at,created_at,updated_at)
          VALUES(?,?,?,?,?,CURRENT_TIMESTAMP,?)""",
          (task_id,kind,status,stage,started_at,stamp))
        self.conn.commit()

    def test_restored_never_started_pending_tasks_are_not_live_workers(self):
        for i in range(7):
            self.task('queued'+str(i),'batch_fill_ingredients','pending')
        report=rematch_execution_blockers(self.conn)
        self.assertTrue(report['can_start'],report)
        self.assertEqual(report['active_jobs'],[])
        self.assertEqual(report['idle_pending_records'],7)
        with patch('pipeline.saved_identity_rematch._eligible',
                   return_value=({11:[{'id':23,'evidence_hash':'fixed',
                                       'status':'pending'}]},{})):
            plan=preview_saved_rematch(self.conn,500)
        self.assertEqual(plan['eligible_products'],1)
        self.assertTrue(plan['can_start'],plan)
        self.assertEqual(plan['idle_pending_records'],7)

    def test_running_unrelated_task_is_visible_and_blocks(self):
        self.task('old-gemini','product_identity_gemini','running',
                  'batch_5',started_at='2026-10-25T10:00:00Z')
        report=rematch_execution_blockers(self.conn)
        self.assertFalse(report['can_start'])
        self.assertEqual(report['active_jobs'][0]['task_id'],'old-gemini')
        self.assertEqual(report['active_jobs'][0]['task_type'],'product_identity_gemini')
        self.assertEqual(report['active_jobs'][0]['current_stage'],'batch_5')

    def test_own_running_rematch_excluded_only_with_its_exact_id(self):
        self.task('own-rematch','saved_observation_identity_rematch','running',
                  'starting',started_at='2026-10-25T10:00:00Z')
        self.assertFalse(rematch_execution_blockers(self.conn)['can_start'])
        self.assertTrue(rematch_execution_blockers(
            self.conn,exclude_task_id='own-rematch')['can_start'])
        self.task('other-run','catalog_discovery','running',
                  'scan_page_99',started_at='2026-10-25T10:00:00Z')
        again=rematch_execution_blockers(self.conn,exclude_task_id='own-rematch')
        self.assertFalse(again['can_start'])
        self.assertEqual(again['active_jobs'][0]['task_id'],'other-run')

    def test_recent_cancel_still_finishing_blocks_but_old_cancel_does_not(self):
        self.task('old-cancel','catalog_discovery','cancelled',
                  'scan_page_15',when="datetime('now','-3 hours')")
        old=rematch_execution_blockers(self.conn)
        self.assertTrue(old['can_start'],old)
        self.assertEqual(old['old_cancelled_records'],1)
        self.task('new-cancel','catalog_discovery','cancelled','scan_page_17')
        recent=rematch_execution_blockers(self.conn)
        self.assertFalse(recent['can_start'])
        self.assertEqual(recent['active_jobs'][0]['task_id'],'new-cancel')
        self.conn.execute("""UPDATE pipeline_tasks SET current_stage='worker_stopped'
            WHERE task_id='new-cancel'""")
        self.conn.commit()
        self.assertTrue(rematch_execution_blockers(self.conn)['can_start'])

    def test_gemini_processing_belongs_to_failed_task_does_not_block(self):
        self.task('failed-model','product_identity_gemini','failed','worker_stopped')
        self.conn.execute("""INSERT INTO identity_gemini_queue(review_id,evidence_hash,status,
            attempts,queued_by,task_id) VALUES(9022,'sha','processing',1,'admin','failed-model')""")
        self.conn.commit()
        report=rematch_execution_blockers(self.conn)
        self.assertTrue(report['can_start'],report)
        self.assertEqual(report['orphaned_gemini_processing_rows'],1)
        self.assertNotIn('identity_gemini_queue',report['active_workers'])

    def test_live_gemini_processing_still_blocks(self):
        self.task('active-model','product_identity_gemini','running','evaluating',
                  started_at='2026-10-25T10:00:00Z')
        self.conn.execute("""INSERT INTO identity_gemini_queue(review_id,evidence_hash,status,
            attempts,queued_by,task_id) VALUES(9023,'sha','processing',1,'admin','active-model')""")
        self.conn.commit()
        report=rematch_execution_blockers(self.conn)
        self.assertFalse(report['can_start'])
        self.assertIn('identity_gemini_queue',report['active_workers'])

    def test_catalog_maintenance_lock_is_real_blocker(self):
        _control_schema(self.conn)
        self.conn.execute("""INSERT INTO catalog_reset_control(singleton,active) VALUES(1,1)""")
        self.conn.commit()
        report=rematch_execution_blockers(self.conn)
        self.assertTrue(report['maintenance_locked'])
        self.assertFalse(report['can_start'])

    def test_no_preview_mutations_and_review_evidence_is_preserved(self):
        self.task('idle','catalog_refresh','pending')
        before=self.conn.total_changes
        report=preview_saved_rematch(self.conn,50)
        self.assertEqual(self.conn.total_changes,before)
        self.assertEqual(report['active_jobs'],[])
        self.assertEqual(report['idle_pending_records'],1)
        self.assertEqual(report['eligible_products'],0)
        self.assertFalse(report['can_start'])  # No product evidence eligible.


if __name__=='__main__':
    unittest.main()
