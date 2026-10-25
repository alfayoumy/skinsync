"""Read-only post-restore audit: completeness, duplicate flags and INCI risk."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.db_manager import init_db
from pipeline.catalog_quality_audit import inspect_catalog_quality
from pipeline.catalog_reset import _backup
from pipeline.catalog_restore import (
    preview_catalog_restore,queue_catalog_restore,apply_pending_catalog_restore,
    RESTORE_CONFIRMATION,
)
from security.admin import permission_for


class CatalogQualityAuditTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir=Path(t.name)
        env=patch.dict(os.environ,{'SKINSYNC_CATALOG_BACKUP_DIR':str(self.dir/'backups')})
        env.start();self.addCleanup(env.stop)
        self.path=self.dir/'main.sqlite3'
        self.conn=sqlite3.connect(self.path)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        init_db(conn=self.conn,seed_defaults=False)
        self.addCleanup(self.conn.close)
        self.conn.execute("""INSERT INTO stores(id,name,domain)
            VALUES('sourcebeauty','Source Beauty','sourcebeauty.com')""")
        self.conn.execute("""INSERT INTO canonical_products(canonical_title,brand,category,
            normalized_key,identity_status,classification_status,is_skincare,ingredients_raw,
            ingredient_source) VALUES
            ('Example Cream','Brand','Moisturizer','key1','legacy','legacy',1,
             'Water, Glycerin, Phenoxyethanol','historical_store_scrape'),
            ('Example Cream','Brand','Moisturizer','key2','verified','auto_approved',1,
             NULL,NULL),
            ('Example Sunscreen','Brand','Sunscreen','key3','verified','auto_approved',1,
             'Water, Glycerin, Zinc Oxide','source:sourcebeauty')""")
        self.conn.execute("""INSERT INTO products(store_id,store_product_id,title,brand,category,
            product_url,canonical_product_id) VALUES
            ('sourcebeauty','100','Example Cream','Brand','Moisturizer',
             'https://sourcebeauty.com/100',1),
            ('sourcebeauty','200','Example Cream','Brand','Moisturizer',
             'https://sourcebeauty.com/200',NULL)""")
        self.conn.commit()

    def test_audit_never_modifies_catalog_and_flags_only_suspicion(self):
        before=self.conn.total_changes
        report=inspect_catalog_quality(self.conn,self.path)
        self.assertEqual(report['restore']['status'],'no_recent_restore')
        self.assertEqual(report['integrity']['quick_check'],'ok')
        self.assertEqual(report['integrity']['foreign_key_errors'],0)
        self.assertEqual(report['catalog_counts']['canonical_products'],3)
        self.assertEqual(report['catalog_counts']['products'],2)
        risk=report['risk_indicators']
        self.assertEqual(risk['approved_formula_on_unverified_identity'],1)
        self.assertEqual(risk['historical_scrape_formula_still_assigned'],1)
        self.assertEqual(risk['verified_skincare_without_inci'],1)
        self.assertEqual(risk['exact_brand_title_collision_groups'],1)
        self.assertEqual(risk['legacy_listings_without_canonical'],1)
        self.assertEqual(risk['approved_formula_missing_approval_evidence'],2)
        self.assertEqual(self.conn.total_changes,before)
        self.assertEqual(next(v for v in report['sources'] if v['id']=='sourcebeauty')['historical_listings'],2)

    def test_report_distinguishes_scheduled_from_confirmed_restore(self):
        full_backup=_backup(self.conn,self.path)
        self.conn.execute('DELETE FROM products')
        self.conn.commit()
        selected=preview_catalog_restore(self.conn,full_backup.name,self.path)
        queue_catalog_restore(self.conn,full_backup.name,selected['sha256'],
                              RESTORE_CONFIRMATION,'qa',self.path)
        staged=inspect_catalog_quality(self.conn,self.path)
        self.assertEqual(staged['restore']['status'],'restart_required')
        self.assertFalse(staged['restore']['verified_completed'])
        self.conn.close()
        try:
            apply_pending_catalog_restore(self.path)
            with sqlite3.connect(self.path) as restored:
                restored.row_factory=sqlite3.Row
                verified=inspect_catalog_quality(restored,self.path)
                self.assertTrue(verified['restore']['verified_completed'])
                self.assertEqual(verified['restore']['status'],'restored')
                self.assertEqual(verified['catalog_counts']['products'],2)
                self.assertEqual(verified['restore']['baseline_counts']['products'],2)
                self.assertEqual(verified['restore']['current_minus_baseline']['products'],0)
                restored.execute("""INSERT INTO products(store_id,title,brand,category,product_url)
                    VALUES('sourcebeauty','New Thing','Brand','Other','https://sourcebeauty.com/new')""")
                restored.commit()
                changed=inspect_catalog_quality(restored,self.path)
                self.assertTrue(changed['restore']['verified_completed'])
                self.assertEqual(changed['restore']['current_minus_baseline']['products'],1)
        finally:
            self.conn=sqlite3.connect(self.path)

    def test_audit_has_read_permission_only(self):
        self.assertEqual(permission_for('/admin/workflows/catalog/quality-audit','GET'),'read')


if __name__=='__main__':
    unittest.main()
