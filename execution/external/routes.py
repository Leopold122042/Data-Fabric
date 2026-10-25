from fastapi import APIRouter,Request,Response,HTTPException
from external_api import session
from platform_core.storage import DomainError


def create_router(runtime,identity):
    router=APIRouter(prefix='/api/external/governance',tags=['外部数据治理'])
    g=runtime.external_governance
    def who(request,admin=False):
        actor=identity.principal(request)
        if admin and actor['role']!='platform':raise DomainError('仅平台方可配置检测和签发授权',403)
        return actor
    async def body(request):
        try:data=await request.json()
        except ValueError:raise DomainError('请求必须为JSON对象')
        if not isinstance(data,dict):raise DomainError('请求必须为JSON对象')
        return data
    @router.get('/status')
    def status(request:Request,response:Response):
        actor=who(request);current=session(request,response)
        with current.virtual_lock:
            if not current.cloud_credentials:return {'connected':False,'tables':[],'catalog_owner':None,'monitor':{'enabled':False},'grants':[],'metadata':[],'changes':[]}
            owner=g.owner(current)
            grants=[x for x in g.store.list('external_grant') if x['owner']==owner and (actor['role']=='platform' or x['subject']==actor['id'])]
            return {'connected':True,'catalog_owner':owner,'tables':[{'id':r['id'],'name':r.get('name',r['id'])} for r in g.records(current).values()],'monitor':g.watch_status(current),'grants':grants,'metadata':[x for x in g.store.list('external_metadata') if x['owner']==owner], 'changes':sorted([x for x in g.store.list('external_change') if x['owner']==owner],key=lambda x:x['updated_at'],reverse=True)[:100]}
    @router.post('/grants')
    async def grant(request:Request,response:Response):
        actor=who(request,True);current=session(request,response)
        return g.grant(current,await body(request),actor['id'])
    @router.post('/grants/{gid}/revoke')
    def revoke(gid:str,request:Request,response:Response):
        actor=who(request,True);current=session(request,response)
        if g.store.require('external_grant',gid)['owner']!=g.owner(current):raise DomainError('授权不属于当前云盘',403)
        return g.revoke(gid,actor['id'])
    @router.post('/monitor')
    async def monitor(request:Request,response:Response):
        actor=who(request,True);current=session(request,response);data=await body(request)
        if data.get('enabled') is False:return g.unwatch(current)
        return g.watch(current,data.get('table_ids'),data.get('interval',300),actor['id'])
    @router.post('/check')
    async def check(request:Request,response:Response):
        from starlette.concurrency import run_in_threadpool
        actor=who(request,True);current=session(request,response);data=await body(request)
        return await run_in_threadpool(g.scan,current,data.get('table_ids'),actor['id'])
    return router


async def authorize(request,stage='before'):
    """All integrated-app query routes pass this boundary, including alternate URLs."""
    runtime=getattr(request.app.state,'runtime',None)
    if runtime is None or not hasattr(runtime,'external_governance'):return
    path=request.url.path
    query_paths={'/api/external/query','/api/external/virtual/query','/api/external/discovery/query','/api/external/discovery/collection-query'}
    actor=request.app.state.identity.principal(request)
    admin_paths={'/api/external/github/load','/api/external/box/load','/api/external/discovery/start','/api/external/discovery/step','/api/external/discovery/collection-membership'}
    if path in admin_paths and actor['role']!='platform':raise HTTPException(403,'仅平台方可维护外部数据源')
    if path not in query_paths:return
    # The legacy arbitrary session query has no stable resource binding. Fail closed.
    if path in ('/api/external/query','/api/external/virtual/query'):
        raise HTTPException(403,'此查询入口缺少资源级授权绑定，请将来源扫描为虚拟表，再选择授权查询。')
    try:
        data=await request.json()
        if not isinstance(data,dict):return
        if not data.get('view_id'):return  # Pydantic will reject malformed input.
        if data.get('confirm') or data.get('preview'):
            if actor['role']!='platform':raise DomainError('仅平台方可维护字段映射',403)
        current=session(request,Response())
        ids=data.get('source_ids') if path.endswith('collection-query') else [data['view_id']]
        if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids):raise DomainError('来源列表无效')
        grant=runtime.external_governance.check(current,request.headers.get('x-external-grant'),ids,actor['id'],__import__('urllib.parse',fromlist=['unquote']).unquote(request.headers.get('x-external-purpose','')),stage)
        request.state.external_grant_id=grant['id']
    except DomainError as exc:raise HTTPException(exc.status,str(exc)) from None
