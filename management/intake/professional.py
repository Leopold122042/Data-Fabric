"""Compile the user workbench's explicit, bounded SELECT into catalog bindings."""
from platform_core.storage import DomainError
from execution.security.output_policy import field_classification, name_classification
from management.intake.calculations import (person_identifier, summary_count_kind, SUMMARY_SCOPES,
    validate_summary_scope)
from management.catalog.query_links import MAX_JOIN_TABLES, executable_links, describe_links
from contracts.execution.filters import filter_values


def professional_shape(sql, store):
    import sqlglot
    from sqlglot import exp
    from management.catalog.field_library import FieldLibrary
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
        raise DomainError('请填写查询 SQL（不超过 20000 字符）')
    try:
        statements = sqlglot.parse(sql, read='duckdb')
    except Exception:
        raise DomainError('SQL 语法无法识别，请检查表名、字段和条件')
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise DomainError('专业查询只接受一条只读 SELECT')
    tree = statements[0]
    allowed = {'expressions', 'from_', 'joins', 'where', 'order', 'limit', 'distinct'}
    if any(value and key not in allowed for key, value in tree.args.items()):
        raise DomainError('当前支持明细查询与单表计数；请移除未支持的 SQL 子句')
    if list(tree.find_all(exp.Subquery)) or len(list(tree.find_all(exp.Select))) != 1:
        raise DomainError('请直接选择目录中的表，暂不支持子查询')
    distinct=tree.args.get('distinct')
    if distinct and any(distinct.args.values()):
        raise DomainError('当前支持整条结果去重，请选择明确的输出列')
    tables = []
    aliases = {}
    catalog = [x for x in store.list('object') if x.get('status') == 'active']
    sql_tables = list(tree.find_all(exp.Table))
    if not 1 <= len(sql_tables) <= MAX_JOIN_TABLES:
        raise DomainError('请选择一张表，或至多四张有已登记关联的表')
    for ref in sql_tables:
        if ref.db or ref.catalog or not isinstance(ref.this, exp.Identifier):
            raise DomainError('只允许当前目录中的表名')
        matches = [x for x in catalog if x.get('kind') == 'table'
                   and str(x.get('table') or x.get('name')).casefold() == ref.name.casefold()]
        if len(matches) != 1:
            raise DomainError('表名没有唯一匹配到当前目录：' + ref.name)
        if ref.alias_or_name.casefold() in aliases or matches[0] in tables:
            raise DomainError('表别名重复或引用了同一张表两次')
        tables.append(matches[0]); aliases[ref.alias_or_name.casefold()] = matches[0]['id']
    fields = [x for x in catalog if x.get('kind') == 'field' and x.get('parent_id') in {t['id'] for t in tables}]
    def resolve(column):
        if not isinstance(column, exp.Column) or column.is_star:
            raise DomainError('请使用明确的目录字段，不支持星号或计算表达式')
        candidates = [f for f in fields if f['name'].casefold() == column.name.casefold()
                      and (not column.table or f['parent_id'] == aliases.get(column.table.casefold()))]
        if len(candidates) != 1:
            raise DomainError('字段不存在或有歧义，请使用表别名：' + column.sql())
        return candidates[0]
    joins = []
    raw_joins = tree.args.get('joins') or []
    if len(raw_joins) != len(tables) - 1:
        raise DomainError('多表查询必须声明每一步的关联')
    graph_fields,edges={},{}
    if raw_joins:
        _,graph_fields,edges=executable_links(store,{table['id'] for table in tables})
    base=tree.args.get('from_')
    if not base or not isinstance(base.this,exp.Table) or base.this.alias_or_name.casefold() not in aliases:
        raise DomainError('请选择已登记的起始逻辑表')
    reached={aliases[base.this.alias_or_name.casefold()]}
    for join in raw_joins:
        if (join.args.get('side') or join.args.get('method') or join.args.get('kind') not in (None, '', 'INNER')
                or join.args.get('using') or not isinstance(join.this,exp.Table)):
            raise DomainError('当前支持已登记关联的 INNER JOIN')
        on = join.args.get('on')
        if not isinstance(on, exp.EQ):
            raise DomainError('关联条件必须对已登记的字段使用等值比较')
        left, right = resolve(on.this), resolve(on.expression)
        target=aliases[join.this.alias_or_name.casefold()]
        origins={left['parent_id'],right['parent_id']}
        if target in reached or target not in origins or len(origins)!=2 or not (origins-{target})<=reached:
            raise DomainError('每一步应将新表连接到已加入的表')
        if tuple(sorted((left['id'],right['id']))) not in edges:
            raise DomainError('该字段对缺少可执行的已登记关联',409)
        joins.append([left['id'], right['id']])
        reached.add(target)
    definitions = FieldLibrary(store).confirmed()
    calculation = None
    measure_field = None
    expression = tree.expressions[0] if len(tree.expressions) == 1 else None
    aggregate = expression.this if isinstance(expression, exp.Alias) else expression
    if isinstance(aggregate, (exp.Count, exp.Max)):
        if tree.args.get('order'):
            raise DomainError('单值统计不接受排序条件')
        if any(value and key not in ('this','big_int') for key,value in aggregate.args.items()):
            raise DomainError('统计表达式存在未支持的参数')
        if isinstance(expression, exp.Alias) and expression.alias.casefold() != 'count':
            raise DomainError('统计结果的列名为 count')
        if isinstance(aggregate, exp.Count) and isinstance(aggregate.this, exp.Star) and not any(aggregate.this.args.values()):
            kind = 'row_count'
        elif (isinstance(aggregate, exp.Count) and isinstance(aggregate.this, exp.Distinct)
                and len(aggregate.this.expressions) == 1
                and not any(value for key,value in aggregate.this.args.items() if key!='expressions')):
            measure_field = resolve(aggregate.this.expressions[0])
            measure_table=next(table for table in tables if table['id']==measure_field['parent_id'])
            if not person_identifier(measure_table,measure_field):
                raise DomainError('去重人数需要已确认的人员标识；可另选统计记录数')
            kind = 'person_count'
        elif isinstance(aggregate, exp.Max):
            measure_field = resolve(aggregate.this)
            kind=summary_count_kind(tables[0],measure_field)
            if not kind:
                raise DomainError('当前支持记录数、人员去重人数及已登记的汇总口径')
        else:
            raise DomainError('请使用 COUNT(*) 或 COUNT(DISTINCT 患者标识)')
        if len(tables)!=1 and kind!='person_count':
            raise DomainError('跨表统计需明确人员标识，以避免重复计入多条关联记录')
        calculation = {'kind':kind,'table':measure_field['parent_id'] if measure_field else tables[0]['id'],
                       'field':measure_field['id'] if measure_field else None}
        if distinct:
            raise DomainError('单值汇总已经定义统计口径')
        tree.set('expressions',[exp.alias_(aggregate.copy(),'count',quoted=True)])
    outputs = [] if calculation else [resolve(x) for x in tree.expressions]
    if not calculation and (not 1 <= len(outputs) <= 10 or len({x['id'] for x in outputs}) != len(outputs)):
        raise DomainError('请选择 1 至 10 个不重复的输出字段')
    for field in outputs:
        if field['id'] not in definitions:
            raise DomainError('字段定义待管理员确认：' + field['name'])
        classification = field_classification(field)
        if classification not in ('public', 'internal', 'health', 'identifier'):
            raise DomainError('字段交付分类待管理员确认：' + definitions[field['id']]['label'])
        if name_classification(field['name']) == 'identifier' and field['name'].casefold() != 'patient_id':
            raise DomainError('当前不支持直接交付该个人标识字段：' + definitions[field['id']]['label'])
    where = tree.args.get('where')
    if not where and not calculation:
        raise DomainError('请至少添加一个条件筛选条件，避免无范围读取')
    def terms(node):
        if isinstance(node, exp.Paren): return terms(node.this)
        if isinstance(node, exp.And): return terms(node.this) + terms(node.expression)
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
                                 exp.In, exp.Between, exp.Like)):
            raise DomainError('筛选条件须同时成立，支持等值、范围、集合和前缀')
        return [node]
    filters = []
    def literal_value(value):
        if isinstance(value,exp.Neg) and isinstance(value.this,exp.Literal) and not value.this.is_string:
            value=exp.Literal.number('-'+str(value.this.this))
        if not isinstance(value,exp.Literal) or not str(value.this).strip():
            raise DomainError('条件筛选需要明确的文本或数字常量')
        return str(value.this)

    for term in terms(where.this) if where else ():
        if isinstance(term,(exp.In,exp.Between,exp.Like)):
            field=resolve(term.this)['id']
            if isinstance(term,exp.In) and not any(value for key,value in term.args.items() if key not in ('this','expressions')):
                op='in'
                value=[literal_value(item) for item in term.expressions]
            elif isinstance(term,exp.Between) and not term.args.get('symmetric'):
                filters.extend({'field':field,'op':operator,'value':literal_value(term.args[key])}
                               for operator,key in (('>=','low'),('<=','high')))
                continue
            elif isinstance(term,exp.Like) and isinstance(term.expression,exp.Literal) and term.expression.is_string:
                pattern=literal_value(term.expression)
                if not pattern.endswith('%'):
                    raise DomainError('前缀筛选使用固定前缀，末尾带一个百分号')
                op,value='prefix',pattern[:-1]
            else:
                raise DomainError('请使用明确的固定集合、闭区间或编码前缀')
            filter_values(op,value)
            filters.append({'field':field,'op':op,'value':value})
            continue
        left, right = term.this, term.expression
        op={exp.EQ:'=',exp.NEQ:'!=',exp.GT:'>',exp.GTE:'>=',exp.LT:'<',exp.LTE:'<='}[type(term)]
        if isinstance(left, exp.Literal):
            left, right = right, left
            op={'<':'>','<=':'>=','>':'<','>=':'<=','=':'=','!=':'!='}[op]
        field = resolve(left)
        filters.append({'field': field['id'], 'op': op, 'value': literal_value(right)})
    if len(filters) > 20: raise DomainError('筛选条件最多 20 项')
    if calculation and calculation['kind'] in SUMMARY_SCOPES:
        cohort=next((f['id'] for f in fields if f['name']=='cohort'),None)
        validate_summary_scope(calculation['kind'],filters,cohort)
    ordering = []
    for item in (tree.args.get('order').expressions if tree.args.get('order') else []):
        field = resolve(item.this)
        ordering.append({'field': field['id'], 'direction': 'desc' if item.args.get('desc') else 'asc',
                         'nulls_first': bool(item.args.get('nulls_first'))})
    limit = tree.args.get('limit')
    try: count = int(limit.expression.this) if limit and isinstance(limit.expression, exp.Literal) else 200
    except (ValueError, TypeError): raise DomainError('返回条数必须是整数')
    if limit and (not isinstance(limit.expression, exp.Literal) or limit.expression.is_string):
        raise DomainError('返回条数必须是整数')
    if not 1 <= count <= 200: raise DomainError('每次查询可返回 1 至 200 条')
    if calculation:
        count=1
    tree = tree.limit(count)
    required = list(dict.fromkeys([x['id'] for x in outputs] + [x['field'] for x in filters]
                    + [x['field'] for x in ordering] + [x for pair in joins for x in pair]
                    + ([measure_field['id']] if measure_field else [])))
    if calculation and not required:
        anchor = next((f['id'] for f in fields if f['id'] in definitions),None)
        if not anchor:raise DomainError('此表缺少已确认的目录字段')
        required.append(anchor)
    if any(fid not in definitions for fid in required):
        raise DomainError('筛选、排序或关联字段定义尚未确认，请由管理员确认')
    return {'sql': tree.sql(dialect='duckdb'), 'table': tables[-1], 'tables': tables, 'joins': joins,
            **({'join_path':describe_links(joins,graph_fields,edges)} if joins else {}),
            **({'distinct':True} if distinct else {}),
            'output_field': outputs[0] if outputs else None, 'output_fields': outputs, 'filters': filters, 'fields': required,
            'filter_names': [store.require('object', x['field'])['name'] for x in filters],
            'max_rows': count, 'order_by': ordering, 'row_limit': count, **({'calculation':calculation} if calculation else {})}


def workbench_policy(shape, allow_missing=False):
    calculation=shape.get('calculation')
    common={'time_field':None,'circulation_modes':['controlled_compute'],
            'quality_min_complete':0 if allow_missing else 1,'max_rows':shape['max_rows'],
            'min_group_size':0}
    if calculation:
        return {**common,'scope':{'kind':'aggregate_lookup','filters':shape['filters'],'all_rows':not shape['filters']},
                'output_mode':'aggregate','calculation':calculation}
    return {**common,'scope':{'kind':'lookup','filters':shape['filters']},'output_mode':'object',
            'field_outputs':[f['id'] for f in shape['output_fields']]}
