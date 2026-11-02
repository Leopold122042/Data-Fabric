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
        from management.intake.ollama import OllamaIntake
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
                         if needs_sql and not auto_plan and not auto_admission_plan else
                         '任务规格已确认；平台已自动生成固定方案草案，待你确认。尚未授权或执行查询。'
                         if auto_plan or auto_admission_plan else '任务规格已确认；目录检索链路已完成，未执行数据查询或授予权限。')
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
            if item.get('state') not in ('awaiting_execution_authorization','authorization_declined'):continue
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

    @router.post('/intake/flows/{flow_id}/revise-pseudonymized-id')
    async def revise_pseudonymized_id(flow_id:str,request:Request):
        user=who(request,action='demand.confirm');require_action(user,'plan.create')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        result=intake_flow.revise_for_pseudonymized_id(flow_id,await request.json(),user['id'])
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
                item['status']='waiting_authorization';item['connection_kind']='execution'
                item['detail']='固定方案已确认。下一步需单独登记本地执行授权；授权前不会读取患者数据，也不会提交查询。'
            steps.append(item)
        saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,
            'state':'awaiting_execution_authorization','plan_confirmed_by':user['id'],
            'plan_confirmed_version':plan['version'],'execution_authorized':False},
            flow['version'],user['id'],'人工确认固定执行方案；等待单独授权')
        grants=matching_intake_grants(plan,user['id'])
        visible={**saved,'existing_execution_grant':
            ({'id':grants[0]['id'],'expires_at':grants[0]['expires_at']} if grants else None)}
        return {'flow':visible,'plan':plan,'note':'方案已确认；执行授权仍需单独申请，查询尚未提交。'}

    @router.post('/intake/flows/{flow_id}/execution-authorization')
    async def intake_execution_authorization(flow_id:str,request:Request):
        user=who(request,action='grant.create')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化，请刷新后重试',409)
        if flow.get('state') not in ('awaiting_execution_authorization','authorization_declined'):
            raise DomainError('当前流程没有待处理的执行授权',409)
        decision=body.get('decision')
        if decision not in ('yes','no'):raise DomainError('请选择是或否')
        if decision=='no':
            plan=runtime.execution.get(flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
            matches=matching_intake_grants(plan,user['id'])
            if matches:require_action(user,'grant.revoke')
            revoked=[]
            for grant in matches:
                runtime.execution.security.revoke(grant['id'],user['id']);revoked.append(grant['id'])
            steps=[dict(step) for step in flow.get('steps',[])]
            for step in steps:
                if step.get('capability')=='data_query':
                    step.update(status='authorization_declined',detail='用户选择暂不授权；未登记授权，也未提交查询。可稍后重新选择。')
            saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,'state':'authorization_declined',
                'execution_authorized':False,'authorization_decision':{'value':'no','actor':user['id'],'at':datetime.now(timezone.utc).isoformat()},
                'grant_id':None,'revoked_grant_ids':revoked},flow['version'],user['id'],'用户选择暂不授权；撤销匹配授权且未执行查询')
            return {'flow':saved,'note':'已记录暂不授权；匹配的现有授权已撤销，没有提交查询。'}
        if flow.get('execution_authorized') or flow.get('grant_id'):
            raise DomainError('本流程已经登记授权，请直接查看当前下一步',409)
        plan_id=flow.get('execution_plan_id');plan_version=flow.get('execution_plan_version')
        if not plan_id or not plan_version or not flow.get('plan_confirmed_by'):
            raise DomainError('固定方案尚未确认，不能登记授权',409)
        plan=runtime.execution.get(plan_id,plan_version);runtime.execution.current(plan)
        policy=plan.get('execution_policy') or {}
        try:deadline=datetime.fromisoformat(str(policy.get('deadline_at','')).replace('Z','+00:00'))
        except (TypeError,ValueError):raise DomainError('固定方案交付时限无效，不能登记授权',409)
        if deadline.tzinfo is None:raise DomainError('固定方案交付时限缺少时区，不能登记授权',409)
        expires_at=min(datetime.now(timezone.utc)+timedelta(hours=1),deadline)
        if expires_at<=datetime.now(timezone.utc):raise DomainError('固定方案已超过交付时限，请先修订规格和方案',409)
        existing=matching_intake_grants(plan,user['id'])
        grant=existing[0] if existing else runtime.execution.security.grant({'subject':user['id'],'purpose':plan['purpose'],
            'plan_id':plan['id'],'plan_version':plan['version'],'expires_at':expires_at.isoformat(),
            'mode':plan['output_mode'],'max_rows':policy['max_rows'],'min_group_size':policy['min_group_size']},user['id'])
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item.update(status='awaiting_query_submission',detail='本次授权已登记并绑定固定方案；查询仍未提交。请在下一步单独确认提交。')
            steps.append(item)
        saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,'state':'awaiting_query_submission',
            'execution_authorized':True,'grant_id':grant['id'],'authorization_reused':bool(existing),
            'authorization_decision':{'value':'yes','actor':user['id'],
                'at':datetime.now(timezone.utc).isoformat(),'expires_at':grant['expires_at']}},
            flow['version'],user['id'],'用户确认并登记本地固定方案执行授权；查询仍未提交')
        return {'flow':saved,'grant':{'id':grant['id'],'expires_at':grant['expires_at'],'reused':bool(existing)},
                'note':'已关联并确认本次授权；没有自动执行。请单独确认后提交受控查询。'}

    @router.post('/intake/flows/{flow_id}/submit-query')
    async def submit_intake_query(flow_id:str,request:Request):
        user=who(request,action='plan.run')
        flow=m.store.require('intake_flow',flow_id);own('demand',flow['demand_id'],user)
        body=await request.json()
        if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化，请刷新后重试',409)
        if flow.get('state')!='awaiting_query_submission':raise DomainError('当前流程没有待提交的受控查询',409)
        if body.get('confirmed') is not True:raise DomainError('需明确确认后才会提交查询')
        if not flow.get('execution_authorized') or not flow.get('grant_id'):
            raise DomainError('尚未登记本次执行授权，不能提交查询',403)
        plan=runtime.execution.get(flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
        runtime.execution.current(plan)
        with runtime.execution.lock:
            existing=[item for item in runtime.execution.store.list('job')
                      if item.get('plan_id')==plan['id'] and item.get('plan_version')==plan['version']
                      and item.get('grant_id')==flow['grant_id'] and item.get('actor')==user['id']]
            existing.sort(key=lambda item:item.get('updated_at',''))
            job=existing[-1] if existing else runtime.execution.submit(plan['id'],plan['version'],flow['grant_id'],user['id'])
            steps=[]
            for step in flow.get('steps',[]):
                item=dict(step)
                if item.get('capability')=='data_query':
                    item.update(status='query_queued',detail='已由人工单独提交，任务已进入受控执行队列。')
                steps.append(item)
            saved=m.store.save('intake_flow',flow_id,{**flow,'steps':steps,'state':'query_queued',
                'job_id':job['id'],'job_state':job['state'],'execution_submitted_by':user['id'],
                'execution_submitted_at':job.get('created_at') or job['updated_at']},flow['version'],user['id'],'用户单独确认并提交受控查询')
        saved=intake_flow.reconcile_execution(saved,user['id'])
        return {'flow':saved,'job':{'id':job['id'],'state':job['state']},
                'note':'受控查询已单独提交；结果将在此流程进度中更新。'}

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
                'model':OllamaIntake().model},user['id'])
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
