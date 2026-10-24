"""Atomic source observation -> identity -> offers -> INCI evidence workflow."""
from dataclasses import asdict
import hashlib
import json
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver, extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.source_registry import get_source, url_matches_source
from pipeline.offer_service import upsert_offer, refresh_offer_aggregates
from pipeline.formulation_service import FormulationCandidate, FormulationRepository
from pipeline.inci_tokenizer import tokenize_inci_string


def queue_review(conn,kind,source_id,product_id,canonical_id,details):
    key=json.dumps([kind,source_id,product_id,canonical_id],separators=(',',':'))
    evidence=json.dumps(details,sort_keys=True,ensure_ascii=False)
    fingerprint=hashlib.sha256(evidence.encode()).hexdigest()
    conn.execute('''INSERT INTO product_review_queue(case_key,kind,source_id,source_product_id,canonical_product_id,details,evidence_hash)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(case_key) DO UPDATE SET details=excluded.details,
        status=CASE WHEN evidence_hash=excluded.evidence_hash THEN status ELSE 'pending' END,
        decision=CASE WHEN evidence_hash=excluded.evidence_hash THEN decision ELSE NULL END,
        reviewed_by=CASE WHEN evidence_hash=excluded.evidence_hash THEN reviewed_by ELSE NULL END,
        review_notes=CASE WHEN evidence_hash=excluded.evidence_hash THEN review_notes ELSE NULL END,
        evidence_hash=excluded.evidence_hash,updated_at=CURRENT_TIMESTAMP''',
        (key,kind,source_id,product_id,canonical_id,evidence,fingerprint))
    return conn.execute('SELECT id FROM product_review_queue WHERE case_key=?',(key,)).fetchone()[0]


