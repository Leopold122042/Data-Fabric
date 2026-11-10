"""Build a fixed, non-executing plan from a human-confirmed intake specification."""
from platform_core.storage import DomainError
from execution.security.output_policy import field_classification, name_classification


def _where_terms(expression):
    from sqlglot import exp
    if isinstance(expression, exp.And):
        return _where_terms(expression.this) + _where_terms(expression.expression)
    if isinstance(expression, exp.EQ):
        return [expression]
    raise DomainError('自动编制仅支持由精确等值条件组成的只读查询；请在需求规格中修改筛选条件', 409)


def query_shape(sql, catalog_store, approved_resource_ids=()):
    """Resolve one-table, one-field exact lookups against registered metadata only."""
    try:
        import sqlglot
        from sqlglot import exp
        parsed = sqlglot.parse(sql, read='duckdb')
    except Exception:
        raise DomainError('已确认SQL无法解析，不能生成受控方案', 409)
    if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
        raise DomainError('自动编制仅支持单条单表SELECT查询', 409)
    tree = parsed[0]
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1 or tree.args.get('joins'):
        raise DomainError('自动编制当前仅支持单表查询；请先人工补充表关联方案', 409)
    table_name = tables[0].name
    candidates = [item for item in catalog_store.list('object')
                  if item.get('kind') == 'table' and item.get('status') == 'active'
                  and str(item.get('table') or item.get('name') or '').casefold() == table_name.casefold()]
    approved=set(approved_resource_ids)
    if approved:
        candidates=[item for item in candidates if item['id'] in approved]
    if len(candidates) != 1:
        raise DomainError('SQL表未匹配到有效目录表，请重新确认资源候选', 409)
    table=candidates[0]
    fields = [item for item in catalog_store.list('object')
              if item.get('kind') == 'field' and item.get('parent_id') == table['id']
              and item.get('status') == 'active']
    by_name = {str(item.get('name', '')).casefold(): item for item in fields}
    if len(by_name) != len(fields):
        raise DomainError('目录表含重名字段，需人工建立视图', 409)

    projections = tree.expressions
    if len(projections) != 1 or not isinstance(projections[0], exp.Column):
        raise DomainError('患者伪名查询必须只输出一个目录字段', 409)
    output_field = by_name.get(projections[0].name.casefold())
    if not output_field or field_classification(output_field) != 'identifier':
        raise DomainError('输出字段不是目录中已识别的患者标识字段', 403)
    if str(output_field.get('name', '')).casefold() not in ('patient_id', 'person_id', 'medical_record_no', 'mrn'):
        raise DomainError('当前患者伪名输出仅支持患者ID类字段', 409)

    where = tree.args.get('where')
    if not where:
        raise DomainError('患者精确查找必须包含明确筛选条件', 409)
    filters = []
    filter_names = []
    for term in _where_terms(where.this):
        left, right = term.this, term.expression
        if isinstance(left, exp.Literal) and isinstance(right, exp.Column):
            left, right = right, left
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Literal) or not right.is_string:
            raise DomainError('患者查找只支持字段与文本常量的精确匹配', 409)
        field = by_name.get(left.name.casefold())
        if not field:
            raise DomainError('SQL筛选字段不在该表的有效目录字段中', 409)
        filters.append({'field': field['id'], 'op': '=', 'value': right.this})
        filter_names.append(field.get('name', ''))
    if len(filters) > 5:
        raise DomainError('自动编制最多支持5个精确筛选条件', 409)
    limit = tree.args.get('limit')
    if limit:
        try:
            max_rows = int(limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            raise DomainError('SQL行数上限无效，不能生成受控方案', 409)
    else:
        max_rows = 200
    required = [output_field['id'], *(condition['field'] for condition in filters)]
    return {'table': table, 'output_field': output_field, 'filters': filters, 'filter_names': filter_names,
            'fields': list(dict.fromkeys(required)), 'max_rows': min(max_rows, 200)}


def create_plan(runtime, flow, specification, actor):
    """Create a logical view and a draft plan. This function never reads source rows."""
    spec_body=specification.get('specification') or {}
    policy = spec_body.get('execution_constraints') or {}
    scope = policy.get('scope') or {}
    if (scope.get('kind') != 'lookup' or policy.get('output_mode') != 'object'
            or policy.get('identifier_output') != 'pseudonymized'):
        raise DomainError('先确认对象级精确查询规格，才能预览患者伪名ID方案', 409)
    approved_ids=[resource['id'] for resource in flow.get('resources',[])
                  if resource.get('review_status')=='human_confirmed']
    shape = query_shape(spec_body.get('query_draft'), runtime.management.store, approved_ids)
    if scope.get('filters') != shape['filters']:
        raise DomainError('规格中的精确筛选与已确认SQL不一致，拒绝生成方案', 403)
    if policy.get('max_rows', 0) < shape['max_rows']:
        raise DomainError('规格允许的行数少于SQL草案上限，请先修订规格', 409)

    view_id='VIEW-'+flow['id']+'-PATIENT-PSEUDONYM'
    old_view=runtime.execution.store.get('view',view_id)
    view_body={
        'id':view_id,'expected_version':old_view['version'] if old_view else 0,
        'name': '患者伪名ID查询 · ' + flow['id'],
        'fields': shape['fields'],
        'output_fields': [shape['output_field']['id']],
        'pseudonymize_fields': [shape['output_field']['id']],
        'filters': [], 'group_by': [], 'output_mode': 'object',
    }
    current_field_versions={field_id:runtime.management.store.require('object',field_id)['version']
                            for field_id in shape['fields']}
    view_matches=bool(old_view and old_view.get('fields')==view_body['fields']
        and old_view.get('output_fields')==view_body['output_fields']
        and old_view.get('pseudonymize_fields')==view_body['pseudonymize_fields']
        and old_view.get('field_versions')==current_field_versions
        and old_view.get('filters')==[] and old_view.get('group_by')==[]
        and old_view.get('joins')==[] and old_view.get('projections')=={}
        and old_view.get('output_mode')=='object')
    view=old_view if view_matches else runtime.execution.queries.define(view_body,actor)

    plan_id='PLAN-'+flow['id']+'-PATIENT-PSEUDONYM'
    old_plan=runtime.execution.store.get('plan',plan_id)
    old_nodes=(old_plan or {}).get('nodes',[])
    from contracts.execution.constraints import normalize, fingerprint
    policy_digest=fingerprint(normalize(spec_body))
    plan_matches=bool(old_plan and old_plan.get('spec_id')==specification['id']
        and old_plan.get('spec_version')==specification['version']
        and old_plan.get('view_versions',{}).get(view['id'])==view['version']
        and old_plan.get('output_mode')=='object'
        and old_plan.get('execution_policy_digest')==policy_digest
        and old_plan.get('output_node')=='patient-lookup'
        and len(old_nodes)==1 and old_nodes[0].get('operation')=='view'
        and old_nodes[0].get('view_id')==view['id']
        and old_plan.get('output_policy',{}).get('columns',{}).keys()=={'patient_token'})
    plan = old_plan if plan_matches else runtime.execution.plan({
        'id':plan_id,'expected_version':old_plan['version'] if old_plan else 0,
        'name': '患者伪名ID受控查询 · ' + flow['id'],
        'spec_id': specification['id'],
        'spec_version': specification['version'],
        'output_mode': 'object',
        'circulation_mode': policy.get('circulation_modes', ['controlled_compute'])[0],
        'quality_min_complete': policy.get('quality_min_complete', 1),
        'nodes': [{'id': 'patient-lookup', 'operation': 'view', 'view_id': view['id'],
                   'depends_on': [], 'params': {}}],
        'output_node': 'patient-lookup',
    }, actor)
    return view, plan, shape


def field_query_shape(sql, catalog_store, approved_resource_ids=()):
    """Resolve a one-field exact-name query, including one registered patient join."""
    try:
        import sqlglot
        from sqlglot import exp
        parsed=sqlglot.parse(sql,read='duckdb')
    except Exception:
        raise DomainError('已确认SQL无法解析，不能生成受控方案',409)
    if len(parsed)!=1 or not isinstance(parsed[0],exp.Select):
        raise DomainError('普通字段查询仅支持单条SELECT',409)
    tree=parsed[0];sql_tables=list(tree.find_all(exp.Table));joins=tree.args.get('joins') or []
    if len(sql_tables) not in (1,2) or len(joins)!=len(sql_tables)-1:
        raise DomainError('普通字段查询仅支持单表或一次目录内患者关联',409)
    candidates=[item for item in catalog_store.list('object')
        if item.get('kind')=='table' and item.get('status')=='active']
    approved=set(approved_resource_ids)
    tables=[]
    for sql_table in sql_tables:
        found=[item for item in candidates if str(item.get('table') or item.get('name') or '').casefold()==sql_table.name.casefold()]
        if approved:found=[item for item in found if item['id'] in approved]
        if len(found)!=1:raise DomainError('SQL表未匹配到已确认的目录表',409)
        tables.append(found[0])
    table=tables[-1]
    fields=[item for item in catalog_store.list('object') if item.get('kind')=='field'
        and item.get('parent_id') in {x['id'] for x in tables} and item.get('status')=='active']
    by_table={x['id']:{str(f.get('name','')).casefold():f for f in fields if f.get('parent_id')==x['id']} for x in tables}
    if any(len(group)!=sum(f.get('parent_id')==tid for f in fields) for tid,group in by_table.items()):
        raise DomainError('目录表含重名字段，需人工建立视图',409)
    aliases={sql_table.alias_or_name.casefold():tables[i] for i,sql_table in enumerate(sql_tables)}
    joins_ids=[]
    if len(tables)==2:
        join=joins[0];on=join.args.get('on')
        if not isinstance(on,exp.EQ) or not isinstance(on.this,exp.Column) or not isinstance(on.expression,exp.Column):
            raise DomainError('患者表关联必须使用单一字段等值条件',409)
        pair=[]
        for column in (on.this,on.expression):
            parent=aliases.get(column.table.casefold());field=by_table.get(parent['id'],{}).get(column.name.casefold()) if parent else None
            if not field or str(field.get('name','')).casefold()!='patient_id':
                raise DomainError('跨表查询仅允许使用目录中已登记的 patient_id 关联',409)
            pair.append(field['id'])
        if {by_table[t['id']].get('patient_id',{}).get('id') for t in tables}!=set(pair):
            raise DomainError('patient_id 关联字段与所选目录表不匹配',409)
        joins_ids=[pair]
    projections=tree.expressions
    if not 1<=len(projections)<=10 or any(not isinstance(item,exp.Column) for item in projections):
        raise DomainError('普通字段查询只能输出1至10个已登记目录字段',409)
    output_fields=[]
    for projection in projections:
        output_table=aliases.get(projection.table.casefold()) if projection.table else table
        output_field=by_table.get(output_table['id'],{}).get(projection.name.casefold())
        if not output_field:
            raise DomainError('输出字段不在已确认的目录表中',409)
        if name_classification(output_field.get('name',''))=='identifier':
            if str(output_field.get('name','')).casefold()!='patient_id' or field_classification(output_field)!='identifier':
                raise DomainError('仅支持通过逐方案批准输出目录中的patient_id',403)
        elif field_classification(output_field) not in ('public','internal','health'):
            raise DomainError('输出字段分类尚未确认或当前不允许直接交付',403)
        output_fields.append(output_field)
    if len({field['id'] for field in output_fields})!=len(output_fields):
        raise DomainError('输出字段重复',409)
    where=tree.args.get('where')
    if not where:raise DomainError('普通字段查询必须包含姓名精确筛选',409)
    terms=_where_terms(where.this)
    if len(terms)!=1:raise DomainError('普通字段查询当前只支持一个姓名精确筛选',409)
    left,right=terms[0].this,terms[0].expression
    if isinstance(left,exp.Literal) and isinstance(right,exp.Column):left,right=right,left
    if not isinstance(left,exp.Column) or not isinstance(right,exp.Literal) or not right.is_string:
        raise DomainError('姓名筛选只支持字段与文本常量的精确匹配',409)
    filter_table=aliases.get(left.table.casefold()) if left.table else next((t for t in tables if 'name' in by_table[t['id']]),None)
    filter_field=by_table.get(filter_table['id'],{}).get(left.name.casefold()) if filter_table else None
    if not filter_field or str(filter_field.get('name','')).casefold() not in (
            'name','patient_name','fullname','full_name','姓名','患者姓名'):
        raise DomainError('普通字段查询必须按已登记的姓名字段精确筛选',409)
    limit=tree.args.get('limit')
    if limit:
        try:max_rows=int(limit.expression.this)
        except (AttributeError,TypeError,ValueError):raise DomainError('SQL行数上限无效',409)
    else:max_rows=200
    filters=[{'field':filter_field['id'],'op':'=','value':right.this}]
    required=[*(field['id'] for field in output_fields),filter_field['id'],*(field_id for pair in joins_ids for field_id in pair)]
    return {'table':table,'tables':tables,'joins':joins_ids,'output_field':output_fields[0],
        'output_fields':output_fields,'filters':filters,
        'filter_names':[filter_field.get('name','')],'fields':list(dict.fromkeys(required)),
        'max_rows':min(max_rows,200)}


def create_field_plan(runtime,flow,specification,actor):
    """Create a fixed plan for one catalog field; never read source rows here."""
    spec_body=specification.get('specification') or {};policy=spec_body.get('execution_constraints') or {}
    scope=policy.get('scope') or {}
    expected_outputs=policy.get('field_outputs') or ([policy.get('field_output')] if policy.get('field_output') else [])
    if (scope.get('kind')!='lookup' or policy.get('output_mode')!='object'
            or not expected_outputs):
        raise DomainError('先确认精确匹配和输出字段规格，才能生成固定方案',409)
    approved_ids=[item['id'] for item in flow.get('resources',[])
        if item.get('review_status')=='human_confirmed']
    if flow.get('query_mode')=='professional':
        from management.intake.professional import professional_shape
        shape=professional_shape(spec_body.get('query_draft'),runtime.management.store)
        if not {table['id'] for table in shape['tables']}<=set(approved_ids):
            raise DomainError('专业查询表尚未确认',409)
    else:
        shape=field_query_shape(spec_body.get('query_draft'),runtime.management.store,approved_ids)
    if (scope.get('filters')!=shape['filters'] or policy.get('max_rows',0)<shape['max_rows']
            or set(expected_outputs)!={field['id'] for field in shape['output_fields']}):
        raise DomainError('规格范围或行数上限与已确认SQL不一致',403)
    view_id='VIEW-'+flow['id']+'-FIELD';old_view=runtime.execution.store.get('view',view_id)
    view_body={'id':view_id,'expected_version':old_view['version'] if old_view else 0,
        'name':shape['table'].get('name','目录字段查询')+' · '+'、'.join(field.get('name','') for field in shape['output_fields']),
        'fields':shape['fields'],'output_fields':[field['id'] for field in shape['output_fields']],
        'joins':shape.get('joins',[]),'filters':[],'group_by':[],'output_mode':'object'}
    if flow.get('query_mode')=='professional':
        view_body.update(order_by=shape['order_by'],row_limit=shape['row_limit'])
    versions={field_id:runtime.management.store.require('object',field_id)['version']
        for field_id in shape['fields']}
    view_matches=bool(old_view and old_view.get('fields')==view_body['fields']
        and old_view.get('output_fields')==view_body['output_fields']
        and old_view.get('field_versions')==versions and old_view.get('filters')==[]
        and old_view.get('group_by')==[] and old_view.get('joins')==shape.get('joins',[])
        and old_view.get('projections')=={} and old_view.get('output_mode')=='object'
        and old_view.get('order_by')==view_body.get('order_by')
        and old_view.get('row_limit')==view_body.get('row_limit'))
    view=old_view if view_matches else runtime.execution.queries.define(view_body,actor)
    plan_id='PLAN-'+flow['id']+'-FIELD';old_plan=runtime.execution.store.get('plan',plan_id)
    from contracts.execution.constraints import normalize,fingerprint
    policy_digest=fingerprint(normalize(spec_body));old_nodes=(old_plan or {}).get('nodes',[])
    output_names={field['name'] for field in shape['output_fields']}
    plan_matches=bool(old_plan and old_plan.get('spec_id')==specification['id']
        and old_plan.get('spec_version')==specification['version']
        and old_plan.get('view_versions',{}).get(view['id'])==view['version']
        and old_plan.get('output_mode')=='object'
        and old_plan.get('execution_policy_digest')==policy_digest
        and old_plan.get('output_node')=='field-lookup'
        and len(old_nodes)==1 and old_nodes[0].get('operation')=='view'
        and old_nodes[0].get('view_id')==view['id']
        and set((old_plan.get('output_policy') or {}).get('columns',{}))==output_names)
    plan=old_plan if plan_matches else runtime.execution.plan({
        'id':plan_id,'expected_version':old_plan['version'] if old_plan else 0,
        'name':shape['table'].get('name','目录字段')+' · '+'、'.join(sorted(output_names))+'受控查询',
        'spec_id':specification['id'],'spec_version':specification['version'],
        'output_mode':'object','circulation_mode':policy.get('circulation_modes',['controlled_compute'])[0],
        'quality_min_complete':policy.get('quality_min_complete',1),
        'nodes':[{'id':'field-lookup','operation':'view','view_id':view['id'],'depends_on':[],'params':{}}],
        'output_node':'field-lookup'},actor)
    return view,plan,shape
