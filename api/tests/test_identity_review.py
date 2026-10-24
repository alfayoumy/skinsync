from admin_test_support import authenticate
import json
import sqlite3
import unittest
from unittest.mock import patch

from pipeline.db_manager import init_db
from pipeline.identity_review import (audit_canonical_identity, assess_canonical_identity,
    record_identity_review, review_identity_case)
from pipeline.product_linker import link_catalog_products


class TestCanonicalIdentityReview(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        init_db(conn=self.conn, seed_defaults=False)
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES ('test','Test','test.example')")
        patcher = patch('services.catalog_service.load_catalog_from_db')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.conn.close)

    def canonical(self, cid, title='Brand Hydrating Cream', key=None, raw=None):
        self.conn.execute('''INSERT INTO canonical_products
            (id,canonical_title,brand,category,normalized_key,ingredients_raw)
            VALUES (?,?,'Brand','Moisturizer',?,?)''', (cid,title,key or str(cid),raw))

    def offer(self, pid, title='Brand Hydrating Cream 50ml', cid=None, raw=None, ref=None):
        self.conn.execute('''INSERT INTO products
            (id,store_id,title,brand,category,product_url,canonical_product_id,ingredients_raw,ingredient_source,inci_matched_id)
            VALUES (?,'test',?,'Brand','Moisturizer',?,?,?,'store_page_tab',?)''',
            (pid,title,f'https://test.example/{pid}',cid,raw,ref))

    def link(self):
        self.conn.commit()
        return link_catalog_products(conn=self.conn)

    def test_retitle_reuses_durable_canonical_and_formula(self):
        self.canonical(100,key='old-derived-key',raw='Water, Glycerin, Panthenol, Phenoxyethanol')
        self.offer(1,cid=100)
        self.link()
        self.conn.execute("UPDATE products SET title='Brand Hydrating Cream 100ml New Packaging' WHERE id=1")
        self.link()
        self.assertEqual(self.conn.execute('SELECT canonical_product_id FROM products').fetchone()[0],100)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
        self.assertIn('Panthenol',self.conn.execute('SELECT ingredients_raw FROM canonical_products').fetchone()[0])

    def test_duplicate_existing_canonicals_are_reviewed_never_merged(self):
        self.canonical(100)
        self.canonical(200)
        self.offer(1,cid=100)
        self.offer(2,cid=200)
        self.link()
        self.assertEqual([r[0] for r in self.conn.execute('SELECT canonical_product_id FROM products ORDER BY id')],[100,200])
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM canonical_identity_reviews WHERE kind='duplicate_canonicals'").fetchone()[0],0)

    def test_existing_identity_is_found_before_proposing_new(self):
        self.canonical(100,key='legacy-key')
        self.offer(1)
        result=self.link()
        self.assertEqual(result['newly_created_canonical'],0)
        self.assertEqual(self.conn.execute('SELECT canonical_product_id FROM products').fetchone()[0],100)

    def test_shared_reference_cannot_join_spf_variants(self):
        self.offer(1,'Brand Sunscreen SPF 30 50ml',ref=10)
        self.offer(2,'Brand Sunscreen SPF 50 50ml',ref=10)
        self.link()
        self.assertEqual(self.conn.execute('SELECT COUNT(DISTINCT canonical_product_id) FROM products').fetchone()[0],2)

    def test_audit_detects_identity_and_formula_conflicts_without_writing(self):
        self.canonical(100,'Brand Day Cream',raw='Water, Glycerin, Panthenol, Phenoxyethanol')
        self.offer(1,'Brand Night Cream',cid=100,raw='Water, Glycerin, Niacinamide, Phenoxyethanol')
        before=self.conn.total_changes
        result=audit_canonical_identity(self.conn)
        self.assertEqual(self.conn.total_changes,before)
        self.assertEqual({r['kind'] for r in result['findings']},{'mixed_cluster','mixed_formulations'})

    def test_conflicting_offer_is_not_automatically_attached(self):
        self.canonical(100,raw='Water, Glycerin, Panthenol, Phenoxyethanol')
        self.offer(1,cid=100)
        self.offer(2,raw='Water, Glycerin, Niacinamide, Phenoxyethanol')
        result=self.link()
        self.assertIsNone(self.conn.execute('SELECT canonical_product_id FROM products WHERE id=2').fetchone()[0])
        self.assertEqual(result['deferred_offers'],1)

    def test_review_is_idempotent_and_changed_evidence_reopens(self):
        first=record_identity_review(self.conn,'mixed_cluster',[100],[1],{'reason':'day/night'})
        review_identity_case(self.conn,first,'confirmed','admin','Split needs a separate reviewed correction.')
        second=record_identity_review(self.conn,'mixed_cluster',[100],[1],{'reason':'day/night'})
        self.assertEqual(first,second)
        self.assertEqual(self.conn.execute('SELECT status FROM canonical_identity_reviews').fetchone()[0],'confirmed')
        record_identity_review(self.conn,'mixed_cluster',[100],[1],{'reason':'SPF conflict'})
        self.assertEqual(self.conn.execute('SELECT status FROM canonical_identity_reviews').fetchone()[0],'pending')

    def test_distinguishes_new_existing_duplicate_and_mixed(self):
        self.canonical(100)
        self.canonical(200)
        cs=[dict(r) for r in self.conn.execute('SELECT * FROM canonical_products')]
        def offer(pid,cid,title='Brand Hydrating Cream'):
            return dict(id=pid,canonical_product_id=cid,title=title,brand='Brand')
        self.assertEqual(assess_canonical_identity([offer(1,None)],cs)['decision'],'propose_new_canonical')
        self.assertEqual(assess_canonical_identity([offer(1,100)],cs)['decision'],'existing_canonical')
        self.assertEqual(assess_canonical_identity([offer(1,100),offer(2,200)],cs)['decision'],'duplicate_canonicals')
        self.assertEqual(assess_canonical_identity([offer(1,100,'Brand Night Cream'),offer(2,100,'Brand Day Cream')],cs)['decision'],'mixed_cluster')

    def test_review_rollback_belongs_to_caller(self):
        self.conn.commit()
        record_identity_review(self.conn,'mixed_cluster',[100],[1],{'reason':'test'})
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_identity_reviews').fetchone()[0],0)


