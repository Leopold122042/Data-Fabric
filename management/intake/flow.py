"""Auditable, non-executable demand workflow proposals and gated preparation."""
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from management.store import DomainError, new_id

CAPABILITY_LABELS = {
    'catalog_search': '检索平台目录',
    'connected_source_discovery': '检查已连接数据源结构',
    'data_query': '查询数据',
    'trend_analysis': '分析指标趋势',
}


def validate_query_draft(sql, catalog):
    if sql is None:return {'status':'missing','errors':['模型未提供SQL草案；需补充查询意图和可用字段。'],'normalized_sql':None}
    try:
        import sqlglot
        from sqlglot import exp
        parsed=sqlglot.parse(sql,read='duckdb')
    except Exception:
        return {'status':'rejected','errors':['SQL无法解析。'],'normalized_sql':None}
    if len(parsed)!=1 or not isinstance(parsed[0],(exp.Select,exp.Union,exp.Intersect,exp.Except)):
        return {'status':'rejected','errors':['只允许单条只读SELECT查询。'],'normalized_sql':None}
    tree=parsed[0]
    selects=[tree] if isinstance(tree,exp.Select) else list(tree.find_all(exp.Select))
    if any(isinstance(item,(exp.Star,exp.Column)) and (isinstance(item,exp.Star) or item.is_star)
           for select in selects for item in select.expressions):
        return {'status':'rejected','errors':['禁止SELECT *，请明确列出输出字段。'],'normalized_sql':None}
    # A SQL table must resolve to a registered physical/logical table, not a field name.
    allowed_tables={str(x.get('table') or x.get('name') or '').lower() for x in catalog
                    if x.get('kind') in ('table','connected_external_table')}
    tables={t.name.lower() for t in tree.find_all(exp.Table)}
    if not tables or not tables<=allowed_tables:
        return {'status':'rejected','errors':['SQL引用了目录之外或尚未匹配的逻辑表。'],'normalized_sql':None}
    allowed_columns={str(column).lower() for x in catalog
                     if str(x.get('table') or x.get('name') or '').lower() in tables
                     for column in (x.get('columns') or [])}
    allowed_columns|={str(x.get('name') or '').lower() for x in catalog
                      if x.get('kind')=='field' and str(x.get('table') or '').lower() in tables}
    columns={c.name.lower() for c in tree.find_all(exp.Column) if c.name}
    if not columns<=allowed_columns:
        return {'status':'rejected','errors':['SQL引用了目录中不存在的字段。'],'normalized_sql':None}
    limit=tree.args.get('limit')
    if limit:
        value=limit.expression
        try:
            if int(value.this)>200:return {'status':'rejected','errors':['查询行数上限为200。'],'normalized_sql':None}
        except (AttributeError,TypeError,ValueError):return {'status':'rejected','errors':['LIMIT必须为不超过200的整数。'],'normalized_sql':None}
    else:
        tree=tree.limit(200)
    return {'status':'valid_pending_human_confirmation','errors':[],
            'normalized_sql':tree.sql(dialect='duckdb')}


