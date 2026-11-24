from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

STATIC = Path(__file__).resolve().parents[1] / 'static'
WORKSPACE_ENTRIES = {
    '/user': 'portal/index.html',
    '/user/services': 'portal/services.html',
    '/admin': 'management/index.html',
    '/admin/accounts': 'management/accounts.html',
    '/showcase': 'report/index.html',
}
WORKSPACE_LOGINS = frozenset(('/user/login', '/admin/login', '/showcase/login', '/static/portal/login.html'))


def workspace_login(path):
    path = path.rstrip('/')
    if (path in ('/static/index.html', '/static/workspace.html', '/static/portal/accounts.html')
            or path == '/admin' or path.startswith(('/admin/', '/static/management/', '/static/platform/', '/static/execution/'))):
        return '/admin/login'
    if path == '/showcase' or path.startswith(('/showcase/', '/static/report/')):
        return '/showcase/login'
    return '/user/login'


def is_workspace_page(path):
    path = path.rstrip('/')
    return path in WORKSPACE_ENTRIES or path.endswith('.html')


def _page(filename):
    def serve():
        return FileResponse(STATIC / filename, headers={'Cache-Control': 'no-cache'})
    return serve


def create_router(runtime):
    router = APIRouter()
    @router.get('/api/dashboard')
    def dashboard(request: Request):
        from platform_core.dashboard import dashboard as project
        return JSONResponse(project(runtime, request.app.state.identity.principal(request)), headers={'Cache-Control': 'no-store'})

    @router.get('/api/medical-overview')
    def medical_overview(request: Request):
        from platform_core.storage import DomainError
        if request.app.state.identity.principal(request)['role'] != 'platform':
            raise DomainError('医疗数据统计目前仅对平台方开放', 403)
        data = runtime.cockpit_api()
        return JSONResponse({key: data[key] for key in ('dept_distribution', 'top_diags', 'sources')}, headers={'Cache-Control': 'no-store'})

    @router.get('/api/access')
    def access(request: Request):
        from platform_core.access import access_snapshot
        principal=request.app.state.identity.principal(request)
        return JSONResponse(access_snapshot(principal),headers={'Cache-Control':'no-store'})
    router.add_api_route('/api/health', runtime.health, methods=['GET'])
    router.add_api_route('/api/overview', runtime.overview, methods=['GET'])
    router.add_api_route('/api/events', runtime.events, methods=['GET'])
    router.add_api_route('/api/stream', runtime.stream, methods=['GET'])
    router.add_api_route('/api/cockpit', runtime.cockpit_api, methods=['GET'])
    for path, filename in WORKSPACE_ENTRIES.items():
        router.add_api_route(path, _page(filename), methods=['GET', 'HEAD'], include_in_schema=False)
    for path in WORKSPACE_LOGINS:
        router.add_api_route(path, _page('portal/login.html'), methods=['GET', 'HEAD'], include_in_schema=False)

    @router.api_route('/static/portal/services.html', methods=['GET', 'HEAD'], include_in_schema=False)
    def legacy_user_services(request: Request):
        query = '?' + request.url.query if request.url.query else ''
        return RedirectResponse('/user/services' + query, status_code=303, headers={'Cache-Control': 'no-cache'})

    @router.api_route('/static/portal/cockpit.html', methods=['GET', 'HEAD'], include_in_schema=False)
    def legacy_user_cockpit():
        return RedirectResponse('/user', status_code=303, headers={'Cache-Control': 'no-cache'})

    @router.get('/static/portal/accounts.html', include_in_schema=False)
    def legacy_accounts():
        return RedirectResponse('/admin/accounts', status_code=303)

    @router.get('/')
    def root():
        return RedirectResponse('/user', status_code=303)
    return router
