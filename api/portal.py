"""Role-scoped user workbench projections and explicit query preparation."""
from datetime import datetime, timedelta, timezone
from time import perf_counter
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from platform_core.storage import DomainError, new_id
from platform_core.access import (require_action, require_resource, can_read_management,
    can_read_execution, can_access_table, require_table_scope, require_plan_scope,
    project_result, level_for, account_permission)
from management.intake.flow import IntakeFlow
from management.catalog.field_library import FieldLibrary
from management.intake.professional import professional_shape
from execution.security.output_policy import field_classification


def create_router(runtime, identity):
    router=APIRouter(prefix='/api/portal',tags=['用户工作台'])
    store=runtime.management.store
    intake=IntakeFlow(runtime)
    def user(request, action=None):
        principal=identity.principal(request)
        if action: require_action(principal,action)
        return principal
    def owned(flow_id, principal):
        flow=store.require('intake_flow',flow_id)
        demand=store.require('demand',flow['demand_id'])
        if not can_read_management(runtime,'demand',demand,principal):
            raise DomainError('无权查看这次查询',403)
        return flow
    def visible(principal):
        demands={d['id'] for d in store.list('demand') if can_read_management(runtime,'demand',d,principal)}
        return [f for f in store.list('intake_flow') if f.get('demand_id') in demands]
    def brief(flow):
        return {k:flow.get(k) for k in ('id','demand_id','request_text','state','query_mode','updated_at','created_by')}
    def query_permission_context(principal, available_tables):
        permission=account_permission(principal)
        return {'level':permission['level'],'visibility':permission['visibility'],
                'allowed_tables':[table['name'] for table in available_tables],
                'scope_limited':permission['level']<4}

    @router.get('/services')
    def services(request:Request):
        principal=user(request);require_resource(principal,'demand')
        builtins=[
            {'id':'fn.age','name':'年龄计算','category':'常用函数','description':'根据出生日期和指定日期计算周岁','status':'可直接使用','input':'出生日期、计算日期'},
            {'id':'fn.date_range','name':'日期范围','category':'常用函数','description':'把自然语言时间范围转换为可执行的起止日期','status':'可直接使用','input':'时间表达'},
            {'id':'fn.stats','name':'统计汇总','category':'常用函数','description':'计算数量、均值、最大值、最小值和分布','status':'可直接使用','input':'数值字段'},
            {'id':'fn.abnormal','name':'异常值判断','category':'常用函数','description':'结合参考范围或规则标记异常记录','status':'可直接使用','input':'结果值、参考范围'},
            {'id':'model.summary','name':'病历摘要','category':'医疗小模型','description':'从现病史、主诉和治疗计划生成带来源摘要','status':'适配器已就绪，等待真实模型','input':'病历文本'},
            {'id':'model.lab','name':'检验结果解释','category':'医疗小模型','description':'结合项目、结果值、单位和参考范围解释异常','status':'适配器已就绪，等待真实模型','input':'检验结果'},
            {'id':'model.risk','name':'风险分层','category':'医疗小模型','description':'根据已授权的结构化指标生成可追溯风险分层','status':'适配器已就绪，等待真实模型','input':'结构化指标'},
        ]
        registered=[]
        for item in runtime.management.store.list('service'):
            source_objects=item.get('source_objects') or []
            in_scope=True
            if source_objects:
                try:
                    from platform_core.access import require_object_scope
                    require_object_scope(principal,store,source_objects)
                except DomainError:in_scope=False
            elif level_for(principal)<4:in_scope=False
            if item.get('lifecycle')=='published' and can_read_management(runtime,'service',item,principal) and in_scope:
                registered.append({'id':item['id'],'name':item.get('name',item['id']),'category':'已发布数据服务','description':item.get('description',''),'status':'已发布','input':'按服务规格'})
        level=level_for(principal)
        doris=runtime.management.catalog.doris
        if level==4 and doris:
            for source,alias,service_id in doris.services():
                _, table=source.tables[alias]
                registered.append({'id':service_id,'name':f'{table} 记录数',
                    'category':'实时统计','description':'实时统计记录数，少于 5 条时隐藏。',
                    'status':'可调用' if doris.status(source.id)=='connected' else '待连接',
                    'input':'无参数','live_call':True})
        minimum={'fn.age':3,'fn.date_range':1,'fn.stats':1,'fn.abnormal':1,
                 'model.summary':3,'model.lab':1,'model.risk':3}
        builtins=[item for item in builtins if level>=minimum[item['id']]]
        return {'services':builtins+registered,
                'openalex_sync':runtime.openalex.status() if runtime.openalex is not None else None,
                'doris_sources':doris.statuses() if level==4 and doris else [],
                'doris_config_error':doris.config_error if level==4 and doris else None,
                'permission':{'level':level,'label':f'{level}级 · '+principal.get('visibility','按账户权限提供服务'),
                              'dataset_tables':principal.get('dataset_tables',[])},
                'composition':{'enabled':True,'message':'确认查询意图后，自动按账户等级和数据范围执行；每一步保留来源、版本和运行记录。'}}

    @router.post('/services/{service_id}/run-live')
    def run_live_service(service_id:str,request:Request):
        principal=user(request,'demand.create')
        if level_for(principal)!=4:
            raise DomainError('当前账户等级无权调用此实时数据服务',403)
        doris=runtime.management.catalog.doris
        service=next((item for item in doris.services() if item[2]==service_id),None) if doris else None
        if service is None:
            raise DomainError('实时服务未发布或已撤回',404)
        source,alias,_=service
        table=next((item for item in runtime.management.catalog.objects(source_id=source.id,kind='table',status='active')
                    if item['name']==alias),None)
        if table is None:
            raise DomainError('实时服务尚未完成目录扫描，请检查数据源和 VPN 连接',503)
        require_table_scope(principal,[table])
        doris.for_table(source.id,alias,[table])
        evidence={'object_kind':'service','object_id':service_id,'object_version':table['version'],
                  'environment':store.environment,'actor':principal['id'],
                  'task_scope':{'source_id':source.id,'table_id':table['id'],'operation':'count'}}
        runtime.management.evidence.record({**evidence,'status':'started'})
        started=perf_counter()
        try:
            count=source.count(alias)
            current=user(request)
            if current['id']!=principal['id'] or level_for(current)!=4:
                raise DomainError('账户权限已变化，未返回服务结果',403)
            latest=store.require('object',table['id'])
            if latest['version']!=table['version'] or latest['status']!='active':
                raise DomainError('数据源结构发生变化，请重新调用服务',409)
        except DomainError as exc:
            runtime.management.evidence.record({**evidence,'status':'failed','error':str(exc)})
            raise
        record=runtime.management.evidence.record({**evidence,'status':'success',
            'latency_ms':round((perf_counter()-started)*1000,2),'suppressed':count is None})
        return {'service_id':service_id,'count':count,'suppressed':count is None,
                'source_id':source.id,'evidence_id':record['id']}
    def catalog(principal, objects=None):
        definitions={r['field_id']:r for r in FieldLibrary(store).items() if r['status']!='retired'}
        if objects is None:
            objects=[o for o in store.list('object') if o.get('status')=='active']
        from collections import defaultdict
        fields_by_table=defaultdict(list)
        for obj in objects:
            if obj.get('kind')=='field':
                fields_by_table[obj.get('parent_id')].append(obj)
        tables=[]
        for table in objects:
            if table.get('kind')!='table' or not can_access_table(principal,table):continue
            fields=[]
            for field in fields_by_table[table['id']]:
                definition=definitions.get(field['id'],{})
                fields.append({'id':field['id'],'name':field['name'],'label':definition.get('label') or field['name'],
                    'definition':definition.get('definition',''),'status':definition.get('status','draft'),
                    'classification':field_classification(field),'type':field.get('structure',{}).get('type','unknown')})
            tables.append({'id':table['id'],'name':table.get('table') or table['name'],'source':table.get('source_id'),
                'connected':intake._source_connection_status(table)=='connected','fields':fields,
                'label':table.get('meaning') or table['name'],
                'physical_name':table.get('structure',{}).get('physical_name')})
        return tables

    @router.get('/overview')
    def overview(request:Request):
        principal=user(request);require_resource(principal,'overview');require_resource(principal,'demand')
        latest={}
        for flow in sorted(visible(principal),key=lambda f:f.get('updated_at','')):
            latest[flow['demand_id']]=flow
        rows=sorted(latest.values(),key=lambda f:f.get('updated_at',''),reverse=True)
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        trend=[]
        for offset in range(6,-1,-1):
            day=today-timedelta(days=offset);count=0
            for flow in rows:
                date=datetime.fromisoformat(flow['updated_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()
                count+=date==day
            trend.append({'date':day.isoformat(),'count':count})
        tables=catalog(principal)
        return {'tables':len(tables),'fields':sum(len(t['fields']) for t in tables),
                'sources':len({t['source'] for t in tables if t['connected']}),
                'completed':sum(f.get('state')=='query_completed' for f in rows),
                'query_count':len(rows),'trend':trend,'recent':[brief(f) for f in rows[:8]],
                'scope':'我的可见查询' if principal['role']=='requester' else '当前角色可见查询'}

    @router.get('/catalog')
    def get_catalog(request:Request, include_relations:bool=False):
        principal=user(request);require_resource(principal,'object')
        if not include_relations:
            return {'tables':catalog(principal)}
        from management.catalog.graph import graph_relationships
        objects=[obj for obj in store.list('object') if obj.get('status')=='active']
        tables=catalog(principal, objects)
        return {'tables':tables, 'relations':graph_relationships(tables, objects, store.list('relation'),
                    intake._has_confirmed_patient_id_link)}

    @router.get('/ai/providers')
    def ai_providers(request:Request):
        principal=user(request,'demand.understand')
        from management.intake.ollama import IntakeLLM
        return IntakeLLM.provider_options()

    @router.get('/queries/{flow_id}')
    def get_query(flow_id:str,request:Request):
        principal=user(request);flow=owned(flow_id,principal)
        flow=intake.reconcile_execution(flow,principal['id'])
        plan=runtime.execution.store.get('plan',flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
        if plan:
            require_plan_scope(principal,runtime,plan)
        elif flow.get('query_draft'):
            shape=professional_shape(flow['query_draft'],store)
            require_table_scope(principal,shape['tables'])
        return {'flow':flow,'can_download':bool(plan and plan.get('circulation_mode')=='download'
                and can_read_execution(runtime,'plan',plan,principal)),
                'demand':store.require('demand',flow['demand_id'])}

    @router.post('/queries')
    async def ask(request:Request):
        principal=user(request,'demand.create');require_action(principal,'demand.understand')
        body=await request.json();text=body.get('text')
        if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise DomainError('请填写 1 至 4000 字的查询需求')
        from management.intake.semantic_query import prepare
        available=catalog(principal)
        flow=await run_in_threadpool(prepare,runtime,text.strip(),principal['id'],available,
                                     provider=body.get('provider'),
                                     permission_context=query_permission_context(principal,available))
        shape=flow.get('professional_preview') or {}
        require_table_scope(principal,shape.get('tables') or ([shape['table']] if shape.get('table') else []))
        return {'flow':flow}

    @router.post('/queries/{flow_id}/clarify')
    async def clarify_query(flow_id:str,request:Request):
        principal=user(request,'demand.understand');flow=owned(flow_id,principal)
        body=await request.json()
        if flow.get('query_mode')!='semantic' or flow['state']!='awaiting_chain_confirmation':
            raise DomainError('当前查询不在操作澄清阶段',409)
        if body.get('expected_version')!=flow['version']:raise DomainError('查询已变化，请刷新',409)
        answers=body.get('answers')
        if not isinstance(answers,list) or len(answers)!=len(flow['field_bindings']):raise DomainError('请回答必要的查询问题')
        byid={a.get('intent_id'):a.get('answer') for a in answers if isinstance(a,dict)}
        additions=[]
        for binding in flow['field_bindings']:
            answer=byid.get(binding['intent_id'])
            if not isinstance(answer,str) or not 1<=len(answer.strip())<=2000:raise DomainError('请填写查询口径')
            additions.append(binding['question']+'：'+answer.strip())
        text=flow['request_text']+'\n用户补充：'+'；'.join(additions)
        if len(text)>12000:raise DomainError('补充内容过长，请重新描述需求')
        from management.intake.semantic_query import prepare
        available=catalog(principal)
        updated=await run_in_threadpool(prepare,runtime,text,principal['id'],available,flow,
                                        provider=flow.get('ai_provider'),
                                        permission_context=query_permission_context(principal,available))
        shape=updated.get('professional_preview') or {}
        require_table_scope(principal,shape.get('tables') or ([shape['table']] if shape.get('table') else []))
        return {'flow':updated}

    @router.post('/professional/preview')
    async def preview(request:Request):
        principal=user(request,'demand.create');require_resource(principal,'object')
        body=await request.json();shape=professional_shape(body.get('sql'),store)
        require_table_scope(principal,shape['tables'])
        definitions=FieldLibrary(store).confirmed()
        return {'sql':shape['sql'],'outputs':[{'name':f['name'],'label':definitions[f['id']]['label'],
            'classification':field_classification(f)} for f in shape['output_fields']],
            'filters':shape['filters'],'max_rows':shape['max_rows'],'tables':[t['name'] for t in shape['tables']],
            'order_by':shape['order_by']}

    @router.post('/professional')
    async def save_professional(request:Request):
        principal=user(request,'demand.create');require_resource(principal,'object')
        body=await request.json()
        if body.get('confirmed') is not True:raise DomainError('请先核对查询范围与输出字段')
        shape=professional_shape(body.get('sql'),store)
        require_table_scope(principal,shape['tables'])
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date();end=today+timedelta(days=7)
        policy={'scope':{'kind':'lookup','filters':shape['filters']},'time_field':None,'output_mode':'object',
                'field_outputs':[f['id'] for f in shape['output_fields']],'circulation_modes':['controlled_compute'],
                'quality_min_complete':1,'max_rows':shape['max_rows'],'min_group_size':5}
        definitions=FieldLibrary(store).confirmed()
        title='查询 '+ '、'.join(definitions[f['id']]['label'] for f in shape['output_fields'])
        draft={'title':title,'entry':'goal','object_scope':'按已确认 SQL 的条件筛选，最多返回 '+str(shape['max_rows'])+' 条',
               'time_start':today.isoformat(),'time_end':end.isoformat(),'deadline':end.isoformat(),
               'purpose':'自主数据查询','quality':'完整','delivery':'对象级','execution_constraints':policy,
               'cost':'','attachments':[],'service_ids':[],'query_draft':shape['sql']}
        demand=runtime.management.intake.save({**draft,'portal_mode':'professional'},principal['id'])
        resources=[{**store.require('object',oid),'review_status':'human_confirmed'}
                   for oid in dict.fromkeys([t['id'] for t in shape['tables']]+shape['fields'])]
        flow=store.save('intake_flow',new_id('FLOW'),{'demand_id':demand['id'],'created_by':principal['id'],
            'request_text':title,'query_mode':'professional','draft':draft,'resources':resources,
            'state':'waiting_manual_spec_confirmation','query_draft':shape['sql'],'field_bindings':[],
            'clarification_questions':[],'query_validation':{'status':'valid_pending_human_confirmation','normalized_sql':shape['sql'],'errors':[]},
            'steps':[{'id':'query','capability':'data_query','label':'查询数据','status':'waiting_manual_spec_confirmation'}],
            'output_preference':'catalog_field','execution_authorized':False,'professional_preview':shape},0,principal['id'],'用户确认专业查询草案')
        return {'flow':flow}

    @router.post('/queries/{flow_id}/confirm')
    @router.post('/professional/{flow_id}/confirm')
    async def confirm_professional(flow_id:str,request:Request):
        principal=user(request,'demand.confirm');flow=owned(flow_id,principal);body=await request.json()
        if flow.get('query_mode') not in ('professional','semantic') or flow['state']!='waiting_manual_spec_confirmation':
            raise DomainError('当前查询不在规格确认阶段',409)
        if body.get('confirmed') is not True or body.get('expected_version')!=flow['version']:
            raise DomainError('请刷新后确认本次查询范围',409)
        if flow.get('query_mode')=='semantic':
            from management.intake.semantic_query import compile_intent
            checked=compile_intent(flow['semantic_intent'],store)
            if not checked or checked['sql']!=flow['query_draft']:raise DomainError('查询意图与草案不一致，请重新理解查询',409)
        professional_shape(flow['query_draft'],store)
        checked=professional_shape(flow['query_draft'],store)
        require_table_scope(principal,checked['tables'])
        flow={**flow,'resources':[{**r,'review_status':'human_confirmed'} for r in flow['resources']]}
        with store.atomic():
            demand=store.require('demand',flow['demand_id'])
            spec=runtime.management.intake.confirm(demand['id'],{'expected_version':demand['version'],
                'confirmations':{'content':True,'attachments':True,'gaps':True,'execution_constraints':True},
                'note':'在用户工作台核对专业查询字段、条件、排序和返回条数','query_sql_confirmed':True,'query_draft':flow['query_draft']},principal['id'])
            from execution.query.intake_plan import create_field_plan
            view,plan,shape=create_field_plan(runtime,flow,spec,principal['id'])
            updated=store.save('intake_flow',flow['id'],{**flow,'state':'awaiting_plan_confirmation',
                'spec_id':spec['id'],'spec_version':spec['version'],'execution_plan_id':plan['id'],
                'execution_plan_version':plan['version'],'execution_view_id':view['id'],
                'execution_plan_preview':{'output_columns':[f['name'] for f in shape['output_fields']],
                    'output_description':'、'.join(f['name'] for f in shape['output_fields']),
                    'table':' + '.join(t['name'] for t in shape['tables']),'filters':shape['filters'],'output_mode':'object'},
                'steps':[{'id':'query','capability':'data_query','label':'查询数据','status':'awaiting_plan_confirmation'}]},
                flow['version'],principal['id'],'专业查询规格已确认并建立固定方案')
        return {'flow':updated}

    @router.post('/queries/{flow_id}/execute')
    async def confirm_intent_and_execute(flow_id:str,request:Request):
        principal=user(request,'demand.create')
        flow=owned(flow_id,principal)
        body=await request.json()
        if body.get('confirmed') is not True:
            raise DomainError('请先确认页面展示的查询意图')
        if body.get('expected_version')!=flow['version']:
            raise DomainError('查询意图已变化，请刷新后确认',409)
        if flow.get('state') not in ('waiting_manual_spec_confirmation','awaiting_plan_confirmation'):
            raise DomainError('当前查询尚未形成可执行意图；请先补充必要的意图说明',409)
        if flow.get('query_validation',{}).get('status') not in ('valid_pending_human_confirmation','valid'):
            raise DomainError('查询范围未通过结构校验，请重新描述需求',409)
        shape=professional_shape(flow.get('query_draft'),store)
        require_table_scope(principal,shape['tables'])
        confirmed=FieldLibrary(store).confirmed()
        if any(field_id not in confirmed for field_id in shape['fields']):
            raise DomainError('当前查询引用了字段库外或结构待复核的字段，请联系管理员更新目录',409)
        resources=[{**store.require('object',oid),'review_status':'human_confirmed'}
                   for oid in dict.fromkeys([table['id'] for table in shape['tables']]+shape['fields'])]
        flow_for_plan={**flow,'resources':resources}
        with store.atomic():
            if flow.get('state')=='awaiting_plan_confirmation':
                spec=store.require('spec',flow.get('spec_id',''),flow.get('spec_version'))
                if (spec.get('demand_id')!=flow['demand_id']
                        or not spec.get('query_draft_confirmed')
                        or (spec.get('specification') or {}).get('query_draft')!=flow.get('query_draft')):
                    raise DomainError('已生成方案与当前查询意图不一致，请重新描述并确认',409)
                plan=runtime.execution.get(flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
                view=runtime.execution.store.get('view',flow.get('execution_view_id',''))
                if not view or plan.get('spec_id')!=spec['id'] or plan.get('spec_version')!=spec['version']:
                    raise DomainError('固定方案与已确认规格不匹配，请重新生成',409)
                if runtime.execution.store.require('plan_state',plan['id']).get('status')!='confirmed':
                    runtime.execution.confirm(plan['id'],plan['version'],principal['id'])
            else:
                demand=store.require('demand',flow['demand_id'])
                spec=store.get('spec',flow.get('spec_id') or 'SPEC-'+demand['id'])
                if demand.get('state')=='confirmed':
                    if (not spec or not spec.get('query_draft_confirmed')
                            or (spec.get('specification') or {}).get('query_draft')!=flow.get('query_draft')):
                        raise DomainError('已确认规格与当前查询不一致，请重新描述需求',409)
                else:
                    spec=runtime.management.intake.confirm(demand['id'],{
                        'expected_version':demand['version'],
                        'confirmations':{'content':True,'attachments':True,'gaps':True,
                                         'execution_constraints':True,'quality_requirement_reviewed':True},
                        'note':'用户确认查询意图；字段定义已在字段库统一确认，按当前账户等级执行。',
                        'query_sql_confirmed':True,'query_draft':flow['query_draft']},principal['id'])
                from execution.query.intake_plan import create_field_plan
                view,plan,shape=create_field_plan(runtime,flow_for_plan,spec,principal['id'])
                if runtime.execution.store.require('plan_state',plan['id']).get('status')!='confirmed':
                    runtime.execution.confirm(plan['id'],plan['version'],principal['id'])
            shape=professional_shape(flow.get('query_draft'),store)
            require_plan_scope(principal,runtime,plan)
            plan=runtime.execution.store.require('plan',plan['id'],plan['version'])
            policy=plan.get('execution_policy') or {}
            try:
                deadline=datetime.fromisoformat(str(policy.get('deadline_at','')).replace('Z','+00:00'))
            except (TypeError,ValueError):
                raise DomainError('查询方案期限无效，不能自动执行',409)
            if deadline.tzinfo is None:raise DomainError('查询方案期限缺少时区，不能自动执行',409)
            expires_at=min(datetime.now(timezone.utc)+timedelta(hours=1),deadline)
            if expires_at<=datetime.now(timezone.utc):raise DomainError('查询方案已过期，请重新描述需求',409)
            live_query=any(table['source_id'].startswith('doris-') for table in shape['tables'])
            reusable_states=('queued','running') if live_query else ('queued','running','success')
            existing_job=None
            for candidate in runtime.execution.store.list('job'):
                if (candidate.get('plan_id')==plan['id'] and candidate.get('plan_version')==plan['version']
                        and candidate.get('actor')==principal['id']
                        and candidate.get('state') in reusable_states):
                    candidate_grant=runtime.execution.store.get('grant',candidate.get('grant_id',''))
                    if (candidate_grant and candidate_grant.get('authorization_basis')=='account_level'
                            and candidate_grant.get('account_level')==level_for(principal)):
                        _,grant_error=runtime.execution.security.assess(candidate_grant['id'],plan,
                            principal['id'],plan['purpose'])
                        if grant_error is None:existing_job=candidate
            if existing_job:
                job=existing_job
                grant=runtime.execution.store.require('grant',job['grant_id'])
            else:
                grant=runtime.execution.security.grant_for_account_level({
                    'subject':principal['id'],'purpose':plan['purpose'],'plan_id':plan['id'],
                    'plan_version':plan['version'],'expires_at':expires_at.isoformat(),
                    'mode':plan['output_mode'],'max_rows':policy['max_rows'],
                    'min_group_size':policy['min_group_size']},principal['id'],level_for(principal))
                require_plan_scope(principal,runtime,plan)
                job=runtime.execution.submit(plan['id'],plan['version'],grant['id'],principal['id'])
            health=(grant.get('health_output_authorization') or {}).get('field_ids',[])
            identifiers=(grant.get('identifier_output_authorization') or {}).get('field_ids',[])
            fields=sorted(set(health+identifiers))
            auth=(grant.get('health_output_authorization') or grant.get('identifier_output_authorization') or {})
            steps=[]
            for step in flow.get('steps',[]):
                item=dict(step)
                if item.get('capability')=='data_query':
                    item.update(status='query_queued',detail='查询意图已确认；按账户等级校验范围、登记审计授权并自动进入执行队列。')
                steps.append(item)
            updated=store.save('intake_flow',flow_id,{**flow_for_plan,'spec_id':spec['id'],
                'spec_version':spec['version'],'execution_plan_id':plan['id'],
                'execution_plan_version':plan['version'],'execution_view_id':view['id'],
                'execution_plan_preview':{'output_columns':[field['name'] for field in shape['output_fields']],
                    'output_description':'、'.join(field['name'] for field in shape['output_fields']),
                    'table':' + '.join(table['name'] for table in shape['tables']),
                    'filters':shape['filters'],'output_mode':plan['output_mode']},
                'state':'query_queued','steps':steps,'plan_confirmed_by':principal['id'],
                'plan_confirmed_version':plan['version'],'execution_authorized':True,'grant_id':grant['id'],
                'authorization_request':{'id':auth.get('request_id') or grant['id'],'status':'approved',
                    'requester_id':principal['id'],'requester_role':'account_level',
                    'account_level':level_for(principal),'authorization_basis':'account_level',
                    'plan_id':plan['id'],'plan_version':plan['version'],'purpose':plan['purpose'],
                    'field_ids':fields,'grant_id':grant['id'],'expires_at':grant['expires_at']},
                'authorization_decision':{'value':'yes','actor':'account-level-policy',
                    'role':'account_level','account_level':level_for(principal),'field_ids':fields,
                    'expires_at':grant['expires_at']},
                'job_id':job['id'],'job_state':job['state'],'execution_submitted_by':principal['id'],
                'execution_submitted_at':job.get('created_at') or job['updated_at']},
                flow['version'],principal['id'],'用户确认查询意图后按账户等级自动执行')
        updated=intake.reconcile_execution(updated,principal['id'])
        return {'flow':updated,'job':{'id':job['id'],'state':job['state']},
                'permission':{'level':level_for(principal),'visibility':principal.get('visibility')},
                'note':'已按当前账户数据范围自动执行；结果会按账户等级脱敏或汇总。'}
    return router
