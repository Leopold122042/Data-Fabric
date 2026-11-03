from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta, timezone
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
    def matching_intake_grants(plan,subject):
        matches=[]
        for grant in runtime.execution.store.list('grant'):
            if (grant.get('status')!='active' or grant.get('subject')!=subject
                    or grant.get('plan_id')!=plan.get('id') or grant.get('plan_version')!=plan.get('version')
                    or grant.get('purpose')!=plan.get('purpose')):continue
            _,reason=runtime.execution.security.assess(grant['id'],plan,subject,plan['purpose'])
            if reason is None:matches.append(grant)
        return sorted(matches,key=lambda item:item.get('expires_at',''),reverse=True)

    from management.work_orders import WorkOrders
    work_orders=WorkOrders(runtime)
    from management.intake.flow import IntakeFlow
    intake_flow=IntakeFlow(runtime)

    @router.get('/work-orders')
    def work_order_list(request:Request,q:str='',status:str=''):
        return JSONResponse(work_orders.list(who(request),q,status),headers={'Cache-Control':'no-store'})

    @router.get('/work-orders/{oid}')
    def work_order_detail(oid:str,request:Request):
        return JSONResponse(work_orders.get(oid,who(request)),headers={'Cache-Control':'no-store'})

    @router.post('/work-orders/{oid}/messages')
    async def work_order_message(oid:str,request:Request):
        return JSONResponse(work_orders.message(oid,await request.json(),who(request)),headers={'Cache-Control':'no-store'})

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
        from management.intake.ollama import OllamaIntake, explicitly_mentions_cost
        ollama=OllamaIntake()
        return {'principal':user,'environment':m.store.environment,'llm':{'available':True,'provider':'ollama','model':ollama.model,'base_url':ollama.base_url},
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
        user=who(request,action='demand.confirm');own('demand',oid,user);body=await request.json()
        flow=max((x for x in m.store.list('intake_flow') if x.get('demand_id')==oid),key=lambda x:x['updated_at'],default=None)
        if flow and flow['state']=='awaiting_chain_confirmation':raise DomainError('请先确认完整需求链路',409)
        needs_sql=bool(flow and any(s.get('capability') in ('data_query','trend_analysis') for s in flow.get('steps',[])))
        if needs_sql:
            validation=flow.get('query_validation') or {}
            if validation.get('status')!='valid_pending_human_confirmation':raise DomainError('SQL草案未通过规则校验，不能确认包含查询的任务规格',409)
            if flow.get('query_output_blocker'):raise DomainError(flow['query_output_blocker'],409)
            if body.get('query_sql_confirmed') is not True or body.get('query_draft')!=validation.get('normalized_sql'):
                raise DomainError('需核对并确认已通过规则校验的SQL草案',409)
            body={**body,'query_draft':validation['normalized_sql']}
        spec=m.intake.confirm(oid,body,user['id'])
        if flow:
            flow=intake_flow.specification_confirmed(flow,spec,user['id'])
            auto_plan=bool(flow.get('auto_prepare_fixed_plan') and
                (spec.get('specification',{}).get('execution_constraints') or {}).get('identifier_output')=='pseudonymized')
            if auto_plan:
                flow=intake_flow._prepare_pseudonym_plan(flow,spec,user['id'])
            auto_field_plan=bool(flow.get('auto_prepare_field_plan') and flow.get('field_lookup'))
            if auto_field_plan:
                flow=intake_flow._prepare_catalog_field_plan(flow,spec,user['id'])
            auto_admission_plan=bool(flow.get('auto_prepare_admission_time_plan') and not flow.get('query_output_blocker'))
            auto_admission_plan=auto_admission_plan and not flow.get('execution_output_blocker')
            if auto_admission_plan:
                flow=intake_flow._prepare_admission_time_plan(flow,spec,user['id'])
            if flow.get('execution_output_blocker'):
                steps=[]
                for step in flow.get('steps',[]):
                    item=dict(step)
                    if item.get('capability')=='data_query':
                        item['status']='blocked';item['detail']=flow['execution_output_blocker']
                    steps.append(item)
                flow=m.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'state':'attention'},
                    flow['version'],user['id'],'规格已确认；现有输出规则阻止健康信息原值交付')
            next_action=(flow.get('execution_output_blocker') or '创建受控逻辑视图和执行方案；确认方案后另行申请授权并提交执行。模型SQL草案不会直接执行。'
                         if needs_sql and not auto_plan and not auto_field_plan and not auto_admission_plan else
                         '任务规格已确认；平台已自动生成固定方案草案，待你确认。尚未授权或执行查询。'
                         if auto_plan or auto_field_plan or auto_admission_plan else '任务规格已确认；目录检索链路已完成，未执行数据查询或授予权限。')
            return {**spec,'intake_flow':flow,'next_action':next_action}
        return spec

    @router.get('/demands/{oid}/flows')
    def demand_flows(oid:str,request:Request):
        user=who(request,action='demand.gaps');own('demand',oid,user)
        items=sorted([x for x in m.store.list('intake_flow') if x.get('demand_id')==oid],key=lambda x:x['updated_at'],reverse=True)
        needs_cloud_session=any(x.get('state')=='waiting_authorization' and any(
            step.get('capability')=='connected_source_discovery' and step.get('status')=='waiting_authorization'
            for step in x.get('steps',[])) and any(str(resource.get('source_id','')).startswith('cloud-') or
            resource.get('source_id')=='connected-cloud'
            for resource in x.get('resources',[])) for x in items)
        external_catalog=intake_flow.external_catalog(request) if needs_cloud_session else ()
        items=[intake_flow.repair_admission_time_query(
            intake_flow.continue_incomplete_query(item,user['id']),user['id']) for item in items]
        items=[intake_flow.repair_field_query(item,user['id']) for item in items]
        items=[intake_flow.repair_output_field_classification(item,user['id']) for item in items]
        resumed_plans=[]
        for item in items:
            # A previously confirmed plan can be invalidated if an intake repair
            # rewrites the same demand as a draft. Restore only when its business
            # fields still exactly match a confirmed historical demand version.
            if (item.get('state')=='awaiting_authorization_request'
                    and item.get('plan_confirmed_by') and item.get('execution_plan_id')
                    and item.get('execution_plan_version') and not item.get('grant_id')):
                plan_state=runtime.execution.store.get('plan_state',item['execution_plan_id'])
                if plan_state and plan_state.get('status')=='invalidated':
                    spec=m.store.get('spec',item.get('spec_id',''),item.get('spec_version'))
                    demand=m.store.get('demand',item.get('demand_id',''))
                    if spec and spec.get('confirmed') and demand:
                        business_fields=('title','object_scope','time_start','time_end','deadline','purpose',
                                         'quality','delivery','execution_constraints','attachments','cost','service_ids')
                        prior_confirmed=next((old for old in m.store.history('demand',demand['id'])
                            if old.get('state')=='confirmed' and old.get('spec_version')==spec['version']
                            and all(old.get(key)==demand.get(key) for key in business_fields)),None)
                        if prior_confirmed:
                            if demand.get('state')!='confirmed':
                                demand=m.store.save('demand',demand['id'],
                                    {**demand,'state':'confirmed','spec_version':spec['version']},
                                    demand['version'],user['id'],
                                    '恢复与已确认规格内容完全一致的需求状态')
                            plan=runtime.execution.get(item['execution_plan_id'],item['execution_plan_version'])
                            if plan_state.get('plan_version')==plan['version']:
                                runtime.execution.update('plan_state',plan_state,
                                    {'status':'confirmed','reason':None},user['id'])
                                try:
                                    runtime.execution.current(plan)
                                except DomainError as exc:
                                    current_state=runtime.execution.store.require('plan_state',plan['id'])
                                    runtime.execution.update('plan_state',current_state,
                                        {'status':'invalidated','reason':str(exc)},user['id'])
                                else:
                                    item=m.store.save('intake_flow',item['id'],
                                        {**item,'plan_revalidated_at':datetime.now(timezone.utc).isoformat()},
                                        item['version'],user['id'],
                                        '恢复需求确认状态并通过依赖版本复核，保留既有方案确认')
            if (item.get('state')=='waiting_manual_spec_confirmation'
                    and item.get('auto_prepare_field_plan') and item.get('field_lookup')
                    and not item.get('spec_id')):
                validation=item.get('query_validation') or {}
                confirmed_specs=[spec for spec in m.store.list('spec')
                    if spec.get('demand_id')==item.get('demand_id') and spec.get('confirmed')
                    and ((spec.get('specification') or {}).get('execution_constraints') or {}).get('field_output')
                        ==item['field_lookup'].get('field_id')
                    and (spec.get('specification') or {}).get('query_draft')==validation.get('normalized_sql')]
                if confirmed_specs:
                    spec=max(confirmed_specs,key=lambda row:(row.get('version',0),row.get('updated_at','')))
                    item=m.store.save('intake_flow',item['id'],{**item,'state':'awaiting_execution_plan',
                        'spec_id':spec['id'],'spec_version':spec['version'],
                        'resources':[{**resource,'review_status':'human_confirmed'}
                                     for resource in item.get('resources',[])]},item['version'],user['id'],
                        '恢复已确认的目录字段规格；准备补建固定方案')
            spec=m.store.get('spec',item.get('spec_id',''),item.get('spec_version')) if item.get('spec_id') else None
            constraints=((spec or {}).get('specification') or {}).get('execution_constraints') or {}
            if (item.get('state')=='awaiting_execution_plan' and item.get('auto_prepare_field_plan')
                    and item.get('field_lookup') and not item.get('execution_plan_id')
                    and spec and spec.get('confirmed') and constraints.get('field_output')==item['field_lookup'].get('field_id')):
                item=intake_flow._prepare_catalog_field_plan(item,spec,user['id'])
            resumed_plans.append(item)
        items=resumed_plans
        items=[intake_flow.reconcile_connected_sources(item,user['id'],external_catalog) for item in items]
        items=[intake_flow.reconcile_execution(item,user['id']) for item in items]
        resumed=[]
        for item in items:
            spec=m.store.get('spec',item.get('spec_id',''),item.get('spec_version')) if item.get('spec_id') else None
            spec_body=(spec or {}).get('specification') or {}
            constraints=spec_body.get('execution_constraints') or {}
            if (item.get('state')=='awaiting_execution_plan' and spec and spec.get('confirmed')
                    and constraints.get('scope',{}).get('kind')=='lookup'
                    and constraints.get('identifier_output')=='pseudonymized'):
                item={**item,'pseudonym_spec_revision_confirmed':True,
                    'draft':{**item.get('draft',{}),**{k:spec_body.get(k) for k in
                        ('object_scope','delivery','time_start','time_end','purpose','quality','deadline')},
                        'execution_constraints':constraints},
                    'draft_revision_note':'新规格已确认：姓名精确匹配；日期不用于患者数据筛选；结果仅含患者伪名ID。'}
            resumed.append(item)
        items=resumed
        for index,item in enumerate(items):
            if item.get('state')=='awaiting_execution_authorization':
                steps=[]
                for step in item.get('steps',[]):
                    updated=dict(step)
                    if updated.get('capability')=='data_query':
                        updated.update(status='awaiting_authorization_request',
                            detail='固定方案已确认；需求方提交字段交付申请后，由平台方最终批准。')
                    steps.append(updated)
                item=m.store.save('intake_flow',item['id'],{**item,'steps':steps,
                    'state':'awaiting_authorization_request','execution_authorized':False,'grant_id':None},
                    item['version'],user['id'],'迁移为需求方申请、平台方最终审批的授权流程')
                items[index]=item
            if item.get('output_preference')=='admission_time':
                legacy_copy={
                    '规格确认后需由需求方单独确认原值交付，再单独提交查询。平台方和数据方不审批。':
                        '规格确认后由需求方提交原值交付申请，平台方最终审批；批准后仍需单独提交查询。数据方不参与审批。',
                    '方案确认后由需求方单独确认入院时间原值授权。':
                        '方案确认后由需求方提交入院时间原值交付申请，平台方最终审批。',
                    '仅返回入院时间原值（健康信息；待需求方单独授权）':
                        '仅返回入院时间原值（健康信息；待需求方申请并由平台方批准）',
                    '按已确认字段分类和需求方单独授权控制查看':
                        '按已确认字段分类和平台方审批的需求方申请控制查看',
                }
                steps=[];copy_changed=False
                for step in item.get('steps',[]):
                    updated=dict(step);detail=updated.get('detail')
                    if isinstance(detail,str):
                        for old,new in legacy_copy.items():detail=detail.replace(old,new)
                        if detail!=updated.get('detail'):
                            updated['detail']=detail;copy_changed=True
                    steps.append(updated)
                preview=dict(item.get('execution_plan_preview') or {})
                description=preview.get('output_description')
                if isinstance(description,str):
                    for old,new in legacy_copy.items():description=description.replace(old,new)
                    if description!=preview.get('output_description'):
                        preview['output_description']=description;copy_changed=True
                if copy_changed:
                    item=m.store.save('intake_flow',item['id'],{**item,'steps':steps,
                        'execution_plan_preview':preview},item['version'],user['id'],
                        '更新字段交付审批职责说明：需求方申请、平台方最终审批')
                    items[index]=item
            if item.get('state') not in ('awaiting_authorization_request','authorization_declined') or user['role']!='requester':continue
            plan_id=item.get('execution_plan_id');plan_version=item.get('execution_plan_version')
            if not plan_id or not plan_version:continue
            plan=runtime.execution.get(plan_id,plan_version)
            grants=matching_intake_grants(plan,user['id'])
            items[index]={**item,'existing_execution_grant':
                ({'id':grants[0]['id'],'expires_at':grants[0]['expires_at']} if grants else None)}
        return {'items':items}

    @router.post('/intake/flows')
    async def propose_flow(request:Request):
        user=who(request,action='demand.understand');body=await request.json()
        demand_id=body.get('demand_id');own('demand',demand_id,user)
        from management.intake.ollama import OllamaIntake
        external_catalog=intake_flow.external_catalog(request)
        flow=intake_flow.propose(demand_id,body.get('text',''),user['id'],OllamaIntake(),external_catalog)
        return {'flow':flow,'stage':'awaiting_chain_confirmation',
                'note':'仅生成待确认链路；未生成任务规格、未执行查询、未触发服务、未授予权限。'}

    @router.post('/intake/flows/{flow_id}/confirm')
    async def confirm_flow(flow_id:str,request:Request):
        user=who(request,action='demand.understand')
        flow=m.store.require('intake_flow',flow_id)
        needs_cloud_session=any(str(resource.get('source_id','')).startswith('cloud-') or
                                resource.get('source_id')=='connected-cloud' for resource in flow.get('resources',[]))
        external_catalog=intake_flow.external_catalog(request) if needs_cloud_session else ()
        confirmed=intake_flow.confirm(flow_id,await request.json(),user['id'],external_catalog)
        # Advance a confirmed name-to-ID lookup straight to its prefilled spec gate.
        # This uses registered schema only; spec confirmation, authorization, and query submission remain separate.
        confirmed=intake_flow.continue_incomplete_query(confirmed,user['id'])
        return intake_flow.repair_admission_time_query(confirmed,user['id'])

    @router.post('/intake/flows/{flow_id}/confirm-admission-time-classification')
    async def confirm_admission_time_classification(flow_id:str,request:Request):
        user=who(request,action='object.annotate')
        flow=m.store.require('intake_flow',flow_id)
        own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:
            raise DomainError('需求链路已更新，请刷新后重试',409)
        if (flow.get('state')!='waiting_manual_spec_confirmation'
                or not flow.get('auto_adjusted_spec')
                or flow.get('output_preference')!='admission_time'):
            raise DomainError('当前链路没有待确认的入院时间字段分类',409)
        if type(body.get('confirmed')) is not bool:
            raise DomainError('请选择是否确认健康信息分类')
        lookup=intake_flow._admission_time_lookup(flow.get('request_text'),intake_flow._catalog_metadata())
        if not lookup:
            raise DomainError('无法从目录元数据定位入院时间字段',409)
        field= m.store.require('object',lookup['admission_field']['id'])
        from execution.security.output_policy import field_classification
        current_classification=field_classification(field)
        decision='yes' if body['confirmed'] else 'no'
        if body['confirmed'] and current_classification not in ('unknown','health'):
            raise DomainError('字段分类已被其他操作更新，请刷新后重新确认',409)
        with m.store.atomic():
            if body['confirmed'] and current_classification=='unknown':
                m.store.save('object',field['id'],{**field,'classification':'健康信息'},
                    field['version'],user['id'],'人工确认字段分类为健康信息')
            reviewed=m.store.save('intake_flow',flow_id,{**flow,'classification_review':{
                'field_id':field['id'],'field_name':f"{lookup['visit_table'].get('name')}.{lookup['admission_field'].get('name')}",
                'proposed_classification':'健康信息',
                'classification':'健康信息' if body['confirmed'] else field.get('classification'),
                'decision':decision,'actor':user['id'],'at':datetime.now(timezone.utc).isoformat()}},
                flow['version'],user['id'],'人工确认入院时间字段分类' if body['confirmed'] else '暂不确认入院时间字段分类')
        return intake_flow.repair_admission_time_query(reviewed,user['id'])

    @router.post('/intake/flows/{flow_id}/confirm-output-field-classification')
    async def confirm_output_field_classification(flow_id:str,request:Request):
        user=who(request,roles=('platform',),action='object.annotate')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:raise DomainError('流程版本已变化，请刷新后重试',409)
        if flow.get('state')!='waiting_field_classification' or not flow.get('field_lookup'):
            raise DomainError('当前流程没有待确认的输出字段分类',409)
        classification=body.get('classification')
        allowed={'公开','内部','健康信息','个人标识','敏感'}
        if classification not in allowed:raise DomainError('请选择有效的字段分类')
        lookup=flow['field_lookup'];field=m.store.require('object',lookup['field_id'])
        if field.get('kind')!='field' or field.get('parent_id')!=lookup.get('table_id'):
            raise DomainError('输出字段与当前查询表不匹配，拒绝修改分类',409)
        if field.get('classification') not in ('待确认',None,''):
            from execution.security.output_policy import field_classification
            if field_classification(field)!=classification:
                raise DomainError('字段分类已被其他操作更新，请刷新目录后重试',409)
        with m.store.atomic():
            updated_field=m.catalog.annotate(field['id'],{'expected_version':field['version'],
                'classification':classification,
                'classification_basis':'人工确认：用户确认字段分类为'+classification+'；字段名和含义不足以自动判断。'},user['id'])
            allowed_output=classification in ('公开','内部','健康信息')
            validation=flow.get('query_validation') or {}
            confirmed_specs=[spec for spec in m.store.list('spec')
                if spec.get('demand_id')==flow.get('demand_id') and spec.get('confirmed')
                and ((spec.get('specification') or {}).get('execution_constraints') or {}).get('field_output')==field['id']
                and (spec.get('specification') or {}).get('query_draft')==validation.get('normalized_sql')]
            confirmed_spec=max(confirmed_specs,key=lambda row:(row.get('version',0),row.get('updated_at','')),
                default=None)
            plan_ready=bool(allowed_output and confirmed_spec)
            steps=[]
            for step in flow.get('steps',[]):
                item=dict(step)
                if item.get('capability')=='data_query':
                    item.update(status='awaiting_execution_adapter' if plan_ready else
                        'waiting_manual_spec_confirmation' if allowed_output else 'blocked',
                        detail=('字段分类已人工确认；继续按已确认SQL和目录关联准备固定方案。'
                            if plan_ready else '字段分类已由平台方登记；请核对当前窗口预填的查询范围与输出字段。'
                            if allowed_output else '平台登记为“'+classification+'”；当前规则不允许直接输出此字段，请调整输出方式。'))
                steps.append(item)
            next_flow=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,
                'state':'awaiting_execution_plan' if plan_ready else
                    'waiting_manual_spec_confirmation' if allowed_output else 'attention',
                'spec_id':confirmed_spec['id'] if plan_ready else flow.get('spec_id'),
                'spec_version':confirmed_spec['version'] if plan_ready else flow.get('spec_version'),
                'resources':[{**resource,'review_status':'human_confirmed'} for resource in flow.get('resources',[])]
                    if plan_ready else flow.get('resources',[]),
                'field_lookup':{**lookup,'classification':classification,
                    'classification_basis':updated_field.get('classification_basis')},
                'classification_review':{'field_id':field['id'],'field_name':field.get('name'),
                    'classification':classification,'classification_basis':updated_field.get('classification_basis'),
                    'decision':'confirmed','actor':user['id'],
                    'at':datetime.now(timezone.utc).isoformat()}},flow['version'],user['id'],
                '平台方确认普通查询输出字段分类为'+classification)
        if plan_ready:
            next_flow=intake_flow._prepare_catalog_field_plan(next_flow,confirmed_spec,user['id'])
        return {'flow':next_flow,'field':updated_field,
            'note':'字段分类已登记；'+('已按已确认SQL补齐目录关联并生成固定方案草案，请确认方案后再申请交付授权。尚未读取患者数据或执行查询。'
                if next_flow.get('state')=='awaiting_plan_confirmation' else
                '请在当前窗口确认预填任务规格，随后按需求申请、平台审批和单独查询流程办理。'
                if classification in ('公开','内部','健康信息') else '该分类不允许此字段原值交付，尚未生成方案或查询。')}

    @router.post('/intake/flows/{flow_id}/revise-pseudonymized-id')
    async def revise_pseudonymized_id(flow_id:str,request:Request):
        user=who(request,action='demand.confirm');require_action(user,'plan.create')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        result=intake_flow.revise_for_pseudonymized_id(flow_id,await request.json(),user['id'])
        if not result.get('execution_plan_id'):
            return {'flow':result,
                'note':'已根据目录语义将需求匹配到普通输出字段，正在等待确认字段分类和任务规格；没有生成患者标识方案，也未读取数据。'}
        plan=runtime.execution.get(result['execution_plan_id'],result['execution_plan_version'])
        view=runtime.execution.store.require('view',result['execution_view_id'])
        return {'flow':result,'plan':plan,'view':view,
                'note':'新规格和待确认方案已留痕；仅创建目录逻辑视图与固定方案，未读取源数据行、未执行查询、未授予权限。'}

    @router.post('/intake/flows/{flow_id}/add-patient-identifier')
    async def add_patient_identifier(flow_id:str,request:Request):
        user=who(request,roles=('platform',),action='plan.create')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:
            raise DomainError('流程版本已变化，请刷新后重试',409)
        if body.get('confirmed') is not True or body.get('identifier_output')!='raw':
            raise DomainError('需明确确认本次新增原始patient_id输出')
        if flow.get('state')!='query_completed' or flow.get('output_preference')!='catalog_field':
            raise DomainError('当前流程没有可扩展的已完成目录字段查询',409)
        old_plan=runtime.execution.get(flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
        runtime.execution.current(old_plan)
        prior_grant=runtime.execution.store.get('grant',flow.get('grant_id','')) if flow.get('grant_id') else None
        specification=m.store.require('spec',flow.get('spec_id',''),flow.get('spec_version'))
        if not specification.get('confirmed') or not specification.get('query_draft_confirmed'):
            raise DomainError('原查询规格未确认，不能追加输出字段',409)
        old_view=runtime.execution.store.require('view',flow.get('execution_view_id',''))
        if len(old_view.get('output_fields',[]))!=1:
            raise DomainError('当前固定方案不是单一目录字段查询',409)
        diagnosis_field=m.store.require('object',old_view['output_fields'][0])
        from execution.security.output_policy import field_classification, infer_catalog_classification
        if field_classification(diagnosis_field)!='health':
            raise DomainError('当前唯一输出字段不是已识别的健康信息字段',409)
        filter_fields=(specification.get('specification',{}).get('execution_constraints') or {}).get('scope',{}).get('filters',[])
        if len(filter_fields)!=1:
            raise DomainError('仅支持在原有单一精确查找范围内追加患者标识',409)
        name_field=m.store.require('object',filter_fields[0]['field'])
        patient_table=m.store.require('object',name_field.get('parent_id',''))
        patient_ids=[m.store.require('object',field_id) for field_id in old_view.get('fields',[])
            if (m.store.require('object',field_id).get('source_id'),m.store.require('object',field_id).get('table'),
                m.store.require('object',field_id).get('name'))==
               (patient_table.get('source_id'),patient_table.get('table'),'patient_id')]
        if len(patient_ids)!=1:
            raise DomainError('目录中无法唯一定位姓名表的patient_id字段',409)
        patient_id=patient_ids[0]
        if not any(patient_id['id'] in pair for pair in old_view.get('joins',[])):
            raise DomainError('患者标识字段未通过目录登记的patient_id关联连接到诊断表',403)
        classification,basis=infer_catalog_classification(patient_id)
        if classification!='identifier':
            raise DomainError('目录规则无法确认该字段为患者标识',403)
        if patient_id.get('classification') in (None,'','待确认'):
            patient_id=m.catalog.annotate(patient_id['id'],{'expected_version':patient_id['version'],
                'classification':'个人标识','classification_basis':basis},user['id'])
        elif field_classification(patient_id)!='identifier':
            raise DomainError('患者标识目录分类与字段名规则不一致，请先复核目录',409)

        from execution.query.intake_plan import create_field_plan
        from management.intake.flow import validate_query_draft
        import sqlglot
        from sqlglot import exp
        spec_body=dict(specification['specification']);constraints=dict(spec_body.get('execution_constraints') or {})
        constraints.pop('field_output',None)
        output_ids=[diagnosis_field['id'],patient_id['id']]
        constraints['field_outputs']=output_ids
        spec_body['execution_constraints']=constraints
        tree=sqlglot.parse_one(spec_body.get('query_draft',''),read='duckdb')
        sql_tables=list(tree.find_all(exp.Table))
        aliases={}
        for table_object_id in {diagnosis_field.get('parent_id'),patient_table['id']}:
            table_object=m.store.require('object',table_object_id)
            found=[table for table in sql_tables if table.name.casefold()==str(table_object.get('table') or table_object.get('name')).casefold()]
            if len(found)!=1:raise DomainError('已确认查询中的关联表与当前目录版本不匹配',409)
            aliases[table_object_id]=found[0].alias_or_name
        projection=[]
        for field in (diagnosis_field,patient_id):
            table_object=m.store.require('object',field['parent_id'])
            projection.append(exp.column(field['name'],table=aliases[table_object['id']]))
        tree.set('expressions',projection)
        sql=tree.sql(dialect='duckdb')
        validation=validate_query_draft(sql,intake_flow._catalog_metadata())
        if validation.get('status')!='valid_pending_human_confirmation':
            raise DomainError('新增患者标识后的SQL未通过目录规则校验：'+str(validation.get('errors') or []),409)
        spec_body['query_draft']=validation['normalized_sql']
        demand=m.store.require('demand',flow['demand_id'])
        revised_spec=m.store.save('spec',specification['id'],{**specification,
            'demand_version':demand['version']+1,'specification':spec_body,
            'confirmed_by':user['id'],
            'confirmation_note':(specification.get('confirmation_note','')+
                '；平台方确认按原精确匹配范围新增原始patient_id标识输出。'),
            'confirmed':True,'execution_authorized':False},specification['version'],user['id'],
            '平台方确认新增目录patient_id输出字段并修订规格')
        demand=m.store.save('demand',demand['id'],{**demand,'state':'confirmed',
            'spec_id':revised_spec['id'],'spec_version':revised_spec['version']},demand['version'],user['id'],
            '平台方确认在原需求范围内新增患者标识输出')
        if prior_grant and prior_grant.get('status')=='active':
            runtime.execution.security.revoke(prior_grant['id'],user['id'])

        resources=list(flow.get('resources',[]))
        table_objects={patient_table['id']:patient_table,
                       diagnosis_field['parent_id']:m.store.require('object',diagnosis_field['parent_id'])}
        for table_object in table_objects.values():
            if not any(item.get('id')==table_object['id'] for item in resources):
                resources.append({'id':table_object['id'],'name':table_object.get('name'),
                    'source_id':table_object.get('source_id'),'table':table_object.get('table') or table_object.get('name'),
                    'kind':'table','review_status':'human_confirmed','reason':'沿用原已确认目录关联'})
        revised_flow={**flow,'resources':resources,'spec_id':revised_spec['id'],
            'spec_version':revised_spec['version'],'demand_version':demand['version'],
            'query_draft':spec_body['query_draft'],'query_validation':validation,
            'draft':{**flow.get('draft',{}),'query_draft':spec_body['query_draft'],
                'execution_constraints':constraints,
                'object_scope':(flow.get('draft',{}).get('object_scope','')+
                    '；增加目录患者标识patient_id用于区分命中记录。')},
            'output_preference':'catalog_field_with_patient_id',
            'field_lookup':{**(flow.get('field_lookup') or {}),'identifier_field_id':patient_id['id'],
                'identifier_field_name':patient_id['name'],'identifier_classification':'个人标识',
                'identifier_classification_basis':patient_id.get('classification_basis') or basis},
            'execution_authorized':False,'grant_id':None,'authorization_request':None,
            'authorization_decision':None,'plan_confirmed_by':None,'plan_confirmed_version':None}
        view,plan,shape=create_field_plan(runtime,revised_flow,revised_spec,user['id'])
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item.update(status='awaiting_plan_confirmation',detail=(
                    f"已在原姓名精确筛选和目录patient_id关联范围内增加原始patient_id及{diagnosis_field['name']}字段。"
                    '原始患者标识需绑定本方案由平台逐字段批准；未读取源数据行。'))
            steps.append(item)
        prior_runs=list(flow.get('prior_executions',[]))
        prior_runs.append({'plan_id':old_plan['id'],'plan_version':old_plan['version'],
            'job_id':flow.get('job_id'),'delivery_id':flow.get('delivery_id'),
            'grant_id':flow.get('grant_id'),'completed_at':flow.get('execution_submitted_at')})
        preview={'name':plan['name'],'plan_id':plan['id'],'version':plan['version'],
            'source_id':shape['table'].get('source_id'),
            'table':'patient + '+str(m.store.require('object',diagnosis_field['parent_id']).get('table')
                or m.store.require('object',diagnosis_field['parent_id']).get('name')),
            'filters':shape['filters'],'filter_names':shape['filter_names'],
            'output_columns':[diagnosis_field['name'],patient_id['name']],
            'output_description':f"按原姓名精确匹配返回{diagnosis_field['name']}与原始patient_id；仅本固定方案、用途及授权范围内受控查看。",
            'output_mode':'object','reads_source_rows':False,
            'health_output_authorization_required':True,'identifier_output_authorization_required':True}
        saved=m.store.save('intake_flow',flow_id,{**revised_flow,'steps':steps,
            'state':'awaiting_plan_confirmation','execution_plan_id':plan['id'],
            'execution_plan_version':plan['version'],'execution_view_id':view['id'],
            'execution_plan_preview':preview,'execution_authorized':False,'grant_id':None,
            'authorization_request':None,'authorization_decision':None,'prior_executions':prior_runs,
            'job_id':None,'job_state':None,'delivery_id':None,'query_error':None,
            'delivery_result':None,'delivery_result_read_at':None},flow['version'],user['id'],
            '平台方按明确请求新增原始patient_id输出，生成新规格和待确认固定方案')
        return {'flow':saved,'plan':plan,'view':view,
            'note':'已按原筛选范围新增目录patient_id字段并生成新方案。患者标识已记录为个人标识；新方案尚未授权或执行。'}

    @router.post('/intake/flows/{flow_id}/confirm-execution-plan')
    async def confirm_intake_execution_plan(flow_id:str,request:Request):
        user=who(request,action='plan.confirm')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if flow.get('state')!='awaiting_plan_confirmation':raise DomainError('当前流程没有待确认的执行方案',409)
        if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化，请刷新后重试',409)
        if body.get('confirmed') is not True:raise DomainError('需明确确认固定方案')
        plan=runtime.execution.get(flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
        runtime.execution.confirm(plan['id'],plan['version'],user['id'])
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item['status']='awaiting_authorization_request';item['connection_kind']='execution'
                item['detail']=('固定方案已确认。需求方可提交入院时间原值交付申请；平台方也可直接批准本次查询。授权后仍需单独确认提交。'
                    if flow.get('output_preference')=='admission_time' else
                    '固定方案已确认。需求方可提交本次授权申请；平台方也可直接批准本次查询。授权后仍需单独确认提交。')
            steps.append(item)
        saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,
            'state':'awaiting_authorization_request','plan_confirmed_by':user['id'],
            'plan_confirmed_version':plan['version'],'execution_authorized':False,
            'grant_id':None,'authorization_request':None},
            flow['version'],user['id'],'人工确认固定执行方案；进入授权确认步骤')
        demand=m.store.require('demand',flow['demand_id'])
        requester_id=demand.get('requester_id') or ('demo-requester' if demand.get('created_by')=='demo-platform' else None)
        grants=matching_intake_grants(plan,requester_id) if requester_id else []
        visible={**saved,'existing_execution_grant':
            ({'id':grants[0]['id'],'expires_at':grants[0]['expires_at']} if grants else None)}
        return {'flow':visible,'plan':plan,'note':'方案已确认；需求方可提交授权申请，平台方也可直接授权。查询仍需单独确认提交。'}

    @router.post('/intake/flows/{flow_id}/request-execution-authorization')
    async def request_intake_execution_authorization(flow_id:str,request:Request):
        user=who(request,roles=('requester',),action='demand.health_request')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化，请刷新后重试',409)
        plan_id=flow.get('execution_plan_id');plan_version=flow.get('execution_plan_version')
        if not plan_id or not plan_version or not flow.get('plan_confirmed_by'):
            raise DomainError('固定方案尚未确认，不能提交授权申请',409)
        if flow.get('state') not in ('awaiting_authorization_request','authorization_declined'):
            raise DomainError('当前流程不能提交授权申请',409)
        if body.get('request') is not True:raise DomainError('需明确提交本次字段交付申请')
        plan=runtime.execution.get(plan_id,plan_version);runtime.execution.current(plan)
        demand=m.store.require('demand',flow['demand_id'])
        requester_id=demand.get('requester_id') or ('demo-requester' if demand.get('created_by')=='demo-platform' else None)
        if not requester_id or requester_id!=user['id']:
            raise DomainError('当前需求没有关联到此需求方账号，不能提交授权申请',403)
        from execution.security.output_policy import output_authorization_requirements
        requirements=output_authorization_requirements(plan)
        if requirements is None:raise DomainError('固定方案包含不支持的敏感信息输出，不能提交授权申请',403)
        health_fields=requirements['health'];identifier_fields=requirements['identifier']
        approved_fields={**health_fields,**identifier_fields}
        request_id=new_id('AUTHREQ')
        request_record={'id':request_id,'status':'pending','requester_id':user['id'],
            'requester_role':user['role'],'plan_id':plan['id'],'plan_version':plan['version'],
            'purpose':plan['purpose'],'field_ids':sorted(approved_fields),
            'health_field_ids':sorted(health_fields),'identifier_field_ids':sorted(identifier_fields),
            'fields':[approved_fields[field_id] for field_id in sorted(approved_fields)],
            'requested_at':datetime.now(timezone.utc).isoformat()}
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item.update(status='awaiting_platform_authorization',detail='需求方已提交本次字段交付申请，等待平台方最终批准；尚未登记授权或读取数据。')
            steps.append(item)
        saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,
            'state':'awaiting_platform_authorization','authorization_request':request_record,
            'execution_authorized':False,'grant_id':None,'authorization_decision':None},
            flow['version'],user['id'],'需求方提交字段交付授权申请；等待平台方审核')
        return {'flow':saved,'note':'授权申请已提交给平台方审核；批准前不会登记授权或提交查询。'}

    @router.post('/intake/flows/{flow_id}/execution-authorization')
    async def intake_execution_authorization(flow_id:str,request:Request):
        user=who(request,roles=('platform',),action='demand.health_approve')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化，请刷新后重试',409)
        direct_platform=(flow.get('state')=='awaiting_authorization_request' and body.get('direct') is True)
        if not direct_platform and flow.get('state')!='awaiting_platform_authorization':
            raise DomainError('当前流程没有待平台审核的授权申请',409)
        request_record=flow.get('authorization_request') or {}
        if not direct_platform and request_record.get('status')!='pending':raise DomainError('授权申请已处理，请刷新流程',409)
        plan_id=flow.get('execution_plan_id');plan_version=flow.get('execution_plan_version')
        if not plan_id or not plan_version or not flow.get('plan_confirmed_by'):
            raise DomainError('固定方案尚未确认，不能审核授权申请',409)
        plan=runtime.execution.get(plan_id,plan_version);runtime.execution.current(plan)
        demand=m.store.require('demand',flow['demand_id'])
        demand_requester_id=demand.get('requester_id') or ('demo-requester' if demand.get('created_by')=='demo-platform' else None)
        from execution.security.output_policy import output_authorization_requirements
        requirements=output_authorization_requirements(plan)
        health_fields=requirements['health'] if requirements is not None else None
        identifier_fields=requirements['identifier'] if requirements is not None else None
        if direct_platform:
            approved_fields={**(health_fields or {}),**(identifier_fields or {})}
            request_record={'id':new_id('AUTHREQ'),'status':'pending','requester_id':user['id'],
                'requester_role':'platform','plan_id':plan['id'],'plan_version':plan['version'],
                'purpose':plan['purpose'],'field_ids':sorted(approved_fields),
                'health_field_ids':sorted(health_fields or {}),
                'identifier_field_ids':sorted(identifier_fields or {}),
                'fields':[approved_fields[field_id] for field_id in sorted(approved_fields)],
                'requested_at':datetime.now(timezone.utc).isoformat(),'direct_platform_approval':True}
            requester_id=user['id']
        else:
            requester_id=demand_requester_id
        request_field_ids=request_record.get('field_ids')
        request_fields=request_record.get('fields')
        approved_fields={**(health_fields or {}),**(identifier_fields or {})}
        expected_fields=([approved_fields[field_id] for field_id in sorted(approved_fields)]
                         if health_fields is not None and identifier_fields is not None else [])
        if (health_fields is None or identifier_fields is None or not isinstance(request_field_ids,list)
                or any(not isinstance(field_id,str) for field_id in request_field_ids)
                or request_record.get('requester_id')!=requester_id
                or request_record.get('plan_id')!=plan['id']
                or request_record.get('plan_version')!=plan['version']
                or request_record.get('purpose')!=plan['purpose']
                or set(request_field_ids)!=set(approved_fields)
                or set(request_record.get('health_field_ids',[]))!=set(health_fields)
                or set(request_record.get('identifier_field_ids',[]))!=set(identifier_fields)
                or request_fields!=expected_fields):
            raise DomainError('授权申请与当前需求方、固定方案或字段分类不匹配，拒绝审核',409)
        decision=body.get('decision')
        if decision not in ('yes','no'):raise DomainError('请选择批准或拒绝')
        now_iso=datetime.now(timezone.utc).isoformat()
        if decision=='no':
            matches=matching_intake_grants(plan,requester_id)
            revoked=[]
            for grant in matches:
                runtime.execution.security.revoke(grant['id'],user['id']);revoked.append(grant['id'])
            request_record={**request_record,'status':'denied','reviewed_by':user['id'],
                'reviewer_role':user['role'],'reviewed_at':now_iso}
            steps=[]
            for step in flow.get('steps',[]):
                item=dict(step)
                if item.get('capability')=='data_query':
                    item.update(status='authorization_declined',detail='平台方已拒绝本次字段交付申请；未登记授权，也未提交查询。')
                steps.append(item)
            saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,'state':'authorization_declined',
                'authorization_request':request_record,'execution_authorized':False,
                'authorization_decision':{'value':'no','actor':user['id'],'role':user['role'],'at':now_iso,
                    'request_id':request_record['id'],'field_ids':request_record['field_ids']},
                'grant_id':None,'revoked_grant_ids':revoked},flow['version'],user['id'],
                '平台方拒绝本次字段交付申请' if not direct_platform else '平台方暂不授权本次查询')
            return {'flow':saved,'note':'平台方已拒绝本次授权；没有提交查询。'}
        if flow.get('execution_authorized') or flow.get('grant_id'):
            raise DomainError('本流程已经登记授权，请刷新查看当前步骤',409)
        policy=plan.get('execution_policy') or {}
        try:deadline=datetime.fromisoformat(str(policy.get('deadline_at','')).replace('Z','+00:00'))
        except (TypeError,ValueError):raise DomainError('固定方案交付时限无效，不能批准授权',409)
        if deadline.tzinfo is None:raise DomainError('固定方案交付时限缺少时区，不能批准授权',409)
        expires_at=min(datetime.now(timezone.utc)+timedelta(hours=1),deadline)
        if expires_at<=datetime.now(timezone.utc):raise DomainError('固定方案已超过交付时限，请先修订规格和方案',409)
        consent=({'decision':'approved','approver_role':user['role'],'approver_id':user['id'],
                  'requester_id':requester_id,'request_id':request_record['id'],'plan_id':plan['id'],
                  'plan_version':plan['version'],'purpose':plan['purpose'],'field_ids':sorted(health_fields)}
                 if health_fields else None)
        identifier_consent=({'decision':'approved','approver_role':user['role'],'approver_id':user['id'],
                  'requester_id':requester_id,'request_id':request_record['id'],'plan_id':plan['id'],
                  'plan_version':plan['version'],'purpose':plan['purpose'],'field_ids':sorted(identifier_fields)}
                 if identifier_fields else None)
        grant=runtime.execution.security.grant({'subject':requester_id,'purpose':plan['purpose'],
            'plan_id':plan['id'],'plan_version':plan['version'],'expires_at':expires_at.isoformat(),
            'mode':plan['output_mode'],'max_rows':policy['max_rows'],'min_group_size':policy['min_group_size']},user['id'],
            health_output_authorization=consent,identifier_output_authorization=identifier_consent)
        request_record={**request_record,'status':'approved','reviewed_by':user['id'],
            'reviewer_role':user['role'],'reviewed_at':now_iso,'grant_id':grant['id'],
            'expires_at':grant['expires_at']}
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item.update(status='awaiting_query_submission',detail='本次授权已登记并绑定固定方案；查询仍未提交。请在下一步单独确认提交。')
            steps.append(item)
        saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,'state':'awaiting_query_submission',
            'authorization_request':request_record,
            'execution_authorized':True,'grant_id':grant['id'],'authorization_reused':False,
            'authorization_decision':{'value':'yes','actor':user['id'],'role':user['role'],'at':now_iso,
                'request_id':request_record['id'],'field_ids':request_record['field_ids'],
                'health_field_ids':request_record['health_field_ids'],
                'identifier_field_ids':request_record['identifier_field_ids'],
                'direct_platform_approval':direct_platform,'expires_at':grant['expires_at']}},
            flow['version'],user['id'],'平台方直接授权本次查询' if direct_platform else
                '平台方批准需求方字段交付申请并登记授权；查询仍未提交')
        return {'flow':saved,'grant':{'id':grant['id'],'expires_at':grant['expires_at'],'reused':False},
                'note':'平台方已'+('直接批准' if direct_platform else '批准需求方申请')+
                    '并登记本次授权；查询尚未执行，请单独确认提交。'}

    @router.post('/intake/flows/{flow_id}/submit-query')
    async def submit_intake_query(flow_id:str,request:Request):
        user=who(request,roles=('requester','platform'),action='plan.run')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化，请刷新后重试',409)
        retrying=flow.get('state')=='query_failed'
        if flow.get('state')!='awaiting_query_submission' and not retrying:
            raise DomainError('当前流程没有待提交或可重试的受控查询',409)
        if body.get('confirmed') is not True:raise DomainError('需明确确认后才会提交查询')
        if not flow.get('execution_authorized') or not flow.get('grant_id'):
            raise DomainError('尚未登记本次执行授权，不能提交查询',403)
        authorization_request=flow.get('authorization_request') or {}
        platform_direct=(user['role']=='platform' and authorization_request.get('direct_platform_approval') is True
            and authorization_request.get('requester_id')==user['id'])
        if user['role']!='requester' and not platform_direct:
            raise DomainError('只有本次授权申请人或直接批准本次查询的平台方可提交',403)
        if user['role']=='requester' and authorization_request.get('requester_id') not in (None,user['id']):
            raise DomainError('当前账号不是本次授权申请人，不能提交查询',403)
        plan=runtime.execution.get(flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
        runtime.execution.current(plan)
        with runtime.execution.lock:
            if retrying:
                prior=runtime.execution.store.require('job',flow.get('job_id',''))
                if (prior.get('state') not in ('failed','interrupted','cancelled')
                        or prior.get('plan_id')!=plan['id'] or prior.get('plan_version')!=plan['version']
                        or prior.get('grant_id')!=flow['grant_id'] or prior.get('actor')!=user['id']):
                    raise DomainError('只能在原方案、原授权和原提交人范围内重试失败任务',403)
                retries=[item for item in runtime.execution.store.list('job')
                         if item.get('retry_of')==prior['id'] and item.get('plan_id')==plan['id']
                         and item.get('plan_version')==plan['version'] and item.get('grant_id')==flow['grant_id']
                         and item.get('actor')==user['id']]
                retries.sort(key=lambda item:item.get('updated_at',''))
                active_retry=next((item for item in reversed(retries)
                                   if item.get('state') in ('queued','running')),None)
                job=active_retry or runtime.execution.submit(plan['id'],plan['version'],flow['grant_id'],user['id'],
                    retry_of=prior['id'])
            else:
                existing=[item for item in runtime.execution.store.list('job')
                          if item.get('plan_id')==plan['id'] and item.get('plan_version')==plan['version']
                          and item.get('grant_id')==flow['grant_id'] and item.get('actor')==user['id']
                          and item.get('state') in ('queued','running')]
                existing.sort(key=lambda item:item.get('updated_at',''))
                job=existing[-1] if existing else runtime.execution.submit(plan['id'],plan['version'],flow['grant_id'],user['id'])
            steps=[]
            for step in flow.get('steps',[]):
                item=dict(step)
                if item.get('capability')=='data_query':
                    item.update(status='query_queued',detail='查询已在原方案和授权范围内进入受控执行队列。')
                steps.append(item)
            saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,'state':'query_queued',
                'job_id':job['id'],'job_state':job['state'],'execution_submitted_by':user['id'],
                'execution_submitted_at':job.get('created_at') or job['updated_at']},flow['version'],user['id'],
                '用户在原方案和授权范围内重试受控查询' if retrying else '用户单独确认并提交受控查询')
        saved=intake_flow.reconcile_execution(saved,user['id'])
        return {'flow':saved,'job':{'id':job['id'],'state':job['state']},
                'note':'受控查询已在原授权范围内'+('重试' if retrying else '提交')+'；结果将在此流程进度中更新。'}

    @router.post('/intake/flows/{flow_id}/read-delivery')
    async def read_intake_delivery(flow_id:str,request:Request):
        user=who(request,action='delivery.read')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        if flow.get('state')!='query_completed' or not flow.get('delivery_id'):
            raise DomainError('当前流程还没有可读取的查询结果',409)
        plan=runtime.execution.get(flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
        runtime.execution.current(plan)
        with runtime.execution.lock,m.store.lock:
            delivery=runtime.execution.delivery.read(flow['delivery_id'],user['id'],plan)
            flow=m.store.save('intake_flow',flow_id,{**flow,'delivery_result_read_at':datetime.now(timezone.utc).isoformat()},
                flow['version'],user['id'],'用户读取受控查询交付结果')
        return {'flow':flow,'delivery_id':delivery['id'],'result':delivery['result'],
                'issued_at':delivery['receipt']['issued_at'],'expires_at':delivery['receipt']['expires_at'],
                'checks':delivery['receipt']['checks']}

    @router.post('/intake/understand')
    async def understand(request:Request):
        user=who(request,action='demand.understand');body=await request.json()
        from management.intake.ollama import OllamaIntake
        draft=OllamaIntake().extract(body.get('text',''),intake_flow._catalog_metadata())
        demand_id=body.get('demand_id')
        if demand_id:
            own('demand',demand_id,user)
            result=m.intake.apply_draft(demand_id,{'expected_version':body.get('expected_version'),'proposal':draft,
                'model':OllamaIntake().model,'cost_explicit':explicitly_mentions_cost(body.get('text',''))},user['id'])
            return {**result,'model':OllamaIntake().model,'stage':'draft_only'}
        return {'draft':draft,'model':OllamaIntake().model,'stage':'draft_only',
                'note':'仅为待规则校验的抽取草案；未生成规格、未触发调用、未授权'}

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