class IntakeFlow:
    def __init__(self, runtime):
        self.runtime=runtime
        self.store=runtime.management.store

    def _catalog_metadata(self, external_catalog=()):
        # Give the local model structure metadata only. Never inspect source rows.
        candidates=[]
        for row in self.store.list('object'):
            if row.get('status')=='deleted':continue
            structure=row.get('structure') or {}
            candidates.append({'id':row['id'],'kind':row.get('kind','object'),'name':row.get('name'),'table':row.get('table'),
                'parent_id':row.get('parent_id'),
                'source_id':row.get('source_id'),'meaning':row.get('meaning'),'domain':row.get('domain'),
                'data_type':row.get('data_type') or structure.get('type'),'status':row.get('status'),
                'classification':row.get('classification'),
                'columns':row.get('columns') or structure.get('columns',[])})
        for row in self.store.list('service'):
            candidates.append({'id':row['id'],'kind':'service','name':row.get('name'),'description':row.get('description'),
                'purpose':row.get('purpose'),'output':row.get('output'),'lifecycle':row.get('lifecycle')})
        for row in external_catalog:
            candidates.append({'id':row['id'],'kind':'connected_external_table','name':row.get('name'),
                'table':row['id'],'source_id':'connected-cloud','columns':row.get('columns',[]),'types':row.get('types',[])})
        return candidates[:300]

    def _source_connection_status(self, candidate, external_catalog=()):
        """Resolve metadata-source availability without opening or reading source rows."""
        source_id=str(candidate.get('source_id') or '')
        table_id=str(candidate.get('table') or candidate.get('id') or '')
        external_ids={str(row.get('id')) for row in external_catalog if row.get('id')}
        if table_id in external_ids:
            return 'connected'
        if source_id=='connected-cloud':return 'connected' if external_ids else 'cloud'
        if source_id in ('his','lis','pacs','iot'):
            import medsim
            return 'connected' if (Path(medsim.DATA_DIR)/(source_id+'.db')).is_file() else 'unavailable'
        if source_id.startswith('cloud-'):
            return 'cloud'
        registration=self.store.get('source',source_id) if source_id else None
        registry=getattr(self.runtime.management.catalog,'sources',None)
        if registration and registry and registration.get('connector') in registry.factories:
            try:
                return 'connected' if registry.resolve_path(registration.get('path','')).is_file() else 'unavailable'
            except (DomainError, OSError, ValueError):
                return 'unavailable'
        return 'unknown'

    def _person_id_lookup(self, text, catalog):
        match=re.search(r'(?:查询|查找|搜索|检索|获取|找出|查看)\s*([\u4e00-\u9fff]{2,4})\s*的\s*(?:(?:患者|病人|住院|病案)\s*)?(?:patient[_\s-]*id|id|编号|标识|号码)',
                        str(text or ''),re.I)
        if not match:return None
        person_name=match.group(1)
        tables=[row for row in catalog if row.get('kind')=='table' and row.get('status')=='active'
                and {'name','patient_id'}<={str(column).casefold() for column in row.get('columns',[])}]
        tables.sort(key=lambda row:(row.get('source_id')!='his',row.get('name','')))
        for table in tables:
            name_field=next((row for row in catalog if row.get('kind')=='field' and row.get('table')==table.get('table')
                             and str(row.get('name','')).casefold()=='name'),None)
            id_field=next((row for row in catalog if row.get('kind')=='field' and row.get('table')==table.get('table')
                           and str(row.get('name','')).casefold()=='patient_id'),None)
            if not name_field or not id_field:continue
            safe_name=person_name.replace("'","''")
            sql=f"SELECT patient_id FROM {table.get('table') or table['name']} WHERE name = '{safe_name}' LIMIT 200"
            reason='目录规则匹配到同时含 name 和 patient_id 的表，可按姓名精确查找并返回患者ID。'
            return {'name':person_name,'table':table,'name_field':name_field,'id_field':id_field,
                    'sql':sql,'reason':reason}
        return None

    def _admission_time_lookup(self, text, catalog):
        match=re.search(r'(?:查询|查找|搜索|检索|获取|找出|查看)\s*([\u4e00-\u9fff]{2,4}?)\s*(?:的\s*)?入院时间',
                        str(text or ''))
        if not match:return None
        person_name=match.group(1)
        tables=[row for row in catalog if row.get('kind')=='table' and row.get('status')=='active']
        patient=next((row for row in tables if {'name','patient_id'}<={str(c).casefold() for c in row.get('columns',[])}),None)
        visit=next((row for row in tables if {'patient_id','admission_time','vtype'}<={str(c).casefold() for c in row.get('columns',[])}
                    and row.get('source_id')==patient.get('source_id')) if patient else None,None)
        if not patient or not visit:return None
        def field(table,name):
            return next((row for row in catalog if row.get('kind')=='field' and row.get('status')=='active'
                         and row.get('parent_id')==table.get('id')
                         and str(row.get('name','')).casefold()==name.casefold()),None)
        name_field=field(patient,'name');patient_key=field(patient,'patient_id')
        visit_key=field(visit,'patient_id');admission_field=field(visit,'admission_time');visit_type=field(visit,'vtype')
        if not all((name_field,patient_key,visit_key,admission_field,visit_type)):return None
        safe_name=person_name.replace("'","''")
        sql=(f"SELECT v.admission_time FROM {visit.get('table') or visit['name']} AS v "
             f"JOIN {patient.get('table') or patient['name']} AS p ON v.patient_id = p.patient_id "
             f"WHERE p.name = '{safe_name}' AND v.vtype = '住院' LIMIT 200")
        return {'name':person_name,'patient_table':patient,'visit_table':visit,'name_field':name_field,
                'patient_key':patient_key,'visit_key':visit_key,'admission_field':admission_field,
                'visit_type_field':visit_type,'sql':sql}

    def repair_admission_time_query(self, flow, actor):
        """Rebuild an exact-name admission-time draft from table/field metadata only."""
        if (flow.get('state') not in ('completed','waiting_manual_spec_confirmation')
                or flow.get('confirmation',{}).get('confirmed') is not True
                or flow.get('spec_id') or flow.get('execution_plan_id')):return flow
        catalog=self._catalog_metadata()
        lookup=self._admission_time_lookup(flow.get('request_text'),catalog)
        if not lookup:return flow
        demand=self.store.require('demand',flow['demand_id'])
        constraints={'scope':{'kind':'rows','filters':[
            {'field':lookup['name_field']['id'],'op':'=','value':lookup['name']},
            {'field':lookup['visit_type_field']['id'],'op':'=','value':'住院'}], 'all_rows':False},
            'time_field':lookup['admission_field']['id'],'output_mode':'object',
            'circulation_modes':['controlled_compute'],'quality_min_complete':1,
            'max_rows':200,'min_group_size':5}
        object_scope=(f"按患者姓名精确匹配 {lookup['name']}，关联患者与就诊记录，筛选住院记录并返回入院时间；"
                      '姓名、患者ID仅用于匹配和关联，不作为交付字段。')
        proposal={**demand,'expected_version':demand['version'],'object_scope':object_scope,
            'delivery':'对象级','execution_constraints':constraints}
        needs_update=any(demand.get(key)!=proposal.get(key)
                         for key in ('object_scope','delivery','execution_constraints'))
        updated=(self.runtime.management.intake.apply_draft(flow['demand_id'],
            {'expected_version':demand['version'],'proposal':proposal,'model':flow.get('model')},actor)['demand']
            if needs_update else demand)
        validation=validate_query_draft(lookup['sql'],catalog)
        from execution.security.output_policy import field_classification
        classification=field_classification(lookup['admission_field'])
        classification_blocker=None if classification!='unknown' else (
            f"目录字段“{lookup['visit_table'].get('name')}.admission_time”分类为“{lookup['admission_field'].get('classification') or '未登记'}”，"
            '请确认是否按“健康信息”登记。')
        execution_blocker=None if classification in ('public','internal','unknown') else (
            f"字段已分类为“{lookup['admission_field'].get('classification')}”；现有输出规则不允许直接交付该类原始字段。"
            '规格确认不会授予查询权限，也不会执行查询。')
        source_status=self._source_connection_status(lookup['visit_table'])
        resources=[]
        previous={item.get('id'):item for item in flow.get('resources',[])}
        for table in (lookup['patient_table'],lookup['visit_table']):
            accepted=previous.get(table['id'],{}).get('review_status')=='human_confirmed'
            resources.append({**table,'review_status':'human_confirmed' if accepted else
                'model_recommended_pending_human_confirmation',
                'reason':'字段目录确认该表参与患者姓名与入院时间查询。'})
        before_constraints=demand.get('execution_constraints') or {}
        old_time_id=before_constraints.get('time_field')
        old_time=next((item for item in catalog if item.get('id')==old_time_id),None)
        adjustments=[
            {'label':'数据表','before':', '.join(item.get('name','') for item in flow.get('resources',[])) or '未匹配',
             'after':f"{lookup['patient_table'].get('name')} + {lookup['visit_table'].get('name')}（按 patient_id 关联）"},
            {'label':'时间字段','before':f"{old_time.get('table')}.{old_time.get('name')}" if old_time else '未匹配',
             'after':f"{lookup['visit_table'].get('name')}.admission_time"},
            {'label':'查询范围','before':'全部记录',
             'after':f"姓名精确匹配 {lookup['name']}；就诊类型=住院"},
            {'label':'输出方式','before':before_constraints.get('output_mode') or '未设置',
             'after':'对象级；仅返回入院时间'},
            {'label':'数据时间范围','before':'未变更',
             'after':f"{updated.get('time_start')} 至 {updated.get('time_end')}（沿用当前需求窗口）"}
        ]
        if flow.get('auto_adjusted_spec'):
            history=self.store.history('intake_flow',flow['id'])
            original=next((item.get('spec_adjustments') for item in reversed(history)
                           if item.get('auto_adjusted_spec') and item.get('spec_adjustments')),[])
            original_before={item.get('label'):item.get('before') for item in original}
            for item in adjustments:
                if item.get('label') in original_before:
                    item['before']=original_before[item['label']]
        steps=[
            {'id':'step-1','capability':'catalog_search','label':CAPABILITY_LABELS['catalog_search'],
             'reason':'根据字段元数据将错误的 patient.birth_date 候选修正为 visit.admission_time，并补充 patient 与 visit 的 patient_id 关联。',
             'depends_on':[],'status':'completed','candidates':resources,
             'detail':'目录已找到姓名、住院类型和入院时间字段；仅 visit 表候选需要在当前窗口确认。'},
            {'id':'step-2','capability':'connected_source_discovery','label':CAPABILITY_LABELS['connected_source_discovery'],
             'reason':'检查 patient 与 visit 表所属本地医疗数据源连接状态。','depends_on':['step-1'],
             'status':'completed' if source_status=='connected' else 'blocked',
             'connection_kind':None if source_status=='connected' else 'local',
             'detail':'HIS 数据源已连接，沿用当前连接；无需重新登录，也未读取源数据行。' if source_status=='connected'
                      else 'HIS 数据源当前不可用，无法继续。'},
            {'id':'step-3','capability':'data_query','label':CAPABILITY_LABELS['data_query'],
             'reason':'按姓名和住院类型筛选，并使用入院时间作为数据时间字段。','depends_on':['step-2'],
             'status':'waiting_manual_spec_confirmation' if validation['status']=='valid_pending_human_confirmation' and source_status=='connected' else 'blocked',
             'detail':('字段分类已确认；规格确认后按现行规则复核输出，未授权或执行查询。'
                       if execution_blocker else
                       '已生成并通过目录规则校验的只读关联查询；确认规格后自动建立固定方案草案。')}]
        state='waiting_manual_spec_confirmation' if steps[1]['status']=='completed' and steps[2]['status']=='waiting_manual_spec_confirmation' else 'attention'
        note='已自动把出生日期字段改为住院记录的入院时间，增加姓名精确筛选和 patient_id 关联，并将汇总输出改为仅返回入院时间。'
        revised_draft={**flow.get('draft',{}),'object_scope':object_scope,'delivery':'对象级',
            'execution_constraints':constraints,'query_draft':lookup['sql'],
            'time_start':updated.get('time_start'),'time_end':updated.get('time_end'),
            'purpose':updated.get('purpose'),'quality':updated.get('quality'),'deadline':updated.get('deadline'),
            'uncertain_fields':[item for item in flow.get('draft',{}).get('uncertain_fields',[])
                                if item not in ('入院时间',)]}
        changes={'steps':steps,'resources':resources,'query_draft':lookup['sql'],
            'query_validation':validation,'draft':revised_draft,'demand_version':updated['version'],
            'state':state,'auto_adjusted_spec':True,'auto_prepare_admission_time_plan':True,
            'spec_adjustments':adjustments,'query_output_blocker':classification_blocker,
            'execution_output_blocker':execution_blocker,
            'output_preference':'admission_time','draft_revision_note':note,
            'notes':list(flow.get('notes',[]))+([note] if note not in flow.get('notes',[]) else [])}
        if all(flow.get(key)==value for key,value in changes.items()):return flow
        return self.store.save('intake_flow',flow['id'],{**flow,**changes},flow['version'],actor,
            '按字段目录自动修正入院时间查询草案；等待人工确认必要调整')

    def _complete_query_intent(self, text, draft, catalog):
        query_intent=bool(re.search(r'查询|查找|搜索|检索|获取|找出|查看|统计|分析',str(text or '')))
        lookup=self._person_id_lookup(text,catalog)
        if not query_intent and not lookup:return draft,None
        proposed={item.get('capability'):dict(item) for item in draft.get('capability_steps',[])
                  if item.get('capability')}
        defaults={
            'catalog_search':{'reason':'按已登记目录元数据寻找匹配的表和字段。','inputs':[]},
            'connected_source_discovery':{'reason':'检查匹配表所属数据源是否已连接；已连接来源直接沿用。','inputs':[]},
            'data_query':{'reason':'在目录候选和执行范围确认后准备受控查询。','inputs':[]},
        }
        for capability,value in defaults.items():proposed.setdefault(capability,{'capability':capability,**value})
        draft['capability_steps']=list(proposed.values())
        if lookup:
            table=lookup['table'];name_field=lookup['name_field']
            draft['title']=draft.get('title') or f"查询{lookup['name']}的患者ID"
            draft['object_scope']=f"在 {table.get('name')} 表按姓名精确匹配；不按日期筛选；只输出患者伪名ID。"
            draft['delivery']='对象级'
            draft['query_draft']=lookup['sql']
            draft['execution_constraints']={'scope':{'kind':'lookup','filters':[
                {'field':name_field['id'],'op':'=','value':lookup['name']}]},
                'time_field':None,'output_mode':'object','identifier_output':'pseudonymized',
                'circulation_modes':['controlled_compute'],'quality_min_complete':1,'max_rows':200,'min_group_size':5}
            draft['resource_recommendations']=[{'id':table['id'],'reason':lookup['reason']}]
            draft['uncertain_fields']=[item for item in draft.get('uncertain_fields',[])
                                       if not re.search(r'(?:具体.*id|患者标识字段)',str(item),re.I)]
            proposed['catalog_search']['reason']='已用目录规则补全可按姓名匹配 patient_id 的表。'
            proposed['connected_source_discovery']['reason']=(
                f"检查 {table.get('name')} 表所属 {table.get('source_id') or '已连接'} 数据源连接状态。")
            proposed['data_query']['reason']='按姓名精确匹配并只返回 HMAC 患者伪名ID。'
            draft['capability_steps']=list(proposed.values())
        return draft,lookup

    def continue_incomplete_query(self, flow, actor):
        """Repair a confirmed catalog-only flow when metadata can safely complete a person-ID lookup draft."""
        if (flow.get('state')!='completed'
                or flow.get('confirmation',{}).get('confirmed') is not True
                or flow.get('spec_id') or flow.get('execution_plan_id')):return flow
        catalog=self._catalog_metadata()
        draft,lookup=self._complete_query_intent(flow.get('request_text'),dict(flow.get('draft') or {}),catalog)
        if not lookup:return flow
        demand=self.store.require('demand',flow['demand_id'])
        proposal={**demand,'expected_version':demand['version'],
            'object_scope':draft['object_scope'],'delivery':'对象级',
            'execution_constraints':draft['execution_constraints']}
        needs_demand_update=any(demand.get(key)!=proposal.get(key)
                                for key in ('object_scope','delivery','execution_constraints'))
        updated=(self.runtime.management.intake.apply_draft(flow['demand_id'],
            {'expected_version':demand['version'],'proposal':{**draft,**proposal},'model':flow.get('model')},actor)['demand']
            if needs_demand_update else demand)
        validation=validate_query_draft(lookup['sql'],catalog)
        table=lookup['table'];source_status=self._source_connection_status(table)
        prior_resource=next((item for item in flow.get('resources',[]) if item.get('id')==table.get('id')),None)
        resource={**table,'reason':lookup['reason'],'review_status':
            'human_confirmed' if prior_resource and prior_resource.get('review_status')=='human_confirmed'
            else 'model_recommended_pending_human_confirmation'}
        candidate={**resource}
        old_names=[item.get('name') or item.get('id') for item in flow.get('resources',[])]
        replacement_needed=not prior_resource
        note=(f"已按姓名精确查询规则修正目录候选：改用含 name 与 patient_id 字段的 {table.get('name')} 表；"
              f"原候选 {', '.join(old_names)} 无法可靠完成姓名匹配。" if replacement_needed else
              f"已按姓名精确查询规则确认目录候选：{table.get('name')} 表同时登记了 name 与 patient_id 字段。")
        catalog_reason=(f"目录规则发现 {table.get('name')} 表包含姓名和 patient_id 字段；"
                        f"原先推荐的 {', '.join(old_names)} 候选不适合按姓名匹配。"
                        if replacement_needed else
                        f"目录规则确认 {table.get('name')} 表同时包含姓名和 patient_id 字段。")
        steps=[
            {'id':'step-1','capability':'catalog_search','label':CAPABILITY_LABELS['catalog_search'],
             'reason':catalog_reason,
             'depends_on':[],'status':'completed','candidates':[candidate],
             'detail':'已依据已登记表/字段元数据修正候选；等待你在规格弹窗中确认该表。未读取源数据行。'
                      if resource['review_status']!='human_confirmed' else
                      f"已依据已登记表/字段元数据确认并沿用 {table.get('name')} 表。未读取源数据行。"},
            {'id':'step-2','capability':'connected_source_discovery','label':CAPABILITY_LABELS['connected_source_discovery'],
             'reason':f"核对 {table.get('name')} 表所属 {table.get('source_id') or '已连接'} 数据源连接状态。",'depends_on':['step-1'],
             'status':'completed' if source_status=='connected' else 'waiting_authorization' if source_status=='cloud' else 'blocked',
             'connection_kind':'cloud' if source_status=='cloud' else 'local' if source_status!='connected' else None,
             'detail':f"{table.get('source_id') or '数据源'} 来源已连接，沿用现有连接继续；无需重新登录，也未读取源数据行。" if source_status=='connected'
                      else '该候选来源尚未连接，需先处理数据源连接。'},
            {'id':'step-3','capability':'data_query','label':CAPABILITY_LABELS['data_query'],
             'reason':lookup['reason'],'depends_on':['step-2'],
             'status':'waiting_manual_spec_confirmation' if validation['status']=='valid_pending_human_confirmation' and source_status=='connected' else 'blocked',
             'detail':'已预填姓名精确匹配的只读 SQL 和伪名ID输出；确认任务规格后平台会自动建立固定方案草案。查询尚未提交。'}]
        state='waiting_manual_spec_confirmation' if steps[1]['status']=='completed' and steps[2]['status']=='waiting_manual_spec_confirmation' else 'attention'
        revised_draft={**draft,'time_start':updated.get('time_start'),'time_end':updated.get('time_end'),
            'purpose':updated.get('purpose'),'quality':updated.get('quality'),'deadline':updated.get('deadline')}
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'resources':[resource],
            'query_draft':lookup['sql'],'query_validation':validation,'draft':revised_draft,
            'demand_version':updated['version'],'state':state,'auto_prepared_spec':True,
            'auto_prepare_fixed_plan':True,'candidate_correction_pending':resource['review_status']!='human_confirmed',
            'candidate_correction':{'replaced':old_names,'selected':table.get('name'),'reason':note} if replacement_needed else None,
            'draft_revision_note':note,
            'notes':list(flow.get('notes',[]))+[note]},flow['version'],actor,
            '目录规则补全遗漏的查询步骤并修正患者姓名映射候选；等待人工确认规格')

    def reconcile_connected_sources(self, flow, actor, external_catalog=()):
        """Clear stale login waits when a confirmed candidate uses an available source."""
        confirmed_resources=[resource for resource in flow.get('resources',[])
                             if resource.get('review_status')=='human_confirmed']
        sources_available=not confirmed_resources or all(
            self._source_connection_status(resource,external_catalog)=='connected' for resource in confirmed_resources)
        if flow.get('state')=='preparing' and flow.get('steps') and all(
                step.get('status')=='completed' for step in flow.get('steps',[])) and sources_available:
            return self.store.save('intake_flow',flow['id'],{**flow,'state':'completed'},flow['version'],actor,
                '平台元数据链路已完成；继续办理时无需重复登录')
        if flow.get('state')!='waiting_authorization':return flow
        steps=[dict(step) for step in flow.get('steps',[])]
        changed=False
        accepted={resource.get('id'):resource for resource in flow.get('resources',[])
                  if resource.get('review_status')=='human_confirmed'}
        for step in steps:
            if step.get('capability')!='connected_source_discovery' or step.get('status')!='waiting_authorization':continue
            candidates=[candidate for candidate in step.get('candidates',[])
                        if candidate.get('id') in accepted]
            if not candidates:candidates=list(accepted.values())
            if not candidates:continue
            statuses=[self._source_connection_status(candidate,external_catalog) for candidate in candidates]
            if all(status=='connected' for status in statuses):
                step.update(status='completed',label=CAPABILITY_LABELS['connected_source_discovery'],connection_kind=None,
                    detail='已确认的数据表来自平台可用的数据源，沿用当前连接与目录结构继续办理，无需重新登录。本步骤只检查目录和连接状态，不代表查询授权。')
                changed=True
            elif any(status in ('unavailable','unknown') for status in statuses):
                step.update(status='blocked',label=CAPABILITY_LABELS['connected_source_discovery'],connection_kind='local',
                    detail='所选数据源当前不可用或未登记连接配置。请检查平台资源目录中的来源状态；这不是云盘登录问题。')
                changed=True
        if not changed:return flow
        if any(step.get('status') in ('gap','blocked') for step in steps):state='attention'
        elif any(step.get('status')=='waiting_authorization' for step in steps):state='waiting_authorization'
        elif any(step.get('status')=='waiting_manual_spec_confirmation' for step in steps):state='waiting_manual_spec_confirmation'
        else:state='completed'
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'state':state},flow['version'],actor,
            '核对已确认数据源连接状态；可用的医疗数据源跳过重复登录')

    def reconcile_execution(self, flow, actor):
        """Mirror the controlled job state into the visible intake workflow."""
        job_id=flow.get('job_id')
        if (not job_id and flow.get('execution_authorized') and flow.get('grant_id')
                and flow.get('execution_plan_id')):
            authorization_actor=(flow.get('authorization_decision') or {}).get('actor')
            prior_jobs=[job for job in self.runtime.execution.store.list('job')
                        if job.get('plan_id')==flow.get('execution_plan_id')
                        and job.get('plan_version')==flow.get('execution_plan_version')
                        and job.get('grant_id')==flow.get('grant_id')
                        and (not authorization_actor or job.get('actor')==authorization_actor)]
            if prior_jobs:
                prior_jobs.sort(key=lambda job:job.get('updated_at',''))
                latest=prior_jobs[-1]
                flow={**flow,'job_id':latest['id'],'execution_submitted_by':latest.get('actor'),
                      'execution_submitted_at':latest.get('updated_at')}
                job_id=latest['id']
        if not job_id:return flow
        job=self.runtime.execution.store.get('job',job_id)
        if not job:return flow
        state_map={'queued':'query_queued','running':'query_running','success':'query_completed',
                   'failed':'query_failed','cancelled':'query_failed','interrupted':'query_failed'}
        next_state=state_map.get(job.get('state'))
        if not next_state:return {**flow,'job_state':job.get('state')}
        if flow.get('state')==next_state and flow.get('delivery_id')==job.get('delivery_id'):
            return {**flow,'job_state':job.get('state'),'query_error':job.get('error')}
        steps=[]
        detail={'query_queued':'受控查询已进入队列，等待本地执行器。',
                'query_running':'本地执行器正在按已确认方案查询。',
                'query_completed':'受控查询完成，结果已保存为平台内待读取交付。',
                'query_failed':'查询未完成：'+str(job.get('error') or '执行器未交付结果。')}
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item.update(status=next_state,detail=detail[next_state])
            steps.append(item)
        changes={'state':next_state,'job_state':job.get('state'),'steps':steps,
                 'delivery_id':job.get('delivery_id'),'query_error':job.get('error')}
        return self.store.save('intake_flow',flow['id'],{**flow,**changes},flow['version'],actor,
            '同步受控查询执行状态与交付标记')

    def external_catalog(self, request):
        """Read schema names only from this browser's existing external session."""
        from external_api import COOKIE, _lock, _sessions
        with _lock:
            session=_sessions.get(request.cookies.get(COOKIE))
            credentials=getattr(session,'cloud_credentials',None) if session else None
            username=credentials[0] if credentials else None
        if not username:return []
        from external_discovery import definitions
        return [{'id':row['id'],'name':row.get('name') or row['id'],'columns':row.get('columns',[]),
                 'types':row.get('types',[])} for row in definitions(username)]

    def propose(self, demand_id, text, actor, model_client, external_catalog=()):
        demand=self.store.require('demand',demand_id)
        if demand.get('created_by')!=actor and actor!='demo-platform':raise DomainError('无权修改他人需求',403)
        catalog=self._catalog_metadata(external_catalog)
        draft=model_client.extract(text,catalog)
        draft,person_lookup=self._complete_query_intent(text,draft,catalog)
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        assumptions=[]
        if not draft.get('title'):draft['title']=text.strip()[:80]
        if not draft.get('entry'):draft['entry']='goal'
        if not draft.get('object_scope'):draft['object_scope']='按需求描述匹配目录资源；范围由已确认执行条件约束'
        if not draft.get('time_end'):draft['time_end']=today.isoformat();assumptions.append('时间窗终点默认今天')
        if not draft.get('time_start'):
            match=re.search(r'近\s*(\d+)\s*天',text)
            days=int(match.group(1)) if match else (7 if '近一周' in text else 90 if '近三个月' in text else 30)
            draft['time_start']=(today-timedelta(days=days-1)).isoformat()
            assumptions.append(f'时间窗起点默认近{days}天')
        if not draft.get('deadline'):
            draft['deadline']=(today+timedelta(days=7)).isoformat();assumptions.append('交付时限默认7天后')
        if not draft.get('purpose'):
            draft['purpose']='指标趋势分析' if 'trend_analysis' in [x.get('capability') for x in draft.get('capability_steps',[])] else '按需求进行数据查询'
            assumptions.append('用途由需求意图推断')
        if not draft.get('quality'):draft['quality']='完整'
        if not draft.get('delivery'):
            draft['delivery']='对象级' if any(word in text for word in ('明细','逐条','每条记录')) else '汇总'
            assumptions.append('交付形态默认汇总' if draft['delivery']=='汇总' else '交付形态按明细意图推断')
        if assumptions:
            draft['uncertain_fields']=list(dict.fromkeys(draft.get('uncertain_fields',[])+assumptions))
        if not draft.get('execution_constraints'):
            recommended=set(x.get('id') for x in draft.get('resource_recommendations',[]))
            tables={x.get('table') for x in catalog if x.get('id') in recommended
                    and x.get('kind') in ('table','field') and x.get('table')}
            fields=[x for x in catalog if x.get('kind')=='field' and x.get('table') in tables]
            time_field=next((x for x in fields if re.search(r'(date|time|日期|时间)',str(x.get('name','')),re.I)
                             or re.search(r'(date|time)',str(x.get('data_type','')),re.I)),None)
            if time_field:
                output_mode='object' if draft['delivery']=='对象级' else 'aggregate'
                draft['execution_constraints']={'scope':{'kind':'rows','filters':[],'all_rows':True},
                    'time_field':time_field['id'],'output_mode':output_mode,'circulation_modes':['controlled_compute'],
                    'quality_min_complete':1,'max_rows':200,'min_group_size':5}
                assumptions.append('执行范围默认使用匹配表的全部记录并受时间窗限制；可在确认前修改')
                draft['uncertain_fields']=list(dict.fromkeys(draft.get('uncertain_fields',[])+[assumptions[-1]]))
        # Store model interpretation into the demand draft so its fields are prefilled,
        # editable and versioned before any task specification is confirmed.
        saved=self.runtime.management.intake.apply_draft(demand_id,{'expected_version':demand['version'],
            'proposal':draft,'model':model_client.model},actor)
        demand=saved['demand']
        sql_validation=validate_query_draft(draft.get('query_draft'),catalog)
        by_id={x['id']:x for x in catalog}
        resources=[{**by_id[item['id']],'reason':item['reason'],'review_status':'model_recommended_pending_human_confirmation'}
                   for item in draft.get('resource_recommendations',[]) if item['id'] in by_id]
        proposed=draft.get('capability_steps',[])
        capabilities=list(dict.fromkeys(item['capability'] for item in proposed))
        if any(cap in capabilities for cap in ('data_query','trend_analysis')) and 'catalog_search' not in capabilities:
            capabilities.insert(0,'catalog_search')
        order={name:index for index,name in enumerate(('catalog_search','connected_source_discovery','data_query','trend_analysis'))}
        capabilities.sort(key=lambda cap:order[cap])
        reason_by_cap={item['capability']:item.get('reason','') for item in proposed}
        inputs_by_cap={item['capability']:item.get('inputs',[]) for item in proposed}
        steps=[]
        for index,cap in enumerate(capabilities,1):
            steps.append({'id':f'step-{index}','capability':cap,'label':CAPABILITY_LABELS[cap],
                'reason':reason_by_cap.get(cap,'规则要求先检索并校验资源目录。' if cap=='catalog_search' else ''),
                'inputs':inputs_by_cap.get(cap,[]),'depends_on':[f'step-{index-1}'] if index>1 else [],'status':'proposed'})
        if not steps:raise DomainError('模型未提出可识别的平台能力步骤，请补充需求描述')
        for step in steps:
            if step['capability']=='catalog_search':
                step['candidates']=resources
                step['status']='matched' if resources else 'gap'
                step['detail']='本地模型仅依据登记的表/字段/服务元数据推荐；待人工确认，未读取源数据行。' if resources else '当前模型未从已登记元数据中找到候选项。'
            elif step['capability']=='connected_source_discovery':
                step['status']='matched'
                step['detail']='等待人工确认目录候选；确认后按所选表所属数据源检查连接。已连接的医疗数据源直接继续，只有尚未连接的外部来源才需要连接；不会读取源数据行。'
            elif step['capability'] in ('data_query','trend_analysis'):
                step['status']='waiting_spec'
                step['detail']='需完成资源匹配并人工确认任务规格后，才能建立并运行查询方案。'
        record=self.store.save('intake_flow',new_id('FLOW'),{
            'demand_id':demand_id,'demand_version':demand['version'],'created_by':actor,
            'request_text':text,'model':model_client.model,'draft':draft,'steps':steps,
            'resources':resources,'query_draft':draft.get('query_draft'),'query_validation':sql_validation,
            'state':'awaiting_chain_confirmation',
            'confirmation':None,'execution_authorized':False,
            'auto_prepared_spec':bool(person_lookup),'auto_prepare_fixed_plan':bool(person_lookup),
            'notes':['模型只提出能力链路建议；未生成规格、未执行调用、未授予权限。',
                     '字段/表结构变更历史追踪按用户要求暂不纳入。']},0,actor,'生成待确认需求能力链路')
        return record

    def confirm(self, flow_id, body, actor, external_catalog=()):
        with self.store.atomic():
            flow=self.store.require('intake_flow',flow_id)
            demand=self.store.require('demand',flow['demand_id'])
            if demand.get('created_by')!=actor and actor!='demo-platform':raise DomainError('无权确认他人需求链路',403)
            if flow['state']!='awaiting_chain_confirmation':raise DomainError('该链路不在待确认状态',409)
            if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化',409)
            if body.get('confirmed') is not True:raise DomainError('需明确确认完整链路')
            note=str(body.get('note','')).strip()
            if not note:raise DomainError('请记录链路确认说明')
            candidate_ids={candidate.get('id') for step in flow.get('steps',[])
                           for candidate in step.get('candidates',[]) if candidate.get('id')}
            reviewed_ids=body.get('reviewed_resource_ids')
            accepted_ids=body.get('accepted_resource_ids')
            if not isinstance(reviewed_ids,list) or not isinstance(accepted_ids,list):
                raise DomainError('请在确认窗口逐项核对目录候选')
            if any(not isinstance(resource_id,str) for resource_id in reviewed_ids+accepted_ids):
                raise DomainError('目录候选编号格式无效')
            if set(reviewed_ids)!=candidate_ids or not set(accepted_ids)<=candidate_ids:
                raise DomainError('目录候选确认内容与当前链路不一致，请刷新后重试',409)
            accepted_ids=set(accepted_ids)
            steps=[]
            blocked=False
            for step in flow['steps']:
                item=dict(step)
                if item.get('candidates'):
                    item['candidates']=[{**candidate,'review_status':
                        'human_confirmed' if candidate.get('id') in accepted_ids else 'human_rejected'}
                        for candidate in item['candidates']]
                    if any(candidate.get('id') in accepted_ids for candidate in item['candidates']):
                        item['status']='completed'
                        item['detail']=f"人工确认采用 {sum(candidate.get('id') in accepted_ids for candidate in item['candidates'])} 项目录候选。"
                    else:
                        item['status']='blocked';blocked=True
                        item['detail']='已逐项复核目录候选，但没有选中可用资源。请重新描述需求或补充目录后重新生成链路。'
                elif item.get('capability')=='connected_source_discovery':
                    selected=[resource for resource in flow.get('resources',[]) if resource.get('id') in accepted_ids]
                    statuses=[self._source_connection_status(resource,external_catalog) for resource in selected]
                    if selected and all(status=='connected' for status in statuses):
                        item['status']='completed';item.pop('connection_kind',None)
                        item['detail']='所选数据表来自平台可用的数据源，沿用现有连接继续办理，无需重新登录。本步骤只检查目录和连接状态，不代表查询授权。'
                    elif any(status=='cloud' for status in statuses):
                        item['status']='waiting_authorization';item['connection_kind']='cloud'
                        item['action_url']='/static/platform/connections.html?connect=1#extDiscoveryPanel'
                        item['detail']='所选候选来自云盘，但当前会话未连接。请连接云盘后重新检索目录。'
                    else:
                        item['status']='blocked';blocked=True;item['connection_kind']='local'
                        item['detail']='所选数据源当前不可用或未登记连接配置。请检查平台资源目录中的来源状态；这不是云盘登录问题。'
                elif item['status']=='matched':item['status']='completed'
                elif item['status']=='gap':item['status']='blocked';blocked=True
                elif item['status']=='waiting_connection':item['status']='waiting_authorization'
                elif item['status']=='waiting_spec':item['status']='waiting_manual_spec_confirmation'
                steps.append(item)
            resources=[{**resource,'review_status':'human_confirmed'} for resource in flow.get('resources',[])
                       if resource.get('id') in accepted_ids]
            state='completed'
            if blocked:state='attention'
            elif any(s['status']=='waiting_authorization' for s in steps):state='waiting_authorization'
            elif any(s['status']=='waiting_manual_spec_confirmation' for s in steps):state='waiting_manual_spec_confirmation'
            return self.store.save('intake_flow',flow_id,{**flow,'steps':steps,'resources':resources,'state':state,
                'confirmation':{'actor':actor,'note':note,'confirmed':True,
                    'reviewed_resource_ids':sorted(candidate_ids),
                    'accepted_resource_ids':sorted(accepted_ids)},
                'execution_authorized':False},flow['version'],actor,'人工确认完整能力链路；仅推进元数据准备')

    def specification_confirmed(self, flow, spec, actor):
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item['capability'] in ('data_query','trend_analysis'):
                item['status']='awaiting_execution_adapter'
                item['detail']='只读SQL已通过目录规则并经人工确认，随任务规格版本保存。要执行查询，请在“方案与执行”中基于该规格建立逻辑视图和固定方案，再确认方案并单独申请授权。模型SQL不直接执行。'
            steps.append(item)
        needs_execution=any(step.get('capability') in ('data_query','trend_analysis') for step in steps)
        waiting_authorization=any(step.get('status')=='waiting_authorization' for step in steps)
        state='waiting_authorization' if waiting_authorization else 'awaiting_execution_plan' if needs_execution else 'completed'
        resources=[{**resource,'review_status':'human_confirmed'} for resource in flow.get('resources',[])]
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'resources':resources,
            'state':state,'spec_id':spec['id'],'spec_version':spec['version'],
            'query_draft_confirmed_by':actor,'execution_authorized':False,
            'candidate_correction_pending':False},flow['version'],actor,
            '人工确认任务规格；平台继续自动准备固定方案草案')

    def revise_for_pseudonymized_id(self, flow_id, body, actor):
        """Revise a confirmed aggregate/name lookup only after an explicit human action."""
        flow=self.store.require('intake_flow',flow_id)
        demand=self.store.require('demand',flow['demand_id'])
        if demand.get('created_by')!=actor and actor!='demo-platform':raise DomainError('无权修订他人需求规格',403)
        if flow.get('state')!='awaiting_execution_plan':raise DomainError('当前流程不在执行方案准备阶段',409)
        spec=self.store.require('spec',flow.get('spec_id',''),flow.get('spec_version'))
        if not spec.get('confirmed') or spec['version']!=body.get('expected_spec_version'):
            raise DomainError('已确认规格版本变化，请刷新后重试',409)
        if body.get('expected_version')!=flow['version']:raise DomainError('需求链路版本已变化，请刷新后重试',409)
        current_constraints=spec.get('specification',{}).get('execution_constraints') or {}
        if (current_constraints.get('scope',{}).get('kind')=='lookup'
                and current_constraints.get('output_mode')=='object'
                and spec.get('specification',{}).get('delivery')=='对象级'):
            return self._prepare_pseudonym_plan(flow,spec,actor)
        if body.get('confirmed') is not True or not str(body.get('note','')).strip():
            raise DomainError('请明确确认对象级伪名输出和姓名精确筛选，并填写确认说明')
        validation=flow.get('query_validation') or {}
        sql=spec.get('specification',{}).get('query_draft')
        if validation.get('status')!='valid_pending_human_confirmation' or sql!=validation.get('normalized_sql'):
            raise DomainError('已确认SQL与规则校验版本不一致，不能修订方案',409)
        from execution.query.intake_plan import query_shape
        approved=[resource['id'] for resource in flow.get('resources',[])
                  if resource.get('review_status')=='human_confirmed']
        shape=query_shape(sql,self.store,approved)
        if len(shape['filters'])!=1 or str(shape['filter_names'][0]).casefold() not in (
                'name','patient_name','fullname','full_name','姓名','患者姓名'):
            raise DomainError('当前SQL不是单一姓名精确查询；请先修订并确认查询草案',409)
        constraints=dict(spec['specification'].get('execution_constraints') or {})
        constraints.update(scope={'kind':'lookup','filters':shape['filters']},time_field=None,
                           output_mode='object',identifier_output='pseudonymized',
                           max_rows=min(int(constraints.get('max_rows',200)),shape['max_rows']))
        proposal={**demand,'expected_version':demand['version'],'object_scope':
            '在人工确认的数据表中按姓名字段精确匹配；不按出生日期或住院时间筛选；只返回患者伪名ID。',
            'delivery':'对象级','time_end':demand.get('deadline') or demand.get('time_end'),
            'execution_constraints':constraints}
        note=str(body['note']).strip()
        with self.store.atomic():
            updated=self.runtime.management.intake.save(proposal,actor)
            revised=self.runtime.management.intake.confirm(demand['id'],{
                'expected_version':updated['version'],
                'confirmations':{'content':True,'attachments':True,'gaps':True,'execution_constraints':True},
                'query_sql_confirmed':True,'query_draft':sql,
                'note':note+'；新规格仅保留姓名精确筛选，移除出生日期数据筛选，结果为对象级HMAC患者伪名ID。'},actor)
            flow=self.specification_confirmed(flow,revised,actor)
        return self._prepare_pseudonym_plan(flow,revised,actor)

    def _prepare_pseudonym_plan(self, flow, revised, actor):
        from execution.query.intake_plan import create_plan
        view,plan,shape=create_plan(self.runtime,flow,revised,actor)
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item['status']='awaiting_plan_confirmation'
                item['detail']='已自动预填只读逻辑视图与固定方案草案。只用已确认的姓名精确筛选，输出本地密钥生成的HMAC伪名ID；未读取源数据行、未执行查询。请先确认方案，再另行申请授权。'
            steps.append(item)
        specification=revised['specification']
        current_draft={**flow.get('draft',{}),
            'object_scope':specification.get('object_scope'),
            'delivery':specification.get('delivery'),
            'time_end':specification.get('time_end'),
            'execution_constraints':specification.get('execution_constraints')}
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,
            'draft':current_draft,
            'draft_revision_note':'已按人工确认修订：姓名精确匹配；日期仅限制执行有效期，不作为患者数据筛选；输出患者伪名ID。',
            'state':'awaiting_plan_confirmation','execution_plan_id':plan['id'],
            'demand_version':revised['demand_version'],
            'execution_plan_version':plan['version'],'execution_view_id':view['id'],
            'execution_plan_preview':{'name':plan['name'],'plan_id':plan['id'],'version':plan['version'],
                'source_id':shape['table'].get('source_id'),'table':shape['table'].get('table') or shape['table'].get('name'),
                'filters':shape['filters'],'filter_names':shape['filter_names'],
                'output_columns':['patient_token'],'output_mode':'object',
                'transform':'HMAC-SHA256 · installation secret','reads_source_rows':False},
            'output_preference':'pseudonymized_patient_id','execution_authorized':False},
            flow['version'],actor,'人工确认修订规格并生成待确认伪名查询方案；未执行查询')

    def _prepare_admission_time_plan(self, flow, specification, actor):
        from execution.security.output_policy import field_classification
        catalog=self._catalog_metadata()
        lookup=self._admission_time_lookup(flow.get('request_text'),catalog)
        if not lookup:raise DomainError('目录元数据无法重建入院时间字段和患者关联，暂不能生成方案',409)
        classification=field_classification(lookup['admission_field'])
        if classification not in ('public','internal'):
            raise DomainError('入院时间字段尚未登记为可交付的公开或内部级别；请先复核目录字段分类',409)
        constraints=specification.get('specification',{}).get('execution_constraints') or {}
        if constraints.get('scope',{}).get('filters')!=[
            {'field':lookup['name_field']['id'],'op':'=','value':lookup['name']},
            {'field':lookup['visit_type_field']['id'],'op':'=','value':'住院'}]:
            raise DomainError('已确认规格的姓名或住院类型筛选与目录查询草案不一致',409)
        field_ids=[lookup['name_field']['id'],lookup['patient_key']['id'],lookup['visit_key']['id'],
                   lookup['visit_type_field']['id'],lookup['admission_field']['id']]
        view_id='VIEW-'+flow['id']+'-ADMISSION-TIME'
        old_view=self.runtime.execution.store.get('view',view_id)
        view_body={'id':view_id,'expected_version':old_view['version'] if old_view else 0,
            'name':'患者入院时间查询 · '+flow['id'],'fields':field_ids,
            'output_fields':[lookup['admission_field']['id']],
            'joins':[[lookup['patient_key']['id'],lookup['visit_key']['id']]],
            'filters':[],'group_by':[],'output_mode':'object'}
        view=self.runtime.execution.queries.define(view_body,actor)
        plan_id='PLAN-'+flow['id']+'-ADMISSION-TIME'
        old_plan=self.runtime.execution.store.get('plan',plan_id)
        plan=self.runtime.execution.plan({'id':plan_id,'expected_version':old_plan['version'] if old_plan else 0,
            'name':'患者入院时间受控查询 · '+flow['id'],'spec_id':specification['id'],
            'spec_version':specification['version'],'output_mode':'object',
            'circulation_mode':'controlled_compute','quality_min_complete':1,
            'nodes':[{'id':'admission-time','operation':'view','view_id':view['id'],
                      'depends_on':[],'params':{}}],'output_node':'admission-time'},actor)
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item['status']='awaiting_plan_confirmation'
                item['detail']='固定方案已按姓名、住院类型和入院时间自动预填。未读取源数据行；请确认方案后再单独申请授权。'
            steps.append(item)
        filters=[{'field':lookup['name_field']['id'],'op':'=','value':lookup['name']},
                 {'field':lookup['visit_type_field']['id'],'op':'=','value':'住院'}]
        draft={**flow.get('draft',{}),'execution_constraints':constraints,
            'object_scope':specification['specification'].get('object_scope'),
            'delivery':'对象级'}
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'draft':draft,
            'state':'awaiting_plan_confirmation','spec_id':specification['id'],
            'spec_version':specification['version'],'execution_plan_id':plan['id'],
            'execution_plan_version':plan['version'],'execution_view_id':view['id'],
            'execution_plan_preview':{'name':plan['name'],'plan_id':plan['id'],'version':plan['version'],
                'source_id':lookup['visit_table'].get('source_id'),'table':
                    lookup['patient_table'].get('name')+' + '+lookup['visit_table'].get('name'),
                'filters':filters,'filter_names':['name','vtype'],'output_columns':['admission_time'],
                'output_description':'仅返回入院时间；不返回姓名或患者ID',
                'output_mode':'object','transform':'按已确认字段分类控制查看','reads_source_rows':False},
            'execution_authorized':False,'output_preference':'admission_time',
            'auto_adjusted_spec':True,'auto_prepare_admission_time_plan':True},flow['version'],actor,
            '规格确认后自动生成入院时间固定方案草案；未执行查询')
