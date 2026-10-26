"""Incomplete drug declarations cannot remain authoritative or be republished."""
import json
import unittest
from pipeline.formulation_service import FormulationRepository,FormulationCandidate
from pipeline.inci_tokenizer import tokenize_inci_string
from pipeline.source_detail_ingredients import retain_detail_ingredients
import test_source_detail_ingredients as detail_tests
RAW=detail_tests.RAW


class FormulationWithdrawalTests(unittest.TestCase):
    setUp=detail_tests.SourceDetailIngredientTests.setUp
    page=detail_tests.SourceDetailIngredientTests.page

    def test_separate_zone_declarations_cannot_publish_as_one_formula(self):
        raw='T Zone Cream (Blue) '+RAW+' | U Zone Cream (White) '+RAW
        result=tokenize_inci_string(raw)
        self.assertEqual(result.completeness,'partial')
        self.assertEqual(result.validation_flags['partial_reason'],'multiple_component_formulas')
        candidate=FormulationCandidate(self.cid,'curated_v10',raw,result.tokens)
        self.assertFalse(FormulationRepository().publish_formulation(candidate,'Reviewed dual-zone page',force=True,conn=self.conn))
        self.assertEqual(tokenize_inci_string(RAW).completeness,'full')

    def test_missing_explicit_drug_active_is_partial_even_with_many_carriers(self):
        title='Panoxyl 4% Benzoyl Peroxide Acne Creamy Wash'
        missing=tokenize_inci_string(RAW,product_title=title)
        self.assertEqual(missing.completeness,'partial')
        self.assertEqual(missing.validation_flags['missing_named_active'],'benzoyl peroxide')
        complete=tokenize_inci_string('Benzoyl Peroxide 4%, '+RAW,product_title=title)
        self.assertEqual(complete.completeness,'full')
        self.conn.execute('UPDATE canonical_products SET canonical_title=? WHERE id=?',(title,self.cid))
        candidate=FormulationCandidate(self.cid,'curated_v10',RAW,missing.tokens)
        self.assertFalse(FormulationRepository().publish_formulation(candidate,'Reviewed drug label',force=True,conn=self.conn))

    def test_withdrawal_preserves_history_and_respects_caller_transaction(self):
        retain_detail_ingredients(self.conn,self.oid,self.page(),approve=True)
        self.conn.commit()
        before=dict(self.conn.execute('SELECT * FROM canonical_products WHERE id=?',(self.cid,)).fetchone())
        versions=self.conn.execute('SELECT COUNT(*) FROM formulation_versions').fetchone()[0]
        repo=FormulationRepository()
        with self.assertRaisesRegex(ValueError,'changed'):
            repo.withdraw_formulation(self.cid,'catalog_repair_agent','Missing a declared drug active','stale',conn=self.conn)
        result=repo.withdraw_formulation(self.cid,'catalog_repair_agent','Missing a declared drug active',before['formula_hash'],conn=self.conn)
        self.assertTrue(self.conn.in_transaction)
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM formulation_versions').fetchone()[0],versions)
        audit=self.conn.execute('SELECT * FROM inci_audit_evidence WHERE id=?',(result['evidence_id'],)).fetchone()
        self.assertEqual(audit['decision'],'withdrawn_authoritative')
        self.assertEqual(audit['raw_ingredients_found'],RAW)
        self.assertEqual(json.loads(audit['validation_flags'])['previous_canonical']['formula_hash'],before['formula_hash'])
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],RAW)
        self.assertIsNone(self.conn.execute('SELECT id FROM inci_audit_evidence WHERE id=?',(result['evidence_id'],)).fetchone())

    def test_closed_source_section_can_still_end_in_a_cut_off_chemical(self):
        result=tokenize_inci_string(RAW+', Caprylyl.')
        self.assertTrue(result.is_truncated)
        self.assertEqual(result.completeness,'partial')
        self.assertFalse(tokenize_inci_string(RAW+', Caprylyl Glycol.').is_truncated)

    def test_long_vitamin_summary_and_navigation_are_not_full_inci(self):
        for raw in [RAW+', Vitamin C Liposomal, Multivitamin Liposomal',
                    RAW+', Share:',RAW+', caprylic/capric, triglyceride, swertia']:
            with self.subTest(raw=raw):
                self.assertEqual(tokenize_inci_string(raw).completeness,'partial')

    def test_republication_after_withdrawal_allocates_a_new_history_version(self):
        retain_detail_ingredients(self.conn,self.oid,self.page(),approve=True)
        before=self.conn.execute('SELECT formula_hash FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0]
        repo=FormulationRepository()
        repo.withdraw_formulation(self.cid,'catalog_repair_agent','Correct a contaminated declaration',before,conn=self.conn)
        raw=RAW+', Squalane'
        candidate=FormulationCandidate(self.cid,'curated_v10',raw,tokenize_inci_string(raw).tokens)
        self.assertTrue(repo.publish_formulation(candidate,'Reviewed corrected complete source declaration',audited_by='catalog_repair_agent',conn=self.conn))
        versions=self.conn.execute('SELECT formula_version,raw_ingredients FROM formulation_versions WHERE canonical_product_id=? ORDER BY formula_version',(self.cid,)).fetchall()
        self.assertEqual([tuple(x) for x in versions],[(1,RAW),(2,raw)])
        self.assertEqual(self.conn.execute('SELECT formula_version FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],2)
        self.assertTrue(self.conn.in_transaction)
