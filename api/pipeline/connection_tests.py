"""Live, bounded diagnostics. Never ingests observations or changes publication gates."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from pipeline.config import cfg
from pipeline.source_registry import url_matches_source
from pipeline.source_adapters import get_adapter, HTMLAdapter
from pipeline.source_collection import USER_AGENT, MAX_RESPONSE, _LOCK, _LAST

POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='connection-test')
LOCK = threading.Lock()
RESULTS = {}
RUNNING = set()
SAMPLES = {
    'zahra': 'https://zahraegypt.com/en/shop/product/2954/810400030824',
    'cerave_eg': 'https://www.cerave.eg/en/skincare/cleansers/hydrating-facial-cleanser',
}


def request(url, payload=None, headers=None, source=None):
    req = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={'User-Agent': USER_AGENT, 'Content-Type': 'application/json', **(headers or {})})
    class SourceRedirects(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if source and (not url_matches_source(source, newurl) or urlsplit(newurl).port not in (None, 443)):
                raise ValueError('Redirect outside registered source')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    if source and urlsplit(url).port not in (None, 443):
        raise ValueError('Unsupported source port')
    opener = build_opener(SourceRedirects()).open if source else urlopen
    with opener(req, timeout=15) as response:
        if source and not url_matches_source(source, response.geturl()):
            raise ValueError('Unexpected redirect outside registered source')
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ValueError('Response exceeds diagnostic size limit')
        return body.decode('utf-8', 'replace')


def source_test(source):
    root = 'https://' + source['domain']
    result = {'online': 'unknown', 'functional': 'not_tested'}
    try:
        request(root, source=source)
        result['online'] = 'yes'
    except HTTPError as exc:
        result.update(online='yes', http_status=exc.code)
    except Exception:
        return {**result, 'online': 'unreachable', 'detail': 'Website connection failed or timed out.'}
    sample = source.get('test_url') or source.get('section_rules', {}).get('test_url') or SAMPLES.get(source['id'])
    if not sample and source['adapter_type'] == 'shopify':
        sample = root + '/products.json?limit=1'
    if not sample:
        return {**result, 'functional': 'needs_sample', 'detail': 'Provide a product URL to verify extraction.'}
    if not url_matches_source(source, sample):
        return {**result, 'functional': 'failed', 'detail': 'Sample URL must belong to this source.'}
    result['url'] = sample
    try:
        robots = RobotFileParser()
        robots.parse(request(root + '/robots.txt', source=source).splitlines())
        if not robots.can_fetch(USER_AGENT, sample):
            return {**result, 'functional': 'blocked', 'detail': 'Collection disallowed by robots.txt.'}
        delay = max(float(source.get('rate_limit_seconds') or 0), robots.crawl_delay(USER_AGENT) or 0)
        if delay > 60:
            return {**result, 'functional': 'deferred', 'detail': 'Crawl delay requires scheduled collection.'}
        with _LOCK:
            remaining = delay - (time.monotonic() - _LAST.get(source['id'], 0))
            if remaining > 0:
                time.sleep(remaining)
            _LAST[source['id']] = time.monotonic()
        body = request(sample, source=source)
        is_json = body.lstrip().startswith(('{', '['))
        adapter = get_adapter(source) if is_json else HTMLAdapter(source)
        observations = adapter.parse(json.loads(body) if is_json else body, None if is_json else sample)
        valid = [o for o in observations if o.raw_title and o.source_product_id]
        priced = sum(any(v.source_variant_id and v.price > 0 for v in o.variants) for o in valid)
        inci = sum(bool(o.ingredient_text and o.ingredient_order_verified) for o in valid)
        working = bool(valid) and (not source['provides_prices'] or priced > 0) and (not source['provides_inci'] or inci > 0)
        return {**result, 'functional': 'passed' if working else 'incomplete', 'products': len(valid),
                'priced_products': priced, 'ordered_inci_products': inci,
                'detail': 'Sample extraction passed.' if working else 'Expected product, price or ordered ingredient evidence missing in sample.'}
    except HTTPError as exc:
        return {**result, 'functional': 'blocked' if exc.code in (401, 403, 429) else 'failed', 'http_status': exc.code,
                'detail': f'Collection request returned HTTP {exc.code}.'}
    except Exception:
        return {**result, 'functional': 'failed', 'detail': 'Access policy, response format or product extraction failed.'}


def api_test(provider):
    if provider == 'gemini':
        key = cfg.GEMINI_API_KEY
        model = cfg.GEMINI_MODEL_NAME.removeprefix('models/')
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        headers = {'x-goog-api-key': key}
        payload = {'contents': [{'parts': [{'text': 'Reply with exactly OK'}]}], 'generationConfig': {'maxOutputTokens': 32}}
        validate = lambda d: any('OK' in p.get('text', '') for c in d.get('candidates', []) for p in c.get('content', {}).get('parts', []))
    elif provider == 'jev':
        key = os.getenv('OPENCODE_API_KEY') or os.getenv('JEV_API_KEY') or os.getenv('TYPESAFE_API_KEY')
        opencode = bool(os.getenv('OPENCODE_API_KEY'))
        url = 'https://opencode.ai/zen/v1/systemone' if opencode else 'https://api.typesafe.ai/v1/systemone'
        headers = {'Authorization': f'Bearer {key}'}
        payload = {'model': cfg.JEV_MODEL_NAME if opencode else 'jev-latest', 'state': 'Water is water.',
                   'questions': {'same': {'type': 'noul', 'instructions': 'Is water water?'}}}
        def validate(d):
            value = d.get('answers', {}).get('same', {}).get('noul')
            return value is not None and 0 <= float(value) <= 1
    else:
        key = cfg.LANGSEARCH_API_KEY
        url = 'https://api.langsearch.com/v1/web-search'
        headers = {'Authorization': f'Bearer {key}'}
        payload = {'query': 'CeraVe ingredients', 'count': 1, 'freshness': 'noLimit', 'contents': {'text': True}}
        validate = lambda d: any(p.get('url') for p in d.get('data', {}).get('webPages', {}).get('value', []))
    if not key:
        return {'online': 'not_tested', 'functional': 'not_configured', 'detail': 'API credential missing.'}
    if provider == 'langsearch':
        from pipeline.rate_limiter import langsearch_limiter
        if not langsearch_limiter.acquire(estimated_tokens=1500):
            return {'online': 'not_tested', 'functional': 'quota_exhausted', 'detail': 'Local API quota prevents a test request.'}
    try:
        body = request(url, payload, headers)
    except HTTPError as exc:
        return {'online': 'yes', 'functional': {401: 'authentication_failed', 403: 'forbidden', 429: 'rate_limited'}.get(exc.code, 'failed'),
                'http_status': exc.code, 'detail': f'API returned HTTP {exc.code}.'}
    except Exception:
        return {'online': 'unreachable', 'functional': 'failed', 'detail': 'API connection failed or timed out.'}
    try:
        data = json.loads(body)
        valid = validate(data)
        if provider == 'langsearch':
            usage = data.get('usage') or data.get('data', {}).get('usage') or {}
            langsearch_limiter.record_usage(input_tokens=usage.get('input_tokens') or 10,
                                           output_tokens=usage.get('output_tokens') or max(1, len(body)//4))
    except Exception:
        valid = False
    return {'online': 'yes', 'functional': 'passed' if valid else 'failed',
            'detail': 'Live API response validated.' if valid else 'API response did not match the expected schema/content.'}


def start(kind, items):
    for ident, config in items:
        key = f'{kind}:{ident}'
        with LOCK:
            if key in RUNNING:
                continue
            RUNNING.add(key)
        def run(key=key, ident=ident, config=config):
            started = time.monotonic()
            try:
                result = source_test(config) if kind == 'source' else api_test(ident)
            except Exception:
                result = {'online': 'unknown', 'functional': 'failed', 'detail': 'Diagnostic failed; retry the test.'}
            result.update(checked_at=datetime.now(timezone.utc).isoformat(), duration_seconds=round(time.monotonic()-started, 2))
            with LOCK:
                RESULTS[key] = result
                RUNNING.discard(key)
        POOL.submit(run)
    return snapshot()


def snapshot():
    with LOCK:
        return {'results': dict(RESULTS), 'running': sorted(RUNNING)}
