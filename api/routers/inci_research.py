"""Separate operator-controlled INCI source-research queue (not identity Gemini queue)."""
from fastapi import APIRouter,BackgroundTasks,HTTPException,Query
from pydantic import BaseModel,Field
from typing import Literal
from pipeline.db_manager import get_connection
from pipeline.inci_research import (ensure_schema,list_queue,enqueue_unresolved,
    retry_case,start_run,cancel_run,run_queue,run_status,review_case)
from security.admin import reviewer_name

router=APIRouter(prefix='/admin/inci-research',tags=['INCI MCP research'])


def operation(fn):
    conn=get_connection()
    try:
        result=fn(conn)
        conn.commit()
        return result
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=409,detail=str(exc))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class QueueRequest(BaseModel):
    canonical_ids: list[int]=Field(min_length=1,max_length=500)


class RunRequest(BaseModel):
    limit: int=Field(default=10,ge=1,le=100)
    min_confidence: float=Field(default=0.85,ge=0,le=1)
    max_pages: int=Field(default=3,ge=1,le=5)


class ReviewRequest(BaseModel):
    action: Literal['approve','reject']
    reason: str=Field(min_length=8)
    market_compatible: bool=False
    correction: bool=False


@router.get('/queue')
def queue(status:str|None=None,search:str='',min_confidence:float=Query(0,ge=0,le=1),
          limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
    return operation(lambda conn:list_queue(conn,status,search,limit,offset,min_confidence))


@router.post('/queue')
def enqueue(request:QueueRequest):
    return operation(lambda conn:enqueue_unresolved(conn,request.canonical_ids,
                                                   actor=reviewer_name('admin')))


@router.post('/queue/{canonical_id}/retry')
def retry(canonical_id:int):
    return operation(lambda conn:retry_case(conn,canonical_id,reviewer_name('admin')))


@router.post('/run')
def start(request:RunRequest,background_tasks:BackgroundTasks):
    run=operation(lambda conn:start_run(conn,request.limit))
    background_tasks.add_task(run_queue,run['run_id'],threshold=request.min_confidence,
                              max_pages=request.max_pages)
    return {'status':'started',**run}


@router.get('/runs/{run_id}')
def status(run_id:str):
    return operation(lambda conn:run_status(conn,run_id))


@router.post('/runs/{run_id}/cancel')
def stop(run_id:str):
    return operation(lambda conn:cancel_run(conn,run_id))


@router.post('/queue/{canonical_id}/review')
def review(canonical_id:int,request:ReviewRequest):
    return operation(lambda conn:review_case(
        conn,canonical_id,request.action,reviewer_name('admin'),
        request.reason,request.market_compatible,request.correction))


@router.get('/connection')
def readiness():
    """No API keys are ever sent to the browser."""
    import os,shutil
    from pipeline.inci_research import DEFAULT_MODEL
    return {'donsetch_installed':bool(shutil.which(os.getenv('DONSETCH_COMMAND','donsetch'))),
            'gemini_configured':bool(os.getenv('GEMINI_API_KEY')),
            'model':os.getenv('INCI_RESEARCH_GEMINI_MODEL',DEFAULT_MODEL),
            'safety':'candidate_only_manual_approval'}
