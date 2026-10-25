"""Stale Gemini empty-list cases rematch from retained sources, without new INCI."""
import json
import sqlite3
import unittest
from copy import deepcopy

from pipeline.db_manager import init_db
from pipeline.source_registry import get_source
from pipeline.source_adapters import ShopifyAdapter
from pipeline.canonical_identity_resolver import (
    CanonicalIdentityResolver,extract_identity
)
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.aggregation_service import queue_review,save_observation,process_source_observation
from pipeline.saved_identity_rematch import (
    preview_saved_rematch,rematch_saved_reviews,CONFIRMATION
)


class SavedIdentityRematchTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False)
        self.addCleanup(self.conn.close)
        self.source=get_source(self.conn,'bashrety')
        self.assertEqual(self.source['adapter_type'],'shopify')
        self.brand='SVR'
        self.new_title='SVR Sun Secure Eau Solaire SPF50+'
        self.candidate_title='SVR Sun Secure Water SPF50'
        cls=classify_product_attributes(self.candidate_title,self.brand)
        self.candidate=CanonicalIdentityResolver(self.conn).create_provisional(
            self.candidate_title,self.brand,cls,
            extract_identity(self.candidate_title,self.brand,self.source['market']))
        self.conn.commit()

    def source_observation(self,product_id=937001,title=None):
        title=title or self.new_title
        raw={'id':product_id,'title':title,'vendor':self.brand,'handle':'eau-solaire-'+str(product_id),
             'product_type':'Sun Protection',
             'body_html':'<p>Sun water for face and body</p>',
             'variants':[{'id':product_id*10,'title':'Default Title',
                          'price':'999.00','available':True,'sku':'SVR-'+str(product_id)}],
             'images':[{'src':'https://cdn.shopify.com/test.png'}]}
        return ShopifyAdapter(self.source).parse(
            {'product':raw},'https://bashrety.tv/products/eau-solaire-'+str(product_id))[0]

    def create_stale_review(self,obs=None,score=0.7018):
        obs=obs or self.source_observation()
        attrs=extract_identity(obs.raw_title,obs.raw_brand,obs.market)
        cls=classify_product_attributes(obs.raw_title,obs.raw_brand,
                                        obs.raw_category,obs.description)
        oid=save_observation(self.conn,obs,attrs,cls)
        old_decision={'status':'ambiguous','canonical_product_id':None,
            'method':'review_required','candidates':[
                {'canonical_product_id':self.candidate,'state':'unknown',
                 'similarity':score,'unknown':['vehicle'],'conflicts':[],
                 'exact_name':False,'durable_link':False,'reviewed_link':False,
                 'strong_identifier':False,'verified_name':False}]}
        details={'identity':old_decision,'observation_id':oid,
                 'title':obs.raw_title,'raw_brand':obs.raw_brand,
                 'source_url':obs.source_url,'images':obs.images}
        rid=queue_review(self.conn,'identity',obs.source_id,obs.source_product_id,
                         None,details)
        self.conn.execute('UPDATE product_review_queue SET status=? WHERE id=?',
                          ('pending',rid))
        self.conn.execute("""INSERT INTO identity_gemini_queue(
            review_id,evidence_hash,status,attempts,confidence,verdict,
            explanation,model_name,queued_by)
            SELECT id,evidence_hash,'needs_manual',1,0,'uncertain',
            'Product listings are empty.','gemini-identity','prior_user_run'
            FROM product_review_queue WHERE id=?""",(rid,))
        self.conn.commit()
        return oid,rid

    def test_fuzzy_suggestion_with_no_identifier_makes_new_canonical(self):
        before=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        result=process_source_observation(self.conn,self.source_observation())
        entry=result['results'][0]
        self.assertEqual(entry['status'],'provisional')
        self.assertIn(entry['decision']['method'],
                           ('new_identity','no_defensible_existing_match'))
        self.assertNotEqual(entry['canonical_product_id'],self.candidate)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before+1)
        self.assertEqual(self.conn.execute("""SELECT COUNT(*) FROM product_review_queue
            WHERE kind='identity' AND status='pending'""").fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0],1)
        self.assertIsNone(self.conn.execute(
            'SELECT ingredients_raw FROM canonical_products WHERE id=?',
            (entry['canonical_product_id'],)).fetchone()[0])

    def test_real_empty_gemini_list_is_reprocessed_with_saved_source_no_ai(self):
        oid,rid=self.create_stale_review()
        plan=preview_saved_rematch(self.conn,500)
        self.assertEqual(plan['eligible_products'],1,plan)
        sample=rematch_saved_reviews(self.conn,5,dry_run=True)
        self.assertEqual(sample['recovered_products'],1,sample)
        self.assertEqual(sample['failed_products'],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0],0)
        real=rematch_saved_reviews(self.conn,5,expected_fingerprint=plan['fingerprint'])
        self.conn.commit()
        self.assertEqual(real['recovered_products'],1,real)
        self.assertEqual(real['review_cases_superseded'],1)
        self.assertEqual(real['provider_calls'],0)
        self.assertEqual(real['source_observations_deleted'],0)
        self.assertEqual(real['published_inci'],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],1)
        review=self.conn.execute('SELECT status,decision,reviewed_by FROM product_review_queue WHERE id=?',
                                 (rid,)).fetchone()
        self.assertEqual(review['status'],'confirmed')
        self.assertEqual(review['reviewed_by'],'saved_source_rematch_v1')
        self.assertEqual(json.loads(review['decision'])['action'],'superseded_by_saved_rematch')
        old_gemini=self.conn.execute('SELECT verdict,confidence,explanation FROM identity_gemini_queue WHERE review_id=?',
                                     (rid,)).fetchone()
        self.assertEqual(old_gemini['verdict'],'uncertain')
        self.assertEqual(old_gemini['confidence'],0)
        self.assertEqual(old_gemini['explanation'],'Product listings are empty.')
        self.assertEqual(preview_saved_rematch(self.conn)['eligible_products'],0)
        cid=self.conn.execute('SELECT canonical_product_id FROM product_offers').fetchone()[0]
        self.assertNotEqual(cid,self.candidate)
        self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',
                          (cid,)).fetchone()[0],'provisional')

    def test_no_new_source_records_and_no_new_inci_evidence(self):
        obs=self.source_observation()
        obs.ingredient_text='Water, Glycerin, Panthenol, Phenoxyethanol'
        obs.ingredient_section_type='full'
        obs.ingredient_order_verified=True
        self.create_stale_review(obs)
        count=self.conn.execute('SELECT COUNT(*) FROM inci_audit_evidence').fetchone()[0]
        result=rematch_saved_reviews(self.conn,10)
        self.assertEqual(result['recovered_products'],1,result)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inci_audit_evidence').fetchone()[0],count)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],1)

    def test_curated_review_is_preserved_not_auto_cleared(self):
        oid,rid=self.create_stale_review()
        self.conn.execute("""UPDATE product_review_queue
            SET reviewed_by='human',decision='{"action":"request_evidence"}' WHERE id=?""",
            (rid,))
        self.conn.commit()
        self.assertEqual(preview_saved_rematch(self.conn)['eligible_products'],0)
        self.assertEqual(rematch_saved_reviews(self.conn,10)['processed'],0)

    def test_superseded_source_snapshot_cannot_resurrect_old_offer(self):
        oid,rid=self.create_stale_review()
        replacement=self.source_observation(title='SVR Sun Secure Reworked SPF50+')
        replacement.raw_payload['changed']='new formula revision'
        attrs=extract_identity(replacement.raw_title,replacement.raw_brand,replacement.market)
        cls=classify_product_attributes(replacement.raw_title,replacement.raw_brand,
                                        replacement.raw_category,replacement.description)
        oid2=save_observation(self.conn,replacement,attrs,cls)
        self.assertNotEqual(oid,oid2)
        self.assertEqual(preview_saved_rematch(self.conn)['eligible_products'],0)

    def test_correct_exact_existing_canonical_still_matches(self):
        cls=classify_product_attributes(self.new_title,self.brand)
        matching=CanonicalIdentityResolver(self.conn).create_provisional(
            self.new_title,self.brand,cls,
            extract_identity(self.new_title,self.brand,self.source['market']))
        self.conn.commit()
        result=process_source_observation(self.conn,self.source_observation())
        self.assertEqual(result['results'][0]['status'],'matched')
        self.assertEqual(result['results'][0]['canonical_product_id'],matching)

    def test_bona_fide_strong_identifiers_are_not_soft_new_products(self):
        identity=extract_identity(self.new_title,self.brand)
        self.assertTrue(identity['name'])
        # Same source, same product: exact identifier remains match even with
        # additional unrelated products of that brand.
        first=process_source_observation(self.conn,self.source_observation())
        result=process_source_observation(self.conn,self.source_observation())
        self.assertEqual(result['results'][0]['canonical_product_id'],
                         first['results'][0]['canonical_product_id'])


if __name__=='__main__':
    unittest.main()
