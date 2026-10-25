"""Full backup restore is deliberate, integrity-checked and restart-only."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.db_manager import init_db
from pipeline.catalog_reset import _backup,preview_catalog_reset
from pipeline.catalog_restore import (
    list_catalog_backups,preview_catalog_restore,queue_catalog_restore,
    cancel_catalog_restore,apply_pending_catalog_restore,RESTORE_CONFIRMATION,
)
from security.admin import permission_for


class CatalogBackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name)
        self.db=base/'catalog.sqlite3'
        self.backups=base/'backups'
        env=patch.dict(os.environ,{'SKINSYNC_CATALOG_BACKUP_DIR':str(self.backups)})
        env.start()
        self.addCleanup(env.stop)
        self.conn=sqlite3.connect(self.db)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("""INSERT INTO stores(id,name,domain)
            VALUES('teststore','Test','test.example')""")
        self.conn.execute("""INSERT INTO products(store_id,title,brand,category,product_url)
            VALUES('teststore','Old Serum','Brand','Serum','https://test.example/old')""")
        self.conn.commit()
        self.backup=_backup(self.conn,self.db)

    def tearDown(self):
        self.conn.close()

    def test_preview_is_read_only_and_selects_managed_snapshot(self):
        self.conn.execute('DELETE FROM products')
        self.conn.commit()
        before=self.conn.total_changes
        plan=preview_catalog_restore(self.conn,self.backup.name,self.db)
        self.assertTrue(plan['can_schedule'])
        self.assertEqual(plan['tables']['products'],1)
        self.assertEqual(self.conn.total_changes,before)
        inventory=list_catalog_backups(self.conn,self.db)
        self.assertEqual(inventory['backups'][0]['filename'],self.backup.name)
        self.assertFalse(inventory['pending'])

    def test_path_traversal_symlink_and_corrupt_backup_refused(self):
        with self.assertRaisesRegex(ValueError,'managed'):
            preview_catalog_restore(self.conn,'../catalog.sqlite3',self.db)
        bad=self.backups/'catalog-20260909T010101Z-deadbeef.sqlite3'
        bad.write_bytes(b'this is not SQLite')
        with self.assertRaises((sqlite3.DatabaseError,ValueError)):
            preview_catalog_restore(self.conn,bad.name,self.db)
        dangling=self.backups/'catalog-20260909T010101Z-feedface.sqlite3'
        dangling.symlink_to(self.backup)
        with self.assertRaisesRegex(ValueError,'not available'):
            preview_catalog_restore(self.conn,dangling.name,self.db)

    def test_requires_exact_confirmation_and_blocks_running_jobs(self):
        plan=preview_catalog_restore(self.conn,self.backup.name,self.db)
        with self.assertRaisesRegex(ValueError,'Type RESTORE'):
            queue_catalog_restore(self.conn,self.backup.name,plan['sha256'],'BAD','tester',self.db)
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES('active-scan','catalog_discovery','running')""")
        self.conn.commit()
        blocked=preview_catalog_restore(self.conn,self.backup.name,self.db)
        self.assertFalse(blocked['can_schedule'])
        with self.assertRaisesRegex(ValueError,'Active jobs'):
            queue_catalog_restore(self.conn,self.backup.name,plan['sha256'],
                RESTORE_CONFIRMATION,'tester',self.db)

    def test_schedule_and_cancel_restore_leave_catalog_intact(self):
        self.conn.execute('DELETE FROM products')
        self.conn.commit()
        plan=preview_catalog_restore(self.conn,self.backup.name,self.db)
        result=queue_catalog_restore(self.conn,self.backup.name,plan['sha256'],
                                    RESTORE_CONFIRMATION,'tester',self.db)
        self.assertEqual(result['status'],'restart_required')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT active FROM catalog_reset_control').fetchone()[0],1)
        self.assertIsNotNone(list_catalog_backups(self.conn,self.db)['pending'])
        self.assertEqual(cancel_catalog_restore(self.conn,self.db)['status'],'cancelled')
        self.assertIsNone(list_catalog_backups(self.conn,self.db)['pending'])
        self.assertEqual(self.conn.execute('SELECT active FROM catalog_reset_control').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0],0)

    def test_restart_restores_exact_snapshot_and_preserves_rescue(self):
        self.conn.execute('DELETE FROM products')
        self.conn.execute("""INSERT INTO products(store_id,title,brand,category,product_url)
            VALUES('teststore','New Empty Catalog','Brand','Serum','https://test.example/new')""")
        self.conn.commit()
        plan=preview_catalog_restore(self.conn,self.backup.name,self.db)
        queue_catalog_restore(self.conn,self.backup.name,plan['sha256'],
                              RESTORE_CONFIRMATION,'operator',self.db)
        self.conn.close()
        try:
            applied=apply_pending_catalog_restore(self.db)
            self.assertEqual(applied,self.backup.name)
            restored=sqlite3.connect(self.db)
            try:
                self.assertEqual(restored.execute('SELECT title FROM products').fetchone()[0],'Old Serum')
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(restored.execute('SELECT active FROM catalog_reset_control').fetchone()[0],0)
                entry=restored.execute('SELECT actor,rescue_backup FROM catalog_restore_history').fetchone()
                self.assertEqual(entry[0],'operator')
                rescue=sqlite3.connect(entry[1])
                try:
                    self.assertEqual(rescue.execute('SELECT title FROM products').fetchone()[0],'New Empty Catalog')
                finally:
                    rescue.close()
            finally:restored.close()
            report=list_catalog_backups(db_path=self.db)
            self.assertIsNone(report['pending'])
            self.assertEqual(report['last_restore']['filename'],self.backup.name)
        finally:
            self.conn=sqlite3.connect(self.db)

    def test_edited_backup_cannot_restore_at_restart(self):
        plan=preview_catalog_restore(self.conn,self.backup.name,self.db)
        queue_catalog_restore(self.conn,self.backup.name,plan['sha256'],RESTORE_CONFIRMATION,
                              'tester',self.db)
        self.conn.close()
        self.backup.write_bytes(b'invalid')
        try:
            with self.assertRaisesRegex(RuntimeError,'hash changed'):
                apply_pending_catalog_restore(self.db)
            before=sqlite3.connect(self.db)
            try:
                self.assertEqual(before.execute('SELECT title FROM products').fetchone()[0],'Old Serum')
            finally:before.close()
        finally:
            self.conn=sqlite3.connect(self.db)

    def test_restore_requires_operations_scope(self):
        self.assertEqual(permission_for('/admin/workflows/catalog/restore','POST'),'operations')
        self.assertEqual(permission_for('/admin/workflows/catalog/backups','GET'),'read')


if __name__=='__main__':
    unittest.main()
