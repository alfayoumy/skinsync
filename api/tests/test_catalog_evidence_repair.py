import json
import sqlite3
import unittest
from dataclasses import replace

from pipeline.db_manager import init_db
from pipeline.aggregation_service import process_source_observation,queue_review
from pipeline.source_adapters.contracts import SourceProductObservation,SourceVariant
from pipeline.catalog_repair import repair_catalog,verify_corroborated_identities,repair_canonical_categories,merge_exact_brand_duplicates,repair_explicit_hair_types
from pipeline.catalog_health import catalog_health
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.canonical_identity_resolver import identity_name

RAW='Water, Glycerin, Panthenol, Sodium Hyaluronate, Phenoxyethanol'


class CatalogEvidenceRepairTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id IN ('sourcebeauty','feel22')")

    def observation(self,source,pid='cream',title='CeraVe Moisturizing Cream 50ml'):
        domain='sourcebeauty.com' if source=='sourcebeauty' else 'eg.feel22.com'
        return SourceProductObservation(source,'retailer',pid,'https://'+domain+'/products/'+pid,title,'CeraVe',
            variants=[SourceVariant(pid,'50ml',price=200)],market='EG',
            ingredient_text=RAW,ingredient_section_type='full',ingredient_order_verified=True,
            raw_payload={'id':pid,'title':title})

    def pair(self):
        a=process_source_observation(self.conn,self.observation('sourcebeauty'))
        process_source_observation(self.conn,self.observation('feel22'))
        return a['results'][0]['canonical_product_id']

    def test_brand_name_is_not_reintroduced_as_function_by_peer_checks(self):
        observation=replace(self.observation('sourcebeauty',title='Essence Juicy Serum Hydrating Lip Treatment'),raw_brand='Essence')
        result=process_source_observation(self.conn,observation)
        cid=result['results'][0]['canonical_product_id']
        self.conn.execute("UPDATE canonical_products SET category='Toner',is_skincare=1 WHERE id=?",(cid,))
        repair=repair_canonical_categories(self.conn)
        self.assertEqual(repair['counts']['updated'],1)
        self.assertEqual(self.conn.execute('SELECT category FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],'Lip Care')


    def test_preview_rolls_back_identity_publication_and_audit_then_apply_is_idempotent(self):
        cid=self.pair();self.conn.commit()
        before=self.conn.execute('SELECT COUNT(*) FROM inci_audit_evidence').fetchone()[0]
        result=repair_catalog(self.conn,approve_staged=True)
        self.assertEqual(result['identities']['verified'],1)
        self.assertEqual(result['formulations']['published'],1)
        row=self.conn.execute('SELECT identity_status,ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()
        self.assertEqual(row['identity_status'],'provisional');self.assertFalse(row['ingredients_raw'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inci_audit_evidence').fetchone()[0],before)
        result=repair_catalog(self.conn,dry_run=False,approve_staged=True)
        self.assertEqual(result['formulations']['published'],1)
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],RAW)
        self.assertEqual(self.conn.execute('SELECT SUM(publish_enabled) FROM sources').fetchone()[0],0)
        self.assertEqual(repair_catalog(self.conn,dry_run=False,approve_staged=True)['formulations'].get('published',0),0)

    def test_single_source_and_open_identity_conflicts_cannot_verify(self):
        a=process_source_observation(self.conn,self.observation('sourcebeauty'))
        cid=a['results'][0]['canonical_product_id']
        self.assertEqual(verify_corroborated_identities(self.conn).get('verified',0),0)
        process_source_observation(self.conn,self.observation('feel22'))
        queue_review(self.conn,'identity','sourcebeauty','conflict',None,
            {'identity':{'candidates':[{'canonical_product_id':cid}]}})
        self.assertEqual(verify_corroborated_identities(self.conn).get('verified',0),0)

    def test_unlinked_source_suggestion_does_not_dispute_established_retailer_cluster(self):
        from pipeline.identity_trust import pending_identity_review,publication_block
        from pipeline.formulation_service import FormulationCandidate
        cid=self.pair()
        result=process_source_observation(self.conn,self.observation('sourcebeauty',pid='unlinked',title='CeraVe Moisturizing Cream Tub 50ml'))
        oid=result['results'][0]['observation_id']
        review=self.conn.execute("SELECT * FROM product_review_queue WHERE source_product_id='unlinked' AND kind='identity' AND status='pending'").fetchone()
        self.assertIsNotNone(review)
        self.assertFalse(pending_identity_review(self.conn,cid))
        self.assertEqual(verify_corroborated_identities(self.conn).get('verified',0),1)
        candidate=FormulationCandidate(cid,'source:sourcebeauty',RAW,RAW.split(', '),
            source_record_id=str(oid),completeness='full',validation_flags={'identity_resolved':True})
        self.assertIn('Source identity link',publication_block(self.conn,candidate))
        # A pending review of a listing already accepted into this canonical
        # is a genuine dispute and still blocks publication and certification.
        anchored=self.conn.execute("SELECT id FROM source_product_observations WHERE source_id='sourcebeauty' AND source_product_id='cream'").fetchone()[0]
        queue_review(self.conn,'identity','sourcebeauty','cream',None,
            {'observation_id':anchored,'identity':{'method':'review_required','candidates':[{'canonical_product_id':cid}]}})
        self.assertTrue(pending_identity_review(self.conn,cid))

    def test_conflicting_ordered_formulas_never_publish(self):
        cid=self.pair()
        changed=replace(self.observation('sourcebeauty'),ingredient_text='Water, Squalane, Glycerin, Phenoxyethanol',
                        raw_payload={'id':'cream','revision':2})
        process_source_observation(self.conn,changed)
        outcome=repair_catalog(self.conn,dry_run=False,approve_staged=True)
        self.assertEqual(outcome['formulations'].get('published',0),0)
        self.assertFalse(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()[0])

    def test_category_repair_retains_identity_review_and_human_category(self):
        o=self.observation('sourcebeauty',title='CeraVe Gel Moussant 200ml')
        result=process_source_observation(self.conn,o)
        cid=result['results'][0]['canonical_product_id'];oid=result['results'][0]['observation_id']
        rid=queue_review(self.conn,'category',o.source_id,o.source_product_id,cid,{'title':o.raw_title,'observation_id':oid})
        self.conn.execute("UPDATE canonical_products SET category='Treatment',classification_status='approved' WHERE id=?",(cid,))
        repaired=repair_catalog(self.conn,dry_run=False)
        self.assertEqual(repaired['categories'].get('resolved',0),0)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(rid,)).fetchone()[0],'pending')

    def test_health_is_read_only_and_separates_category_from_identity(self):
        queue_review(self.conn,'category','sourcebeauty','one',None,{'title':'Unknown'})
        queue_review(self.conn,'identity','sourcebeauty','two',None,{'title':'Unknown'})
        before=self.conn.total_changes
        health=catalog_health(self.conn)
        self.assertEqual(health['pending_category_reviews'],1)
        self.assertEqual(health['pending_identity_reviews'],1)
        self.assertEqual(self.conn.total_changes,before)

    def test_canonical_projection_repairs_history_and_preserves_human_and_offer_conflicts(self):
        cid=self.pair()
        self.conn.execute("UPDATE canonical_products SET category='Serum' WHERE id=?",(cid,))
        result=repair_canonical_categories(self.conn)
        self.assertEqual(result['counts']['updated'],1)
        self.assertEqual(self.conn.execute('SELECT category FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],'Moisturizer')
        self.assertEqual(repair_canonical_categories(self.conn)['counts'].get('updated',0),0)
        self.conn.execute("UPDATE canonical_products SET category='Treatment',classification_status='approved' WHERE id=?",(cid,))
        self.assertEqual(repair_canonical_categories(self.conn)['counts'].get('updated',0),0)
        self.conn.execute("UPDATE canonical_products SET classification_status='auto_approved' WHERE id=?",(cid,))
        self.conn.execute("UPDATE canonical_products SET category='Serum' WHERE id=?",(cid,))
        self.conn.execute("UPDATE product_offers SET title='CeraVe Face Cleanser 50ml' WHERE canonical_product_id=?",(cid,))
        result=repair_canonical_categories(self.conn)
        self.assertEqual(result['counts'].get('updated',0),0)
        self.assertEqual(result['counts']['cluster_conflict_preserved'],1)

    def test_known_bundle_retains_offers_without_mandatory_category_review(self):
        result=process_source_observation(self.conn,self.observation('sourcebeauty',title='CeraVe Skincare Routine Set'))
        cid=result['results'][0]['canonical_product_id']
        self.assertTrue(result['results'][0]['offer_ids'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE kind='category' AND status='pending'").fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT is_skincare FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],0)

    def test_typography_does_not_erase_variant_names(self):
        self.assertEqual(identity_name('Dr.Althea 345 Relief Serum 15ml','Dr.Althea'),identity_name('Dr. Althea 345 Relief Serum 30ml','Dr.Althea'))
        self.assertEqual(identity_name('Acti-Solaire SPF 50+ Melting Cream','Dermactive'),identity_name('Dermactive Acti-Solaire SPF50 Melting Cream','Dermactive'))
        self.assertNotEqual(identity_name('Glow Potion Plus Serum','Potions'),identity_name('Glow Potion Serum','Potions'))

    def test_explicit_functions_and_exclusions(self):
        cases={'SVR SEBIACLEAR GEL MOUSSANT 200ML':'Cleanser','NUXE Lip Moist Stick 4 GR':'Lip Care',
               'Medicube Kojic Gel Mask':'Mask','Avene Thermal Spring Water150ml':'Toner',
               'Anivagene Hair Tonic Lotion':'Hair Care','Soul Lip & Cheek Tint':'Makeup'}
        for title,category in cases.items():
            with self.subTest(title=title):
                cls=classify_product_attributes(title,description='Moisturizing cream with serum ingredients')
                self.assertEqual(cls.primary_category,category)
                self.assertIn(cls.classification_status,('auto_approved','excluded'))
        cls=classify_product_attributes('Rose Glow',product_type='Face Serum')
        self.assertEqual((cls.primary_category,cls.classification_status),('Serum','auto_approved'))
        self.assertEqual(classify_product_attributes('Rose Glow',product_type='Skin Care').classification_status,'review')
        cls=classify_product_attributes('Gentle Face Wash',product_type='Face Serum')
        self.assertEqual(cls.primary_category,'Cleanser')

    def duplicate_pair(self):
        from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
        cid=self.pair()
        title='CeraVe Moisturizing Cream 50ml'
        other=CanonicalIdentityResolver(self.conn).create_provisional(title,'CeraVe',classify_product_attributes(title,'CeraVe'),extract_identity(title,'CeraVe','EG'),False)
        self.conn.execute("UPDATE product_offers SET canonical_product_id=? WHERE canonical_product_id=? AND source_id='feel22'",(other,cid))
        self.conn.execute("UPDATE product_identity_links SET canonical_product_id=? WHERE canonical_product_id=? AND source_id='feel22'",(other,cid))
        bid=self.conn.execute('SELECT brand_id FROM canonical_products WHERE id=?',(cid,)).fetchone()[0]
        return cid,other,bid

    def test_exact_brand_duplicates_merge_only_with_agreeing_retained_evidence(self):
        cid,other,bid=self.duplicate_pair()
        result=merge_exact_brand_duplicates(self.conn,bid)
        self.assertEqual(result[0]['action'],'merged')
        self.assertEqual(self.conn.execute('SELECT target_id FROM canonical_redirects WHERE canonical_product_id=?',(other,)).fetchone()[0],cid)
        self.assertEqual(self.conn.execute('SELECT COUNT(DISTINCT canonical_product_id) FROM product_offers').fetchone()[0],1)

    def test_exact_titles_cannot_override_conflicting_full_formulas(self):
        cid,other,bid=self.duplicate_pair()
        self.conn.execute("""INSERT INTO inci_audit_evidence(canonical_product_id,source_name,
            raw_ingredients_found,completeness,order_verified,decision)
            VALUES(?,'source:feel22','Water, Squalane, Glycerin, Phenoxyethanol','full',1,'candidate_evidence')""",(other,))
        result=merge_exact_brand_duplicates(self.conn,bid)
        self.assertEqual(result[0]['action'],'preserved')
        self.assertIn('declarations disagree',result[0]['reason'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_redirects').fetchone()[0],0)

    def test_explicit_source_hair_type_repairs_generic_vehicle_but_preserves_face_type_disagreement(self):
        for source in ('sourcebeauty','feel22'):
            o=replace(self.observation(source,title='Pull The Curl Trigger Definition Cream'),raw_brand='LOCKEN')
            cid=process_source_observation(self.conn,o)['results'][0]['canonical_product_id']
        self.conn.execute("UPDATE source_product_observations SET raw_category='Styling Cream' WHERE source_id='sourcebeauty'")
        self.conn.execute("UPDATE source_product_observations SET raw_category='Face Cream' WHERE source_id='feel22'")
        self.assertEqual(repair_explicit_hair_types(self.conn)['counts'].get('updated',0),0)
        self.conn.execute("UPDATE source_product_observations SET raw_category='Hair Care' WHERE source_id='feel22'")
        self.conn.execute("UPDATE canonical_products SET classification_status='approved' WHERE id=?",(cid,))
        self.assertEqual(repair_explicit_hair_types(self.conn)['counts'].get('updated',0),0)
        self.conn.execute("UPDATE canonical_products SET classification_status='auto_approved' WHERE id=?",(cid,))
        self.assertEqual(repair_explicit_hair_types(self.conn)['counts']['updated'],1)
        self.assertEqual(tuple(self.conn.execute('SELECT category,is_skincare FROM canonical_products WHERE id=?',(cid,)).fetchone()),('Hair Care',0))
        repair_canonical_categories(self.conn)
        self.assertEqual(tuple(self.conn.execute('SELECT category,is_skincare FROM canonical_products WHERE id=?',(cid,)).fetchone()),('Hair Care',0))


if __name__=='__main__':unittest.main()
