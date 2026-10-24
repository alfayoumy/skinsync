"""Bounded, rate-limited collection through registered adapters and robots policies."""
import json
import threading
import time
from urllib.robotparser import RobotFileParser
from pipeline.source_registry import get_source,url_matches_source
from pipeline.source_adapters import get_adapter, HTMLAdapter
from pipeline.aggregation_service import process_source_observation

_LOCK=threading.Lock();_LAST={}
USER_AGENT='SkinSync/1.0 (+product-ingredient-research)'
MAX_RESPONSE=4*1024*1024


def _read(url, source=None):
    if source is None:
        raise ValueError('Registered source policy is required.')
    from pipeline.source_http import read_source
    return read_source(source, url)


def collect_source(conn,source_id,urls,max_products=100,dry_run=False):
    from pipeline.catalog_reset import guard_catalog_writes
    guard_catalog_writes(conn)
    source=get_source(conn,source_id)
    if not source['enabled']:raise ValueError('Source is staged. Enable collection explicitly before making requests.')
    if not urls or len(urls)>max_products or not 1<=max_products<=1000:raise ValueError('Provide a bounded collection URL list.')
    for url in urls:
        if not url_matches_source(source,url):raise ValueError('Collection URL is outside the registered source.')
    robots=RobotFileParser()
    try:
        robots_text,_=_read('https://'+source['domain']+'/robots.txt',source)
        robots.parse(robots_text.splitlines())
    except Exception as exc:
        raise ValueError('Could not establish the source access policy; collection is deferred.') from exc
    results=[]
    adapter=get_adapter(source)
    for url in urls:
        guard_catalog_writes(conn)
        if not robots.can_fetch(USER_AGENT,url):
            results.append({'url':url,'status':'disallowed_by_robots'});continue
        delay=max(float(source['rate_limit_seconds']),robots.crawl_delay(USER_AGENT) or 0)
        if delay>60:raise ValueError('Source crawl delay exceeds this interactive collector; defer to a scheduled job.')
        with _LOCK:
            remaining=delay-(time.monotonic()-_LAST.get(source_id,0))
            if remaining>0:time.sleep(min(remaining,60))
            _LAST[source_id]=time.monotonic()
        content,final_url=_read(url,source)
        if not url_matches_source(source,final_url):
            # The historical database name redirects to its verified new hostname.
            alias=source_id in ('incidecoder','inkeedecoder') and final_url.startswith(('https://incidecoder.com/','https://inkeedecoder.com/'))
            if not alias:raise ValueError('Source redirected outside its registered domain.')
        is_json=content.lstrip().startswith(('{','[')) and source['adapter_type'] in ('shopify','woocommerce','openbeautyfacts')
        payload=json.loads(content) if is_json else content
        selected_adapter=adapter if is_json or source['adapter_type'] not in ('shopify','woocommerce') else HTMLAdapter(source)
        observations=selected_adapter.parse(payload,None if is_json else url)
        for observation in observations:
            if len(results)>=max_products:break
            results.append(process_source_observation(conn,observation,dry_run))
    return {'source_id':source_id,'observations':len(results),'results':results,'dry_run':dry_run}
