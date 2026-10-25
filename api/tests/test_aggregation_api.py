from admin_test_support import authenticate
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from fastapi.testclient import TestClient
from app import app


class TestAggregationApi(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=self.tmp.name+'/catalog.db'
        conn=self.connect();init_db(conn=conn,seed_defaults=False)
        self.cid=CanonicalIdentityResolver(conn).create_provisional('CeraVe Moisturizing Cream','CeraVe',
            classify_product_attributes('CeraVe Moisturizing Cream','CeraVe'),extract_identity('CeraVe Moisturizing Cream','CeraVe'),True)
        conn.execute("UPDATE sources SET enabled=1 WHERE id='sourcebeauty'");conn.commit();conn.close()
        patcher=patch('routers.aggregation.get_connection',side_effect=self.connect);patcher.start();self.addCleanup(patcher.stop)
        self.client=TestClient(app, headers=authenticate(self));self.addCleanup(self.client.close)
    def connect(self):
        conn=sqlite3.connect(self.path);conn.row_factory=sqlite3.Row;return conn
    def payload(self):
        return {'id':'new','title':'CeraVe Moisturizing Cream','vendor':'CeraVe','handle':'cream',
            'body_html':'<h3>Ingredients</h3><p>Water, Glycerin, Panthenol, Phenoxyethanol</p>',
            'variants':[{'id':'small','title':'30ml','price':'100','available':True},{'id':'large','title':'50ml','price':'150','available':True}]}
    def test_ingestion_defaults_to_dry_run_and_preserves_live_database(self):
        response=self.client.post('/admin/aggregation/sources/sourcebeauty/ingest',json={'payload':self.payload()})
        self.assertEqual(response.status_code,200,response.text)
        conn=self.connect()
        try:self.assertEqual(conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],0)
        finally:conn.close()
    def test_ingest_coverage_and_publication_gate(self):
        response=self.client.post('/admin/aggregation/sources/sourcebeauty/ingest',json={'payload':self.payload(),'dry_run':False})
        self.assertEqual(response.status_code,200,response.text)
        coverage=self.client.get('/admin/aggregation/sources/sourcebeauty/coverage').json()
        self.assertEqual(coverage['additional_offers'],2)
        self.assertEqual(coverage['discovered_products'],1)
        resolve=self.client.post(f'/admin/aggregation/formulations/{self.cid}/resolve').json()
        self.assertEqual(resolve['status'],'staged')
        self.assertFalse(resolve['published'])
    def test_unknown_brand_creates_provisional_and_reviewed_alias_can_be_corrected(self):
        payload=self.payload();payload['vendor']='Unverified Alias'
        response=self.client.post('/admin/aggregation/sources/sourcebeauty/ingest',
                                  json={'payload':payload,'dry_run':False})
        self.assertEqual(response.status_code,200,response.text)
        reviews=self.client.get('/admin/aggregation/reviews').json()['reviews']
        self.assertFalse(any(r['kind']=='identity' for r in reviews))
        conn=self.connect()
        try:
            new=conn.execute("""SELECT id,identity_status,brand_id FROM canonical_products
                WHERE id!=?""",(self.cid,)).fetchone()
            self.assertIsNotNone(new)
            self.assertEqual(new['identity_status'],'provisional')
            self.assertEqual(conn.execute('SELECT status FROM brands WHERE id=?',(new['brand_id'],)).fetchone()[0],
                             'observed_unverified')
            original_id=new['id']
        finally:conn.close()
        result=self.client.post('/admin/aggregation/brands/aliases',json={
            'alias':'Unverified Alias','canonical_name':'CeraVe',
            'reviewer':'curator','reason':'Verified manufacturer brand name'})
        self.assertEqual(result.status_code,200,result.text)
        conn=self.connect()
        try:
            self.assertEqual(conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',
                       (original_id,)).fetchone()[0],'provisional')
        finally:conn.close()
    def test_rollout_cannot_skip_benchmarks(self):
        response=self.client.post('/admin/aggregation/sources/sourcebeauty/rollout',json={'report':{},'reviewer':'curator','reason':'Enable source'})
        self.assertEqual(response.status_code,422)
    def test_new_sources_and_domains_are_not_automatically_enabled(self):
        result=self.client.get('/admin/aggregation/sources').json()
        by_id={s['id']:s for s in result['sources']}
        self.assertFalse(by_id['zynah']['enabled'])
        self.assertFalse(by_id['zynah']['publish_enabled'])
        response=self.client.post('/admin/aggregation/sources/zynah/collect',json={'urls':['https://evil.example/product']})
        self.assertEqual(response.status_code,400)

    def test_source_inventory_includes_uncollected_staged_sources(self):
        response=self.client.get('/admin/aggregation/sources')
        self.assertEqual(response.status_code,200,response.text)
        rows=response.json()['sources']
        self.assertEqual(len(rows),31)
        zynah=next(row for row in rows if row['id']=='zynah')
        self.assertEqual(zynah['total_products'],0)
        self.assertEqual(zynah['observed_products'],0)
        self.assertFalse(zynah['enabled'])
        self.assertEqual(zynah['source_type'],'retailer')
        self.assertTrue(any(row['source_type']=='manufacturer' for row in rows))
        self.assertTrue(any(row['source_type']=='reference' for row in rows))

    def test_main_overview_exposes_registry_separately_from_legacy_stores(self):
        with patch('routers.admin.get_connection',side_effect=self.connect):
            response=self.client.get('/admin/overview')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(len(data['sources']),31)
        self.assertEqual(data['source_summary']['registered'],31)
        self.assertEqual(len(data['stores']),0)
        self.assertGreater(data['source_summary']['staged'],0)

    def test_connection_tests_validate_targets_and_do_not_ingest(self):
        self.assertEqual(self.client.post('/admin/aggregation/connection-tests',json={'kind':'api','ids':['unknown']}).status_code,400)
        self.assertEqual(self.client.post('/admin/aggregation/connection-tests',json={'kind':'source','ids':['sourcebeauty'],'sample_url':'https://evil.example/product'}).status_code,400)
        with patch('pipeline.connection_tests.start',return_value={'results':{},'running':['source:sourcebeauty']}) as start:
            response=self.client.post('/admin/aggregation/connection-tests',json={'kind':'source','ids':['sourcebeauty']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(start.call_args.args[0],'source')
        conn=self.connect()
        try:self.assertEqual(conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],0)
        finally:conn.close()

    def test_collection_toggle_does_not_enable_publication(self):
        for enabled in (True, False):
            response=self.client.patch('/admin/aggregation/sources/zynah',json={'enabled':enabled})
            self.assertEqual(response.status_code,200,response.text)
            conn=self.connect()
            try:
                row=conn.execute("SELECT enabled,publish_enabled FROM sources WHERE id='zynah'").fetchone()
                self.assertEqual(bool(row['enabled']),enabled)
                self.assertFalse(row['publish_enabled'])
            finally:conn.close()

    def test_legacy_sync_respects_collection_pause(self):
        from pipeline.harvester import sync_store
        conn=self.connect()
        try:
            conn.execute("UPDATE sources SET enabled=0 WHERE id='sourcebeauty'");conn.commit()
            with patch('pipeline.harvester.fetch_shopify_page') as fetch:
                result=sync_store({'id':'sourcebeauty','domain':'sourcebeauty.com'},conn=conn)
            self.assertEqual(result['status'],'skipped')
            fetch.assert_not_called()
            self.assertEqual(conn.execute("SELECT enabled FROM sources WHERE id='sourcebeauty'").fetchone()[0],0)
        finally:conn.close()
