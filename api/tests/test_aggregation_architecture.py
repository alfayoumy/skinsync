import copy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pipeline.db_manager import init_db
from pipeline.brand_registry import resolve_brand,approve_brand_alias,register_legacy_brands
from pipeline.taxonomy_service import classify_product_attributes,reconcile_canonical_classification
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity,compare_attributes,valid_gtin
from pipeline.source_registry import get_source
from pipeline.source_adapters import get_adapter
from pipeline.source_adapters.contracts import SourceProductObservation,SourceVariant
from pipeline.source_adapters.extraction import extract_sections,preferred_section
from pipeline.aggregation_service import process_source_observation
from pipeline.source_formulations import resolve_source_formulations
from pipeline.offer_service import package_details
from pipeline.review_service import merge_canonicals,split_canonical,reverse_identity_operation,resolve_canonical_redirect,resolve_review
from pipeline.aggregation_benchmark import evaluate_classification,source_coverage_report,enable_source_rollout
from pipeline.formulation_service import FormulationRepository,FormulationCandidate
from services.price_service import get_canonical_price_comparison

RAW='Water, Glycerin, Panthenol, Phenoxyethanol'
OTHER='Water, Glycerin, Niacinamide, Phenoxyethanol'
GOLD=json.loads((Path(__file__).parent/'fixtures/aggregation_gold.json').read_text())


class TestTaxonomyAndIdentity(unittest.TestCase):
    def test_classification_gold_set(self):
        report=evaluate_classification(GOLD['classification'])
        self.assertGreaterEqual(report['macro_f1'],.95,report)
        self.assertLessEqual(report['skincare_false_exclusion_rate'],.01)
    def test_critical_identity_gates(self):
        for a,b in GOLD['critical_pairs']:
            with self.subTest(a=a,b=b):
                self.assertTrue(compare_attributes(extract_identity(a,'CeraVe'),extract_identity(b,'CeraVe'))['conflicts'])
    def test_missing_spf_is_unknown(self):
        comparison=compare_attributes(extract_identity('CeraVe Sunscreen SPF50','CeraVe'),extract_identity('CeraVe Sunscreen','CeraVe'))
        self.assertEqual(comparison['status'],'unknown')
    def test_curated_brands_never_fuzzily_collide(self):
        self.assertEqual(resolve_brand('LRP')['name'],'La Roche-Posay')
        self.assertFalse(resolve_brand('CareVe')['verified'])
        self.assertIsNone(resolve_brand('Unknown'))
    def test_multipacks_use_total_quantity_and_separate_units(self):
        self.assertEqual(package_details('Cream 2 × 50 ml',price=600)['unit_price'],600)
        self.assertEqual(package_details('Cream 50 g',price=200)['normalized_size_g'],50)
        self.assertIsNone(package_details('Cream 50 g',price=200)['normalized_size_ml'])
    def test_gtin_checksum(self):
        self.assertEqual(valid_gtin('810400030824'),'00810400030824')
        self.assertIsNone(valid_gtin('810400030825'))
    def test_ppm_commas_do_not_split_ingredient(self):
        from pipeline.inci_tokenizer import tokenize_inci_string
        result=tokenize_inci_string('Centella Asiatica Leaf Water(74,349ppm), Glycerin, Panthenol, Phenoxyethanol')
        self.assertEqual(len(result.tokens),4,result.tokens)


