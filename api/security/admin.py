"""Fail-closed administrator authentication and independent permission boundaries."""
import hmac
import json
import os
from contextvars import ContextVar
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

ACTOR = ContextVar('admin_actor', default=None)
PUBLIC_POSTS = {'/onboarding', '/recommend', '/similarity/score', '/dermo/score', '/inci/match'}
PERMISSIONS = {'read', 'collection', 'identity', 'formula', 'formula_correction', 'operations'}


def credentials():
    try:
        records = json.loads(os.getenv('SKINSYNC_ADMIN_CREDENTIALS', '{}'))
        if not isinstance(records, dict):
            return {}
        return {name: record for name, record in records.items() if isinstance(record, dict)
                and isinstance(record.get('token'), str) and len(record['token']) >= 32
                and set(record.get('permissions', [])) <= PERMISSIONS}
    except (ValueError, TypeError):
        return {}


def permission_for(path, method):
    if path.startswith('/admin/workflows/products/recover-unmatched') and method not in ('GET','HEAD'):
        return 'identity'
    if path.startswith('/admin/inci-research/queue/') and path.endswith('/review') and method=='POST':
        return 'formula_correction'
    if path.startswith('/admin/inci-research/') and method not in ('GET','HEAD'):
        return 'formula'
    if path.startswith('/admin/workflows/catalog/stale-tasks') and method not in ('GET','HEAD'):
        return 'operations'
    if path.startswith(('/admin/workflows/catalog/reset', '/admin/workflows/catalog/selective-rebuild')) and method not in ('GET','HEAD'):
        return 'operations'
    if path.startswith(('/admin/workflows/products/verify-existing','/admin/workflows/products/prepare-reviews')) and method not in ('GET','HEAD'):
        return 'identity'
    if path.startswith('/admin/workflows/') and method not in ('GET','HEAD'):
        return 'collection'
    if path.startswith('/admin/waterfall/identity-certification') and method not in ('GET','HEAD'):
        return 'identity'
    if method in ('GET', 'HEAD'):
        return 'read'
    if ('/formulations/evidence/' in path and path.endswith('/approve')) or path.endswith('/apply-tier'):
        # Evidence approval permits explicit force corrections; require both scopes.
        return 'formula_correction'
    if any(part in path for part in ('waterfall', 'apply-tier', 'execute-all-tiers', 'fill-ingredients', 'batch-fill', 'match-inci', '/inci/batch', '/admin/verify', '/queues/', '/formulations/')):
        return 'formula'
    if '/aggregation/sources/' in path and path.endswith('/rollout'):
        return 'identity'
    if any(part in path for part in ('/identity', '/reviews/', '/brands/', 'trigger-linker', 'link-clusters', 'classification', 'trigger-classifier', 'enrich-titles', '/synonyms', '/evaluations', '/reconcile', '/reconciliation')):
        return 'identity'
    if any(part in path for part in ('/sources/', '/connection-tests', '/refresh', '/admin/sync')):
        return 'collection'
    return 'operations'


def reviewer_name(fallback):
    actor = ACTOR.get()
    return actor['name'] if actor else fallback  # CLI/internal reviews retain explicit attribution.


class AdminAuthentication:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] == 'OPTIONS':
            return await self.app(scope, receive, send)
        path = scope.get('path', '').rstrip('/') or '/'
        method = scope['method']
        protected = path.startswith(('/admin', '/tasks', '/logs')) or (method not in ('GET', 'HEAD') and path not in PUBLIC_POSTS)
        if not protected:
            return await self.app(scope, receive, send)
        records = credentials()
        if not records:
            return await JSONResponse({'detail': 'Administrator credentials are not configured.'}, 503)(scope, receive, send)
        authorization = Headers(scope=scope).get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        actor = None
        for name, record in records.items():
            if hmac.compare_digest(token.encode('utf-8'), record['token'].encode('utf-8')):
                actor = {'name': name, 'permissions': record['permissions']}
        if actor is None:
            return await JSONResponse({'detail': 'Administrator authentication required.'}, 401,
                                      headers={'WWW-Authenticate': 'Bearer'})(scope, receive, send)
        required = permission_for(path, method)
        if required not in actor['permissions'] or (required == 'formula_correction' and 'formula' not in actor['permissions']):
            return await JSONResponse({'detail': f'Administrator permission required: {required}.'}, 403)(scope, receive, send)
        scope.setdefault('state', {})['admin'] = actor
        context = ACTOR.set(actor)
        try:
            await self.app(scope, receive, send)
        finally:
            ACTOR.reset(context)