class TestIdentityReviewApi(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.directory.name + '/identity.db'
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        init_db(conn=conn, seed_defaults=False)
        for cid in (100, 200):
            conn.execute('''INSERT INTO canonical_products(id,canonical_title,brand,category,normalized_key)
                VALUES (?,'Brand Hydrating Cream','Brand','Moisturizer',?)''', (cid,str(cid)))
        conn.execute("UPDATE canonical_products SET identity_status='verified'")
        conn.commit()
        conn.close()
        def connect():
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            return connection
        patcher = patch('routers.admin.get_connection', side_effect=connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        from app import app
        from fastapi.testclient import TestClient
        self.client = TestClient(app, headers=authenticate(self))
        self.addCleanup(self.client.close)

    def test_read_only_audit_then_persist_and_review(self):
        result = self.client.get('/admin/identity/audit')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['review_count'],1)
        result = self.client.post('/admin/identity/audit')
        self.assertEqual(result.status_code,200)
        review_id = result.json()['findings'][0]['review_id']
        repeated = self.client.post('/admin/identity/audit').json()
        self.assertEqual(repeated['findings'][0]['review_id'],review_id)
        decision = self.client.post(f'/admin/identity/reviews/{review_id}',json={
            'status':'confirmed','reviewer':'admin','notes':'Duplicate identity confirmed; preserve both records pending correction.'})
        self.assertEqual(decision.status_code,200)
        reviews = self.client.get('/admin/identity/reviews?status=confirmed').json()
        self.assertEqual(reviews['total'],1)
        self.assertEqual(reviews['reviews'][0]['canonical_ids'],[100,200])
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],2)
        finally:
            conn.close()

    def test_invalid_review_does_not_change_case(self):
        result = self.client.post('/admin/identity/audit').json()
        review_id = result['findings'][0]['review_id']
        response = self.client.post(f'/admin/identity/reviews/{review_id}',json={
            'status':'merged','reviewer':'','notes':''})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.get('/admin/identity/reviews?status=pending').json()['total'],1)

    def test_legacy_match_publishes_only_verified_candidate_despite_offer_ingredients(self):
        original = 'Water, Glycerin, Niacinamide, Phenoxyethanol'
        proposed = 'Water, Glycerin, Panthenol, Phenoxyethanol'
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO stores(id,name,domain) VALUES ('test','Test','test.example')")
        conn.execute('''INSERT INTO products(id,store_id,title,brand,category,product_url,canonical_product_id,ingredients_raw)
            VALUES (1,'test','Brand Hydrating Cream','Brand','Moisturizer','https://test.example/1',100,?)''',(original,))
        from pipeline.canonical_identity_resolver import CanonicalIdentityResolver
        CanonicalIdentityResolver(conn).record_link('test','1',100,'accepted','manual_review',{},'fixture-reviewer')
        conn.commit()
        conn.close()
        def connect():
            connection=sqlite3.connect(self.path)
            connection.row_factory=sqlite3.Row
            return connection
        # Approval of the language-model match does not verify the INCI source identity.
        for status, expected in [('pending_jev',None),('jev_approved',None)]:
            with self.subTest(status=status):
                match=dict(ingredients=proposed,matched_id=10,matched_name='Brand Hydrating Cream',
                    hybrid_score=.95,gate4_jev_status=status)
                with patch('routers.inci.get_connection',side_effect=connect), \
                     patch('routers.inci.match_with_inci_db',return_value=match), \
                     patch('routers.inci.load_catalog_from_db'):
                    response=self.client.post('/products/1/match-inci',json={'scoring_mode':'local'})
                self.assertEqual(response.status_code,200)
                conn=connect()
                try:
                    self.assertEqual(conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=100').fetchone()[0],expected)
                    self.assertEqual(conn.execute('SELECT ingredients_raw FROM products WHERE id=1').fetchone()[0],original)
                    last=conn.execute('SELECT completeness FROM inci_audit_evidence ORDER BY id DESC LIMIT 1').fetchone()[0]
                    self.assertEqual(last,'full' if status=='jev_approved' else 'unverified')
                    if status=='jev_approved':
                        decision=conn.execute('SELECT decision FROM inci_audit_evidence ORDER BY id DESC LIMIT 1').fetchone()[0]
                        self.assertEqual(decision,'identity_blocked')
                finally:
                    conn.close()
