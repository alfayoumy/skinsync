"""Staged multi-source ingestion, review, reconciliation and controlled rollout."""
import json
from typing import Any,Optional,Literal
from fastapi import APIRouter,HTTPException,Query,BackgroundTasks
from pydantic import BaseModel,Field
from pipeline.db_manager import get_connection
from security.admin import reviewer_name

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
    evaluation_id: int = Field(gt=0)


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
    return operation(lambda conn:enable_source_rollout(conn,source_id,request.evaluation_id,reviewer_name(request.reviewer),request.reason))


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
def reviews(kind:Optional[str]=None,status:str='pending',
            view:Literal['manual','awaiting_gemini','all']='manual',
            limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0)):
    """Pending human queue excludes Gemini cases until a model verdict requires review."""
    def run(conn):
        from pipeline.aggregation_schema import ensure_identity_gemini_schema
        ensure_identity_gemini_schema(conn)
        query='''FROM product_review_queue r LEFT JOIN identity_gemini_queue q ON q.review_id=r.id
            WHERE r.status=?'''
        params=[status]
        if kind:query+=' AND r.kind=?';params.append(kind)
        if view=='manual':
            query+=" AND (q.review_id IS NULL OR q.status='needs_manual')"
        elif view=='awaiting_gemini':
            query+=" AND q.status IN ('queued','processing')"
        count=conn.execute('SELECT COUNT(*) '+query,params).fetchone()[0]
        rows=[dict(r) for r in conn.execute('SELECT r.*,q.status gemini_status,q.verdict gemini_verdict,'
            'q.confidence gemini_confidence,q.explanation gemini_explanation '+query+
            ' ORDER BY r.id LIMIT ? OFFSET ?',params+[limit,offset])]
        for row in rows:
            # A malformed legacy JSON payload must not hide every other case.
            try:
                row['details']=json.loads(row['details'] or '{}')
            except (ValueError,TypeError):
                row['details']={'title':'Legacy review #'+str(row['id']),
                                'reason_code':'unparseable_legacy_evidence'}
            try:
                row['decision']=json.loads(row['decision']) if row['decision'] else None
            except (ValueError,TypeError):
                row['decision']=None
        return {'reviews':rows,'total':count,'view':view}
    return operation(run)


class GeminiEnqueueRequest(BaseModel):
    review_ids:Optional[list[int]]=None
    limit:int=Field(default=20000,ge=1,le=20000)


class GeminiRunRequest(BaseModel):
    limit:int=Field(default=500,ge=1,le=20000)
    batch_size:int=Field(default=10,ge=1,le=20)


@router.get('/reviews/gemini/queue')
def gemini_identity_queue_status():
    from pipeline.product_identity_gemini import queue_counts
    def fn(conn):
        from pipeline.aggregation_schema import ensure_identity_gemini_schema
        ensure_identity_gemini_schema(conn)
        counts=queue_counts(conn)
        return {'counts':counts,
            'pending_identity_cases':conn.execute("""SELECT COUNT(*) FROM product_review_queue
                WHERE status='pending' AND kind IN ('identity','duplicate')""").fetchone()[0]}
    return operation(fn)


@router.post('/reviews/gemini/enqueue')
def gemini_identity_enqueue(request:GeminiEnqueueRequest):
    from pipeline.product_identity_gemini import queue_cases
    def fn(conn):
        from pipeline.aggregation_schema import ensure_identity_gemini_schema
        ensure_identity_gemini_schema(conn)
        return queue_cases(conn,request.review_ids,request.limit,reviewer_name('identity_admin'))
    return operation(fn)


@router.post('/reviews/gemini/run')
def gemini_identity_run(request:GeminiRunRequest,background_tasks:BackgroundTasks):
    from pipeline.product_identity_gemini import run_identity_gemini_queue,recover_abandoned_cases
    from pipeline.task_tracker import create_task
    from pipeline.inci_matcher import _load_env
    from pipeline.config import cfg
    import os
    _load_env()
    if not (os.environ.get('GEMINI_API_KEY') or getattr(cfg,'GEMINI_API_KEY','')):
        raise HTTPException(status_code=409,detail='Configure GEMINI_API_KEY before starting product identity review.')
    conn=get_connection()
    try:
        from pipeline.aggregation_schema import ensure_identity_gemini_schema
        ensure_identity_gemini_schema(conn)
        recover_abandoned_cases(conn)
        ready=conn.execute("SELECT COUNT(*) FROM identity_gemini_queue WHERE status='queued'").fetchone()[0]
        conn.commit()
        active=conn.execute("""SELECT task_id FROM pipeline_tasks WHERE task_type='product_identity_gemini'
            AND status='running' LIMIT 1""").fetchone()
    finally:conn.close()
    if not ready:raise HTTPException(status_code=409,detail='No product identity cases are queued.')
    if active:raise HTTPException(status_code=409,detail='A Gemini product-review job is already running.')
    task_id=create_task('product_identity_gemini',target_source='review_queue',
        params={'limit':request.limit,'batch_size':request.batch_size},
        total_items=min(ready,request.limit),auto_start=True)
    background_tasks.add_task(run_identity_gemini_queue,task_id,request.limit,request.batch_size)
    return {'task_id':task_id,'status':'running','queued':ready,
        'message':'Gemini will evaluate several product-review cases per API call. Only guarded high-confidence matches are auto-resolved.'}


