"""Role-scoped user workbench projections and explicit query preparation."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from platform_core.storage import DomainError, new_id
from platform_core.access import require_action, require_resource, can_read_management, can_read_execution
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
    def catalog():
        definitions={r['field_id']:r for r in FieldLibrary(store).items() if r['status']!='retired'}
        objects=[o for o in store.list('object') if o.get('status')=='active']
        tables=[]
        for table in objects:
            if table.get('kind')!='table':continue
            fields=[]
            for field in objects:
                if field.get('kind')!='field' or field.get('parent_id')!=table['id']:continue
                definition=definitions.get(field['id'],{})
                fields.append({'id':field['id'],'name':field['name'],'label':definition.get('label') or field['name'],
                    'definition':definition.get('definition',''),'status':definition.get('status','draft'),
                    'classification':field_classification(field),'type':field.get('structure',{}).get('type','unknown')})
            tables.append({'id':table['id'],'name':table.get('table') or table['name'],'source':table.get('source_id'),
                'connected':intake._source_connection_status(table)=='connected','fields':fields})
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
        tables=catalog() if principal['role'] in ('platform','requester','auditor') else []
        return {'tables':len(tables),'fields':sum(len(t['fields']) for t in tables),
                'sources':len({t['source'] for t in tables if t['connected']}),
                'completed':sum(f.get('state')=='query_completed' for f in rows),
                'query_count':len(rows),'trend':trend,'recent':[brief(f) for f in rows[:8]],
                'scope':'我的可见查询' if principal['role']=='requester' else '当前角色可见查询'}

    @router.get('/catalog')
    def get_catalog(request:Request):
        principal=user(request);require_resource(principal,'object')
        return {'tables':catalog()}

    @router.get('/queries/{flow_id}')
    def get_query(flow_id:str,request:Request):
        principal=user(request);flow=owned(flow_id,principal)
        flow=intake.reconcile_execution(flow,principal['id'])
        plan=runtime.execution.store.get('plan',flow.get('execution_plan_id',''),flow.get('execution_plan_version'))
        return {'flow':flow,'can_download':bool(plan and plan.get('circulation_mode')=='download'
                and can_read_execution(runtime,'plan',plan,principal)),
                'demand':store.require('demand',flow['demand_id'])}

    @router.post('/queries')
    async def ask(request:Request):
        principal=user(request,'demand.create');require_action(principal,'demand.understand')
        body=await request.json();text=body.get('text')
        if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise DomainError('请填写 1 至 4000 字的查询需求')
        from management.intake.semantic_query import prepare
        flow=await run_in_threadpool(prepare,runtime,text.strip(),principal['id'],catalog())
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
        updated=await run_in_threadpool(prepare,runtime,text,principal['id'],catalog(),flow)
        return {'flow':updated}

    @router.post('/professional/preview')
    async def preview(request:Request):
        principal=user(request,'demand.create');require_resource(principal,'object')
        body=await request.json();shape=professional_shape(body.get('sql'),store)
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
    return router
