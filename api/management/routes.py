from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import re
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
    from management.intake.flow import IntakeFlow,normalized_field_output_ids
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

    from management.catalog.field_library import FieldLibrary
    field_library=FieldLibrary(m.store)

    @router.get('/field-library')
    def fields_list(request:Request):
        user=who(request);require_resource(user,'object')
        return {'items':field_library.items()}

    @router.post('/field-library/sync')
    def fields_sync(request:Request):
        user=who(request,action='object.annotate')
        return {'changed':len(field_library.sync(user['id']))}

    @router.post('/field-library/match')
    async def fields_match(request:Request):
        user=who(request);require_resource(user,'object')
        return field_library.match((await request.json()).get('text'))

    @router.get('/field-library/{oid}/history')
    def field_history(oid:str,request:Request):
        user=who(request);require_resource(user,'object')
        return {'items':m.store.history('field_definition',oid)}

    @router.post('/field-library/{oid}/save')
    async def field_save(oid:str,request:Request):
        user=who(request,action='object.annotate')
        return field_library.save(oid,await request.json(),user['id'])

    @router.post('/field-library/{oid}/confirm')
    async def field_confirm(oid:str,request:Request):
        user=who(request,action='object.annotate')
        return field_library.save(oid,await request.json(),user['id'],confirm=True)

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
                    and set(item['field_lookup'].get('field_ids') or [item['field_lookup'].get('field_id')]) <= normalized_field_output_ids(
                        ((spec.get('specification') or {}).get('execution_constraints') or {}))
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
            confirmed_outputs=normalized_field_output_ids(constraints)
            if (item.get('state')=='awaiting_execution_plan' and item.get('auto_prepare_field_plan')
                    and item.get('field_lookup') and not item.get('execution_plan_id')
                    and spec and spec.get('confirmed')
                    and set(item['field_lookup'].get('field_ids') or [item['field_lookup'].get('field_id')])<=confirmed_outputs):
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
        request_text=body.get('text','')
        preparsed_draft=None
        if body.get('reuse_saved_draft') is True:
            prior=max((item for item in m.store.list('intake_flow')
                if item.get('demand_id')==demand_id and item.get('request_text')==request_text
                and isinstance(item.get('draft'),dict)),key=lambda item:item.get('updated_at',''),default=None)
            preparsed_draft=prior.get('draft') if prior else None
        flow=intake_flow.propose(demand_id,request_text,user['id'],OllamaIntake(),external_catalog,
            preparsed_draft=preparsed_draft)
        return {'flow':flow,'stage':'awaiting_chain_confirmation',
                'note':'仅生成待确认链路；未生成任务规格、未执行查询、未触发服务、未授予权限。'}

    @router.post('/intake/flows/{flow_id}/clarify')
    async def clarify_intake_flow(flow_id:str,request:Request):
        user=who(request,action='demand.understand')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow.get('version'):
            raise DomainError('需求链路已更新，请刷新后再回答',409)
        if flow.get('state')!='awaiting_chain_confirmation':
            raise DomainError('当前链路不在字段澄清阶段，请重新检索目录后再补充',409)
        answers=body.get('answers')
        if not isinstance(answers,list) or not answers or len(answers)>20:
            raise DomainError('请逐项确认字段候选或填写澄清说明')
        all_bindings={item.get('intent_id'):item for item in flow.get('field_bindings',[])
                      if item.get('intent_id')}
        pending={intent_id:item for intent_id,item in all_bindings.items()
                 if item.get('status')!='suggested'}
        answerable={intent_id:item for intent_id,item in all_bindings.items()
                    if item.get('status')!='suggested'
                    or (len(item.get('candidates',[]))>1 and not item.get('selection_locked'))}
        answered=set();field_selections={};details=[];has_free_text_answer=False
        candidate_by_id={candidate.get('field_id'):candidate
            for item in answerable.values() for candidate in item.get('candidates',[])}
        for answer in answers:
            if not isinstance(answer,dict) or not isinstance(answer.get('intent_id'),str):
                raise DomainError('每条回答需包含有效的字段编号')
            intent_id=answer['intent_id']
            if intent_id not in answerable:
                raise DomainError('回答的问题已变化，请刷新后重新填写',409)
            if intent_id in answered:
                raise DomainError('同一字段不能重复提交，请刷新后重试')
            answered.add(intent_id)
            binding=answerable[intent_id]
            candidate_ids={item.get('field_id') for item in binding.get('candidates',[])
                           if item.get('field_id')}
            selected=answer.get('selected_field_ids',[])
            if not isinstance(selected,list) or len(selected)>5 or any(not isinstance(value,str) for value in selected):
                raise DomainError('字段候选选择格式无效，请刷新后重新选择')
            if len(set(selected))!=len(selected):
                raise DomainError('字段候选不能重复选择')
            if binding.get('selection_locked'):
                locked_id=binding.get('suggested_field_id')
                if selected and selected!=[locked_id]:
                    raise DomainError('该患者唯一标识由目录规则固定加入，不能改选其他字段')
                selected=[locked_id] if locked_id else []
            if candidate_ids:
                if not selected:
                    raise DomainError(f'请至少勾选一个“{binding.get("phrase") or "字段"}”候选')
                if not set(selected)<=candidate_ids:
                    raise DomainError('所选字段不属于当前候选，请刷新后重新选择',409)
                phrase=binding.get('phrase') or '字段'
                field_selections.setdefault(phrase,[])
                field_selections[phrase]=list(dict.fromkeys(field_selections[phrase]+selected))
                labels=[candidate_by_id[field_id] for field_id in selected]
                details.append(f'需求字段“{phrase}”用户选定：'+'、'.join(
                    f'{item.get("table_name") or item.get("table")}.{item.get("field_name")}'
                    for item in labels)+'；只按所选字段匹配本次需求。')
            answer_text=answer.get('answer','')
            if not isinstance(answer_text,str) or len(answer_text)>1000:
                raise DomainError('补充说明须为不超过1000字的文本')
            answer_text=answer_text.strip()
            requires_text=(not candidate_ids or binding.get('status') in
                           ('sql_missing','model_missing','missing'))
            if requires_text and not answer_text:
                phrase=binding.get('phrase') or '字段'
                if binding.get('status')=='sql_missing':
                    raise DomainError(f'字段“{phrase}”已匹配；请补充本次查询的筛选条件、统计口径和输出方式')
                if binding.get('status')=='model_missing':
                    raise DomainError('请说明本次查询需要返回、筛选、分组或统计什么')
                raise DomainError(f'请补充“{phrase}”的业务含义、数据来源或实际字段名称')
            if answer_text:
                has_free_text_answer=True
                details.append(f'{binding.get("phrase") or "字段"}：{answer_text}')
        missing=set(pending)-answered
        if missing:raise DomainError('请回答所有待澄清字段后再继续')
        clarified_text=flow.get('request_text','')
        if details:
            clarified_text+='\n用户针对字段映射的补充说明：'+'；'.join(details)
        preparsed_draft=None
        person_request=bool(re.search(r'[\u4e00-\u9fff]{2,4}\s*的\s*.+',flow.get('request_text','')))
        reusable_statuses={'ambiguous','sql_missing','model_missing'}
        if field_selections and person_request and all(
                item.get('status') in reusable_statuses for item in pending.values()):
            answers_by_id={item.get('intent_id'):item for item in answers if isinstance(item,dict)}
            reusable=True
            draft_copy=deepcopy(flow.get('draft') or {})
            acknowledged_gaps=[]
            for intent_id,binding in pending.items():
                answer_text=str((answers_by_id.get(intent_id) or {}).get('answer','')).strip()
                if binding.get('status')=='sql_missing' and not re.search(r'输出|返回',answer_text):
                    reusable=False
                    break
                if binding.get('status')=='model_missing':
                    if not re.search(r'(?:没有|无).{0,12}(?:额外|其他)',answer_text):
                        reusable=False
                        break
                    acknowledged_gaps.append(binding.get('meaning') or binding.get('question') or '')
            if reusable:
                def compact(value):
                    return ''.join(str(value or '').casefold().split())
                draft_copy['uncertain_fields']=[gap for gap in draft_copy.get('uncertain_fields',[])
                    if not any(compact(note) and compact(note) in compact(gap)
                               for note in acknowledged_gaps)]
                preparsed_draft=draft_copy
        from management.intake.ollama import OllamaIntake
        external_catalog=intake_flow.external_catalog(request)
        next_flow=intake_flow.propose(flow['demand_id'],clarified_text,user['id'],OllamaIntake(),
            external_catalog,field_selections=field_selections,
            preparsed_draft=(preparsed_draft or
                (flow.get('draft') if field_selections and not has_free_text_answer else None)))
        return {'flow':next_flow,'stage':'awaiting_chain_confirmation',
                'note':'已按勾选结果固定字段映射并重新检查目录和关联条件；若字段组合不能安全关联，会明确说明且不会生成部分查询。'}

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
            from execution.security.output_policy import field_classification
            output_fields=[]
            for item in lookup.get('output_fields',[]):
                current=m.store.require('object',item['field_id'])
                output_fields.append({**item,'classification':field_classification(current)})
            unresolved=[item for item in output_fields if item.get('classification')=='unknown']
            allowed_output=all(item.get('classification') in ('public','internal','health','identifier')
                               for item in output_fields) and bool(output_fields)
            validation=flow.get('query_validation') or {}
            expected_outputs=set(lookup.get('field_ids') or [field['id']])
            confirmed_specs=[spec for spec in m.store.list('spec')
                if spec.get('demand_id')==flow.get('demand_id') and spec.get('confirmed')
                and expected_outputs<=normalized_field_output_ids(
                    ((spec.get('specification') or {}).get('execution_constraints') or {}))
                and (spec.get('specification') or {}).get('query_draft')==validation.get('normalized_sql')]
            confirmed_spec=max(confirmed_specs,key=lambda row:(row.get('version',0),row.get('updated_at','')),
                default=None)
            plan_ready=bool(allowed_output and not unresolved and confirmed_spec)
            next_lookup={**lookup,'output_fields':output_fields}
            if unresolved:
                target=unresolved[0]
                target_field=m.store.require('object',target['field_id'])
                table_obj=m.store.require('object',target_field['parent_id'])
                next_lookup.update(field_id=target_field['id'],field_name=target_field.get('name'),
                    table_id=table_obj['id'],table_name=table_obj.get('name'),
                    classification='待确认',classification_basis=None)
            else:
                next_lookup.update(classification=classification,
                    classification_basis=updated_field.get('classification_basis'))
            steps=[]
            for step in flow.get('steps',[]):
                item=dict(step)
                if item.get('capability')=='data_query':
                    item.update(status='awaiting_execution_adapter' if plan_ready else
                        'waiting_field_classification' if unresolved else
                        'waiting_manual_spec_confirmation' if allowed_output else 'blocked',
                        detail=('字段分类已人工确认；继续按已确认SQL和目录关联准备固定方案。'
                            if plan_ready else '已确认一个字段分类；还需逐项确认：'+ '、'.join(item.get('field_name','') for item in unresolved)
                            if unresolved else '所有输出字段分类已登记；请核对预填的查询范围和字段。'
                            if allowed_output else '至少一个输出字段被登记为敏感分类，当前不能直接交付原值。请调整输出字段或定义受控脱敏方案。'))
                steps.append(item)
            next_flow=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,
                'state':'awaiting_execution_plan' if plan_ready else
                    'waiting_field_classification' if unresolved else
                    'waiting_manual_spec_confirmation' if allowed_output else 'attention',
                'spec_id':confirmed_spec['id'] if plan_ready else flow.get('spec_id'),
                'spec_version':confirmed_spec['version'] if plan_ready else flow.get('spec_version'),
                'resources':[{**resource,'review_status':'human_confirmed'} for resource in flow.get('resources',[])]
                    if plan_ready else flow.get('resources',[]),
                'field_lookup':next_lookup,
                'classification_review':{'field_id':field['id'],'field_name':field.get('name'),
                    'classification':classification,'classification_basis':updated_field.get('classification_basis'),
                    'decision':'confirmed','remaining_field_ids':[item['field_id'] for item in unresolved],
                    'actor':user['id'],
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