@router.get('/reviews/gemini/jobs/{task_id}')
def gemini_identity_job_status(task_id:str):
    conn=get_connection()
    try:
        row=conn.execute("""SELECT task_id,status,processed_items,total_items,metadata,error_message,
            current_stage FROM pipeline_tasks WHERE task_id=? AND task_type='product_identity_gemini'""",(task_id,)).fetchone()
        if not row:raise HTTPException(status_code=404,detail='Gemini review job not found.')
        result=dict(row);result['metadata']=json.loads(result['metadata'] or '{}')
        return result
    finally:conn.close()


@router.post('/reviews/gemini/jobs/{task_id}/cancel')
def gemini_identity_job_stop(task_id:str):
    from pipeline.task_tracker import cancel_task
    conn=get_connection()
    try:
        row=conn.execute("""SELECT status FROM pipeline_tasks
            WHERE task_id=? AND task_type='product_identity_gemini'""",(task_id,)).fetchone()
        if not row:raise HTTPException(status_code=404,detail='Gemini review job not found.')
    finally:conn.close()
    return {'cancelled':cancel_task(task_id,reason='Admin stopped Gemini product reviews')}



class SavedGeminiDecisionsRequest(BaseModel):
    limit:int=Field(default=5000,ge=1,le=20000)
    confidence_threshold:float=Field(default=0.90,ge=0.90,le=1.0)


@router.post('/reviews/gemini/saved/preview')
def preview_saved_gemini(request:SavedGeminiDecisionsRequest):
    """Re-evaluate persisted Gemini verdicts locally: zero model calls and zero writes."""
    from pipeline.saved_gemini_decisions import apply_saved_decisions
    from pipeline.aggregation_schema import ensure_identity_gemini_schema
    conn=get_connection()
    try:
        ensure_identity_gemini_schema(conn)
        report=apply_saved_decisions(conn,request.limit,request.confidence_threshold,dry_run=True)
        conn.rollback()
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post('/reviews/gemini/saved/apply')
def start_saved_gemini_application(request:SavedGeminiDecisionsRequest,
                                   background_tasks:BackgroundTasks):
    """Apply stored decisions in a checkpointed task; Gemini is NEVER called."""
    from pipeline.saved_gemini_decisions import candidate_rows,background_apply_saved
    from pipeline.aggregation_schema import ensure_identity_gemini_schema
    from pipeline.task_tracker import create_task
    conn=get_connection()
    try:
        ensure_identity_gemini_schema(conn)
        total=len(candidate_rows(conn,request.limit,request.confidence_threshold))
        active=conn.execute("""SELECT task_id FROM pipeline_tasks WHERE task_type='apply_saved_gemini'
            AND status='running' LIMIT 1""").fetchone()
        conn.commit()
    finally:conn.close()
    if active:raise HTTPException(status_code=409,detail='Saved-decision application is already running.')
    if total==0:raise HTTPException(status_code=409,detail='No saved Gemini decisions qualify. Preview the current queue.')
    task_id=create_task('apply_saved_gemini',target_source='review_queue',
        params={'confidence_threshold':request.confidence_threshold,'limit':request.limit,
                'calls_gemini':False},total_items=total,auto_start=True)
    background_tasks.add_task(background_apply_saved,task_id,request.limit,
                              request.confidence_threshold)
    return {'task_id':task_id,'status':'running','saved_decisions':total,
            'gemini_calls':0,'message':'Applying stored Gemini decisions only; no provider requests.'}


@router.get('/reviews/gemini/saved/jobs/{task_id}')
def saved_gemini_job_status(task_id:str):
    conn=get_connection()
    try:
        row=conn.execute("""SELECT task_id,status,processed_items,total_items,metadata,
            error_message,current_stage FROM pipeline_tasks
            WHERE task_id=? AND task_type='apply_saved_gemini'""",(task_id,)).fetchone()
        if not row:raise HTTPException(status_code=404,detail='Saved Gemini application job not found.')
        result=dict(row);result['metadata']=json.loads(result['metadata'] or '{}')
        return result
    finally:conn.close()