def save_observation(conn,observation,identity,classification):
    values=(observation.source_id,observation.source_product_id,observation.source_url,observation.raw_title,
        observation.raw_brand,observation.raw_category,observation.source_gtin,observation.manufacturer_sku,
        observation.description,json.dumps(observation.images),observation.ingredient_text,observation.ingredient_section_type,
        observation.market,observation.language,observation.observed_at,observation.raw_payload_hash,
        json.dumps(observation.raw_payload,ensure_ascii=False),json.dumps(identity),json.dumps(classification.to_dict()))
    conn.execute('''INSERT OR IGNORE INTO source_product_observations(source_id,source_product_id,source_url,raw_title,raw_brand,
        raw_category,source_gtin,manufacturer_sku,description,images,ingredient_text,ingredient_section_type,market,language,
        observed_at,payload_hash,raw_payload,identity_attributes,classification) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',values)
    oid=conn.execute('SELECT id FROM source_product_observations WHERE source_id=? AND source_product_id=? AND payload_hash=?',
        (observation.source_id,observation.source_product_id,observation.raw_payload_hash)).fetchone()[0]
    for variant in observation.variants:
        conn.execute('''INSERT OR IGNORE INTO source_product_variants(observation_id,source_variant_id,sku,gtin,title,attributes)
            VALUES (?,?,?,?,?,?)''',(oid,variant.source_variant_id,variant.sku,variant.gtin,variant.title,json.dumps(asdict(variant))))
    return oid


def process_source_observation(conn,observation,dry_run=False,legacy_product_id=None,offer_fields=None):
    source=get_source(conn,observation.source_id)
    if source['source_type']!=observation.source_type or not url_matches_source(source,observation.source_url):
        raise ValueError('Observation source type or URL does not match the registered source.')
    if not observation.source_product_id or not observation.raw_title.strip():
        raise ValueError('Stable source product ID and original title are required.')
    if observation.market and observation.market!=source['market']:
        raise ValueError('Observation market differs from source policy.')
    if not conn.in_transaction:conn.execute('BEGIN')
    conn.execute('SAVEPOINT process_observation')
    try:
        classification=classify_product_attributes(observation.raw_title,observation.raw_brand,observation.raw_category or '',
            observation.description or '')
        from pipeline.brand_registry import resolve_brand
        brand_identity=resolve_brand(observation.raw_brand,conn)
        identity=extract_identity(observation.raw_title,brand_identity['name'] if brand_identity else observation.raw_brand,
            observation.market,observation.identity_attributes)
        oid=save_observation(conn,observation,identity,classification)
        reviewed=conn.execute("SELECT details,decision FROM product_review_queue WHERE source_id=? AND source_product_id=? AND kind='category' AND status='confirmed'",(source['id'],observation.source_product_id)).fetchone()
        if reviewed and json.loads(reviewed['details']).get('observation_id')==oid:
            decision=json.loads(reviewed['decision'] or '{}')
            if decision.get('action')=='category':
                from dataclasses import replace
                from pipeline.taxonomy_service import SKINCARE
                classification=replace(classification,primary_category=decision['category'],is_skincare=decision['category'] in SKINCARE,
                    classification_status='approved',classification_source='manual')
        resolver=CanonicalIdentityResolver(conn)
        results=[];evidence_canonicals=set()
        groups={}
        variants=observation.variants or [None]
        for variant in variants:
            attrs=variant.attributes if variant else {}
            key=json.dumps(attrs,sort_keys=True)
            groups.setdefault(key,[]).append(variant)
        for key,variants in groups.items():
            representative=variants[0]
            attributes=representative.attributes if representative else {}
            variant_identity={**identity,**attributes}
            link_product_id=observation.source_product_id
            if attributes:
                link_product_id+=':'+hashlib.sha256(key.encode()).hexdigest()[:16]
            decision=resolver.resolve(variant_identity,observation.raw_brand,observation.raw_title,
                source['id'],link_product_id,(representative.gtin if representative else None) or observation.source_gtin,observation.manufacturer_sku)
            if classification.classification_status=='review' or classification.primary_category=='Set':
                queue_review(conn,'category',source['id'],link_product_id,decision.canonical_product_id,
                    {'classification':classification.to_dict(),'identity':decision.to_dict(),'title':observation.raw_title,'observation_id':oid})
                results.append({'status':'review','kind':'category','observation_id':oid});continue
            if classification.classification_status=='excluded':
                results.append({'status':'excluded','observation_id':oid});continue
            if decision.status=='ambiguous':
                for candidate in decision.candidates:
                    candidate_id=candidate['canonical_product_id']
                    existing_link=conn.execute('SELECT status FROM product_identity_links WHERE source_id=? AND source_product_id=? AND canonical_product_id=?',
                        (source['id'],link_product_id,candidate_id)).fetchone()
                    if not existing_link or existing_link[0]=='pending':
                        resolver.record_link(source['id'],link_product_id,candidate_id,'pending','requires_review',decision.to_dict())
                queue_review(conn,'identity',source['id'],link_product_id,None,
                    {'identity':decision.to_dict(),'observation_id':oid,'title':observation.raw_title,'raw_brand':observation.raw_brand,
                     'source_url':observation.source_url,'images':observation.images})
                results.append({'status':'review','kind':'identity','observation_id':oid,'decision':decision.to_dict()});continue
            cid=decision.canonical_product_id
            if decision.status=='new':
                # Unknown/provisional identities never become consumer-visible by accident.
                cid=resolver.create_provisional(observation.raw_title,observation.raw_brand,classification,variant_identity,
                    visible=False)
            accepted_category=conn.execute('SELECT category,classification_status FROM canonical_products WHERE id=?',(cid,)).fetchone()
            if accepted_category['category']!=classification.primary_category:
                queue_review(conn,'category',source['id'],link_product_id,cid,
                    {'observation_id':oid,'title':observation.raw_title,'current_category':accepted_category['category'],
                     'classification':classification.to_dict(),'approved_category_preserved':accepted_category['classification_status']=='approved'})
            resolver.record_link(source['id'],link_product_id,cid,'accepted',decision.method,decision.to_dict())
            resolver.add_aliases(cid,variant_identity,source['id'],observation.source_gtin,observation.manufacturer_sku)
            offers=[]
            for variant in variants:
                if variant is None:continue
                if variant.gtin:
                    trade_item=resolver.resolve(variant_identity,observation.raw_brand,observation.raw_title,source['id'],link_product_id,variant.gtin,observation.manufacturer_sku)
                    if trade_item.status=='ambiguous' or trade_item.canonical_product_id not in (None,cid):
                        queue_review(conn,'identity',source['id'],link_product_id+':variant:'+variant.source_variant_id,cid,
                            {'observation_id':oid,'variant_id':variant.source_variant_id,'gtin':variant.gtin,'identity':trade_item.to_dict()})
                        continue
                offer=upsert_offer(conn,source,observation,variant,cid,oid,legacy_product_id,offer_fields)
                if offer:offers.append(offer)
                if variant.gtin:resolver.add_aliases(cid,variant_identity,source['id'],variant.gtin)
            if observation.ingredient_text and cid not in evidence_canonicals:
                validation=tokenize_inci_string(observation.ingredient_text,product_title=observation.raw_title)
                full=observation.ingredient_section_type=='full' and observation.ingredient_order_verified and not attributes
                completeness=validation.completeness if full else ('partial' if observation.ingredient_section_type=='partial' else 'unverified')
                candidate=FormulationCandidate(cid,'source:'+source['id'],observation.ingredient_text,validation.tokens,
                    validation.declared_actives,confidence_score=0,evidence_url=observation.source_url,source_record_id=str(oid),
                    completeness=completeness,validation_flags={'identity_resolved':True,'is_truncated':validation.is_truncated,
                        'order_verified':observation.ingredient_order_verified,'source_market':source['market'],
                        'identity_link_product_id':link_product_id,'source_type':source['source_type'],'formula_code':observation.formula_code,'source_id':source['id']})
                old=conn.execute('SELECT id FROM inci_audit_evidence WHERE observation_id=? AND canonical_product_id=?',(oid,cid)).fetchone()
                if not old:
                    eid=FormulationRepository().record_candidate_evidence(candidate,conn=conn)
                    conn.execute('UPDATE inci_audit_evidence SET observation_id=?,source_market=?,formula_code=?,order_verified=? WHERE id=?',
                        (oid,source['market'],observation.formula_code,int(observation.ingredient_order_verified),eid))
                conn.execute('''INSERT INTO formulation_resolution_queue(canonical_product_id) VALUES (?)
                    ON CONFLICT(canonical_product_id) DO UPDATE SET status='pending',updated_at=CURRENT_TIMESTAMP''',(cid,))
                from pipeline.source_formulations import resolve_source_formulations
                assessment=resolve_source_formulations(conn,cid,publish=False)
                if assessment['status']=='conflict':
                    queue_review(conn,'formulation',None,None,cid,assessment)
                evidence_canonicals.add(cid)
            refresh_offer_aggregates(conn,cid)
            results.append({'status':'matched' if decision.status=='matched' else 'provisional','canonical_product_id':cid,
                            'observation_id':oid,'offer_ids':offers,'decision':decision.to_dict()})
        conn.execute('UPDATE sources SET last_successful_sync=? WHERE id=?',(observation.observed_at,source['id']))
        if dry_run:conn.execute('ROLLBACK TO SAVEPOINT process_observation')
        conn.execute('RELEASE SAVEPOINT process_observation')
        return {'source_id':source['id'],'source_product_id':observation.source_product_id,'dry_run':dry_run,'results':results}
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT process_observation');conn.execute('RELEASE SAVEPOINT process_observation')
        raise
