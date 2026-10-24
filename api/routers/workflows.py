"""Four distinct catalog workflows: discovery, existing-offer updates, INCI, and AI queues.

This router intentionally contains no INCI publication and no LLM calls.
"""
from collections import Counter
from pipeline.legacy_identity_reconcile import reconcile_legacy_exact
from typing import Literal
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from pipeline.db_manager import get_connection
from pipeline.source_registry import get_source
from pipeline.source_adapters import ShopifyAdapter
from pipeline.source_adapters.contracts import money
from pipeline.aggregation_service import process_source_observation
from pipeline.canonical_identity_resolver import identity_name, extract_identity, compare_attributes, valid_gtin
from pipeline.brand_registry import resolve_brand
from pipeline.identity_gates import validate_identity_gates
from pipeline.offer_service import refresh_offer_aggregates

router=APIRouter(prefix='/admin/workflows',tags=['Catalog workflows'])


class ShopifyScan(BaseModel):
    source_id: str
    start_page: int=Field(default=1,ge=1,le=5000)
    pages: int=Field(default=1,ge=1,le=3)
    dry_run: bool=True


class OfferRefresh(BaseModel):
    source_id: str
    start_page: int=Field(default=1,ge=1,le=5000)
    pages: int=Field(default=1,ge=1,le=3)
    update_price: bool=True
    update_stock: bool=True
    dry_run: bool=True


def _shopify_source(conn,source_id):
    source=get_source(conn,source_id)
    if not source['enabled']:
        raise ValueError('This source is paused. Enable collection in Product Sources first.')
    if source['adapter_type']!='shopify':
        raise ValueError('For this source, import specific product URLs using the Product Sources collector.')
    return source


def discover_shopify(conn,source_id,start_page=1,pages=1,dry_run=True):
    """Import product observations and classify/link identities; never run INCI waterfall."""
    from pipeline.harvester import fetch_shopify_page
    source=_shopify_source(conn,source_id)
    adapter=ShopifyAdapter(source)
    summary=Counter()
    if dry_run:
        conn.execute('SAVEPOINT catalog_scan_preview')
    try:
        for page in range(start_page,start_page+pages):
            products=fetch_shopify_page(source['domain'],page)
            if not products:break
            summary['fetched']+=len(products)
            for raw in products:
                for obs in adapter.parse({'product':raw}):
                    result=process_source_observation(conn,obs,dry_run=False)
                    summary['observations']+=1
                    for item in result['results']:
                        summary[item['status']]+=1
                        if item.get('offer_ids'):summary['offers_seen']+=len(item['offer_ids'])
        if dry_run:
            conn.execute('ROLLBACK TO SAVEPOINT catalog_scan_preview')
            conn.execute('RELEASE SAVEPOINT catalog_scan_preview')
        return {
            'workflow':'product_discovery','dry_run':dry_run,'source_id':source_id,
            'pages_requested':pages,'fetched':summary['fetched'],
            'observations':summary['observations'],
            'existing_products_matched':summary['matched'],
            'new_products_staged':summary['provisional'],
            'needs_review':summary['review'],'excluded':summary['excluded'],
            'offers_seen':summary['offers_seen'],
            'note':'New identities remain hidden until approved. This does not run the INCI waterfall or LLM workers.',
        }
    except Exception:
        if dry_run:
            conn.execute('ROLLBACK TO SAVEPOINT catalog_scan_preview')
            conn.execute('RELEASE SAVEPOINT catalog_scan_preview')
        raise


