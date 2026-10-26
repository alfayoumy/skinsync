import json
import sqlite3
import unittest
import urllib.error
from unittest.mock import patch,MagicMock
from pipeline.db_manager import init_db
from pipeline.aggregation_service import queue_review
from pipeline.identity_pairs import product_pairs,pair_prompt,compare_case_pairs,aggregate_pairs
from pipeline.product_identity_gemini import call_gemini_identity_batch


class OpenConnection:
    def __init__(self,conn):self.conn=conn
    def close(self):pass
    def __getattr__(self,name):return getattr(self.conn,name)


class IdentityPairTests(unittest.TestCase):
    def test_http_failure_reports_status_without_request_credentials(self):
        pair=product_pairs(self.case)[0]
        failure=urllib.error.HTTPError('https://provider.test/?key=private-secret',429,'quota',{},None)
        with patch.dict('os.environ',{'GEMINI_API_KEY':'private-secret'}),patch('pipeline.inci_matcher.gemini_limiter.acquire',return_value=True),patch('urllib.request.urlopen',side_effect=failure):
            with self.assertRaises(RuntimeError) as error:call_gemini_identity_batch([pair])
        self.assertIn('HTTP 429',str(error.exception))
        self.assertNotIn('private-secret',str(error.exception))

    def test_quota_diagnostic_distinguishes_daily_from_minute_limits(self):
        import io
        from pipeline.product_identity_gemini import _provider_failure_reason
        body={'error':{'message':'Private request URL must not be exposed',
            'details':[{'violations':[{'quotaId':'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}}
        failure=urllib.error.HTTPError('https://provider.test/?key=secret',429,'quota',{},io.BytesIO(json.dumps(body).encode()))
        reason=_provider_failure_reason(failure)
        self.assertIn('daily provider quota',reason)
        self.assertNotIn('secret',reason);self.assertNotIn('Private request',reason)

    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close);init_db(conn=self.conn,seed_defaults=False)
        rid=queue_review(self.conn,'identity','sourcebeauty','white',None,{'title':'White Musk Lotion'})
        hash_value=self.conn.execute('SELECT evidence_hash FROM product_review_queue WHERE id=?',(rid,)).fetchone()[0]
        self.case={'id':rid,'evidence_hash':hash_value,'source_product_id':'white','details':{},
            'source_comparison':{'source':{'title':'White Musk Travel Lotion','brand':'Bubblzz',
                'source_id':'sourcebeauty','source_product_id':'white','url':'https://sourcebeauty.com/products/white',
                'identity_attributes':{}},'candidates':[
                    {'canonical_product_id':i,'canonical_title':title,'brand':'Bubblzz','identity_attributes':{},
                     'retailer_listings':[{'title':'This extra listing must never enter the provider request.'}]}
                    for i,title in enumerate(['Cupcake Lotion','Strawberries Lotion','Bonbon Lotion','White Musk Lotion'],11)]}}
        self.calls=[]

    def provider(self,cases):
        self.assertEqual(len(cases),1)
        record=json.loads(pair_prompt(cases).split('Pair:\n',1)[1])
        self.assertEqual(len(record['products']),2)
        self.assertNotIn('retailer_listings',record['products'][1])
        self.calls.append(cases[0]['pair_key'])
        return {self.case['id']:{'verdict':'same_product' if cases[0]['pair_canonical_id']==14 else 'different_product',
            'confidence':.99,'reason':'Verdict for these two products only.','model_name':'test'}}

    def test_four_candidates_make_four_distinct_two_product_requests(self):
        answer,calls=compare_case_pairs(self.case,self.provider,lambda:OpenConnection(self.conn),10)
        self.assertEqual(calls,4);self.assertEqual(len(set(self.calls)),4)
        self.assertEqual(answer['verdict'],'same_product');self.assertEqual(answer['matched_canonical_id'],14)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_pair_results WHERE verdict IS NOT NULL').fetchone()[0],4)

    def test_request_budget_and_resume_reuse_completed_pairs(self):
        answer,calls=compare_case_pairs(self.case,self.provider,lambda:OpenConnection(self.conn),2)
        self.assertIsNone(answer);self.assertEqual(calls,2)
        answer,calls=compare_case_pairs(self.case,self.provider,lambda:OpenConnection(self.conn),2)
        self.assertEqual(calls,2);self.assertEqual(answer['matched_canonical_id'],14)
        self.assertEqual(len(set(self.calls)),4)
        _,calls=compare_case_pairs(self.case,self.provider,lambda:OpenConnection(self.conn),0)
        self.assertEqual(calls,0)

    def test_failed_pair_does_not_lose_completed_pairs(self):
        def fail_third(cases):
            if len(self.calls)==2:raise RuntimeError('Provider unavailable')
            return self.provider(cases)
        with self.assertRaises(RuntimeError) as error:
            compare_case_pairs(self.case,fail_third,lambda:OpenConnection(self.conn),4)
        self.assertEqual(error.exception.identity_pair_calls,3)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_pair_results WHERE verdict IS NOT NULL').fetchone()[0],2)
        answer,calls=compare_case_pairs(self.case,self.provider,lambda:OpenConnection(self.conn),4)
        self.assertEqual(calls,2);self.assertEqual(answer['matched_canonical_id'],14)

    def test_new_candidate_reuses_identical_pairs_but_changed_product_inputs_do_not(self):
        from copy import deepcopy
        compare_case_pairs(self.case,self.provider,lambda:OpenConnection(self.conn),10)
        refreshed=deepcopy(self.case);refreshed['evidence_hash']='fresh-review-snapshot'
        refreshed['source_comparison']['candidates'].append({'canonical_product_id':15,
            'canonical_title':'Another Scent Lotion','brand':'Bubblzz','identity_attributes':{}})
        answer,calls=compare_case_pairs(refreshed,self.provider,lambda:OpenConnection(self.conn),10)
        self.assertEqual(calls,1);self.assertEqual(answer['matched_canonical_id'],14)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM identity_gemini_pair_results WHERE evidence_hash=? AND verdict IS NOT NULL',('fresh-review-snapshot',)).fetchone()[0],5)
        refreshed['source_comparison']['source']['title']='White Musk Travel Lotion Reformulated'
        _,calls=compare_case_pairs(refreshed,self.provider,lambda:OpenConnection(self.conn),10)
        self.assertEqual(calls,5)

    def test_prompt_rejects_group_input_and_multiple_pairs(self):
        with self.assertRaises(ValueError):pair_prompt([self.case])
        pairs=product_pairs(self.case)
        with self.assertRaises(ValueError):pair_prompt(pairs[:2])
        pairs[0]['pair_products'].append({'title':'Third product','brand':'Bubblzz'})
        with self.assertRaises(ValueError):pair_prompt(pairs[:1])
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(ValueError):call_gemini_identity_batch([self.case])
            network.assert_not_called()

    def test_http_contract_rejects_multi_case_batch_size(self):
        from routers.aggregation import GeminiRunRequest
        with self.assertRaises(ValueError):GeminiRunRequest(batch_size=10)

    def test_two_positive_pairs_do_not_choose_a_winner_arbitrarily(self):
        answers=[(p,{'verdict':'same_product','confidence':.99,'reason':'Same','model_name':'test'}) for p in product_pairs(self.case)]
        result=aggregate_pairs(self.case,answers)
        self.assertEqual(result['verdict'],'uncertain');self.assertIsNone(result['matched_canonical_id'])

    def test_wire_request_has_one_pair_and_no_candidate_selection_field(self):
        pair=product_pairs(self.case)[0];bodies=[]
        def respond(request,**kwargs):
            body=json.loads(request.data);bodies.append(body)
            record=json.loads(body['contents'][0]['parts'][0]['text'].split('Pair:\n')[1])
            self.assertEqual(len(record['products']),2)
            entry={'verdict':'different_product','confidence':.99,'reason':'Different scents.'}
            response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps(entry)}]}}]}).encode()
            return response
        with patch.dict('os.environ',{'GEMINI_API_KEY':'test-key'}),patch('pipeline.inci_matcher.gemini_limiter.acquire',return_value=True),patch('urllib.request.urlopen',side_effect=respond):
            result=call_gemini_identity_batch([pair])
        self.assertEqual(len(bodies),1);self.assertEqual(result[pair['id']]['verdict'],'different_product')


if __name__=='__main__':unittest.main()