@router.post('/reviews/gemini/saved/jobs/{task_id}/cancel')
def stop_saved_gemini_application(task_id:str):
    from pipeline.task_tracker import cancel_task
    conn=get_connection()
    try:
        row=conn.execute("""SELECT status FROM pipeline_tasks
            WHERE task_id=? AND task_type='apply_saved_gemini'""",(task_id,)).fetchone()
        if not row:raise HTTPException(status_code=404,detail='Saved decision application job not found.')
        if row['status']!='running':
            return {'cancelled':False,'status':row['status']}
    finally:conn.close()
    return {'cancelled':bool(cancel_task(task_id,reason='Admin stopped saved Gemini decision application'))}


@router.get('/reviews/gemini/results')
def gemini_identity_results(
    verdict:Optional[Literal['same_product','different_product','uncertain']]=None,
    outcome:Optional[Literal['queued','processing','auto_resolved','needs_manual']]=None,
    review_status:Optional[Literal['pending','confirmed']]=None,
    confidence_min:float=Query(0,ge=0,le=1),
    confidence_max:float=Query(1,ge=0,le=1),
    search:str=Query('',max_length=120),
    sort_by:Literal['confidence','review_id','updated_at','verdict','outcome']='confidence',
    sort_order:Literal['asc','desc']='desc',
    limit:int=Query(50,ge=1,le=200),
    offset:int=Query(0,ge=0)
):
    """Server-side, paginated view of persisted Gemini outputs. No model calls."""
    if confidence_min>confidence_max:
        raise HTTPException(status_code=422,detail='Minimum confidence cannot exceed maximum.')
    from pipeline.aggregation_schema import ensure_identity_gemini_schema
    order_fields={'confidence':'q.confidence','review_id':'r.id',
                  'updated_at':'q.updated_at','verdict':'q.verdict',
                  'outcome':'q.status'}
    def fn(conn):
        ensure_identity_gemini_schema(conn)
        where=["q.verdict IS NOT NULL","q.confidence BETWEEN ? AND ?"]
        args=[confidence_min,confidence_max]
        if verdict:where.append('q.verdict=?');args.append(verdict)
        if outcome:where.append('q.status=?');args.append(outcome)
        if review_status:where.append('r.status=?');args.append(review_status)
        if search.strip():
            where.append("""(r.details LIKE ? OR
                CAST(r.id AS TEXT) LIKE ? OR CAST(r.canonical_product_id AS TEXT) LIKE ?)""")
            term='%'+search.strip()+'%'
            args.extend([term,term,term])
        joins=""" FROM identity_gemini_queue q
            JOIN product_review_queue r ON r.id=q.review_id """
        filter_sql=' WHERE '+' AND '.join(where)
        total=conn.execute('SELECT COUNT(*)'+joins+filter_sql,args).fetchone()[0]
        order=order_fields[sort_by]+' '+sort_order.upper()+', r.id DESC'
        sql="""SELECT r.id review_id,r.kind,r.status review_status,
            r.canonical_product_id,r.details,r.decision,r.review_notes,
            q.status outcome,q.verdict,q.confidence,q.model_name,
            q.explanation,q.attempts,q.queued_at,q.updated_at,
            q.evidence_hash queued_evidence_hash,r.evidence_hash current_evidence_hash
            """+joins+filter_sql+' ORDER BY '+order+' LIMIT ? OFFSET ?'
        records=[]
        for row in conn.execute(sql,args+[limit,offset]):
            data=dict(row)
            try:details=json.loads(data.pop('details') or '{}')
            except (ValueError,TypeError):details={}
            try:decision=json.loads(data['decision']) if data['decision'] else None
            except (ValueError,TypeError):decision=None
            data['decision']=decision
            data['title']=details.get('title') or details.get('original_title') or (
                'Canonical #'+str(data['canonical_product_id']))
            data['brand']=details.get('brand')
            data['issue']=details.get('reason_code')
            data['offers']=[{'product_id':o.get('product_id'),
                'store_id':o.get('store_id'),'brand':o.get('brand'),
                'title':o.get('title')} for o in (details.get('offers') or [])[:30]
                if isinstance(o,dict)]
            data['evidence_current']=data.pop('queued_evidence_hash')==data.pop('current_evidence_hash')
            records.append(data)
        return {'results':records,'total':total,'limit':limit,'offset':offset,
                'sort_by':sort_by,'sort_order':sort_order,
                'no_gemini_calls':True}
    return operation(fn)


@router.post('/reviews/{review_id}')
def review(review_id:int,request:ReviewRequest):
    from pipeline.review_service import resolve_review
    return operation(lambda conn:resolve_review(conn,review_id,request.action,reviewer_name(request.reviewer),request.reason,request.canonical_id,request.category))


@router.post('/brands/aliases')
def brand_alias(request:BrandAliasRequest):
    from pipeline.brand_registry import approve_brand_alias
    return operation(lambda conn:{'brand_id':approve_brand_alias(conn,request.alias,request.canonical_name,reviewer_name(request.reviewer))})


