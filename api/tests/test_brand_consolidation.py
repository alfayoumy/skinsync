import json
import sqlite3
import unittest
from pipeline.db_manager import init_db
from pipeline.brand_registry import consolidate_brand_labels,resolve_brand
from pipeline.canonical_identity_resolver import identity_name


class BrandConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close);init_db(conn=self.conn,seed_defaults=False)
        self.target=resolve_brand('ISISPHARMA',self.conn)['id']
        self.old=self.conn.execute("INSERT INTO brands(name,status) VALUES('Isis Pharma','legacy_exact')").lastrowid
        self.conn.execute("UPDATE brand_aliases SET brand_id=? WHERE alias='isis pharma'",(self.old,))
        self.cid=self.conn.execute("""INSERT INTO canonical_products(canonical_title,brand,brand_id,category,
            normalized_key,is_skincare,identity_status) VALUES('Isis Pharma Secalia Face Cream','Isis Pharma',?,'Moisturizer','old-brand',1,'provisional')""",(self.old,)).lastrowid

    def apply(self):
        return consolidate_brand_labels(self.conn,'ISISPHARMA',['Isis Pharma'],'catalog_repair_agent',
            'Official manufacturer and retailer use these labels for the same brand.',
            ['https://www.isispharma.com/en/product/face-cream/'])

    def test_explicit_consolidation_audits_brand_mapping_without_verifying_or_merging_products(self):
        before=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        result=self.apply()
        row=self.conn.execute('SELECT * FROM canonical_products WHERE id=?',(self.cid,)).fetchone()
        self.assertEqual(row['brand_id'],self.target)
        self.assertEqual(row['brand'],'Isis Pharma')
        self.assertEqual(row['identity_status'],'provisional')
        self.assertFalse(row['ingredients_raw'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before)
        history=self.conn.execute('SELECT * FROM brand_identity_history WHERE id=?',(result['history_id'],)).fetchone()
        self.assertEqual(json.loads(history['before_state'])['canonicals'][0]['brand_id'],self.old)
        self.assertEqual(resolve_brand('Isis Pharma',self.conn)['id'],self.target)
        self.assertEqual(identity_name('Isis Pharma Secalia Face Cream 40ml','ISISPHARMA'),identity_name('Secalia Face Cream','ISISPHARMA'))

    def test_unreviewed_aliases_and_missing_evidence_block_consolidation(self):
        self.conn.execute("INSERT INTO brand_aliases(alias,brand_id,approved_by) VALUES('another label',?,'legacy_exact_label')",(self.old,))
        with self.assertRaisesRegex(ValueError,'Every alias'):self.apply()
        with self.assertRaisesRegex(ValueError,'evidence URLs'):
            consolidate_brand_labels(self.conn,'ISISPHARMA',['Isis Pharma'],'agent','Evidence reviewed',[])
        self.assertEqual(resolve_brand('Isis Pharma',self.conn)['id'],self.old)


if __name__=='__main__':unittest.main()