class TestAggregationDatabase(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id IN ('sourcebeauty','feel22','cerave_eg','cosrx')")
        self.addCleanup(self.conn.close)
    def canonical(self,title='CeraVe Moisturizing Cream',raw=None,cid=None):
        cls=classify_product_attributes(title,'CeraVe')
        result=CanonicalIdentityResolver(self.conn).create_provisional(title,'CeraVe',cls,extract_identity(title,'CeraVe','EG'),True)
        if raw:
            candidate=FormulationCandidate(result,'admin_override',raw,raw.lower().split(', '))
            FormulationRepository().publish_formulation(candidate,'Test approved formula',conn=self.conn)
        return result
    def observation(self,source='sourcebeauty',pid='one',title='CeraVe Moisturizing Cream 50ml',raw=RAW):
        source_cfg=get_source(self.conn,source)
        return SourceProductObservation(source,source_cfg['source_type'],pid,'https://'+source_cfg['domain']+'/products/'+pid,
            title,'CeraVe',raw_category='Treatment',variants=[SourceVariant('small','50ml',price=200,available=True)],
            ingredient_text=raw,ingredient_section_type='full',ingredient_order_verified=True,market=source_cfg['market'],raw_payload={'id':pid,'title':title})
    def test_migration_is_additive_and_repeatable(self):
        cid=self.canonical(raw=RAW)
        init_db(conn=self.conn,seed_defaults=False)
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],RAW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],31)
    def test_stable_profile_matches_across_category_and_size(self):
        cid=self.canonical()
        result=process_source_observation(self.conn,self.observation())
        self.assertEqual(result['results'][0]['canonical_product_id'],cid)
        different=replace(self.observation(source='feel22',pid='two'),raw_category='Sun Protection',variants=[SourceVariant('big','100ml',price=300,available=True)])
        process_source_observation(self.conn,different)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
    def test_observations_offers_and_evidence_are_idempotent(self):
        self.canonical()
        o=self.observation()
        process_source_observation(self.conn,o)
        process_source_observation(self.conn,replace(o,observed_at='2099-01-01T00:00:00+00:00'))
        for table in ('source_product_observations','product_offers','inci_audit_evidence'):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],1,table)
    def test_changed_payload_is_a_new_snapshot_same_identity_and_offer(self):
        self.canonical();o=self.observation();process_source_observation(self.conn,o)
        o=replace(o,raw_payload={'id':'one','revision':2},variants=[SourceVariant('small','50ml',price=250,available=False)])
        process_source_observation(self.conn,o)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT price FROM product_offers').fetchone()[0],250)
    def test_dry_run_rolls_back_and_caller_transaction_is_preserved(self):
        cid=self.canonical();self.conn.commit()
        self.conn.execute("UPDATE canonical_products SET family_name='pending' WHERE id=?",(cid,))
        process_source_observation(self.conn,self.observation(),dry_run=True)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT family_name FROM canonical_products').fetchone()[0],'pending')
        self.conn.rollback()
        self.assertIsNone(self.conn.execute('SELECT family_name FROM canonical_products').fetchone()[0])
    def test_unknown_brand_creates_provisional_canonical_without_identity_review(self):
        result=process_source_observation(self.conn,replace(self.observation(),raw_brand='Unverified Brand'))
        self.assertEqual(result['results'][0]['status'],'provisional')
        cid=result['results'][0]['canonical_product_id']
        row=self.conn.execute('SELECT identity_status,brand_id FROM canonical_products WHERE id=?',(cid,)).fetchone()
        self.assertEqual(row['identity_status'],'provisional')
        self.assertEqual(self.conn.execute('SELECT status FROM brands WHERE id=?',(row['brand_id'],)).fetchone()[0],'observed_unverified')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE kind='identity' AND status='pending'").fetchone()[0],0)
    def test_missing_variant_discriminator_does_not_create_duplicate(self):
        self.canonical('CeraVe Sunscreen SPF50')
        o=replace(self.observation(title='CeraVe Sunscreen'),raw_category='Sun Protection')
        result=process_source_observation(self.conn,o)
        # A low-evidence unknown SPF/vehicle case is not a *human-required*
        # conflict. Keep it deferred, but never merge or create a duplicate.
        self.assertEqual(result['results'][0]['status'],'evidence_needed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE status='deferred' AND kind='identity'").fetchone()[0],1)
    def test_identity_conflicting_spf_creates_separate_hidden_identity(self):
        self.canonical('CeraVe Sunscreen SPF30')
        result=process_source_observation(self.conn,self.observation(title='CeraVe Sunscreen SPF50'))
        cid=result['results'][0]['canonical_product_id']
        self.assertEqual(self.conn.execute('SELECT catalog_visible FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],0)
    def test_ingredients_never_define_product_identity(self):
        self.canonical('CeraVe Hydrating Cleanser',RAW)
        result=process_source_observation(self.conn,self.observation(title='CeraVe Vitamin C Serum'))
        cid=result['results'][0]['canonical_product_id']
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],2)
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()[0])
    def test_manufacturer_contributes_evidence_without_prices(self):
        cid=self.canonical();o=self.observation('cerave_eg')
        process_source_observation(self.conn,o)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inci_audit_evidence').fetchone()[0],1)
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()[0])
    def test_existing_formula_survives_new_retailer_declaration(self):
        cid=self.canonical(raw=RAW)
        process_source_observation(self.conn,self.observation(raw=OTHER))
        self.assertEqual(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],RAW)
        self.assertEqual(resolve_source_formulations(self.conn,cid)['status'],'conflict')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE kind='formulation'").fetchone()[0],1)
    def test_disagreeing_full_sources_are_not_resolved_by_priority(self):
        cid=self.canonical()
        process_source_observation(self.conn,self.observation(raw=RAW))
        process_source_observation(self.conn,self.observation('cerave_eg',raw=OTHER))
        self.conn.execute("UPDATE sources SET publish_enabled=1 WHERE id IN ('sourcebeauty','cerave_eg')")
        self.assertEqual(resolve_source_formulations(self.conn,cid,True)['status'],'conflict')
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products').fetchone()[0])
    def test_verified_concordant_sources_can_publish_only_after_rollout(self):
        cid=self.canonical();process_source_observation(self.conn,self.observation())
        self.assertEqual(resolve_source_formulations(self.conn,cid,True)['status'],'staged')
        self.conn.execute("UPDATE sources SET publish_enabled=1 WHERE id='sourcebeauty'")
        self.assertTrue(resolve_source_formulations(self.conn,cid,True)['published'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM formulation_versions').fetchone()[0],1)
    def test_international_manufacturer_requires_market_compatibility_review(self):
        cid=self.canonical();process_source_observation(self.conn,self.observation('cosrx'))
        result=resolve_source_formulations(self.conn,cid)
        self.assertEqual(result['status'],'no_eligible_evidence')
        self.assertEqual(result['rejected_evidence'][0]['reason'],'regional_compatibility_requires_review')
    def test_variant_prices_packaging_and_same_page_url_are_retained(self):
        cid=self.canonical();o=self.observation()
        o.variants=[SourceVariant('30','30 ml',price=100,available=True),SourceVariant('50','50ml',price=150,available=False),
                    SourceVariant('pack','2 × 50 ml',price=260,available=True)]
        process_source_observation(self.conn,o)
        result=get_canonical_price_comparison(cid,self.conn)
        self.assertEqual(result['listing_count'],3)
        self.assertEqual(len({p['product_url'] for p in result['offers']}),1)
        self.assertEqual(len(result['size_options']),3)
        self.assertEqual(next(p for p in result['offers'] if p['source_variant_id']=='pack')['unit_price'],260)
    def test_foreign_domain_or_market_cannot_be_ingested(self):
        for changes in ({'source_url':'https://evil.example/p'},{'market':'US'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                process_source_observation(self.conn,replace(self.observation(),**changes))
    def test_distinct_shades_have_distinct_identities(self):
        o=self.observation(title='CeraVe Tinted Sunscreen SPF50')
        o.variants=[SourceVariant('rose','50 ml Rose',price=200,attributes={'shade':'Rose'}),SourceVariant('sand','50 ml Sand',price=210,attributes={'shade':'Sand'})]
        process_source_observation(self.conn,o)
        self.assertEqual(self.conn.execute('SELECT COUNT(DISTINCT canonical_product_id) FROM product_offers').fetchone()[0],2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inci_audit_evidence WHERE completeness='full'").fetchone()[0],0)
    def test_brand_alias_decisions_are_durable_and_non_destructive(self):
        bid=approve_brand_alias(self.conn,'SV','CeraVe','reviewer')
        self.assertEqual(resolve_brand('SV',self.conn)['id'],bid)
        with self.assertRaises(ValueError):approve_brand_alias(self.conn,'SV','Different Brand','reviewer')
    def test_approved_canonical_classification_is_not_overwritten(self):
        cid=self.canonical()
        self.conn.execute("UPDATE canonical_products SET classification_status='approved' WHERE id=?",(cid,))
        self.assertFalse(reconcile_canonical_classification(self.conn,cid,classify_product_attributes('CeraVe Serum','CeraVe')))
    def test_merge_and_reverse_preserve_ids_and_audit(self):
        a=self.canonical();b=self.canonical()
        history=merge_canonicals(self.conn,a,b,'reviewer','Confirmed duplicate identity')
        self.assertEqual(resolve_canonical_redirect(self.conn,b),a)
        reverse_identity_operation(self.conn,history,'reviewer','Undo duplicate decision')
        self.assertEqual(resolve_canonical_redirect(self.conn,b),b)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],2)
    def test_merge_cannot_hide_a_previously_approved_formula(self):
        empty=self.canonical();approved=self.canonical(raw=RAW)
        with self.assertRaises(ValueError):merge_canonicals(self.conn,empty,approved,'curator','Reviewed duplicate')
        self.assertEqual(resolve_canonical_redirect(self.conn,approved),approved)
    def test_reviewed_merge_aliases_are_preserved_and_reversal_restores_them(self):
        a=self.canonical();b=self.canonical()
        self.conn.execute("INSERT INTO canonical_product_aliases(canonical_product_id,alias_type,alias_value,source_id,verified) VALUES (?,'title','approved alternate name','manufacturer',1)",(b,))
        history=merge_canonicals(self.conn,a,b,'curator','Approved manufacturer alias')
        self.assertTrue(self.conn.execute("SELECT 1 FROM canonical_product_aliases WHERE canonical_product_id=? AND alias_value='approved alternate name'",(a,)).fetchone())
        reverse_identity_operation(self.conn,history,'curator','Undo merge')
        self.assertFalse(self.conn.execute("SELECT 1 FROM canonical_product_aliases WHERE canonical_product_id=? AND alias_value='approved alternate name'",(a,)).fetchone())
    def test_merge_rejects_conflicting_formulas(self):
        a=self.canonical(raw=RAW);b=self.canonical(raw=OTHER)
        with self.assertRaises(ValueError):merge_canonicals(self.conn,a,b,'reviewer','Duplicate request')
    def test_split_does_not_copy_ingredients_and_can_be_reversed(self):
        cid=self.canonical(raw=RAW)
        process_source_observation(self.conn,self.observation())
        offer_id=self.conn.execute('SELECT id FROM product_offers').fetchone()[0]
        from pipeline.canonical_identity_resolver import identity_name
        offer=self.conn.execute('SELECT source_id,title FROM product_offers WHERE id=?',(offer_id,)).fetchone()
        alias=identity_name(offer['title'],'CeraVe')
        self.conn.execute("INSERT OR IGNORE INTO canonical_product_aliases(canonical_product_id,alias_type,alias_value,source_id,verified) VALUES (?,'title',?,?,1)",(cid,alias,offer['source_id']))
        self.conn.execute("INSERT INTO canonical_product_aliases(canonical_product_id,alias_type,alias_value,source_id,verified) VALUES (?,'title','unmoved sibling','feel22',1)",(cid,))
        result=split_canonical(self.conn,cid,[],[offer_id],'CeraVe Night Cream',{'timing':'night'},'reviewer','Distinct night product')
        self.assertIsNone(self.conn.execute('SELECT ingredients_raw FROM canonical_products WHERE id=?',(result['new_canonical_id'],)).fetchone()[0])
        self.assertEqual(self.conn.execute('SELECT verified FROM canonical_product_aliases WHERE canonical_product_id=? AND source_id=? AND alias_value=?',(cid,offer['source_id'],alias)).fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT verified FROM canonical_product_aliases WHERE alias_value='unmoved sibling'").fetchone()[0],1)
        reverse_identity_operation(self.conn,result['history_id'],'reviewer','Incorrect split')
        self.assertEqual(self.conn.execute('SELECT canonical_product_id FROM product_offers').fetchone()[0],cid)
        self.assertEqual(self.conn.execute('SELECT verified FROM canonical_product_aliases WHERE canonical_product_id=? AND source_id=? AND alias_value=?',(cid,offer['source_id'],alias)).fetchone()[0],1)
    def test_reviewed_cannot_link_is_remembered(self):
        cid=self.canonical()
        resolver=CanonicalIdentityResolver(self.conn)
        resolver.record_link('sourcebeauty','one',cid,'rejected','manual_review',{},'reviewer')
        decision=resolver.resolve(extract_identity('CeraVe Moisturizing Cream','CeraVe'),'CeraVe','CeraVe Moisturizing Cream','sourcebeauty','one')
        self.assertNotEqual(decision.canonical_product_id,cid)
    def test_rollout_requires_complete_report_and_no_pending_reviews(self):
        with self.assertRaises(ValueError):enable_source_rollout(self.conn,'sourcebeauty',{},'reviewer','Enable publication')
        self.assertEqual(self.conn.execute("SELECT publish_enabled FROM sources WHERE id='sourcebeauty'").fetchone()[0],0)
    def test_coverage_reports_products_not_just_scrape_count(self):
        self.canonical();o=self.observation();process_source_observation(self.conn,o);process_source_observation(self.conn,o)
        report=source_coverage_report(self.conn,'sourcebeauty')
        self.assertEqual(report['discovered_products'],1);self.assertEqual(report['additional_offers'],1)

    def test_changed_source_identity_does_not_inherit_a_durable_match(self):
        self.canonical();original=self.observation();process_source_observation(self.conn,original)
        changed=replace(original,raw_title='CeraVe Regenerating Cream',raw_payload={'repurposed':True},variants=[SourceVariant('small','50ml',price=999)])
        result=process_source_observation(self.conn,changed)
        self.assertEqual(result['results'][0]['status'],'review')
        self.assertEqual(self.conn.execute('SELECT price FROM product_offers').fetchone()[0],200)
    def test_shared_barcode_does_not_override_different_product_name(self):
        cid=self.canonical('CeraVe Repair Cream')
        resolver=CanonicalIdentityResolver(self.conn)
        resolver.add_aliases(cid,extract_identity('CeraVe Repair Cream','CeraVe'),'sourcebeauty','810400030824')
        result=resolver.resolve(extract_identity('CeraVe Brightening Cream','CeraVe'),'CeraVe','CeraVe Brightening Cream',gtin='810400030824')
        self.assertEqual(result.status,'ambiguous')
        self.assertEqual(result.method,'gtin_identity_conflict')
    def test_staged_new_retailer_prices_do_not_change_public_price(self):
        cid=self.canonical()
        process_source_observation(self.conn,self.observation())
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id='zynah'")
        o=self.observation('zynah',pid='cheap')
        o.variants=[SourceVariant('cheap','50ml',price=1,available=True)]
        process_source_observation(self.conn,o)
        self.assertEqual(get_canonical_price_comparison(cid,self.conn)['min_price'],200)
        self.assertEqual(self.conn.execute('SELECT min_price FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],200)
    def test_out_of_stock_variants_do_not_inherit_legacy_availability(self):
        cid=self.canonical();o=self.observation();o.variants[0].available=False
        process_source_observation(self.conn,o)
        result=get_canonical_price_comparison(cid,self.conn)
        self.assertFalse(result['offers'][0]['is_available'])
    def test_regional_evidence_requires_explicit_reviewed_compatibility(self):
        from pipeline.source_formulations import approve_formulation_evidence
        cid=self.canonical();process_source_observation(self.conn,self.observation('cosrx'))
        eid=self.conn.execute('SELECT id FROM inci_audit_evidence').fetchone()[0]
        with self.assertRaises(ValueError):approve_formulation_evidence(self.conn,eid,'curator','Reviewed package')
        result=approve_formulation_evidence(self.conn,eid,'curator','Verified Egyptian packaging matches this declaration',market_compatible=True)
        self.assertTrue(result['published'])
    def test_reversal_refuses_intervening_edits(self):
        a=self.canonical();b=self.canonical();hid=merge_canonicals(self.conn,a,b,'curator','Verified duplicate')
        self.conn.execute("UPDATE canonical_products SET canonical_title='Reviewed new title' WHERE id=?",(a,))
        with self.assertRaises(ValueError):reverse_identity_operation(self.conn,hid,'curator','Undo')
    def test_collector_obeys_robots_without_fetching_product(self):
        from pipeline.source_collection import collect_source
        with patch('pipeline.source_collection._read',return_value=('User-agent: *\nDisallow: /products/', 'https://sourcebeauty.com/robots.txt')) as fetch:
            result=collect_source(self.conn,'sourcebeauty',['https://sourcebeauty.com/products/test'],1,True)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(result['results'][0]['status'],'disallowed_by_robots')
    def test_failed_variant_import_rolls_back_the_entire_observation(self):
        self.canonical();o=self.observation();o.variants[0].currency='USD'
        with self.assertRaises(ValueError):process_source_observation(self.conn,o)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_product_observations').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM product_offers').fetchone()[0],0)


class TestSourceAdapters(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False);self.addCleanup(self.conn.close)
    def test_full_list_wins_over_highlighted_actives(self):
        html='<h3>Key Active Ingredients</h3><p>Niacinamide, Zinc</p><h3>Full Ingredients List</h3><p>'+RAW+'</p><h3>Directions</h3><p>Apply daily</p>'
        section=preferred_section(extract_sections(html))
        self.assertEqual(section.text,RAW);self.assertEqual(section.completeness,'full');self.assertTrue(section.order_verified)
    def test_bullets_preserve_order_and_formula_code_is_metadata(self):
        html="<h2>What's in it</h2><ul><li>Water</li><li>Glycerin</li><li>Caprylic/Capric Triglyceride</li><li>Phenoxyethanol</li></ul><p>Formula code: ABC1234</p><h2>Reviews</h2>"
        section=preferred_section(extract_sections(html))
        self.assertTrue(section.text.startswith('Water, Glycerin, Caprylic/Capric'))
        self.assertEqual(section.formula_code,'ABC1234');self.assertNotIn('ABC1234',section.text)
    def test_shopify_keeps_all_variants_and_source_title(self):
        source=get_source(self.conn,'feel22');adapter=get_adapter(source)
        o=adapter.parse({'id':10,'title':'CeraVe Cream','vendor':'CeraVe','handle':'cream','body_html':'<h3>Ingredients</h3><p>'+RAW+'</p>',
            'variants':[{'id':1,'title':'30ml','price':'100','available':True},{'id':2,'title':'50ml','price':'180','available':False}]})[0]
        self.assertEqual(len(o.variants),2);self.assertEqual(o.raw_title,'CeraVe Cream');self.assertEqual(o.ingredient_text,RAW)
    def test_html_manufacturer_uses_product_identity_and_accordion(self):
        source=get_source(self.conn,'cerave_eg')
        html='<script type="application/ld+json">'+json.dumps({'@type':'Product','name':'Hydrating Cleanser','brand':{'name':'CeraVe'}})+'</script><details><summary>Ingredients</summary><p>'+RAW+'</p></details>'
        o=get_adapter(source).parse(html,'https://cerave.eg/hydrating-cleanser')[0]
        self.assertEqual(o.raw_brand,'CeraVe');self.assertEqual(o.ingredient_text,RAW);self.assertTrue(o.ingredient_order_verified)
    def test_partial_truncated_and_unordered_are_not_full(self):
        for html in ('<h3>Key Ingredients</h3><p>'+RAW+'</p>','<h3>Ingredients</h3><p>'+RAW+', ...</p>',
                     '<h3>Ingredients</h3><p>'+RAW+'; displayed in alphabetical order</p>'):
            self.assertNotEqual(preferred_section(extract_sections(html)).completeness,'full')
    def test_woocommerce_requires_expanded_variants_without_fabrication(self):
        source=get_source(self.conn,'zahra');source['adapter_type']='woocommerce'
        observations=get_adapter(source).parse({'id':1,'name':'CeraVe Cream','type':'variable','variations':[1,2],
            'brands':[{'name':'CeraVe'}],'permalink':'https://zahraegypt.com/p','description':'<h3>Ingredients</h3><p>'+RAW+'</p>'})
        self.assertEqual(observations[0].variants,[])

    def test_related_product_json_ld_does_not_capture_wrong_identity(self):
        source=get_source(self.conn,'zynah')
        html='<script type="application/ld+json">'+json.dumps([
            {'@type':'Product','name':'Wrong recommendation','url':'https://zynah.me/products/other','brand':{'name':'Different'}},
            {'@type':'Product','name':'CeraVe Cream','url':'https://zynah.me/products/cream','brand':{'name':'CeraVe'}}])+ '</script><h3>Full Ingredients List</h3><p>'+RAW+'</p>'
        observation=get_adapter({**source,'adapter_type':'html'}).parse(html,'https://zynah.me/products/cream')[0]
        self.assertEqual(observation.raw_title,'CeraVe Cream')
    def test_common_base_is_not_a_full_shade_specific_declaration(self):
        section=preferred_section(extract_sections('<h3>Full Ingredients</h3><h4>Verified common base</h4><p>'+RAW+'</p><p>Colorants can vary by shade</p>'))
        self.assertFalse(section.order_verified)
        self.assertEqual(section.completeness,'unverified')

    def test_arabic_full_ingredients_heading_and_accordion(self):
        html='<h3>المكونات الرئيسية</h3><p>Panthenol</p><h3>قائمة المكونات كاملة</h3><details><summary>عرض قائمة المكونات كاملة</summary><p>'+RAW+'</p></details><h3>قد يعجبك أيضًا</h3><p>Other products</p>'
        section=preferred_section(extract_sections(html))
        self.assertEqual(section.text,RAW)
        self.assertTrue(section.order_verified)
    def test_next_unrecognized_heading_ends_declaration(self):
        html='<h3>Ingredients</h3><p>'+RAW+'</p><h3>Our quality promise</h3><p>Guaranteed authentic products</p>'
        self.assertEqual(preferred_section(extract_sections(html)).text,RAW)
