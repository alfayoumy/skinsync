import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app
from admin_test_support import authenticate, RECORDS, TOKEN
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.formulation_service import FormulationCandidate,FormulationRepository
from pipeline.aggregation_service import queue_review,process_source_observation
from pipeline.source_adapters import ShopifyAdapter
from pipeline.source_registry import get_source
from pipeline.source_http import SourceRedirects,validate_source_url
from pipeline.aggregation_benchmark import enable_source_rollout
from pipeline.source_evaluation import create_evaluation,finalize_evaluation,label_example
from pipeline.reconciliation_service import save_reconciliation_preview,persist_reconciliation_findings

RAW='Water, Glycerin, Panthenol, Phenoxyethanol'

class AdminSecurity(unittest.TestCase):
    def setUp(self):
        self.headers=authenticate(self)
        self.client=TestClient(app);self.addCleanup(self.client.close)
    def test_all_mutation_routes_fail_before_validation_without_auth(self):
        public={'/onboarding','/recommend','/similarity/score','/dermo/score','/inci/match'}
        for route in app.routes:
            for method in (getattr(route,'methods',set()) & {'POST','PUT','PATCH','DELETE'}):
                if route.path in public:continue
                import re
                path=re.sub(r'\{[^}]+\}','1',route.path)
                with self.subTest(method=method,path=path):
                    self.assertEqual(self.client.request(method,path,json={}).status_code,401)
    def test_missing_config_fails_closed(self):
        with patch.dict('os.environ',{'SKINSYNC_ADMIN_CREDENTIALS':'{}'}):
            self.assertEqual(self.client.patch('/admin/aggregation/sources/sourcebeauty',json={'enabled':True},headers=self.headers).status_code,503)
    def test_scopes_do_not_cross_boundaries(self):
        records=json.dumps({'collector':{'token':TOKEN,'permissions':['read','collection']}})
        with patch.dict('os.environ',{'SKINSYNC_ADMIN_CREDENTIALS':records}):
            self.assertEqual(self.client.get('/admin/access',headers=self.headers).json()['name'],'collector')
            for path in ['/admin/aggregation/identity/merge','/admin/aggregation/formulations/1/resolve','/admin/aggregation/sources/sourcebeauty/rollout']:
                self.assertEqual(self.client.post(path,json={},headers=self.headers).status_code,403)
    def test_formula_scope_does_not_grant_force_correction(self):
        records=json.dumps({'publisher':{'token':TOKEN,'permissions':['read','formula']}})
        with patch.dict('os.environ',{'SKINSYNC_ADMIN_CREDENTIALS':records}):
            self.assertEqual(self.client.post('/admin/aggregation/formulations/evidence/1/approve',json={'correction':True},headers=self.headers).status_code,403)
            self.assertEqual(self.client.post('/admin/products/1/apply-tier',json={},headers=self.headers).status_code,403)
    def test_reviewer_comes_from_authenticated_actor(self):
        with patch('routers.aggregation.operation',side_effect=lambda callback:callback(None)),patch('pipeline.brand_registry.approve_brand_alias',return_value=1) as approve:
            r=self.client.post('/admin/aggregation/brands/aliases',json={'alias':'test','canonical_name':'test','reviewer':'forged','reason':'reviewed'},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(approve.call_args.args[-1],'test-admin')
    def test_untrusted_cors_origin_rejected(self):
        r=self.client.options('/admin/access',headers={'Origin':'https://untrusted.example','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'Authorization'})
        self.assertNotEqual(r.headers.get('access-control-allow-origin'),'https://untrusted.example')

class IdentityPublicationSafety(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row;self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        self.resolver=CanonicalIdentityResolver(self.conn)
        self.identity=extract_identity('CeraVe Moisturizing Cream','CeraVe')
        self.cid=self.resolver.create_provisional('CeraVe Moisturizing Cream','CeraVe',classify_product_attributes('CeraVe Moisturizing Cream','CeraVe'),self.identity,visible=True)
    def candidate(self):
        return FormulationCandidate(self.cid,'admin_override',RAW,RAW.split(', '),confidence_score=1,completeness='full')
    def test_pending_identity_case_blocks_even_force(self):
        queue_review(self.conn,'identity','sourcebeauty','one',self.cid,{'conflict':'SPF mismatch'})
        self.assertFalse(FormulationRepository().publish_formulation(self.candidate(),'reviewed',force=True,audited_by='admin',conn=self.conn))
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0])
    def test_null_canonical_review_with_candidate_also_blocks(self):
        queue_review(self.conn,'identity','sourcebeauty','one',None,{'identity':{'candidates':[{'canonical_product_id':self.cid}]}})
        self.assertFalse(FormulationRepository().publish_formulation(self.candidate(),'reviewed',conn=self.conn))
    def test_legacy_canonical_cannot_publish_without_identity_approval(self):
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy' WHERE id=?",(self.cid,))
        self.assertFalse(FormulationRepository().publish_formulation(self.candidate(),'Attempt',conn=self.conn))

    def test_bulk_skips_pending_canonical_before_any_provider_call(self):
        from pipeline.waterfall_inci_resolver import run_waterfall_pipeline
        queue_review(self.conn,'identity','sourcebeauty','one',self.cid,{'conflict':'unknown shade'})
        self.conn.commit()
        with patch('pipeline.waterfall_inci_resolver.resolve_waterfall_for_canonical_product') as resolve:
            result=run_waterfall_pipeline(conn=self.conn,revalidate_all=True,queue_gemini=False,queue_jev=False,langsearch_enabled=False,max_workers=1)
        self.assertEqual(result['total_processed'],0);resolve.assert_not_called()

    def test_verified_canonical_does_not_validate_a_conflicting_offer_origin(self):
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES ('sourcebeauty','Source Beauty','sourcebeauty.com')")
        self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
            VALUES (1,'sourcebeauty','bad-1','CeraVe Different Cream','CeraVe','Moisturizer','https://sourcebeauty.com/products/different',?)""",(self.cid,))
        self.resolver.record_link('sourcebeauty','bad-1',self.cid,'accepted','legacy_preserved',{})
        candidate=self.candidate();candidate.source_name='langsearch_web_search';candidate.source_record_id='1'
        self.assertFalse(FormulationRepository().publish_formulation(candidate,'Wrongly linked offer',force=True,conn=self.conn))

    def test_legacy_link_is_not_identity_verification(self):
        self.resolver.record_link('sourcebeauty','one',self.cid,'accepted','legacy_preserved',{})
        row=self.conn.execute('SELECT identity_status FROM product_identity_links').fetchone()
        self.assertEqual(row[0],'legacy_preserved_unverified')
    def test_rollout_does_not_publish_new_identity(self):
        self.conn.execute("UPDATE sources SET enabled=1,publish_enabled=1 WHERE id='sourcebeauty'")
        obs=ShopifyAdapter(get_source(self.conn,'sourcebeauty')).parse({'id':1,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe','variants':[{'id':1,'price':'100'}]})[0]
        result=process_source_observation(self.conn,obs)
        cid=result['results'][0]['canonical_product_id']
        row=self.conn.execute('SELECT catalog_visible,identity_status FROM canonical_products WHERE id=?',(cid,)).fetchone()
        self.assertEqual(tuple(row),(0,'provisional'))
    def test_fabricated_rollout_metrics_rejected(self):
        with self.assertRaises(ValueError):enable_source_rollout(self.conn,'sourcebeauty',{'reviewed_examples':100000,'auto_link_precision':1},'admin','review')
    def test_evaluation_and_labels_are_immutable(self):
        obs=ShopifyAdapter(get_source(self.conn,'sourcebeauty')).parse({'id':1,'title':'CeraVe Moisturizing Cream','vendor':'CeraVe','variants':[{'id':1,'price':'100'}]})[0]
        process_source_observation(self.conn,obs)
        run=create_evaluation(self.conn,'sourcebeauty','admin')['evaluation_id']
        oid=self.conn.execute('SELECT id FROM source_product_observations').fetchone()[0]
        label_example(self.conn,run,oid,self.cid,False,'Moisturizer',True,'reviewer','Compared package and identity')
        with self.assertRaises(ValueError):finalize_evaluation(self.conn,run,'admin')
        with self.assertRaises(sqlite3.IntegrityError):self.conn.execute("UPDATE source_evaluations SET samples='[]' WHERE id=?",(run,))
        with self.assertRaises(ValueError):label_example(self.conn,run,oid,self.cid,False,'Moisturizer',True,'reviewer','revise')

    def test_preserved_conflicting_title_cannot_create_verified_alias(self):
        from pipeline.product_linker import link_catalog_products
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES ('sourcebeauty','Source Beauty','sourcebeauty.com')")
        self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
            VALUES (1,'sourcebeauty','legacy-1','CeraVe Healing Cream','CeraVe','Moisturizer','https://sourcebeauty.com/products/healing',?)""",(self.cid,))
        self.conn.commit()
        with patch('services.catalog_service.load_catalog_from_db'):
            link_catalog_products(conn=self.conn)
        self.assertEqual(self.conn.execute('SELECT canonical_product_id FROM products WHERE id=1').fetchone()[0],self.cid)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_product_aliases').fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE kind='identity' AND status='pending'").fetchone()[0],1)
        self.assertFalse(FormulationRepository().publish_formulation(self.candidate(),'Attempt',force=True,conn=self.conn))

    def test_linker_preserves_previously_verified_association(self):
        from pipeline.product_linker import link_catalog_products
        self.conn.execute("INSERT INTO stores(id,name,domain) VALUES ('sourcebeauty','Source Beauty','sourcebeauty.com')")
        self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
            VALUES (1,'sourcebeauty','reviewed-1','CeraVe Moisturizing Cream','CeraVe','Moisturizer','https://sourcebeauty.com/products/cream',?)""",(self.cid,))
        self.resolver.record_link('sourcebeauty','reviewed-1',self.cid,'accepted','manual_review',{},'reviewer')
        self.conn.commit()
        with patch('services.catalog_service.load_catalog_from_db'):link_catalog_products(conn=self.conn)
        row=self.conn.execute('SELECT identity_status,reviewed_by FROM product_identity_links').fetchone()
        self.assertEqual(tuple(row),('identity_verified','reviewer'))

    def evaluated_run(self):
        from pipeline.aggregation_service import save_observation
        from pipeline.offer_service import upsert_offer
        source=get_source(self.conn,'sourcebeauty')
        examples={'Sunscreen':'Tinted Sunscreen Fluid SPF 50','Serum':'Hyaluronic Acid Serum',
                  'Treatment':'Acne Spot Treatment','Cleanser':'CeraVe Hydrating Cleanser',
                  'Moisturizer':'CeraVe Moisturizing Cream Fragrance-Free'}
        labels=[]
        for category,title in examples.items():
            for index in range(20):
                title_i=title+' Edition'+str(index)
                classification=classify_product_attributes(title_i,'CeraVe')
                self.assertEqual(classification.primary_category,category)
                attrs=extract_identity(title_i,'CeraVe')
                cid=self.resolver.create_provisional(title_i,'CeraVe',classification,attrs,visible=True)
                pid=category+str(index)
                obs=ShopifyAdapter(source).parse({'id':pid,'title':title_i,'vendor':'CeraVe','variants':[{'id':pid,'price':'100'}]})[0]
                oid=save_observation(self.conn,obs,attrs,classification)
                self.resolver.record_link('sourcebeauty',pid,cid,'accepted','exact_structured_identity',{})
                upsert_offer(self.conn,source,obs,obs.variants[0],cid,oid)
                candidate=FormulationCandidate(cid,'source:sourcebeauty',RAW,RAW.split(', '),completeness='full',source_record_id=str(oid))
                eid=FormulationRepository().record_candidate_evidence(candidate,conn=self.conn)
                self.conn.execute("UPDATE inci_audit_evidence SET observation_id=?,order_verified=1 WHERE id=?",(oid,eid))
                labels.append((oid,cid,category))
        run=create_evaluation(self.conn,'sourcebeauty','collector')['evaluation_id']
        for oid,cid,category in labels:label_example(self.conn,run,oid,cid,False,category,True,'independent-reviewer','Verified product identity and taxonomy')
        return run

    def test_rollout_uses_server_generated_metrics_and_rejects_stale_catalog(self):
        run=self.evaluated_run()
        result=finalize_evaluation(self.conn,run,'finalizer')
        self.assertEqual(result['report']['auto_link_count'],100)
        self.assertEqual(result['report']['ordered_inci_sample_count'],100)
        with self.assertRaisesRegex(ValueError,'separate administrator'):
            enable_source_rollout(self.conn,'sourcebeauty',run,'independent-reviewer','Self approval is forbidden')
        approved=enable_source_rollout(self.conn,'sourcebeauty',run,'release-manager','Reviewed fixed evaluation')
        self.assertTrue(approved['publish_enabled'])
        self.conn.execute("UPDATE canonical_products SET canonical_title='Changed' WHERE id=?",(self.cid,))
        with self.assertRaises(ValueError):enable_source_rollout(self.conn,'sourcebeauty',run,'release-manager','Stale run')

    def test_evaluation_creator_cannot_label_or_finalize_own_evidence(self):
        run=self.evaluated_run()
        with self.assertRaisesRegex(ValueError,'independent'):
            finalize_evaluation(self.conn,run,'collector')

    def test_reconciliation_fingerprint_includes_identity_links(self):
        self.conn.commit()
        preview=save_reconciliation_preview(self.conn,'admin')
        self.conn.commit()
        self.conn.execute("""INSERT INTO product_identity_links(source_id,source_product_id,canonical_product_id,
            status,method,evidence,identity_status) VALUES ('sourcebeauty','changed-link',?,'pending','review','{}','legacy_preserved_unverified')""",(self.cid,))
        with self.assertRaisesRegex(ValueError,'Catalog changed'):
            persist_reconciliation_findings(self.conn,preview['preview_id'],'admin','Approved original snapshot')

    def test_reconciliation_findings_require_same_snapshot_and_preserve_formulas(self):
        self.conn.execute("UPDATE canonical_products SET ingredients_raw=? WHERE id=?",(RAW,self.cid));self.conn.commit()
        report={'reviews':[{'kind':'identity','source_id':'sourcebeauty','source_product_id':'one','canonical_product_id':self.cid,'details':json.dumps({'conflict':'test'})}]}
        with patch('pipeline.reconciliation_service.reconcile_catalog',return_value=report):
            preview=save_reconciliation_preview(self.conn,'admin')
        self.conn.commit()
        result=persist_reconciliation_findings(self.conn,preview['preview_id'],'admin','Reviewed snapshot')
        self.assertEqual(len(result['persisted_review_ids']),1)
        self.assertEqual(self.conn.execute('SELECT ingredients_raw,formulation_provenance FROM canonical_products WHERE id=?',(self.cid,)).fetchone()[0],RAW)
        self.conn.execute("UPDATE canonical_products SET canonical_title='Changed' WHERE id=?",(self.cid,))
        with self.assertRaises(ValueError):persist_reconciliation_findings(self.conn,preview['preview_id'],'admin','Stale snapshot')

class SourceRedirectSafety(unittest.TestCase):
    def test_no_redirect_request_built_for_forbidden_destination(self):
        from urllib.request import Request
        source={'domain':'example.com'}
        handler=SourceRedirects(source)
        for destination in ['http://example.com/p','https://evil.example/p','https://example.com:8443/p','https://user@example.com/p']:
            with self.subTest(destination=destination),self.assertRaises(ValueError):
                handler.redirect_request(Request('https://example.com/p'),None,302,'Found',{},destination)
    def test_normal_subdomain_https_redirect_allowed(self):
        validate_source_url({'domain':'example.com'},'https://www.example.com/p')
