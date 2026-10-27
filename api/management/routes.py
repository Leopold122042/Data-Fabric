from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from management.store import DomainError,new_id
from platform_core.access import require_resource,require_action,can_read_management,can_read_execution,management_record

READ_KINDS={'object','concept','mapping','service','validation','demand','spec','feedback','improvement','metric_definition','case','pair','identifier','notice','relation','scan','mapping_rule','source','external_design'}


def create_router(runtime,identity):
    async def json_object(request:Request):
        if request.method=='POST' and await request.body():
            try:
                body=await request.json()
            except ValueError:
                raise DomainError('请求必须为有效JSON对象')
            if not isinstance(body,dict):raise DomainError('请求必须为JSON对象')
    router=APIRouter(prefix='/api/management',tags=['A 管理域'],dependencies=[Depends(json_object)])
    m=runtime.management
    def who(request,roles=None,action=None):
        user=identity.principal(request)
        if action:require_action(user,action)
        if roles and user['role'] not in roles:raise DomainError('当前角色不能执行该操作',403)
        return user
    def can_read(kind,item,user):
        return can_read_management(runtime,kind,item,user)
    def own(kind,oid,user):
        obj=m.store.require(kind,oid)
        if not can_read(kind,obj,user):raise DomainError('无权访问该对象',403)
        return obj

    @router.post('/semantic/business-concepts')
    def business_concepts(request:Request):
        user=who(request,action='concept.business')
        definitions=[('customer','客户','与组织建立业务关系的自然人或法人；具体识别口径由所属业务域定义','客户管理','客户编号','在客户业务域内标识客户的编号'),
                     ('organization','机构','承担业务职能的组织实体；不将同名组织自动视为同一机构','组织管理','机构编号','在组织业务域内标识机构的编号'),
                     ('metric','指标','按明确统计口径、单位、时间和对象范围定义的业务度量','指标管理','指标编码','标识一项具体指标定义的编码，不等同于指标值')]
        created=[]
        with m.store.atomic():
            for key,name,meaning,domain,attribute,description in definitions:
                cid='CON-business-'+key
                if not m.store.get('concept',cid):
                    created.append(m.semantic.concept({'id':cid,'name':name,'meaning':meaning,'domain':domain,'data_type':'text','aggregate_only':True},user['id']))
                aid=cid+'-id'
                if not m.store.get('concept',aid):
                    created.append(m.semantic.concept({'id':aid,'name':attribute,'meaning':description,'domain':domain,'data_type':'text','parent_id':cid,'context_keywords':[name]},user['id']))
        return {'created':created,'note':'业务对象使用唯一编号；具体属性单独映射。指标值需按具体指标的单位、口径和范围建立属性概念。'}

    @router.get('/status')
    def status(request:Request):
        user=who(request)
        return {'principal':user,'environment':m.store.environment,'llm':{'available':False,'reason':'未配置LLM接口，使用人工受理'},
                'plan_snapshots':m.services.plan_reader is not None,'external_identity':False,'execution_control':m.reconciliation.controller is not None,
                'counts':{k:sum(can_read(k,x,user) for x in m.store.list(k)) for k in ('object','concept','mapping','service','demand','case')},
                'capabilities':{'sources':['SQLite','CSV','JSON','EMR JSON','WebDAV','GitHub'],'external_sources':'已接入：云盘目录、虚拟表；选中结构可纳入平台目录','standards':'本地schema，未验证国家/行业标准'}}

    @router.get('/items/{kind}')
    def items(kind:str,request:Request,q:str='',at:str|None=None):
        user=who(request)
        if kind not in READ_KINDS:raise DomainError('未知资源类型',404)
        require_resource(user,kind)
        rows=m.store.list(kind,at)
        return {'items':[management_record(runtime,kind,r,user) for r in rows if can_read(kind,r,user) and (not q or q.lower() in str(r).lower())]}

    @router.get('/items/{kind}/{oid}/history')
    def history(kind:str,oid:str,request:Request):
        user=who(request)
        if kind not in READ_KINDS:raise DomainError('未知资源类型',404)
        require_resource(user,kind);own(kind,oid,user)
        return {'items':[management_record(runtime,kind,r,user) for r in m.store.history(kind,oid) if can_read(kind,r,user)]}

    @router.get('/catalog/export')
    def export(request:Request):
        who(request,action='object.export')
        return JSONResponse({'schema_version':'local-demo-v1','standard_verified':False,'objects':m.catalog.objects(),
            'identifiers':m.store.list('identifier')},headers={'Content-Disposition':'attachment; filename="catalog-demo.json"'})

    @router.post('/catalog/scan')
    def scan(request:Request):
        who(request,action='object.scan')
        if runtime.engine is None:raise DomainError('元数据引擎未启动',503)
        return {'changes':m.catalog.scan_engine(runtime.engine)}

    @router.post('/catalog/{oid}/annotate')
    async def annotate(oid:str,request:Request):
        user=who(request,action='object.annotate');return m.catalog.annotate(oid,await request.json(),user['id'])

    @router.post('/catalog/{oid}/rename')
    async def rename(oid:str,request:Request):
        user=who(request,action='object.rename');b=await request.json()
        return m.catalog.confirm_rename(oid,b.get('name'),b.get('expected_version'),user['id'])

    @router.get('/catalog/{oid}/impact')
    def impact(oid:str,request:Request):
        who(request,action='object.impact');return m.catalog.impact(oid)

    @router.post('/identifiers')
    async def identifier(request:Request):
        user=who(request,action='identifier.create');return m.catalog.identifier(await request.json(),user['id'])

    @router.post('/sources')
    async def source(request:Request):
        user=who(request,action='source.create')
        return m.catalog.sources.register(await request.json(),user['id'])

    @router.post('/catalog/{oid}/profile')
    def profile(oid:str,request:Request):
        user=who(request,action='object.profile');return m.catalog.profile(oid,user['id'])

    @router.post('/relations')
    async def relation(request:Request):
        user=who(request,action='relation.create');return m.catalog.link(await request.json(),user['id'])

    @router.post('/semantic/import-legacy')
    def import_legacy(request:Request):
        who(request,action='concept.import');return m.semantic.import_legacy()

    @router.post('/concepts')
    async def concept(request:Request):
        user=who(request,action='concept.create');return m.semantic.concept(await request.json(),user['id'])

    @router.get('/concepts/{oid}/candidates')
    def candidates(oid:str,request:Request):
        who(request,action='concept.candidates');return {'items':m.semantic.candidates(oid)}

    @router.get('/concepts/{oid}/resolve')
    def resolve(oid:str,request:Request):
        who(request,action='concept.resolve');return m.semantic.resolve(oid)

    @router.post('/mappings')
    async def mapping(request:Request):
        user=who(request,action='mapping.create');return m.semantic.mapping(await request.json(),user['id'])

    @router.post('/services')
    async def service(request:Request):
        user=who(request,action='service.create');b=await request.json()
        if b.get('id') and m.store.get('service',b['id']):own('service',b['id'],user)
        if user['role']=='provider':b['provider']=user['id']
        return m.services.save(b,user['id'])

    @router.post('/services/compose')
    async def compose(request:Request):
        user=who(request,action='service.compose');body=await request.json()
        if user['role']=='provider':
            body['provider']=user['id']
            plan=runtime.execution.get(body.get('plan_id',''),body.get('plan_version'))
            ids=list(plan.get('service_versions',{}))
            if not ids or not plan.get('nodes') or any((m.store.get('service',sid) or {}).get('provider')!=user['id'] for sid in ids) or any(n.get('operation')!='service' or n.get('service_id') not in ids for n in plan['nodes']):
                raise DomainError('只能复用全部来源服务均属于本方的组合',403)
        return m.services.compose(body,user['id'])

    @router.post('/designs')
    async def designs(request:Request):
        user=who(request,action='external_design.create');return m.register_design(await request.json(),user['id'])

    @router.post('/services/{oid}/validate')
    def validate(oid:str,request:Request):
        user=who(request,action='service.validate');own('service',oid,user)
        return m.services.validate(oid,user['id'])

    @router.post('/services/{oid}/transition')
    async def transition(oid:str,request:Request):
        user=who(request,action='service.transition');return m.services.transition(oid,await request.json(),user['id'])

    @router.post('/services/{oid}/regularize')
    async def regularize(oid:str,request:Request):
        user=who(request,action='service.regularize');own('service',oid,user)
        return m.services.regularize(oid,await request.json(),user['id'])

    @router.post('/demands')
    async def demand(request:Request):
        user=who(request,action='demand.create');b=await request.json()
        if b.get('id') and m.store.get('demand',b['id']):own('demand',b['id'],user)
        return m.intake.save(b,user['id'])

    @router.get('/demands/{oid}/gaps')
    def gaps(oid:str,request:Request):
        user=who(request,action='demand.gaps');own('demand',oid,user);return m.intake.gaps(oid)

    @router.post('/demands/{oid}/confirm')
    async def confirm(oid:str,request:Request):
        user=who(request,action='demand.confirm');return m.intake.confirm(oid,await request.json(),user['id'])

    @router.post('/intake/understand')
    def understand(request:Request):
        who(request,action='demand.understand');raise DomainError('LLM未接入；请使用人工表单，不会自动生成任务规格',503)

    @router.get('/runs')
    def runs(request:Request):
        user=who(request)
        require_resource(user,'run')
        return {'items':[r for r in m.evidence.list() if can_read_execution(runtime,'run',r,user)]}

    @router.post('/feedback')
    async def feedback(request:Request):
        user=who(request,action='feedback.create');body=await request.json()
        run=m.evidence.get(body.get('run_id',''))
        if user['role']!='platform' and run.get('actor')!=user['id']:raise DomainError('不能评价他人运行',403)
        return m.feedback.evaluate(body,user['id'])

    @router.post('/improvements')
    async def improve(request:Request):
        user=who(request,action='improvement.create');return m.feedback.compare(await request.json(),user['id'])

    @router.post('/metric-definitions')
    async def definition(request:Request):
        user=who(request,action='metric_definition.create');return m.feedback.definition(await request.json(),user['id'])

    @router.post('/reconciliation/run')
    def reconcile(request:Request):
        who(request,action='case.reconcile');return {'items':m.reconciliation.run()}

    @router.post('/reconciliation/experiment')
    def experiment(request:Request):
        who(request,action='case.experiment')
        from management.reconciliation.experiment import evaluate
        return evaluate()

    @router.get('/reconciliation/metrics')
    def metrics(request:Request,days:int=30):
        who(request,action='case.metrics')
        if not 1<=days<=365:raise DomainError('窗口为1至365天')
        return m.reconciliation.metrics(days)

    @router.post('/cases/{oid}/action')
    async def action(oid:str,request:Request):
        user=who(request,action='case.action');return m.reconciliation.action(oid,await request.json(),user['id'])

    @router.get('/events')
    def events(request:Request,after:int=0):
        user=who(request);require_resource(user,'event');return {'items':m.store.events(after)}

    return router
