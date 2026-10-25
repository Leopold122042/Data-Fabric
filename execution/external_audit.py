"""Record external query outcomes without credentials, SQL text, paths or row values.
External source definitions do not yet supply an immutable loaded version; keep them
explicitly unpaired rather than constructing a fake design from a response.
"""
import hashlib,json,time
from platform_core.storage import now

QUERY_PATHS={'/api/external/query','/api/external/virtual/query','/api/external/discovery/query','/api/external/discovery/collection-query'}

async def record(request,response,started,status=None):
    runtime=getattr(request.app.state,'runtime',None)
    if runtime is None or request.url.path not in QUERY_PATHS:return
    try:body=await request.json()
    except ValueError:body={}
    if not isinstance(body,dict):body={}
    selector=body.get('view_id') or hashlib.sha256(str(body.get('sql','')).encode()).hexdigest()[:24]
    code=status or response.status_code
    session=request.cookies.get('medfabric_external','anonymous')
    actor=request.app.state.identity.principal(request)['id']
    grant_id=getattr(request.state,'external_grant_id',None)
    runtime.management.evidence.record({'object_kind':'view','object_id':'EXT-'+str(selector),'object_version':'unversioned-external',
        'environment':runtime.management.store.environment,'status':'success' if code<400 else 'failed','task_scope':'external-query:'+str(selector),
        'actor':actor,'grant_id':grant_id,'latency_ms':round((time.perf_counter()-started)*1000,3),'authorization':'allowed' if code<400 else 'denied',
        'delivered':code<400,'delivered_at':now() if code<400 else None,'quality_passed':None,'http_status':code,
        'pairing_note':'既有外部查询尚未提供不可变实际加载版本，保留未配对状态','endpoint':request.url.path})
