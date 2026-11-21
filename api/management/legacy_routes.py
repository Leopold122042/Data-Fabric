from fastapi import APIRouter, Request
from platform_core.access import level_for
from platform_core.storage import DomainError

def create_router(runtime, identity):
    router = APIRouter()
    def administrator(request):
        user=identity.principal(request)
        if level_for(user)<4:raise DomainError('该接口仅对4级管理员开放',403)
        return user
    @router.post('/api/discovery/run')
    async def discovery_run(request:Request):
        administrator(request)
        return await runtime.discovery_run(request)
    @router.get('/api/catalog')
    def catalog(request:Request):
        from platform_core.access import can_access_table
        user=identity.principal(request);payload=runtime.catalog()
        if level_for(user)>=4:return payload
        field_map=[]
        for entity in payload.get('field_map',[]):
            mappings=[item for item in entity.get('mappings',[])
                      if can_access_table(user,item.get('table',''))]
            if mappings:field_map.append({**entity,'mappings':mappings})
        return {**payload,'field_map':field_map}
    @router.get('/api/graph')
    def graph(request:Request,patients:int=100):
        administrator(request)
        return runtime.graph(patients)
    @router.get('/api/services')
    def services(request:Request):
        administrator(request)
        return runtime.services_list()
    @router.get('/api/data-services')
    def data_services(request:Request):
        administrator(request)
        return runtime.data_services()
    @router.post('/api/data-services/register')
    async def data_service_register(request:Request):
        administrator(request)
        return await runtime.data_service_register(request)
    return router