def update_existing_shopify_offers(conn,source_id,start_page=1,pages=1,
                                   update_price=True,update_stock=True,dry_run=True):
    """Only update previously linked variant offers. Never insert products, identities, or INCI."""
    from pipeline.harvester import fetch_shopify_page
    if not (update_price or update_stock):
        raise ValueError('Choose prices, availability, or both.')
    source=_shopify_source(conn,source_id)
    if source['source_type']!='retailer':
        raise ValueError('Only retail sources have prices and availability.')
    counts=Counter()
    touched=set()
    if dry_run:conn.execute('SAVEPOINT offer_refresh_preview')
    try:
        for page in range(start_page,start_page+pages):
            products=fetch_shopify_page(source['domain'],page)
            if not products:break
            counts['fetched']+=len(products)
            for product in products:
                pid=str(product.get('id',''))
                title=str(product.get('title') or '')
                for variant in product.get('variants') or []:
                    key=str(variant.get('id',''))
                    offer=conn.execute("""SELECT id,canonical_product_id,title,price,compare_at_price,
                        is_available,normalized_size_ml,normalized_size_g,currency,sku,gtin FROM product_offers
                        WHERE source_id=? AND source_product_id=? AND source_variant_id=?""",
                        (source_id,pid,key)).fetchone()
                    if not offer:
                        counts['not_previously_linked']+=1
                        continue
                    offer=dict(offer)
                    existing=conn.execute('SELECT brand,canonical_title FROM canonical_products WHERE id=?',
                        (offer['canonical_product_id'],)).fetchone()
                    incoming_brand=resolve_brand(product.get('vendor',''),conn)
                    accepted_brand=resolve_brand(existing['brand'],conn) if existing else None
                    brand_ok=bool(incoming_brand and accepted_brand and incoming_brand['id']==accepted_brand['id'])
                    identity_ok=brand_ok and identity_name(title,existing['brand'])==identity_name(offer['title'],existing['brand'])
                    gates_ok,_=validate_identity_gates(title,offer['title'],existing['brand'] if existing else '',existing['brand'] if existing else '')
                    candidate_identity=extract_identity(title,existing['brand']) if existing else {}
                    accepted_identity=extract_identity(offer['title'],existing['brand']) if existing else {}
                    conflicting=bool(compare_attributes(candidate_identity,accepted_identity)['conflicts']) if existing else True
                    mismatched_sku=bool(offer['sku'] and variant.get('sku') and offer['sku']!=variant['sku'])
                    mismatched_gtin=bool(offer['gtin'] and valid_gtin(variant.get('barcode')) and
                                         offer['gtin']!=valid_gtin(variant['barcode']))
                    if not identity_ok or not gates_ok or conflicting or mismatched_sku or mismatched_gtin:
                        counts['identity_mismatches']+=1
                        continue
                    new_price=money(variant.get('price')) if update_price else offer['price']
                    new_compare=money(variant.get('compare_at_price')) if update_price else offer['compare_at_price']
                    new_available=int(bool(variant.get('available'))) if update_stock else offer['is_available']
                    changed=(
                        (update_price and (new_price!=offer['price'] or new_compare!=offer['compare_at_price']))
                        or (update_stock and new_available!=offer['is_available'])
                    )
                    if not changed:
                        counts['unchanged']+=1
                        continue
                    updates=[]
                    args=[]
                    if update_price:
                        updates.extend(['price=?','compare_at_price=?'])
                        args.extend([new_price,new_compare])
                        measure=offer['normalized_size_ml'] or offer['normalized_size_g']
                        updates.append('unit_price=?')
                        args.append(round(new_price*100/measure,2) if measure and new_price>0 else None)
                    if update_stock:
                        updates.append('is_available=?')
                        args.append(new_available)
                    args.append(offer['id'])
                    conn.execute('UPDATE product_offers SET '+','.join(updates)+',updated_at=CURRENT_TIMESTAMP WHERE id=?',args)
                    touched.add(offer['canonical_product_id'])
                    counts['updated']+=1
        for cid in touched:
            refresh_offer_aggregates(conn,cid)
        if dry_run:
            conn.execute('ROLLBACK TO SAVEPOINT offer_refresh_preview')
            conn.execute('RELEASE SAVEPOINT offer_refresh_preview')
        return {
            'workflow':'existing_offer_update','dry_run':dry_run,'source_id':source_id,
            'fetched_product_pages':counts['fetched'],'offers_updated':counts['updated'],
            'unchanged_offers':counts['unchanged'],'unknown_variants_skipped':counts['not_previously_linked'],
            'identity_mismatches_skipped':counts['identity_mismatches'],
            'new_products_created':0,'canonical_ingredients_changed':0,
            'note':'Existing offers only. Unknown products and variants were skipped, not imported.'
        }
    except Exception:
        if dry_run:
            conn.execute('ROLLBACK TO SAVEPOINT offer_refresh_preview')
            conn.execute('RELEASE SAVEPOINT offer_refresh_preview')
        raise


def run(action,payload):
    conn=get_connection()
    try:
        result=action(conn,**payload.dict())
        conn.commit()
        return result
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400,detail=str(exc))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post('/products/discover')
def discover(request:ShopifyScan):
    return run(discover_shopify,request)


@router.post('/offers/refresh')
def refresh_existing_offers(request:OfferRefresh):
    return run(update_existing_shopify_offers,request)

class LegacyIdentityCheck(BaseModel):
    dry_run: bool=True
    max_verified: int=Field(default=500,ge=1,le=5000)


@router.post('/products/verify-existing')
def verify_existing_products(request:LegacyIdentityCheck):
    return run(reconcile_legacy_exact,request)


class MultiSourceDiscovery(BaseModel):
    source_ids: list[str] = Field(min_length=1,max_length=40)
    start_page: int = Field(default=1,ge=1,le=5000)
    pages: int = Field(default=1,ge=1,le=3)
    dry_run: bool = True
    urls_by_source: dict[str,list[str]] = Field(default_factory=dict)


