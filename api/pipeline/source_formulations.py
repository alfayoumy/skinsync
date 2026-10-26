"""Reconcile exact-source evidence before controlled canonical publication."""
import json
from pipeline.formulation_service import FormulationCandidate,FormulationRepository
from pipeline.inci_tokenizer import tokenize_inci_string
from pipeline.aggregation_service import queue_review

AUTHORITY={'regional_official':0,'official_other_market':1,'local_retailer':2,'reference':3,'unvalidated':4}


def resolve_source_formulations(conn,canonical_id,publish=False):
    canonical=conn.execute('SELECT * FROM canonical_products WHERE id=?',(canonical_id,)).fetchone()
    if not canonical:raise ValueError('Canonical not found.')
    canonical=dict(canonical)
    evidence=[dict(r) for r in conn.execute('''SELECT e.*,s.source_type,s.reliability,s.market,s.publish_enabled,s.enabled
        FROM inci_audit_evidence e JOIN source_product_observations o ON o.id=e.observation_id
        JOIN sources s ON s.id=o.source_id WHERE e.canonical_product_id=? ORDER BY e.id''',(canonical_id,))]
    eligible=[];rejected=[]
    for item in evidence:
        flags=json.loads(item.get('validation_flags') or '{}')
        validation=tokenize_inci_string(item['raw_ingredients_found'],product_title=canonical['canonical_title'])
        reason='untrusted_historical_scrape' if (item.get('source_name')=='historical_store_scrape' or flags.get('method')=='historical_store_scrape') else None
        from pipeline.identity_trust import verified_link
        source_obs=conn.execute('SELECT source_id,source_product_id FROM source_product_observations WHERE id=?',(item['observation_id'],)).fetchone()
        if reason is None and (not source_obs or not verified_link(conn,source_obs[0],flags.get('identity_link_product_id') or source_obs[1],canonical_id)):reason='identity_unverified'
        elif reason is None and (item['completeness']!='full' or not item['order_verified'] or validation.completeness!='full' or validation.is_truncated):reason='incomplete_or_unordered'
        elif reason is None and not flags.get('identity_resolved'):reason='identity_unresolved'
        elif reason is None and item['source_type']=='manufacturer' and item['market'] not in ('EG','MENA') and not flags.get('region_compatibility_approved'):
            reason='regional_compatibility_requires_review'
        elif reason is None and canonical.get('market') and canonical.get('identity_attributes'):
            identity=json.loads(canonical['identity_attributes'])
            if identity.get('region_defining') and item['market']!=canonical['market']:reason='regional_formula_conflict'
        if reason:
            rejected.append({'evidence_id':item['id'],'reason':reason});continue
        item['hash']=FormulationRepository.compute_formula_hash(validation.tokens)
        item['tokens']=validation.tokens;item['actives']=validation.declared_actives
        eligible.append(item)
    groups={}
    for item in eligible:groups.setdefault(item['hash'],[]).append(item)
    existing_hash=None
    if canonical.get('ingredients_raw'):
        existing_hash=FormulationRepository.compute_formula_hash(tokenize_inci_string(canonical['ingredients_raw'],product_title=canonical['canonical_title']).tokens)
    conflicts=len(groups)>1 or bool(existing_hash and any(h!=existing_hash for h in groups))
    details={'canonical_product_id':canonical_id,'eligible_evidence':[e['id'] for e in eligible],
             'rejected_evidence':rejected,'formula_hashes':list(groups),'existing_hash':existing_hash}
    if conflicts:
        if publish:queue_review(conn,'formulation',None,None,canonical_id,details)
        return {'status':'conflict','published':False,**details}
    if not eligible:return {'status':'no_eligible_evidence','published':False,**details}
    # Authority chooses a representative only once credible declarations agree.
    best=min(eligible,key=lambda e:(AUTHORITY.get(e['reliability'],4),e['id']))
    allowed=bool(best['enabled'] and best['publish_enabled'])
    if not publish or not allowed:return {'status':'ready' if allowed else 'staged','published':False,'selected_evidence_id':best['id'],**details}
    candidate=FormulationCandidate(canonical_id,best['source_name'],best['raw_ingredients_found'],best['tokens'],best['actives'],
        confidence_score=1,evidence_url=best['evidence_url'],source_record_id=best['source_record_id'],completeness='full',
        validation_flags=json.loads(best.get('validation_flags') or '{}'))
    published=FormulationRepository().publish_formulation(candidate,'Concordant exact-product source evidence.',conn=conn)
    conn.execute("UPDATE formulation_resolution_queue SET status=?,updated_at=CURRENT_TIMESTAMP WHERE canonical_product_id=?",
                 ('completed' if published else 'review',canonical_id))
    return {'status':'published' if published else 'review','published':published,**details}


def source_candidate_for_waterfall(conn,canonical_id):
    result=resolve_source_formulations(conn,canonical_id,publish=False)
    if result['status']!='ready':return None,result['status']
    row=dict(conn.execute('SELECT * FROM inci_audit_evidence WHERE id=?',(result['selected_evidence_id'],)).fetchone())
    validation=tokenize_inci_string(row['raw_ingredients_found'])
    return FormulationCandidate(canonical_id,row['source_name'],row['raw_ingredients_found'],validation.tokens,
        validation.declared_actives,confidence_score=1,evidence_url=row['evidence_url'],source_record_id=row['source_record_id'],
        completeness='full',validation_flags=json.loads(row['validation_flags'] or '{}')),result['status']


def approve_formulation_evidence(conn,evidence_id,reviewer,reason,correction=False,market_compatible=False):
    from pipeline.review_service import require_review
    require_review(reviewer,reason)
    row=conn.execute('''SELECT e.*,s.source_type,s.market FROM inci_audit_evidence e
        JOIN source_product_observations o ON o.id=e.observation_id JOIN sources s ON s.id=o.source_id WHERE e.id=?''',(evidence_id,)).fetchone()
    if not row:raise ValueError('Source evidence not found.')
    row=dict(row);flags=json.loads(row['validation_flags'] or '{}')
    if row.get('source_name')=='historical_store_scrape' or flags.get('method')=='historical_store_scrape':
        raise ValueError('Historical scraped INCI evidence is not approvable.')
    if not flags.get('identity_resolved') or row['completeness']!='full' or not row['order_verified']:
        raise ValueError('Only complete, ordered, identity-resolved evidence can be approved.')
    if row['source_type']=='manufacturer' and row['market'] not in ('EG','MENA') and not market_compatible:
        raise ValueError('Explicit confirmation of regional compatibility is required.')
    if market_compatible:
        flags['region_compatibility_approved']={'reviewer':reviewer,'reason':reason}
    candidate=FormulationCandidate(row['canonical_product_id'],row['source_name'],row['raw_ingredients_found'],
        json.loads(row['parsed_tokens']),confidence_score=1,evidence_url=row['evidence_url'],source_record_id=row['source_record_id'],
        completeness='full',validation_flags=flags)
    # Explicit reviewed correction is the only route that can replace an accepted formula.
    published=FormulationRepository().publish_formulation(candidate,reason,force=correction,audited_by=reviewer,conn=conn)
    if published:conn.execute('UPDATE inci_audit_evidence SET validation_flags=? WHERE id=?',(json.dumps(flags),evidence_id))
    return {'published':published,'canonical_product_id':row['canonical_product_id'],'evidence_id':evidence_id}
