import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from pipeline import connection_tests as t

class ConnectionTests(unittest.TestCase):
    def source(self):
        return dict(id='example', domain='example.com', adapter_type='shopify', source_type='retailer', market='EG', language='en', provides_prices=True, provides_inci=True, rate_limit_seconds=0)
    def test_missing_ingredients_incomplete(self):
        body={'products':[{'id':1,'title':'Cream','variants':[{'id':2,'price':'100'}]}]}
        with patch.object(t,'request',side_effect=['Home','User-agent: *\nAllow: /',json.dumps(body)]):
            r=t.source_test(self.source())
        self.assertEqual(r['online'],'yes');self.assertEqual(r['functional'],'incomplete');self.assertEqual(r['priced_products'],1)
    def test_robots_blocks(self):
        with patch.object(t,'request',side_effect=['Home','User-agent: *\nDisallow: /']) as req:
            r=t.source_test(self.source())
        self.assertEqual(r['functional'],'blocked');self.assertEqual(req.call_count,2)
    def test_no_sample(self):
        s=self.source();s['adapter_type']='html'
        with patch.object(t,'request',return_value='Home'):r=t.source_test(s)
        self.assertEqual(r['functional'],'needs_sample')
    def test_auth_error_redacted_and_online(self):
        with patch.object(t,'cfg',SimpleNamespace(GEMINI_API_KEY='private-key',GEMINI_MODEL_NAME='test')),patch.object(t,'request',side_effect=HTTPError('secret',401,'private-key',{},None)):
            r=t.api_test('gemini')
        self.assertEqual(r['online'],'yes');self.assertEqual(r['functional'],'authentication_failed');self.assertNotIn('private-key',json.dumps(r))
    def test_api_schema(self):
        with patch.object(t,'cfg',SimpleNamespace(GEMINI_API_KEY='test',GEMINI_MODEL_NAME='test')),patch.object(t,'request',return_value='{}'):
            self.assertEqual(t.api_test('gemini')['functional'],'failed')
        with patch.object(t,'cfg',SimpleNamespace(GEMINI_API_KEY='test',GEMINI_MODEL_NAME='test')),patch.object(t,'request',return_value=json.dumps({'candidates':[{'content':{'parts':[{'text':'OK'}]}}]})):
            self.assertEqual(t.api_test('gemini')['functional'],'passed')
    def test_offline(self):
        with patch.object(t,'request',side_effect=TimeoutError('secret')):r=t.source_test(self.source())
        self.assertEqual(r['online'],'unreachable');self.assertNotIn('secret',json.dumps(r))
