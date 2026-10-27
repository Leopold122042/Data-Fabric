from fastapi import APIRouter,Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse,Response
from platform_core.storage import DomainError,new_id,now
from execution.security.control import timestamp
from platform_core.access import require_resource,require_action,can_read_execution


class ExecutionRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:return await original(request)
            except DomainError:raise
            except (ValueError,TypeError,KeyError):return JSONResponse({'error':'输入结构或字段类型不符合要求'},status_code=422)
        return handler

def create_router(runtime,identity):
    router=APIRouter(prefix='/api/execution',tags=['B 执行与交付'],route_class=ExecutionRoute);e=runtime.execution
    def who(request,roles=None,action=None):
        user=identity.principal(request)
        if action:require_action(user,action)
        if roles and user['role'] not in roles:raise DomainError('当前角色无权执行此操作',403)
        return user
    async def body(request):
        try:value=await request.json()
        except ValueError:raise DomainError('请求必须为JSON对象')
        if not isinstance(value,dict):raise DomainError('请求必须为JSON对象')
        return value
    def owned(kind,oid,user):
        value=e.store.require(kind,oid)
        if not can_read_execution(runtime,kind,value,user):raise DomainError('无权访问该记录',403)
        return value
    @router.get('/status')
    def status(request:Request):
        who(request)
        return {'mode':'local-demo','workers':2,'max_queued_per_subject':20,'external_authorization':False,'trusted_timestamp':False,
                'capabilities':['固定版本方案','本地只读联邦查询','DAG依赖','节点重试','撤销阻断','六项交付复核','本地凭证'],
                'pending':['跨机构真实身份与授权','第三方存证','外部设施实际调用']}
    @router.get('/items/{kind}')
    def items(kind:str,request:Request):
        user=who(request)
        if kind not in ('plan','workflow','view','job','grant','decision','run','delivery','trigger','facility','contract'):raise DomainError('未知资源',404)
        require_resource(user,kind)
        rows=e.store.list(kind);visible=[]
        for row in rows:
            try:owned(kind,row['id'],user)
            except DomainError:continue
            clean={k:v for k,v in row.items() if k!='result'}
            if kind=='facility':
                from datetime import datetime,timezone
                clean['heartbeat_status']='online' if (datetime.now(timezone.utc)-timestamp(row['last_seen'])).total_seconds()<=60 else 'stale'
            if kind=='plan':clean['state']=e.store.require('plan_state',row['id'])
            if kind=='delivery':
                plan=e.store.get('plan',row['plan_id'],row['plan_version'])
                clean['circulation_mode']=plan.get('circulation_mode') if plan else None
            visible.append(clean)
        return {'items':visible}
    @router.post('/views')
    async def view(request:Request):
        user=who(request,action='view.create');return e.queries.define(await body(request),user['id'])
    @router.post('/plans')
    async def plan(request:Request):
        user=who(request,action='plan.create');return e.plan(await body(request),user['id'])
    @router.post('/plans/{oid}/detail-export')
    def detail_export(oid:str,request:Request):
        user=who(request,action='plan.detail_export');plan=owned('plan',oid,user)
        if plan['scenario']!='medical':raise DomainError('该入口只用于医疗虚构示例')
        e.store.save('decision',new_id('DEC'),{'plan_id':oid,'plan_version':plan['version'],'actor':user['id'],'purpose':plan['purpose'],
            'stage':'detail_export','allowed':False,'reason':'仅允许汇总查询','delivered':False,'backend_calls':0,'scope':'local-demo'},0,user['id'],'明细导出前置拒绝')
        raise DomainError('仅允许汇总查询',403)
    @router.post('/plans/{oid}/confirm')
    async def confirm(oid:str,request:Request):
        user=who(request,action='plan.confirm');data=await body(request);return e.confirm(oid,data.get('version'),user['id'])
    @router.post('/grants')
    async def grant(request:Request):
        user=who(request,action='grant.create');data=await body(request);plan=e.get(data.get('plan_id',''),data.get('plan_version'));e.current(plan)
        return e.security.grant(data,user['id'])
    @router.post('/grants/{oid}/revoke')
    def revoke(oid:str,request:Request):
        user=who(request,action='grant.revoke')
        with e.lock:return e.security.revoke(oid,user['id'])
    @router.post('/plans/{oid}/run')
    async def run(oid:str,request:Request):
        user=who(request,action='plan.run');owned('plan',oid,user);data=await body(request)
        return e.submit(oid,data.get('version'),data.get('grant_id',''),user['id'],data.get('retry_of'),data.get('priority',0))
    @router.post('/jobs/{oid}/cancel')
    def cancel(oid:str,request:Request):return e.cancel(oid,who(request,action='job.cancel')['id'])
    @router.get('/jobs/{oid}')
    def job(oid:str,request:Request):return owned('job',oid,who(request,action='job.read'))
    @router.get('/deliveries/{oid}')
    def delivery(oid:str,request:Request):
        user=who(request,action='delivery.read');d=owned('delivery',oid,user);p=e.get(d['plan_id'],d['plan_version']);e.current(p)
        with e.lock,e.management.store.lock:
            e.current(p)
            value=e.delivery.read(oid,user['id'],p)
            if p['circulation_mode']=='api':
                return {**{k:v for k,v in value.items() if k!='result'},'reference':'/api/execution/deliveries/'+oid+'/data'}
            return value
    @router.get('/deliveries/{oid}/data')
    def referenced_data(oid:str,request:Request):
        user=who(request,action='delivery.reference');d=owned('delivery',oid,user);p=e.get(d['plan_id'],d['plan_version'])
        if p['circulation_mode']!='api':raise DomainError('该方案不是API引用交付',409)
        with e.lock,e.management.store.lock:
            e.current(p);return e.delivery.read(oid,user['id'],p)['result']
    @router.get('/deliveries/{oid}/download')
    def download(oid:str,request:Request):
        import json
        user=who(request,action='delivery.download');d=owned('delivery',oid,user);p=e.get(d['plan_id'],d['plan_version'])
        if p['circulation_mode']!='download':raise DomainError('方案未确认下载交付模式',403)
        with e.lock,e.management.store.lock:
            e.current(p);result=e.delivery.read(oid,user['id'],p)
            return Response(json.dumps(result,ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="delivery.json"','Cache-Control':'no-store'})
    @router.post('/deliveries/{oid}/destroy')
    def destroy(oid:str,request:Request):
        user=who(request,action='delivery.destroy');result=e.delivery.destroy(oid,user['id']);return {k:v for k,v in result.items() if k!='result'}
    @router.post('/receipts/verify')
    async def verify(request:Request):
        who(request,action='delivery.verify');data=await body(request);return e.delivery.verify(data.get('receipt',{}),data.get('signature',''))
    @router.post('/control')
    async def control(request:Request):who(request,action='plan.pause');return e.control(await body(request))
    @router.post('/triggers')
    async def trigger(request:Request):
        user=who(request,action='trigger.create');data=await body(request);return e.trigger(data,user['id'])
    @router.post('/triggers/{oid}/disable')
    def disable(oid:str,request:Request):
        user=who(request,action='trigger.disable');item=owned('trigger',oid,user);return e.update('trigger',item,{'enabled':False},user['id'])
    @router.post('/facilities')
    async def facility(request:Request):
        user=who(request,action='facility.create');data=await body(request)
        if not data.get('name'):raise DomainError('设施名称必填')
        return e.store.save('facility',new_id('FAC'),{'name':data['name'],'created_by':user['id'],'capabilities':data.get('capabilities',[]),'last_seen':now(),'scope':'local-registration-only','remote_invocation':False},0,user['id'],'登记本地设施描述')
    @router.post('/facilities/{oid}/heartbeat')
    def heartbeat(oid:str,request:Request):
        user=who(request,action='facility.heartbeat');item=owned('facility',oid,user);return e.update('facility',item,{'last_seen':now()},user['id'])
    @router.post('/contracts')
    async def contract(request:Request):
        user=who(request,action='contract.create');data=await body(request);plan=owned('plan',data.get('plan_id',''),user)
        timestamp(data.get('expires_at'));parties=data.get('parties',[])
        if not isinstance(parties,list) or len(parties)<2 or any(p not in ('demo-platform','demo-provider','demo-requester') for p in parties):raise DomainError('选择至少两个本地演示参与方')
        return e.store.save('contract',new_id('CONTRACT'),{'created_by':user['id'],'plan_id':plan['id'],'plan_version':plan['version'],'purpose':plan['purpose'],'parties':list(set(parties)),
            'accepted':[],'expires_at':data['expires_at'],'scope':'local-demo','obligations':data.get('obligations','仅限约定用途，期限内使用，禁止再分发')},0,user['id'],'登记合作约定，不代替执行授权')
    @router.post('/contracts/{oid}/accept')
    def accept(oid:str,request:Request):
        user=who(request,action='contract.accept');item=e.store.require('contract',oid)
        if user['id'] not in item['parties']:raise DomainError('不是约定参与方',403)
        return e.update('contract',item,{'accepted':list(set(item['accepted']+[user['id']]))},user['id'])
    return router
