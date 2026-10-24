"""Offline deterministic tests for the MCP-backed INCI research review boundary."""
import os
from pathlib import Path
import tempfile
import unittest

from pipeline.db_manager import get_connection,init_db
from pipeline.inci_research import (
    enqueue_unresolved,start_run,run_queue,list_queue,review_case,
    retry_case,run_status,_safe_url,_validate_candidate,ensure_schema,
)
from security.admin import permission_for

INGREDIENTS=('Aqua, Glycerin, Cetearyl Alcohol, Butylene Glycol, Dimethicone, '
             'Niacinamide, Panthenol, Ceramide NP, Caprylic/Capric Triglyceride, '
             'Sodium Hyaluronate, Phenoxyethanol, Ethylhexylglycerin, Tocopherol')
URL='https://sourcebeauty.com/products/cerave-moisturizing-cream'


class InciResearchQueueTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path=str(Path(folder.name)/'catalog.sqlite3')
        self.conn=get_connection(self.path)
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("""INSERT INTO canonical_products(
            canonical_title,brand,category,normalized_key,is_skincare,identity_status,market)
            VALUES ('CeraVe Moisturizing Cream','CeraVe','Moisturizer','research-product',1,'verified','EG')""")
        self.cid=self.conn.execute(
            "SELECT id FROM canonical_products WHERE normalized_key='research-product'").fetchone()[0]
        self.conn.commit()

    def _fetch(self,brand,title,domains,max_pages,known_urls=None):
        self.assertIn('sourcebeauty.com',domains)
        return [{'url':URL,'text':f'{brand} {title}. Full ingredients: {INGREDIENTS}. End of declaration. '}]

    def _gemini(self,brand,title,pages,model):
        return {'found':True,'page_index':0,'matched_title':title,
                'matched_brand':brand,'inci_text':INGREDIENTS,
                'confidence':0.94,'explanation':'Copied from full ingredient declaration.'}

    def test_enqueues_only_empty_identity_verified_products(self):
        result=enqueue_unresolved(self.conn,[self.cid])
        self.assertEqual(result['queued'],1)
        self.assertEqual(enqueue_unresolved(self.conn,[self.cid])['queued'],0)
        self.conn.execute("""UPDATE canonical_products SET identity_status='provisional' WHERE id=?""",(self.cid,))
        self.conn.execute('DELETE FROM inci_research_queue')
        self.assertEqual(enqueue_unresolved(self.conn,[self.cid])['queued'],0)

    def test_research_records_only_evidence_until_explicit_review(self):
        enqueue_unresolved(self.conn,[self.cid])
        self.conn.commit()
        job=start_run(self.conn,1)
        self.conn.commit()
        run_queue(job['run_id'],self.path,fetcher=self._fetch,extractor=self._gemini)
        queue=list_queue(self.conn,status='needs_review')
        self.assertEqual(queue['total'],1)
        item=queue['items'][0]
        self.assertEqual(item['ingredients_raw'],INGREDIENTS)
        self.assertGreater(item['confidence'],0.85)
        self.assertEqual(item['source_url'],URL)
        self.assertEqual(item['ingredients_tokens'][0],'water')
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])
        self.assertEqual(self.conn.execute("""SELECT decision FROM inci_audit_evidence
            WHERE canonical_product_id=? ORDER BY id DESC LIMIT 1""",(self.cid,)).fetchone()[0],'candidate_evidence')
        self.assertEqual(run_status(self.conn,job['run_id'])['succeeded'],1)
        rejected=review_case(self.conn,self.cid,'reject','reviewer',
                             'Regional formulation cannot be substantiated.')
        self.assertEqual(rejected['status'],'rejected')
        self.assertEqual(retry_case(self.conn,self.cid,'reviewer')['status'],'queued')

    def test_rejects_hallucinated_source_ingredients(self):
        row=dict(self.conn.execute("""SELECT id,canonical_title,brand,market,identity_attributes,
            formula_revision,identity_status FROM canonical_products WHERE id=?""",(self.cid,)).fetchone())
        pages=self._fetch('CeraVe','CeraVe Moisturizing Cream',{'sourcebeauty.com'},1)
        response=self._gemini('CeraVe','CeraVe Moisturizing Cream',pages,'model')
        response['inci_text']=INGREDIENTS+', Invented Ingredient'
        data=_validate_candidate(row,pages,response,{'sourcebeauty.com'},.85)
        self.assertEqual(data['status'],'no_result')

    def test_spf_variant_mismatch_is_not_reviewable(self):
        row=dict(self.conn.execute("""SELECT id,canonical_title,brand,market,identity_attributes,
            formula_revision,identity_status FROM canonical_products WHERE id=?""",(self.cid,)).fetchone())
        pages=self._fetch('CeraVe','CeraVe Moisturizing Cream',{'sourcebeauty.com'},1)
        response=self._gemini('CeraVe','CeraVe Moisturizing Cream',pages,'model')
        response['matched_title']='CeraVe Moisturizing Cream SPF 50'
        self.assertEqual(_validate_candidate(row,pages,response,{'sourcebeauty.com'},.85)['status'],'no_result')

    def test_registered_https_sources_only(self):
        domains={'sourcebeauty.com'}
        self.assertIsNone(_safe_url('https://127.0.0.1/private',domains))
        self.assertIsNone(_safe_url('http://sourcebeauty.com/list',domains))
        self.assertIsNone(_safe_url('https://sourcebeauty.com.evil.net/list',domains))
        self.assertEqual(_safe_url(URL,domains),URL)

    def test_operations_scope(self):
        self.assertEqual(permission_for('/admin/inci-research/queue','GET'),'read')
        self.assertEqual(permission_for('/admin/inci-research/run','POST'),'formula')
        self.assertEqual(permission_for('/admin/inci-research/queue/123/review','POST'),'formula_correction')


if __name__=='__main__':
    unittest.main()
