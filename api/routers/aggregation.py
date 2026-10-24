"""Staged multi-source ingestion, review, reconciliation and controlled rollout."""
import json
from typing import Any,Optional,Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from pipeline.db_manager import get_connection

router=APIRouter(prefix='/admin/aggregation',tags=['Source aggregation'])


class IngestRequest(BaseModel):
    payload: Any
    url: Optional[str]=None
    dry_run: bool=True


class CollectRequest(BaseModel):
    urls: list[str]=Field(min_length=1,max_length=100)
    dry_run: bool=True
    max_products: int=Field(default=100,ge=1,le=1000)


class ReviewRequest(BaseModel):
    action: Literal['link','cannot_link','category','dismiss','request_evidence','keep_separate']
    reviewer: str=Field(min_length=1)
    reason: str=Field(min_length=1)
    canonical_id: Optional[int]=None
    category: Optional[str]=None


class ReviewedOperation(BaseModel):
    reviewer: str=Field(min_length=1)
    reason: str=Field(min_length=1)


class MergeRequest(ReviewedOperation):
    survivor_id: int
    merged_id: int


class SplitRequest(ReviewedOperation):
    product_ids: list[int]=[]
    offer_ids: list[int]=[]
    title: str=Field(min_length=1)
    attributes: dict={}


class SourceToggle(BaseModel):
    enabled: bool


class RolloutRequest(ReviewedOperation):
    report: dict


class BrandAliasRequest(ReviewedOperation):
    alias: str
    canonical_name: str


class CanonicalConstraintRequest(ReviewedOperation):
    left_id: int
    right_id: int
    relation: Literal['cannot_link']='cannot_link'


def operation(callback):
    conn=get_connection()
    try:
        result=callback(conn)
        conn.commit()
        return result
    except ValueError as exc:
        conn.rollback();raise HTTPException(status_code=400,detail=str(exc))
    except Exception:
        conn.rollback();raise
    finally:conn.close()


@router.get('/sources')
def sources():
    from pipeline.source_registry import list_source_inventory
    return operation(lambda conn:{'sources':list_source_inventory(conn)})


@router.patch('/sources/{source_id}')
def enable_collection(source_id:str,request:SourceToggle):
    def run(conn):
        if not conn.execute('SELECT 1 FROM sources WHERE id=?',(source_id,)).fetchone():raise ValueError('Source not found.')
        conn.execute('UPDATE sources SET enabled=? WHERE id=?',(int(request.enabled),source_id))
        return {'source_id':source_id,'enabled':request.enabled}
    return operation(run)


@router.post('/sources/{source_id}/ingest')
def ingest(source_id:str,request:IngestRequest):
    from pipeline.source_registry import get_source
    from pipeline.source_adapters import get_adapter
    from pipeline.aggregation_service import process_source_observation
    def run(conn):
        adapter=get_adapter(get_source(conn,source_id))
        observations=adapter.parse(request.payload,request.url)
        if len(observations)>1000:raise ValueError('Ingestion batch exceeds 1000 observations.')
        return {'results':[process_source_observation(conn,o,request.dry_run) for o in observations],'dry_run':request.dry_run}
    return operation(run)


@router.post('/sources/{source_id}/collect')
def collect(source_id:str,request:CollectRequest):
    from pipeline.source_collection import collect_source
    return operation(lambda conn:collect_source(conn,source_id,request.urls,request.max_products,request.dry_run))


@router.get('/sources/{source_id}/coverage')
def coverage(source_id:str):
    from pipeline.aggregation_benchmark import source_coverage_report
    return operation(lambda conn:source_coverage_report(conn,source_id))


@router.post('/sources/{source_id}/rollout')
def rollout(source_id:str,request:RolloutRequest):
    from pipeline.aggregation_benchmark import enable_source_rollout
    return operation(lambda conn:enable_source_rollout(conn,source_id,request.report,request.reviewer,request.reason))


@router.get('/observations')
def observations(source_id:Optional[str]=None,limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0)):
    def run(conn):
        fields='id,source_id,source_product_id,source_url,raw_title,raw_brand,raw_category,source_gtin,observed_at,ingredient_section_type,market,classification'
        query=f'SELECT {fields} FROM source_product_observations'
        params=[]
        if source_id:query+=' WHERE source_id=?';params.append(source_id)
        return {'observations':[dict(r) for r in conn.execute(query+' ORDER BY id DESC LIMIT ? OFFSET ?',params+[limit,offset])]}
    return operation(run)


@router.get('/reviews')
def reviews(kind:Optional[str]=None,status:str='pending',limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0)):
    def run(conn):
        query='SELECT * FROM product_review_queue WHERE status=?';params=[status]
        if kind:query+=' AND kind=?';params.append(kind)
        count=conn.execute('SELECT COUNT(*) FROM ('+query+')',params).fetchone()[0]
        rows=[dict(r) for r in conn.execute(query+' ORDER BY id LIMIT ? OFFSET ?',params+[limit,offset])]
        for row in rows:row['details']=json.loads(row['details']);row['decision']=json.loads(row['decision']) if row['decision'] else None
        return {'reviews':rows,'total':count}
    return operation(run)


@router.post('/reviews/{review_id}')
def review(review_id:int,request:ReviewRequest):
    from pipeline.review_service import resolve_review
    return operation(lambda conn:resolve_review(conn,review_id,request.action,request.reviewer,request.reason,request.canonical_id,request.category))


