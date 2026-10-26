"""Retain literal detail-page INCI on an existing verified source association.

No model calls, product matching, price/stock updates or source-wide approval.
The caller fetches and retains bounded whole-page markdown through MCP.
"""
from dataclasses import replace
import hashlib
import html
import json
import re

from pipeline.brand_registry import resolve_brand
from pipeline.canonical_identity_resolver import identity_name
from pipeline.source_registry import get_source,url_matches_source
from pipeline.source_adapters.extraction import extract_sections,preferred_section
from pipeline.source_adapters.contracts import now
from pipeline.identity_trust import verified_link,pending_identity_review
from pipeline.formulation_service import FormulationCandidate,FormulationRepository


def confirmed_detail_section(observation,page,conn):
    source=get_source(conn,observation.source_id)
    if page.get('url')!=observation.source_url or not url_matches_source(source,page.get('url','')):
        raise ValueError('Detail page URL differs from the retained source product.')
    text=page.get('text') or ''
    first=text.splitlines()[0] if text else ''
    if not first.startswith('[meta] '):raise ValueError('Missing source fetch metadata.')
    meta=json.loads(first[7:])
    if (meta.get('ok') is not True or meta.get('content_ok') is not True or meta.get('thin')
            or meta.get('url')!=observation.source_url):
        raise ValueError('Detail page fetch is incomplete, unavailable or redirected.')
    expected=identity_name(observation.raw_title,observation.raw_brand)
    lines=text.splitlines();region=None
    expected_brand=resolve_brand(observation.raw_brand,conn)
    for i,line in enumerate(lines):
        if line.startswith('# ') and identity_name(line[2:].strip(),observation.raw_brand)==expected:
            end=next((j for j in range(i+1,len(lines)) if lines[j].startswith('# ')),len(lines))
            candidate=lines[i:end]
            brand_line=next((re.match(r'^Brand\s*:\s*(.+)$',l,re.I) for l in candidate if re.match(r'^Brand\s*:\s*(.+)$',l,re.I)),None)
            page_brand=resolve_brand(brand_line[1],conn) if brand_line else None
            if expected_brand and page_brand and expected_brand.get('verified') and expected_brand.get('id')==page_brand.get('id'):
                region=candidate;break
    if region is None:raise ValueError('Product heading and verified brand do not identify the retained source product.')
    # Remove only the exact trailing Share widget before section validation;
    # the complete original fetched page remains in provenance.
    blocks=[]
    for line in region:
        heading=re.match(r'^(#{1,6})\s+(.+)$',line)
        if not heading:line=re.sub(r'[,\s.]*\bShare\s*:\s*$','',line,flags=re.I)
        blocks.append(f'<h{len(heading[1])}>{html.escape(heading[2])}</h{len(heading[1])}>' if heading else '<p>'+html.escape(line)+'</p>')
    section=preferred_section(extract_sections(''.join(blocks),source['section_rules'],observation.raw_title))
    if not section:
        if meta.get('content_complete') is not True:
            raise ValueError('Incomplete page cannot establish that no ingredient declaration exists.')
        return None
    if meta.get('content_complete') is not True:
        # Pagination can omit unrelated navigation/footer while retaining the
        # complete product declaration. Require the literal ingredient text
        # followed by an observed closing product-section heading; a page cut
        # off in the middle of an ingredient list must never qualify.
        region_text='\n'.join(region)
        literal=re.search(r'\s+'.join(re.escape(w) for w in section.text.split()),region_text)
        end_heading=(re.search(r'^#{1,6}\s+(?:additional information|how to use|directions|reviews|customer reviews|warnings|product details|size)\b',
                              region_text[literal.end():],re.I|re.M) if literal else None)
        if not end_heading:raise ValueError('Partial page does not contain a closed literal ingredient declaration.')
    # "What's in it" can contain a short benefits/active summary rather than
    # a complete formulation. Retain that literal evidence without upgrading
    # it to a full list merely because it contains four comma-separated items.
    from pipeline.inci_tokenizer import tokenize_inci_string,CARRIER_BASE_MARKERS
    validation=tokenize_inci_string(section.text,product_title=observation.raw_title)
    generic=any(re.match(r"^#{1,6}\s+what['’]s in it\s*$",l,re.I) for l in region)
    explicit=any(re.match(r'^#{1,6}\s+(?:full ingredients(?: list)?|inci)\s*$',l,re.I) for l in region)
    base_tokens={t for t in validation.tokens if t in CARRIER_BASE_MARKERS or re.search(
        r'\b(?:wax|triglycerides?|myristate|palmitate|stearate|copolymer|crosspolymer|sulfonate|sulfosuccinate|betaine|glucoside)\b',t)}
    if validation.completeness!='full' or (generic and not explicit and (len(validation.tokens)<8 or len(base_tokens)<2) and not validation.validation_flags.get('single_ingredient_formulation')):
        section=replace(section,section_type='partial',completeness='partial',order_verified=False)
    return section


