"""Admin reads published canonicals without copying formulas into retailer rows."""
import json
import sqlite3
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver
from routers import admin

RAW='Water, Glycerin, Niacinamide, Phenoxyethanol'


class BorrowedConnection:
    def __init__(self,conn):self.conn=conn
    def close(self):pass
    def __getattr__(self,name):return getattr(self.conn,name)


class AdminAuthoritativeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:',check_same_thread=False);self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close);init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES('sourcebeauty','Source Beauty','sourcebeauty.com')")
        self.cid=self.conn.execute("""INSERT INTO canonical_products(canonical_title,brand,category,normalized_key,
            is_skincare,identity_status,catalog_visible,ingredients_raw,ingredients_parsed,actives,
            ingredient_source,ingredient_source_url,formulation_published_at,min_price,max_price)
            VALUES('CeraVe Treatment','CeraVe','Treatment','authoritative-admin',1,'verified',1,?,?,?,
                'source:sourcebeauty','https://sourcebeauty.com/products/cream',CURRENT_TIMESTAMP,100,100)""",
            (RAW,json.dumps(['water','glycerin','niacinamide','phenoxyethanol']),json.dumps(['niacinamide']))).lastrowid
        ids=[]
        for pid in ('verified','unverified'):
            ids.append(self.conn.execute("""INSERT INTO products(store_id,store_product_id,title,brand,category,
                is_skincare,canonical_product_id,price,product_url) VALUES('sourcebeauty',?,'CeraVe Treatment','CeraVe','Serum',1,?,100,?)""",(pid,self.cid,'https://sourcebeauty.com/products/'+pid)).lastrowid)
        self.verified,self.unverified=ids
        CanonicalIdentityResolver(self.conn).record_link('sourcebeauty','verified',self.cid,'accepted','reviewed_source',{},reviewer='test-reviewer')
        self.conn.commit()
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch('routers.admin.get_connection',return_value=BorrowedConnection(self.conn)))
        stack.enter_context(patch('routers.admin.get_recommendations',return_value=None))
        stack.enter_context(patch('routers.admin.evaluate_product_dermo',return_value={}))
        self.lookup=stack.enter_context(patch('pipeline.inci_matcher.match_with_inci_db',return_value=None))
        stack.enter_context(patch('pipeline.rate_limiter.langsearch_limiter.get_quota_status',return_value={}))
        app=FastAPI();app.include_router(admin.router);self.client=TestClient(app)
        self.addCleanup(self.client.close)

    def test_overview_counts_formulations_once_and_store_coverage_uses_verified_associations(self):
        data=self.client.get('/admin/overview').json();v=data['catalog_vitals']
        self.assertEqual(v['products_with_ingredients'],1)
        self.assertEqual(v['coverage_percent'],100)
        self.assertEqual(v['total_store_listings'],2)
        self.assertEqual(next(s for s in data['stores'] if s['id']=='sourcebeauty')['with_ingredients'],1)

    def test_reference_inventory_is_distinct_from_linked_catalog_matches(self):
        self.conn.executemany('INSERT INTO inci_reference(brand,product_name,ingredients,source) VALUES(?,?,?,?)',
            [('CeraVe','Reference cream',RAW,'skinsafe'),('CeraVe','Other reference',RAW,'official_inci')])
        self.conn.commit()
        v=self.client.get('/admin/overview').json()['catalog_vitals']
        self.assertEqual(v['inci_reference_products'],2)
        self.assertEqual(v['skinsafe_reference_products'],1)
        self.assertEqual(v['inci_matched_products'],0)
        self.assertEqual(v['products_with_ingredients'],1)

    def test_product_filters_project_canonical_formula_and_category_without_trusting_unverified_mapping(self):
        data=self.client.get('/admin/products',params={'has_ingredients':'true'}).json()
        self.assertEqual([p['id'] for p in data['products']],[self.verified])
        row=data['products'][0];self.assertEqual(row['category'],'Treatment')
        self.assertEqual(row['listing_category'],'Serum')
        self.assertEqual(row['ingredient_source'],'source:sourcebeauty')
        missing=self.client.get('/admin/products',params={'has_ingredients':'false'}).json()
        self.assertEqual([p['id'] for p in missing['products']],[self.unverified])
        filtered=self.client.get('/admin/products',params={'category':'Treatment'}).json()
        self.assertEqual([p['id'] for p in filtered['products']],[self.verified])

    def test_telemetry_uses_published_master_and_preserves_original_retailer_field(self):
        response=self.client.get(f'/admin/products/{self.verified}/telemetry')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(data['ingredients_forensics']['ingredients_raw'],RAW)
        self.assertEqual(data['ingredients_forensics']['listing_ingredients_raw'],'')
        self.assertEqual(data['ingredients_forensics']['formulation_scope'],'published_canonical')
        self.lookup.assert_not_called()
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM products WHERE id=?',(self.verified,)).fetchone()[0])
        untrusted=self.client.get(f'/admin/products/{self.unverified}/telemetry').json()
        self.assertFalse(untrusted['ingredients_forensics']['has_ingredients'])
        self.assertEqual(untrusted['ingredients_forensics']['ingredients_raw'],'')

    def test_real_zero_values_remain_zero_when_canonical_is_outside_skincare(self):
        self.conn.execute('UPDATE canonical_products SET is_skincare=0 WHERE id=?',(self.cid,));self.conn.commit()
        v=self.client.get('/admin/overview').json()['catalog_vitals']
        self.assertEqual(v['active_skincare_products'],0)
        self.assertEqual(v['products_with_ingredients'],0)
        self.assertEqual(v['coverage_percent'],0)


if __name__=='__main__':unittest.main()
