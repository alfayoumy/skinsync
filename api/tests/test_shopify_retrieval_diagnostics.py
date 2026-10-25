"""Shopify page failures must preserve true HTTP/network diagnostics."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from pipeline.harvester import fetch_shopify_page
from security.admin import permission_for


class FakeHTTPResponse:
    def __init__(self,payload):
        self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def read(self):return self.payload


class ShopifyRetrievalDiagnosisTests(unittest.TestCase):
    def test_429_identifies_rate_limit_without_misreporting_catalog_end(self):
        error=urllib.error.HTTPError(
            'https://bashrety.tv/products.json',429,'Too Many Requests',
            {'Retry-After':'60'},io.BytesIO(b''))
        with patch('pipeline.harvester.urllib.request.urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'HTTP 429 .*Retry-After 60'):
                fetch_shopify_page('bashrety.tv',2,retries=1,strict=True)

    def test_403_is_not_empty_end_of_catalog(self):
        error=urllib.error.HTTPError(
            'https://drdawoodstore.com/products.json',403,'Forbidden',{},None)
        with patch('pipeline.harvester.urllib.request.urlopen',side_effect=error) as mocked:
            with self.assertRaisesRegex(RuntimeError,'HTTP 403'):
                fetch_shopify_page('drdawoodstore.com',2,retries=3,strict=True)
        self.assertEqual(mocked.call_count,1)

    def test_connection_failure_reports_network_category(self):
        with patch('pipeline.harvester.urllib.request.urlopen',
                   side_effect=urllib.error.URLError('connection refused')):
            with self.assertRaisesRegex(RuntimeError,'Network failure'):
                fetch_shopify_page('rawafrican.net',1,retries=1,strict=True)

    def test_html_response_is_not_mistaken_for_empty_catalog(self):
        with patch('pipeline.harvester.urllib.request.urlopen',
                   return_value=FakeHTTPResponse(b'<html>challenge</html>')):
            with self.assertRaisesRegex(RuntimeError,'Invalid Shopify JSON'):
                fetch_shopify_page('rawafrican.net',1,retries=1,strict=True)

    def test_nonlist_products_is_invalid_not_empty(self):
        with patch('pipeline.harvester.urllib.request.urlopen',
                   return_value=FakeHTTPResponse(b'{"products":null}')):
            with self.assertRaisesRegex(RuntimeError,'Invalid Shopify JSON'):
                fetch_shopify_page('rawafrican.net',1,retries=1,strict=True)

    def test_real_empty_array_is_catalog_end(self):
        with patch('pipeline.harvester.urllib.request.urlopen',
                   return_value=FakeHTTPResponse(b'{"products":[]}')):
            self.assertEqual(fetch_shopify_page('rawafrican.net',99,retries=1,strict=True),[])

    def test_probe_route_requires_collection_permission(self):
        self.assertEqual(permission_for('/admin/workflows/products/discovery-probe','POST'),'collection')


if __name__=='__main__':
    unittest.main()