@router.post('/identity/constraints')
def cannot_link(request:CanonicalConstraintRequest):
    def run(conn):
        if request.left_id==request.right_id:raise ValueError('Choose two different canonical IDs.')
        pair=sorted([request.left_id,request.right_id])
        if conn.execute('SELECT COUNT(*) FROM canonical_products WHERE id IN (?,?)',pair).fetchone()[0]!=2:raise ValueError('Canonical ID not found.')
        from pipeline.review_service import require_review
        require_review(reviewer_name(request.reviewer),request.reason)
        conn.execute('''INSERT INTO canonical_identity_constraints(left_id,right_id,relation,reviewed_by,reason) VALUES (?,?,'cannot_link',?,?)
            ON CONFLICT(left_id,right_id) DO UPDATE SET relation='cannot_link',reviewed_by=excluded.reviewed_by,reason=excluded.reason''',(*pair,reviewer_name(request.reviewer),request.reason))
        return {'canonical_ids':pair,'relation':'cannot_link'}
    return operation(run)


@router.post('/identity/merge')
def merge(request:MergeRequest):
    from pipeline.review_service import merge_canonicals
    return operation(lambda conn:{'history_id':merge_canonicals(conn,request.survivor_id,request.merged_id,reviewer_name(request.reviewer),request.reason)})


@router.post('/identity/{canonical_id}/split')
def split(canonical_id:int,request:SplitRequest):
    from pipeline.review_service import split_canonical
    return operation(lambda conn:split_canonical(conn,canonical_id,request.product_ids,request.offer_ids,request.title,request.attributes,reviewer_name(request.reviewer),request.reason))


@router.post('/identity/history/{history_id}/reverse')
def reverse(history_id:int,request:ReviewedOperation):
    from pipeline.review_service import reverse_identity_operation
    return operation(lambda conn:{'history_id':reverse_identity_operation(conn,history_id,reviewer_name(request.reviewer),request.reason)})


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
    return operation(lambda conn:approve_formulation_evidence(conn,evidence_id,reviewer_name(request.reviewer),request.reason,request.correction,request.market_compatible))


@router.post('/identity/{canonical_id}/publish')
def publish_identity(canonical_id:int,request:ReviewedOperation):
    from pipeline.review_service import publish_canonical_identity
    return operation(lambda conn:{'history_id':publish_canonical_identity(conn,canonical_id,reviewer_name(request.reviewer),request.reason)})

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


class EvaluationLabel(ReviewedOperation):
    observation_id: int
    expected_canonical_id: Optional[int] = None
    expected_new: bool = False
    category: str
    is_skincare: bool


@router.post('/sources/{source_id}/evaluations')
def create_source_evaluation(source_id:str,request:ReviewedOperation):
    from pipeline.source_evaluation import create_evaluation
    return operation(lambda conn:create_evaluation(conn,source_id,reviewer_name(request.reviewer)))


@router.get('/evaluations/{run_id}')
def evaluation_details(run_id:int):
    def run(conn):
        row=conn.execute('SELECT * FROM source_evaluations WHERE id=?',(run_id,)).fetchone()
        if not row:raise ValueError('Evaluation not found.')
        return {'evaluation_id':run_id,'source_id':row['source_id'],'samples':json.loads(row['samples']),
                'labels':[dict(r) for r in conn.execute('SELECT * FROM source_evaluation_labels WHERE run_id=?',(run_id,))]}
    return operation(run)


@router.post('/evaluations/{run_id}/labels')
def label_evaluation(run_id:int,request:EvaluationLabel):
    from pipeline.source_evaluation import label_example
    return operation(lambda conn:label_example(conn,run_id,request.observation_id,request.expected_canonical_id,
        request.expected_new,request.category,request.is_skincare,reviewer_name(request.reviewer),request.reason))


@router.post('/evaluations/{run_id}/finalize')
def finalize_source_evaluation(run_id:int,request:ReviewedOperation):
    from pipeline.source_evaluation import finalize_evaluation
    return operation(lambda conn:finalize_evaluation(conn,run_id,reviewer_name(request.reviewer)))


@router.post('/reconciliation/previews')
def reconciliation_preview(request:ReviewedOperation):
    from pipeline.reconciliation_service import save_reconciliation_preview
    return operation(lambda conn:save_reconciliation_preview(conn,reviewer_name(request.reviewer)))


@router.post('/reconciliation/previews/{preview_id}/findings')
def reconciliation_findings(preview_id:int,request:ReviewedOperation):
    from pipeline.reconciliation_service import persist_reconciliation_findings
    return operation(lambda conn:persist_reconciliation_findings(conn,preview_id,reviewer_name(request.reviewer),request.reason))
