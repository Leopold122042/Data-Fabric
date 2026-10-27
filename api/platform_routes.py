from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

def create_router(runtime):
    router = APIRouter()
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
