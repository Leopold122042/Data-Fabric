from fastapi import APIRouter

def create_router(runtime):
    router = APIRouter()
    router.add_api_route('/api/discovery/run', runtime.discovery_run, methods=['POST'])
    router.add_api_route('/api/catalog', runtime.catalog, methods=['GET'])
    router.add_api_route('/api/graph', runtime.graph, methods=['GET'])
    router.add_api_route('/api/services', runtime.services_list, methods=['GET'])
    router.add_api_route('/api/data-services', runtime.data_services, methods=['GET'])
    router.add_api_route('/api/data-services/register', runtime.data_service_register, methods=['POST'])
    return router
