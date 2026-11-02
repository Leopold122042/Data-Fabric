from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

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
    router.add_api_route('/', runtime.index, methods=['GET'])
    return router
