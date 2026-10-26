"""Identity Gemini requeue: archive obsolete answers and compare current saved evidence."""
import json
import os
import sqlite3
import tempfile
import unittest
import hashlib
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

    def test_confident_gemini_match_applies_despite_missing_metadata_and_second_store(self):
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
        self.assertEqual(result['auto_resolved'],1)
        self.assertIn('SVR Sun Secure Water SPF50',calls[0])
        self.assertEqual(self.conn.execute('SELECT suggested_canonical_id FROM identity_gemini_source_cases WHERE review_id=?',
                           (self.rid,)).fetchone()[0],self.cid)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',
                           (self.rid,)).fetchone()[0],'confirmed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_identity_links WHERE status=\'accepted\'').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',
                           (self.cid,)).fetchone()[0],None)
        from pipeline.saved_gemini_decisions import candidate_rows, apply_saved_decisions
        self.assertEqual(len(candidate_rows(self.conn)),0,'The live worker must apply the same policy as saved replay.')
        preview=apply_saved_decisions(self.conn,dry_run=True)
        self.assertEqual(preview['source_matches_linked'],0)
        self.assertEqual(preview['still_needs_review'],0)
        applied=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(applied['source_matches_linked'],0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_identity_links WHERE status='accepted'").fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT canonical_product_id FROM product_offers WHERE source_product_id='937001'").fetchone()[0],self.cid)

    def _prepare_independently_corroborated_saved_verdict(self):
        """Synthetic stored verdict with a current matching source and second retailer."""
        from pipeline.source_gemini_requeue import _profile
        title='SVR Sun Secure Water SPF50'
        self.conn.execute("""INSERT OR IGNORE INTO stores(id,name,domain)
            VALUES('bloom','Bloom','bloom.example')""")
        self.conn.execute("""INSERT INTO products(store_id,store_product_id,title,
            brand,category,product_url,canonical_product_id)
            VALUES ('bloom','svr-water',?,'SVR','Sunscreen',?,?)""",
            (title,'https://bloom.example/svr-water',self.cid))
        self.conn.execute("""UPDATE source_product_observations SET raw_title=?,
            identity_attributes=? WHERE id=?""",
            (title,json.dumps(extract_identity(title,'SVR')),self.oid))
        row=self.conn.execute('SELECT details FROM product_review_queue WHERE id=?',
                              (self.rid,)).fetchone()
        details=json.loads(row['details'])
        details['title']=title
        self.conn.execute('UPDATE product_review_queue SET details=? WHERE id=?',
                          (json.dumps(details),self.rid))
        obs=self.conn.execute("""SELECT source_id,source_product_id,source_url,raw_title,
            raw_brand FROM source_product_observations WHERE id=?""",
            (self.oid,)).fetchone()
        payload={
            'mode':'saved_source_identity_candidates_v1',
            'observation_id':self.oid,
            'source':{
                'source_id':obs['source_id'],
                'source_product_id':obs['source_product_id'],
                'url':obs['source_url'],'title':obs['raw_title'],
                'brand':obs['raw_brand'],'gtin':None,
                'identity_attributes':extract_identity(title,'SVR'),
                'variants':[],
            },
            'candidates':[_profile(self.conn,{'canonical_product_id':self.cid})],
        }
        self.conn.execute("""INSERT INTO identity_gemini_source_cases
            (review_id,evidence_hash,payload_json,suggested_canonical_id)
            SELECT id,evidence_hash,?,? FROM product_review_queue WHERE id=?""",
            (json.dumps(payload),self.cid,self.rid))
        self.conn.execute("""UPDATE identity_gemini_queue SET
            confidence=0.99,verdict='same_product',status='needs_manual',comparison_mode='pairwise',
            explanation='Independent listings indicate same product.',
            model_name='test-model' WHERE review_id=?""",(self.rid,))
        self.conn.commit()

    def test_saved_source_match_links_after_independent_identity_checks(self):
        from pipeline.saved_gemini_decisions import candidate_rows,apply_saved_decisions
        self._prepare_independently_corroborated_saved_verdict()
        self.assertEqual(len(candidate_rows(self.conn)),1)
        canonical_before=dict(self.conn.execute('SELECT * FROM canonical_products WHERE id=?',
                                                (self.cid,)).fetchone())
        preview=apply_saved_decisions(self.conn,dry_run=True)
        self.assertEqual(preview['source_verdicts_eligible'],1)
        self.assertEqual(preview['source_matches_linked'],1,preview)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_identity_links WHERE status='accepted'").fetchone()[0],0)
        self.assertEqual(self.conn.execute(
            'SELECT status FROM product_review_queue WHERE id=?',
            (self.rid,)).fetchone()[0],'pending')
        outcome=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(outcome['source_matches_linked'],1,outcome)
        self.assertEqual(self.conn.execute("""SELECT COUNT(*) FROM product_identity_links
            WHERE source_id='bashrety' AND source_product_id='937001'
            AND canonical_product_id=? AND status='accepted'""",
            (self.cid,)).fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',
                           (self.rid,)).fetchone()[0],'confirmed')
        self.assertEqual(self.conn.execute('SELECT status FROM identity_gemini_queue WHERE review_id=?',
                           (self.rid,)).fetchone()[0],'auto_resolved')
        canonical_after=dict(self.conn.execute('SELECT * FROM canonical_products WHERE id=?',
                                               (self.cid,)).fetchone())
        for key in ('canonical_title','brand','identity_attributes','identity_status','ingredients_raw','formula_version'):
            self.assertEqual(canonical_after[key],canonical_before[key],key)
        self.assertEqual(self.conn.execute("SELECT price FROM product_offers WHERE source_product_id='937001'").fetchone()[0],900)
        self.assertEqual(apply_saved_decisions(self.conn,dry_run=False)['source_matches_linked'],0)

    def change_reviewed_source_title(self,title):
        attrs=extract_identity(title,'SVR')
        self.conn.execute('UPDATE source_product_observations SET raw_title=?,identity_attributes=? WHERE id=?',(title,json.dumps(attrs),self.oid))
        row=self.conn.execute('SELECT payload_json FROM identity_gemini_source_cases WHERE review_id=?',(self.rid,)).fetchone()
        payload=json.loads(row[0]);payload['source']['title']=title;payload['source']['identity_attributes']=attrs
        self.conn.execute('UPDATE identity_gemini_source_cases SET payload_json=? WHERE review_id=?',(json.dumps(payload),self.rid))

    def test_actual_spf_contradiction_blocks_same_product_verdict(self):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        self._prepare_independently_corroborated_saved_verdict()
        self.change_reviewed_source_title('SVR Sun Secure Water SPF30')
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['source_matches_linked'],0)
        self.assertIn('conflict',result['sample_results'][0]['reason'])

    def assert_semantic_titles_apply(self,source_title,target_title):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        from pipeline.source_gemini_requeue import _profile
        self._prepare_independently_corroborated_saved_verdict()
        self.conn.execute('UPDATE canonical_products SET canonical_title=?,identity_attributes=? WHERE id=?',
            (target_title,json.dumps(extract_identity(target_title,'SVR')),self.cid))
        self.change_reviewed_source_title(source_title)
        payload=json.loads(self.conn.execute('SELECT payload_json FROM identity_gemini_source_cases WHERE review_id=?',(self.rid,)).fetchone()[0])
        payload['candidates']=[_profile(self.conn,{'canonical_product_id':self.cid})]
        self.conn.execute('UPDATE identity_gemini_source_cases SET payload_json=? WHERE review_id=?',(json.dumps(payload),self.rid))
        result=apply_saved_decisions(self.conn,dry_run=True)
        self.assertEqual(result['source_matches_linked'],1,result)

    def test_inferred_body_category_is_not_a_formulation_conflict(self):
        self.assert_semantic_titles_apply('SVR Body Lotion Musk','SVR Body Musk Lotion')

    def test_french_vehicle_is_not_a_formulation_conflict(self):
        self.assert_semantic_titles_apply('SVR SPF50 Gel Cream','SVR SPF50 Gel-Creme')

    def test_body_milk_and_body_lotion_functions_are_compatible(self):
        self.assert_semantic_titles_apply('SVR Hawaii Body Lotion','SVR Hawaii Body Milk')

    def test_pair_preparation_archives_group_answer_and_is_idempotent(self):
        from pipeline.prepare_identity_pairs import prepare_identity_pairs
        from pipeline.saved_gemini_decisions import candidate_rows
        self._prepare_independently_corroborated_saved_verdict()
        self.conn.execute("UPDATE identity_gemini_queue SET comparison_mode='legacy_group' WHERE review_id=?",(self.rid,))
        result=prepare_identity_pairs(self.conn,apply=True,review_ids=[self.rid])
        self.assertEqual(result['reviews_prepared'],1,result)
        self.assertEqual(result['pairs_prepared'],1)
        archived=json.loads(self.conn.execute('SELECT queue_json FROM identity_gemini_legacy_history').fetchone()[0])
        self.assertEqual(archived['verdict'],'same_product')
        self.assertEqual(archived['confidence'],.99)
        self.assertEqual(self.conn.execute('SELECT status FROM identity_gemini_queue WHERE review_id=?',(self.rid,)).fetchone()[0],'queued')
        self.assertEqual(len(candidate_rows(self.conn)),0)
        again=prepare_identity_pairs(self.conn,apply=True,review_ids=[self.rid])
        self.assertEqual(again.get('reviews_prepared',0),0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_legacy_history').fetchone()[0],1)

    def test_targeted_pair_refresh_keeps_paid_answers_and_adds_only_new_inputs(self):
        from pipeline.prepare_identity_pairs import prepare_identity_pairs
        from pipeline.identity_pairs import product_pairs
        from pipeline.source_gemini_requeue import _profile
        self._prepare_independently_corroborated_saved_verdict()
        case=dict(self.conn.execute('SELECT * FROM product_review_queue WHERE id=?',(self.rid,)).fetchone())
        payload=json.loads(self.conn.execute('SELECT payload_json FROM identity_gemini_source_cases WHERE review_id=?',(self.rid,)).fetchone()[0])
        case['source_comparison']=payload
        pair=product_pairs(case)[0]
        self.conn.execute("""INSERT INTO identity_gemini_pair_results
            (review_id,pair_key,evidence_hash,products_json,canonical_candidate_id,verdict,confidence)
            VALUES (?,?,?,?,?,'different_product',.99)""",
            (self.rid,pair['pair_key'],case['evidence_hash'],json.dumps(pair['pair_products']),self.cid))
        other=CanonicalIdentityResolver(self.conn).create_provisional('SVR Sun Secure Cream SPF50','SVR',
            classify_product_attributes('SVR Sun Secure Cream SPF50','SVR'),extract_identity('SVR Sun Secure Cream SPF50','SVR'))
        CanonicalIdentityResolver(self.conn).record_link('bashrety','new-candidate',other,'accepted','new_identity',{})
        payload['candidates'].append(_profile(self.conn,{'canonical_product_id':other}))
        with self.assertRaises(ValueError):prepare_identity_pairs(self.conn,refresh_pairwise=True)
        with patch('pipeline.prepare_identity_pairs._fresh_payload',return_value=payload):
            result=prepare_identity_pairs(self.conn,apply=True,review_ids=[self.rid],refresh_pairwise=True)
            self.assertEqual(result['new_pairs_prepared'],1,result)
            self.assertEqual(result['completed_pairs_retained'],1,result)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM identity_gemini_pair_results WHERE verdict='different_product'").fetchone()[0],1)
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_pair_results').fetchone()[0],2)
            queue=self.conn.execute('SELECT status,verdict FROM identity_gemini_queue WHERE review_id=?',(self.rid,)).fetchone()
            self.assertEqual(tuple(queue),('queued',None))
            again=prepare_identity_pairs(self.conn,apply=True,review_ids=[self.rid],refresh_pairwise=True)
            self.assertEqual(again.get('reviews_prepared',0),0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_legacy_history').fetchone()[0],1)

    def test_negative_pair_applies_without_a_positive_winner(self):
        from pipeline.saved_gemini_decisions import apply_pair_negatives
        from pipeline.identity_pairs import product_pairs
        self._prepare_independently_corroborated_saved_verdict()
        case=dict(self.conn.execute('SELECT * FROM product_review_queue WHERE id=?',(self.rid,)).fetchone())
        case['source_comparison']=json.loads(self.conn.execute('SELECT payload_json FROM identity_gemini_source_cases WHERE review_id=?',(self.rid,)).fetchone()[0])
        pair=product_pairs(case)[0]
        self.conn.execute('''INSERT INTO identity_gemini_pair_results
            (review_id,pair_key,evidence_hash,products_json,canonical_candidate_id,verdict,confidence,explanation)
            VALUES (?,?,?,?,?,'different_product',.99,'Different scent')''',
            (self.rid,pair['pair_key'],case['evidence_hash'],json.dumps(pair['pair_products']),self.cid))
        self.assertEqual(apply_pair_negatives(self.conn,case),1)
        self.assertEqual(self.conn.execute('SELECT status FROM product_identity_links WHERE source_product_id=?',('937001',)).fetchone()[0],'rejected')
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(self.rid,)).fetchone()[0],'pending')
        self.assertEqual(apply_pair_negatives(self.conn,case),0)

    def prepare_scoped_variant(self,shade='40',verdict='same_product'):
        from pipeline.source_gemini_requeue import _profile,_scoped_payload
        self._prepare_independently_corroborated_saved_verdict()
        original=json.loads(self.conn.execute('SELECT attributes FROM source_product_variants WHERE observation_id=?',(self.oid,)).fetchone()[0])
        self.conn.execute('DELETE FROM source_product_variants WHERE observation_id=?',(self.oid,))
        variants=[]
        for number in ('40','60'):
            value={**original,'source_variant_id':'variant-'+number,'title':number,'attributes':{'shade':number}}
            self.conn.execute('INSERT INTO source_product_variants(observation_id,source_variant_id,title,attributes) VALUES (?,?,?,?)',
                (self.oid,value['source_variant_id'],number,json.dumps(value)))
            variants.append({'title':number,'sku':value.get('sku'),'gtin':value.get('gtin'),'attributes':value['attributes']})
        attrs=json.loads(self.conn.execute('SELECT identity_attributes FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0]);attrs['shade']='40'
        self.conn.execute('UPDATE canonical_products SET identity_attributes=? WHERE id=?',(json.dumps(attrs),self.cid))
        payload=json.loads(self.conn.execute('SELECT payload_json FROM identity_gemini_source_cases WHERE review_id=?',(self.rid,)).fetchone()[0])
        payload['source']['variants']=variants
        payload['candidates']=[_profile(self.conn,{'canonical_product_id':self.cid})]
        key=payload['source']['source_product_id']+':'+hashlib.sha256(json.dumps({'shade':shade},sort_keys=True).encode()).hexdigest()[:16]
        scoped=_scoped_payload(payload,key)
        self.assertEqual(len(payload['source']['variants']),2,'Scoping must not mutate the original saved comparison.')
        self.assertEqual(scoped['source']['variants'][0]['attributes'],{'shade':shade})
        self.conn.execute('UPDATE product_review_queue SET source_product_id=? WHERE id=?',(key,self.rid))
        self.conn.execute('UPDATE identity_gemini_source_cases SET payload_json=? WHERE review_id=?',(json.dumps(scoped),self.rid))
        self.conn.execute('UPDATE identity_gemini_queue SET verdict=? WHERE review_id=?',(verdict,self.rid))

    def test_scoped_shade_match_only_materializes_that_shade(self):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        self.prepare_scoped_variant()
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['source_matches_linked'],1,result)
        self.assertEqual([r[0] for r in self.conn.execute('SELECT source_variant_id FROM product_offers')],['variant-40'])

    def test_scoped_wrong_shade_is_a_real_contradiction(self):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        self.prepare_scoped_variant(shade='60')
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['source_matches_linked'],0,result)
        self.assertIn('shade',result['sample_results'][0]['reason'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0],0)

    def test_scoped_different_shade_retains_only_its_distinct_product(self):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        self.prepare_scoped_variant(shade='60',verdict='different_product')
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['source_comparisons_rejected'],1,result)
        self.assertEqual([r[0] for r in self.conn.execute('SELECT source_variant_id FROM product_offers')],['variant-60'])
        self.assertNotEqual(self.conn.execute('SELECT canonical_product_id FROM product_offers').fetchone()[0],self.cid)

    def test_different_product_verdict_rejects_candidates_and_creates_distinct_source(self):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        self._prepare_independently_corroborated_saved_verdict()
        self.change_reviewed_source_title('SVR Sun Secure Water SPF30')
        self.conn.execute("UPDATE identity_gemini_queue SET verdict='different_product' WHERE review_id=?",(self.rid,))
        before=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        preview=apply_saved_decisions(self.conn,dry_run=True)
        self.assertEqual(preview['source_comparisons_rejected'],1,preview)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before)
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['source_comparisons_rejected'],1,result)
        self.assertEqual(result['new_canonical_products'],1)
        self.assertEqual(self.conn.execute("SELECT status FROM product_identity_links WHERE canonical_product_id=? AND source_product_id='937001'",(self.cid,)).fetchone()[0],'rejected')
        self.assertNotEqual(self.conn.execute("SELECT canonical_product_id FROM product_offers WHERE source_product_id='937001'").fetchone()[0],self.cid)
        self.assertEqual(apply_saved_decisions(self.conn,dry_run=False)['stored_decisions_checked'],0)

    def test_existing_offer_price_is_preserved_when_applying_saved_identity(self):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        self._prepare_independently_corroborated_saved_verdict()
        apply_saved_decisions(self.conn,dry_run=False)
        self.conn.execute("UPDATE product_offers SET price=1234,is_available=0 WHERE source_product_id='937001'")
        from pipeline.saved_gemini_decisions import _materialize_source_match
        row=dict(self.conn.execute('SELECT * FROM product_review_queue WHERE id=?',(self.rid,)).fetchone());row['details']=json.loads(row['details'])
        _materialize_source_match(self.conn,row,self.cid)
        self.assertEqual(tuple(self.conn.execute("SELECT price,is_available FROM product_offers WHERE source_product_id='937001'").fetchone()),(1234,0))

    def test_saved_source_verdict_cannot_apply_when_independent_listing_changes(self):
        from pipeline.saved_gemini_decisions import apply_saved_decisions
        self._prepare_independently_corroborated_saved_verdict()
        self.conn.execute("UPDATE products SET title='Different Sunscreen Lotion' WHERE store_id='bloom'")
        self.conn.commit()
        report=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(report['source_matches_linked'],0,report)
        self.assertEqual(report['still_needs_review'],1)
        self.assertIn('comparison changed',report['sample_results'][0]['reason'])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_identity_links WHERE status='accepted'").fetchone()[0],0)

    def test_uncertain_source_verdict_cannot_enter_saved_application(self):
        from pipeline.saved_gemini_decisions import candidate_rows,apply_saved_decisions
        self._prepare_independently_corroborated_saved_verdict()
        self.conn.execute("""UPDATE identity_gemini_queue SET verdict='uncertain'
            WHERE review_id=?""",(self.rid,))
        self.conn.commit()
        self.assertEqual(len(candidate_rows(self.conn)),0)
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['source_verdicts_seen'],1)
        self.assertEqual(result['source_verdicts_eligible'],0)
        self.assertEqual(result['stored_decisions_checked'],0)

    def test_changed_competing_listing_is_retired_without_empty_gemini_prompt(self):
        plan=preview_identity_requeue(self.conn)
        with patch.dict(os.environ,{'SKINSYNC_CATALOG_BACKUP_DIR':
                                    str(Path(self.folder.name)/'backups')}):
            reset_and_requeue_identity(self.conn,plan['fingerprint'],CONFIRMATION,db_path=self.path)
        self.conn.execute("""UPDATE products SET title='Completely Different Formulation'
            WHERE store_product_id='old-spf50'""")
        self.conn.execute("""INSERT INTO pipeline_tasks(task_id,task_type,status)
            VALUES ('changed-source-test','product_identity_gemini','running')""")
        self.conn.commit()
        with (
            patch('pipeline.product_identity_gemini.get_connection',
                  return_value=OpenConnection(self.conn)),
            patch('pipeline.product_identity_gemini.update_task_progress'),
            patch('pipeline.product_identity_gemini.complete_task'),
            patch('pipeline.product_identity_gemini.fail_task'),
        ):
            calls=[]
            run_identity_gemini_queue('changed-source-test',
                provider=lambda cases: calls.append(cases))
        self.assertEqual(calls,[])
        self.assertEqual(self.conn.execute(
            'SELECT status FROM identity_gemini_queue WHERE review_id=?',
            (self.rid,)).fetchone()[0],'needs_manual')

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
