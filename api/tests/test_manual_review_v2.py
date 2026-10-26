"""Unified manual pair-review regression tests, entirely offline on isolated SQLite."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from admin_test_support import authenticate
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import app
from pipeline.db_manager import init_db
from security.admin import permission_for


class ManualReviewV2Tests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path=str(Path(directory.name)/"review.sqlite3")
        conn=sqlite3.connect(self.path)
        conn.row_factory=sqlite3.Row
        init_db(conn=conn,seed_defaults=False)
        conn.execute("""INSERT INTO stores(id,name,domain)
            VALUES('reviewstore','Review Test Store','reviewstore.example')""")
        conn.execute("""INSERT INTO sources
            (id,name,domain,source_type,adapter_type,market,enabled)
            VALUES('reviewstore','Review Test Store','reviewstore.example','retailer','shopify','EG',1)""")
        conn.execute("""INSERT INTO canonical_products
            (canonical_title,brand,category,normalized_key,is_skincare,identity_status,market)
            VALUES('Demo Hydrating Cream 50ml','Demo','Moisturizer','review-can-01',1,'verified','EG')""")
        self.cid=conn.execute("SELECT id FROM canonical_products WHERE normalized_key='review-can-01'").fetchone()[0]
        conn.execute("""INSERT INTO products(store_id,store_product_id,title,brand,category,
            product_url,canonical_product_id,image_url)
            VALUES('reviewstore','r-1','Demo Hydrating Cream 50ml','Demo','Moisturizer',
                   'https://reviewstore.example/known',?,'https://reviewstore.example/known.jpg')""",
            (self.cid,))
        conn.execute("""INSERT INTO products(store_id,store_product_id,title,brand,category,
            product_url,image_url)
            VALUES('reviewstore','r-2','Demo Hydrating Cream 50ml','Demo','Moisturizer',
                   'https://reviewstore.example/other','https://reviewstore.example/other.jpg')""")
        self.pid=conn.execute("SELECT id FROM products WHERE store_product_id='r-2'").fetchone()[0]
        details=json.dumps({"product_id":self.pid,"identity":{"candidates":[{"canonical_product_id":self.cid}]}})
        conn.execute("""INSERT INTO product_review_queue(case_key,kind,source_id,source_product_id,
            canonical_product_id,details,evidence_hash)
            VALUES ('review-identity-01','identity','reviewstore','r-2',?,?,?)""",
            (self.cid,details,'case-fp-original'))
        self.rid=conn.execute("SELECT id FROM product_review_queue WHERE case_key='review-identity-01'").fetchone()[0]
        from pipeline.inci_research import ensure_schema
        ensure_schema(conn)
        conn.execute("""INSERT INTO inci_research_queue
            (canonical_product_id,status,identity_fingerprint,source_url,source_domain,
             source_title,evidence_hash,ingredients_raw,completeness,confidence)
            VALUES (?,'needs_review','idfp-01','https://reviewstore.example/inci',
                'reviewstore.example','Demo Hydrating Cream','evidence-fp-01',
                'Aqua, Glycerin, Cetearyl Alcohol, Tocopherol','full',0.92)""",(self.cid,))
        conn.commit()
        conn.close()
        def connect():
            connection=sqlite3.connect(self.path)
            connection.row_factory=sqlite3.Row
            return connection
        patched=patch('routers.manual_reviews_v2.get_connection',side_effect=connect)
        patched.start()
        self.addCleanup(patched.stop)
        self.client=TestClient(app,headers=authenticate(self))
        self.addCleanup(self.client.close)

    def db(self):
        connection=sqlite3.connect(self.path)
        connection.row_factory=sqlite3.Row
        self.addCleanup(connection.close)
        return connection

    def current(self,kind):
        response=self.client.get('/admin/v2/manual-reviews',params={'kind':kind})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_identity_pair_has_images_links_and_no_reviewer_input(self):
        data=self.current('identity')
        self.assertEqual(data['reviewer'],'test-admin')
        self.assertEqual(len(data['items']),1)
        pair=data['items'][0]
        self.assertEqual(pair['left']['image'],'https://reviewstore.example/other.jpg')
        self.assertEqual(pair['right']['image'],'https://reviewstore.example/known.jpg')
        self.assertEqual(pair['right']['canonical_id'],self.cid)
        self.assertTrue(pair['left']['url'].startswith('https://'))
        self.assertTrue(pair['right']['url'].startswith('https://'))

    def test_personal_skip_does_not_mutate_identity_or_inci(self):
        pair=self.current('identity')['items'][0]
        response=self.client.post(f"/admin/v2/manual-reviews/identity/{self.rid}",
            json={'action':'skip','fingerprint':pair['fingerprint'],'reviewer':'forged-account'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['reviewer'],'test-admin')
        self.assertEqual(self.current('identity')['counts']['skipped_by_you'],1)
        conn=self.db()
        self.assertEqual(conn.execute("SELECT status FROM product_review_queue WHERE id=?",(self.rid,)).fetchone()[0],'pending')
        self.assertEqual(conn.execute("SELECT status FROM inci_research_queue WHERE canonical_product_id=?",(self.cid,)).fetchone()[0],'needs_review')
        self.assertEqual(conn.execute("SELECT reviewer FROM admin_v2_manual_review_events LIMIT 1").fetchone()[0],'test-admin')

    def test_identity_no_match_records_rejection_with_optional_comment(self):
        pair=self.current('identity')['items'][0]
        r=self.client.post(f"/admin/v2/manual-reviews/identity/{self.rid}",json={
            'action':'no_match','fingerprint':pair['fingerprint'],'comment':''})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['comment_saved'])
        conn=self.db()
        row=conn.execute("SELECT status,reviewed_by,review_notes FROM product_review_queue WHERE id=?",(self.rid,)).fetchone()
        self.assertEqual(row['status'],'confirmed')
        self.assertEqual(row['reviewed_by'],'test-admin')
        self.assertGreater(len(row['review_notes']),8)
        self.assertEqual(self.current('identity')['items'],[])

    def test_inci_no_match_rejects_only_candidate_and_stale_vote_fails(self):
        pair=self.current('inci')['items'][0]
        self.assertEqual(pair['left']['canonical_id'],self.cid)
        self.assertIn('Aqua',pair['right']['ingredients'])
        response=self.client.post(f"/admin/v2/manual-reviews/inci/{self.cid}",json={
            'action':'no_match','fingerprint':pair['fingerprint']})
        self.assertEqual(response.status_code,200,response.text)
        conn=self.db()
        self.assertEqual(conn.execute("SELECT status FROM inci_research_queue WHERE canonical_product_id=?",(self.cid,)).fetchone()[0],'rejected')
        self.assertEqual(conn.execute("SELECT reviewed_by FROM inci_research_queue WHERE canonical_product_id=?",(self.cid,)).fetchone()[0],'test-admin')
        self.assertIsNone(conn.execute("SELECT ingredients_raw FROM canonical_products WHERE id=?",(self.cid,)).fetchone()[0])
        stale=self.client.post(f"/admin/v2/manual-reviews/inci/{self.cid}",json={
            'action':'no_match','fingerprint':pair['fingerprint']})
        self.assertEqual(stale.status_code,409)

    def test_inci_other_market_cannot_quick_publish(self):
        conn=self.db()
        conn.execute("UPDATE sources SET market='US' WHERE id='reviewstore'")
        conn.commit()
        pair=self.current('inci')['items'][0]
        self.assertTrue(pair['requires_market_confirmation'])
        response=self.client.post(f"/admin/v2/manual-reviews/inci/{self.cid}",json={
            'action':'match','fingerprint':pair['fingerprint']})
        self.assertEqual(response.status_code,409,response.text)
        self.assertIsNone(conn.execute("SELECT ingredients_raw FROM canonical_products WHERE id=?",(self.cid,)).fetchone()[0])

    def test_rejects_stale_identity_fingerprint_and_unauthorized_methods(self):
        response=self.client.post(f"/admin/v2/manual-reviews/identity/{self.rid}",
            json={'action':'match','fingerprint':'0'*64})
        self.assertEqual(response.status_code,409)
        self.assertEqual(permission_for('/admin/v2/manual-reviews/identity/123','POST'),'identity')
        self.assertEqual(permission_for('/admin/v2/manual-reviews/inci/123','POST'),'formula_correction')
        self.assertEqual(permission_for('/admin/v2/manual-reviews?kind=inci','GET'),'read')


if __name__=='__main__':
    unittest.main()