def retain_detail_ingredients(conn,observation_id,page,approve=False):
    from pipeline.catalog_reset import guard_catalog_writes
    from pipeline.saved_identity_rematch import _from_frozen_observation
    from pipeline.aggregation_service import save_observation
    from pipeline.taxonomy_service import classify_product_attributes
    from pipeline.source_formulations import resolve_source_formulations,approve_formulation_evidence
    from pipeline.inci_tokenizer import tokenize_inci_string
    guard_catalog_writes(conn)
    original=conn.execute('SELECT * FROM source_product_observations WHERE id=?',(observation_id,)).fetchone()
    if not original:raise ValueError('Source observation not found.')
    latest=conn.execute('SELECT MAX(id) FROM source_product_observations WHERE source_id=? AND source_product_id=?',(original['source_id'],original['source_product_id'])).fetchone()[0]
    if latest!=observation_id:raise ValueError('Source product changed after this page was selected.')
    source=get_source(conn,original['source_id'])
    if not source['enabled'] or source['source_type']!='retailer' or source['market']!='EG':
        raise ValueError('This enrichment requires an enabled Egyptian retailer source.')
    observation=_from_frozen_observation(conn,observation_id,{})
    current_type=classify_product_attributes(observation.raw_title,observation.raw_brand,observation.raw_category or '',observation.description or '')
    if not current_type.is_skincare or current_type.classification_status not in ('auto_approved','approved'):
        raise ValueError('Current retailer product type does not establish an approved skincare product.')
    if any(v.attributes for v in observation.variants):
        raise ValueError('Variant-specific formulas require scoped source evidence.')
    links=list(conn.execute("""SELECT l.canonical_product_id,l.identity_status FROM product_identity_links l
        JOIN canonical_products c ON c.id=l.canonical_product_id
        WHERE l.source_id=? AND l.source_product_id=? AND l.status='accepted'
        AND c.identity_status NOT IN ('redirected','reversed_split')""",(source['id'],observation.source_product_id)))
    if len(links)!=1 or links[0]['identity_status']!='identity_verified':
        raise ValueError('An exact accepted source association is required.')
    cid=links[0]['canonical_product_id']
    canonical=conn.execute('SELECT identity_status,is_skincare FROM canonical_products WHERE id=?',(cid,)).fetchone()
    if not canonical or canonical['identity_status']!='verified' or not canonical['is_skincare'] or pending_identity_review(conn,cid):
        raise ValueError('Canonical identity is unverified, superseded or disputed.')
    if not verified_link(conn,source['id'],observation.source_product_id,cid):raise ValueError('Source association is not verified.')
    section=confirmed_detail_section(observation,page,conn)
    if section is None:return {'status':'no_full_declaration','canonical_id':cid,'observation_id':observation_id}
    if not conn.in_transaction:conn.execute('BEGIN')
    conn.execute('SAVEPOINT source_detail_ingredients')
    try:
        payload={**observation.raw_payload,'detail_ingredient_evidence':{'retained_observation_id':observation_id,
            'retained_observed_at':observation.observed_at,'fetched_at':now(),'method':'donsetch_whole_page',
            'url':page['url'],'text':page['text'],'page_hash':hashlib.sha256(page['text'].encode()).hexdigest()}}
        enriched=replace(observation,ingredient_text=section.text,ingredient_section_type=section.section_type,
            ingredient_order_verified=section.order_verified,formula_code=section.formula_code,observed_at=now(),raw_payload=payload)
        oid=save_observation(conn,enriched,json.loads(original['identity_attributes']),
            classify_product_attributes(enriched.raw_title,enriched.raw_brand,enriched.raw_category or '',enriched.description or ''))
        validation=tokenize_inci_string(section.text,product_title=enriched.raw_title)
        flags={'identity_resolved':True,'order_verified':section.order_verified,'source_market':source['market'],
            'identity_link_product_id':observation.source_product_id,'source_id':source['id'],
            'method':'retained_source_detail','page_hash':payload['detail_ingredient_evidence']['page_hash']}
        flags['source_page_complete']=json.loads(page['text'].splitlines()[0][7:]).get('content_complete') is True
        flags['evidence_scope']='whole_page' if flags['source_page_complete'] else 'closed_product_declaration'
        candidate=FormulationCandidate(cid,'source:'+source['id'],section.text,validation.tokens,
            validation.declared_actives,evidence_url=page['url'],source_record_id=str(oid),completeness=section.completeness,validation_flags=flags)
        eid=FormulationRepository().record_candidate_evidence(candidate,audited_by='catalog_detail_repair_agent',conn=conn)
        conn.execute('UPDATE inci_audit_evidence SET observation_id=?,source_market=?,formula_code=?,order_verified=? WHERE id=?',(oid,source['market'],section.formula_code,int(section.order_verified),eid))
        conn.execute('UPDATE product_offers SET observation_id=? WHERE observation_id=? AND canonical_product_id=? AND source_id=? AND source_product_id=?',(oid,observation_id,cid,source['id'],observation.source_product_id))
        assessment=resolve_source_formulations(conn,cid,publish=False)
        published=False
        if approve and section.completeness=='full' and section.order_verified and assessment['status'] in ('ready','staged'):
            published=approve_formulation_evidence(conn,eid,'catalog_detail_repair_agent',
                'Reviewed literal full ordered declaration on the exact current Egyptian retailer URL, product heading and verified brand; existing verified source association; no competing ordered formula.')['published']
        conn.execute('''INSERT INTO formulation_resolution_queue(canonical_product_id,status) VALUES(?,?)
            ON CONFLICT(canonical_product_id) DO UPDATE SET status=excluded.status,updated_at=CURRENT_TIMESTAMP''',
            (cid,'completed' if published else 'review' if assessment['status']=='conflict' else 'pending'))
        conn.execute('RELEASE SAVEPOINT source_detail_ingredients')
        return {'status':'published' if published else 'evidence_only' if section.completeness!='full' or not section.order_verified else assessment['status'],'canonical_id':cid,
                'original_observation_id':observation_id,'observation_id':oid,'evidence_id':eid,'ingredient_count':len(validation.tokens)}
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT source_detail_ingredients');conn.execute('RELEASE SAVEPOINT source_detail_ingredients');raise