@router.post('/brands/aliases')
def brand_alias(request:BrandAliasRequest):
    from pipeline.brand_registry import approve_brand_alias
    return operation(lambda conn:{'brand_id':approve_brand_alias(conn,request.alias,request.canonical_name,request.reviewer)})


@router.post('/identity/constraints')
def cannot_link(request:CanonicalConstraintRequest):
    def run(conn):
        if request.left_id==request.right_id:raise ValueError('Choose two different canonical IDs.')
        pair=sorted([request.left_id,request.right_id])
        if conn.execute('SELECT COUNT(*) FROM canonical_products WHERE id IN (?,?)',pair).fetchone()[0]!=2:raise ValueError('Canonical ID not found.')
        from pipeline.review_service import require_review
        require_review(request.reviewer,request.reason)
        conn.execute('''INSERT INTO canonical_identity_constraints(left_id,right_id,relation,reviewed_by,reason) VALUES (?,?,'cannot_link',?,?)
            ON CONFLICT(left_id,right_id) DO UPDATE SET relation='cannot_link',reviewed_by=excluded.reviewed_by,reason=excluded.reason''',(*pair,request.reviewer,request.reason))
        return {'canonical_ids':pair,'relation':'cannot_link'}
    return operation(run)


@router.post('/identity/merge')
def merge(request:MergeRequest):
    from pipeline.review_service import merge_canonicals
    return operation(lambda conn:{'history_id':merge_canonicals(conn,request.survivor_id,request.merged_id,request.reviewer,request.reason)})


@router.post('/identity/{canonical_id}/split')
def split(canonical_id:int,request:SplitRequest):
    from pipeline.review_service import split_canonical
    return operation(lambda conn:split_canonical(conn,canonical_id,request.product_ids,request.offer_ids,request.title,request.attributes,request.reviewer,request.reason))


@router.post('/identity/history/{history_id}/reverse')
def reverse(history_id:int,request:ReviewedOperation):
    from pipeline.review_service import reverse_identity_operation
    return operation(lambda conn:{'history_id':reverse_identity_operation(conn,history_id,request.reviewer,request.reason)})


@router.get('/identity/history')
def history(canonical_id:int):
    return operation(lambda conn:{'history':[dict(r) for r in conn.execute('SELECT * FROM canonical_identity_history WHERE canonical_product_id=? OR target_id=? ORDER BY id',(canonical_id,canonical_id))]})


@router.post('/reconcile')
def reconcile():
    from pipeline.reconciliation_service import reconcile_catalog
    return operation(reconcile_catalog)


@router.get('/formulations/{canonical_id}')
def preview_formulations(canonical_id:int):
    from pipeline.source_formulations import resolve_source_formulations
    return operation(lambda conn:resolve_source_formulations(conn,canonical_id,False))


@router.post('/formulations/{canonical_id}/resolve')
def publish_formulations(canonical_id:int):
    from pipeline.source_formulations import resolve_source_formulations
    return operation(lambda conn:resolve_source_formulations(conn,canonical_id,True))


class FormulationEvidenceApproval(ReviewedOperation):
    correction: bool=False
    market_compatible: bool=False


@router.post('/formulations/evidence/{evidence_id}/approve')
def approve_evidence(evidence_id:int,request:FormulationEvidenceApproval):
    from pipeline.source_formulations import approve_formulation_evidence
    return operation(lambda conn:approve_formulation_evidence(conn,evidence_id,request.reviewer,request.reason,request.correction,request.market_compatible))


@router.post('/identity/{canonical_id}/publish')
def publish_identity(canonical_id:int,request:ReviewedOperation):
    from pipeline.review_service import publish_canonical_identity
    return operation(lambda conn:{'history_id':publish_canonical_identity(conn,canonical_id,request.reviewer,request.reason)})

class ConnectionTestRequest(BaseModel):
    kind: Literal['source', 'api']
    ids: list[str] = Field(default_factory=list, max_length=31)
    sample_url: Optional[str] = Field(default=None, max_length=2000)


@router.get('/connection-tests')
def connection_test_status():
    from pipeline.connection_tests import snapshot
    return snapshot()


@router.post('/connection-tests')
def run_connection_tests(body: ConnectionTestRequest):
    from pipeline.connection_tests import start
    from pipeline.source_registry import get_source, url_matches_source
    if body.kind == 'api':
        ids = body.ids or ['gemini', 'jev', 'langsearch']
        if any(i not in ['gemini', 'jev', 'langsearch'] for i in ids):
            raise HTTPException(400, 'Unknown API provider.')
        return start('api', [(i, None) for i in ids])
    conn = get_connection()
    try:
        ids = body.ids or [r[0] for r in conn.execute('SELECT id FROM sources ORDER BY wave,id')]
        items = []
        for ident in ids:
            try:
                source = get_source(conn, ident)
            except ValueError as exc:
                raise HTTPException(404, 'Unknown source.') from exc
            if body.sample_url:
                if len(ids) != 1 or not url_matches_source(source, body.sample_url):
                    raise HTTPException(400, 'Provide a URL belonging to the single selected source.')
                source['test_url'] = body.sample_url
            elif source['adapter_type'] != 'shopify':
                row = conn.execute('SELECT source_url FROM source_product_observations WHERE source_id=? ORDER BY id DESC LIMIT 1', (ident,)).fetchone()
                if row:
                    source['test_url'] = row[0]
            items.append((ident, source))
    finally:
        conn.close()
    return start('source', items)
