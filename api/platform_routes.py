from fastapi import APIRouter

def create_router(runtime):
    router = APIRouter()
    router.add_api_route('/api/health', runtime.health, methods=['GET'])
    router.add_api_route('/api/overview', runtime.overview, methods=['GET'])
    router.add_api_route('/api/events', runtime.events, methods=['GET'])
    router.add_api_route('/api/stream', runtime.stream, methods=['GET'])
    router.add_api_route('/api/cockpit', runtime.cockpit_api, methods=['GET'])
    router.add_api_route('/', runtime.index, methods=['GET'])
    return router