def _multisource_worker(task_id,source_ids,start_page,pages,dry_run,urls_by_source):
    """Run each source independently with a durable task and per-source outcome."""
    from pipeline.task_tracker import update_task_progress,complete_task,fail_task,log_task_event
    from pipeline.source_collection import collect_source
    from pipeline.source_registry import get_source
    reports=[]
    counters=Counter()
    try:
        for index,sid in enumerate(source_ids,1):
            conn=get_connection()
            try:
                source=get_source(conn,sid)
                if not source['enabled']:
                    report={'source_id':sid,'name':source['name'],'status':'skipped','reason':'Collection paused'}
                elif source['adapter_type']=='shopify':
                    res=discover_shopify(conn,sid,start_page,pages,dry_run)
                    report={'source_id':sid,'name':source['name'],'status':'completed',**res}
                    if not dry_run:conn.commit()
                    else:conn.rollback()
                else:
                    urls=urls_by_source.get(sid) or [row[0] for row in conn.execute(
                        """SELECT source_url FROM source_product_observations WHERE source_id=?
                        GROUP BY source_url ORDER BY MAX(id) DESC LIMIT 25""",(sid,))]
                    if not urls:
                        report={'source_id':sid,'name':source['name'],'status':'needs_urls',
                                'reason':'No catalog feed or product URLs yet. Add URLs for this source to import it.'}
                    else:
                        res=collect_source(conn,sid,urls,max_products=len(urls),dry_run=dry_run)
                        outputs=[x for product in res['results'] for x in product.get('results',[])]
                        report={'source_id':sid,'name':source['name'],'status':'completed',
                            'workflow':'product_discovery','fetched':res['observations'],
                            'existing_products_matched':sum(x.get('status')=='matched' for x in outputs),
                            'new_products_staged':sum(x.get('status')=='provisional' for x in outputs),
                            'needs_review':sum(x.get('status')=='review' for x in outputs),
                            'urls_checked':len(urls),
                            'coverage':'Provided or previously seen URLs only; not a whole-site catalog crawl.'}
                        if not dry_run:conn.commit()
                        else:conn.rollback()
                reports.append(report)
                counters[report['status']]+=1
            except Exception as exc:
                conn.rollback()
                report={'source_id':sid,'status':'failed','reason':str(exc)[:500]}
                reports.append(report)
                counters['failed']+=1
            finally:
                conn.close()
            update_task_progress(task_id,processed_items=index,
                successful_items=counters['completed'],failed_items=counters['failed'],
                skipped_items=counters['skipped']+counters['needs_urls'],
                current_stage='finished_'+sid,progress_percent=100*index/len(source_ids),
                metadata={'workflow':'product_discovery','dry_run':dry_run,'sources':reports})
        complete_task(task_id,metadata={'workflow':'product_discovery','dry_run':dry_run,
                      'sources':reports,'summary':dict(counters)},
                      status='completed_with_errors' if counters['failed'] else 'completed')
    except Exception as exc:
        fail_task(task_id,str(exc),metadata={'sources':reports})


@router.post('/products/discover-multiple')
def discover_multiple(request:MultiSourceDiscovery,background_tasks:BackgroundTasks):
    from pipeline.task_tracker import create_task
    ids=list(dict.fromkeys(request.source_ids))
    if len(ids)!=len(request.source_ids):
        raise HTTPException(status_code=400,detail='Choose each source only once.')
    conn=get_connection()
    try:
        sources={row['id']:dict(row) for row in conn.execute('SELECT id,name,enabled FROM sources WHERE id IN ('+
            ','.join('?' for _ in ids)+')',ids)}
        missing=[sid for sid in ids if sid not in sources]
        if missing:raise HTTPException(status_code=400,detail='Unknown sources: '+', '.join(missing))
        stopped=[sid for sid in ids if not sources[sid]['enabled']]
        if stopped:raise HTTPException(status_code=400,detail='These sources have collection disabled: '+', '.join(stopped))
        for sid,urls in request.urls_by_source.items():
            if sid not in ids:raise HTTPException(status_code=400,detail='URL source was not selected: '+sid)
            if len(urls)>25:raise HTTPException(status_code=400,detail='Maximum 25 URLs per source.')
    finally:
        conn.close()
    task_id=create_task('catalog_discovery',target_source='multiple',
                        params={'source_ids':ids,'pages':request.pages,'dry_run':request.dry_run},
                        total_items=len(ids),auto_start=True)
    background_tasks.add_task(_multisource_worker,task_id,ids,request.start_page,
                              request.pages,request.dry_run,request.urls_by_source)
    return {'status':'accepted','task_id':task_id,'sources_selected':len(ids),
            'dry_run':request.dry_run,
            'message':'Product discovery started. Individual source results are available in the task status.'}


@router.get('/products/discover-multiple/{task_id}')
def discovery_progress(task_id:str):
    conn=get_connection()
    try:
        row=conn.execute('SELECT task_id,status,processed_items,total_items,metadata,error_message FROM pipeline_tasks WHERE task_id=? AND task_type=?',
                         (task_id,'catalog_discovery')).fetchone()
        if not row:raise HTTPException(status_code=404,detail='Discovery job not found.')
        import json
        out=dict(row)
        out['metadata']=json.loads(out['metadata'] or '{}')
        return out
    finally:conn.close()


@router.post('/products/prepare-reviews')
def prepare_legacy_review_cases():
    """Create real review cases without auto-verifying products."""
    conn=get_connection()
    try:
        result=reconcile_legacy_exact(conn,dry_run=False,max_verified=0,
            queue_failures=True,max_review_cases=250)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
