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
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

INGREDIENTS=('Aqua, Glycerin, Cetearyl Alcohol, Butylene Glycol, Dimethicone, '
             'Niacinamide, Panthenol, Ceramide NP, Caprylic/Capric Triglyceride, '
             'Sodium Hyaluronate, Phenoxyethanol, Ethylhexylglycerin, Tocopherol')
URL='https://sourcebeauty.com/products/cerave-moisturizing-cream'


class McpCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_page_read_follows_offsets_without_dropping_the_ingredient_tail(self):
        from pipeline.inci_research import _read_mcp_product_page
        import json
        calls=[]
        class Session:
            async def call_tool(self,name,args):
                calls.append(dict(args));last='offset' in args
                meta={'ok':True,'content_ok':True,'content_complete':last,'url':URL}
                if not last:meta['next_offset']=1234
                text='[meta] '+json.dumps(meta)+'\n'+('### Ingredients\n'+INGREDIENTS if last else '# CeraVe Moisturizing Cream\nNavigation')
                return SimpleNamespace(is_error=False,content=[SimpleNamespace(type='text',text=text)])
        text=await _read_mcp_product_page(Session(),URL,{'url':{},'offset':{},'max_chars':{}})
        self.assertEqual(len(calls),2);self.assertNotIn('offset',calls[0]);self.assertEqual(calls[1]['offset'],1234)
        self.assertIn(INGREDIENTS,text)
        self.assertTrue(json.loads(text.splitlines()[0][7:])['content_complete'])

    async def test_fetch_supports_both_sdk_field_conventions(self):
        from pipeline.inci_research import _donsetch_pages
        for modern in (True,False):
            with self.subTest(modern=modern):
                schema_key='input_schema' if modern else 'inputSchema'
                error_key='is_error' if modern else 'isError'
                structured_key='structured_content' if modern else 'structuredContent'
                calls=[]
                class Session:
                    async def initialize(self):pass
                    async def list_tools(self):
                        return SimpleNamespace(tools=[SimpleNamespace(name=name,**{schema_key:{'properties':props}})
                            for name,props in [('web_search',{'query':{}}),('web_fetch',{'url':{},'focus':{},'max_chars':{}})]])
                    async def call_tool(self,name,args):
                        calls.append((name,args))
                        return SimpleNamespace(content=[SimpleNamespace(type='text',text=('x'*150 if name=='web_fetch' else 'Search completed'))],
                            **{error_key:False,structured_key:None})
                @asynccontextmanager
                async def transport(*args):yield (None,None)
                @asynccontextmanager
                async def session(*args):yield Session()
                with patch('mcp.client.stdio.stdio_client',transport),patch('mcp.ClientSession',session):
                    pages=await _donsetch_pages('CeraVe','Moisturizing Cream',{'sourcebeauty.com'},2,[URL])
                self.assertEqual(pages,[{'url':URL,'text':'x'*150}])
                self.assertEqual([c[0] for c in calls],['web_search','web_fetch'])
                self.assertNotIn('focus',calls[-1][1])
                calls.clear()
                with patch('mcp.client.stdio.stdio_client',transport),patch('mcp.ClientSession',session):
                    await _donsetch_pages('CeraVe','Moisturizing Cream',{'sourcebeauty.com'},1,[URL])
                self.assertEqual([c[0] for c in calls],['web_fetch'])


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

    def _known_retailer_research(self):
        import json
        from pipeline.aggregation_service import process_source_observation
        from pipeline.source_adapters.contracts import SourceProductObservation,SourceVariant
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id='sourcebeauty'")
        obs=SourceProductObservation('sourcebeauty','retailer','research-known',URL,
            'CeraVe Moisturizing Cream','CeraVe',market='EG',
            variants=[SourceVariant('one','50ml',price=123,available=True)],raw_payload={'id':'research-known'})
        result=process_source_observation(self.conn,obs)['results'][0]
        self.assertEqual(result['canonical_product_id'],self.cid)
        self.conn.commit();enqueue_unresolved(self.conn,[self.cid]);self.conn.commit()
        job=start_run(self.conn,1);self.conn.commit()
        page={'url':URL,'text':'[meta] '+json.dumps({'ok':True,'content_ok':True,'content_complete':True,'url':URL})+
              '\n# CeraVe Moisturizing Cream\nBrand: CeraVe\n## Full Ingredients\n'+INGREDIENTS+'\n## Additional Information\n50ml'}
        with patch('pipeline.inci_research._gemini_extract',side_effect=AssertionError('No model request expected')) as model:
            run_queue(job['run_id'],self.path,fetcher=lambda *args:[page])
            model.assert_not_called()
        return result['observation_id']

    def test_verified_retailer_page_uses_no_model_and_still_requires_review(self):
        oid=self._known_retailer_research()
        item=list_queue(self.conn,status='needs_review')['items'][0]
        self.assertIsNone(item['model_name']);self.assertIsNone(item['model_confidence'])
        self.assertEqual(item['source_metadata']['method'],'known_retailer_detail')
        self.assertEqual(item['source_metadata']['retained_page']['url'],URL)
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])
        evidence=self.conn.execute('SELECT source_name,source_record_id,completeness FROM inci_audit_evidence WHERE id=?',(item['evidence_id'],)).fetchone()
        self.assertEqual(tuple(evidence),('source:sourcebeauty',str(oid),'unverified'))
        approved=review_case(self.conn,self.cid,'approve','reviewer','Reviewed exact retailer product and the complete retained ingredient declaration.')
        self.assertEqual(approved['status'],'approved')
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],INGREDIENTS)

    def test_research_approval_rechecks_source_link_and_latest_observation(self):
        self._known_retailer_research()
        self.conn.execute("UPDATE product_identity_links SET status='rejected'")
        with self.assertRaisesRegex(ValueError,'no longer identity-verified'):
            review_case(self.conn,self.cid,'approve','reviewer','Reviewed exact full declaration at current URL.')
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])
        self.conn.execute("UPDATE product_identity_links SET status='accepted'")
        self.conn.execute("INSERT INTO source_product_observations(source_id,source_product_id,source_url,raw_title,raw_brand,market,raw_payload,observed_at,payload_hash,classification,identity_attributes) SELECT source_id,source_product_id,source_url,raw_title,raw_brand,market,raw_payload,observed_at,payload_hash || '-changed',classification,identity_attributes FROM source_product_observations WHERE source_product_id='research-known'")
        with self.assertRaisesRegex(ValueError,'observation changed'):
            review_case(self.conn,self.cid,'approve','reviewer','Reviewed exact full declaration at current URL.')

    def test_gemini_research_evidence_id_is_not_an_origin_retailer_id(self):
        enqueue_unresolved(self.conn,[self.cid]);self.conn.commit()
        job=start_run(self.conn,1);self.conn.commit()
        run_queue(job['run_id'],self.path,fetcher=self._fetch,extractor=self._gemini)
        item=list_queue(self.conn,status='needs_review')['items'][0]
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES('sourcebeauty','Source Beauty','sourcebeauty.com')")
        self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
            VALUES(?,'sourcebeauty','unverified','Other listing','CeraVe','Moisturizer','https://sourcebeauty.com/products/unverified',?)""",(item['evidence_id'],self.cid))
        result=review_case(self.conn,self.cid,'approve','reviewer','Reviewed exact product and complete regional source declaration.')
        self.assertEqual(result['status'],'approved')
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],INGREDIENTS)

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
