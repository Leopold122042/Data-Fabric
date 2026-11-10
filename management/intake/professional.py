"""Compile the user workbench's explicit, bounded SELECT into catalog bindings."""
from platform_core.storage import DomainError
from execution.security.output_policy import field_classification, name_classification


def professional_shape(sql, store):
    import sqlglot
    from sqlglot import exp
    from management.catalog.field_library import FieldLibrary
    from management.intake.flow import IntakeFlow
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
        raise DomainError('请填写查询 SQL（不超过 20000 字符）')
    try:
        statements = sqlglot.parse(sql, read='duckdb')
    except Exception:
        raise DomainError('SQL 语法无法识别，请检查表名、字段和条件')
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise DomainError('专业查询只接受一条只读 SELECT')
    tree = statements[0]
    allowed = {'expressions', 'from_', 'joins', 'where', 'order', 'limit'}
    if any(value and key not in allowed for key, value in tree.args.items()):
        raise DomainError('当前查询支持选列、精确筛选、患者关联、排序和条数限制；暂不支持聚合、子查询或其他 SQL 子句')
    if list(tree.find_all(exp.Subquery)) or len(list(tree.find_all(exp.Select))) != 1:
        raise DomainError('请直接选择目录中的表，暂不支持子查询')
    tables = []
    aliases = {}
    catalog = [x for x in store.list('object') if x.get('status') == 'active']
    sql_tables = list(tree.find_all(exp.Table))
    if not 1 <= len(sql_tables) <= 2:
        raise DomainError('请选择一张表，或两张具有已确认患者关联的表')
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
        raise DomainError('两张表必须声明已确认的患者关联')
    for join in raw_joins:
        if join.args.get('side') or join.args.get('kind') not in (None, '', 'INNER') or join.args.get('using'):
            raise DomainError('当前支持患者 ID 的 INNER JOIN')
        on = join.args.get('on')
        if not isinstance(on, exp.EQ):
            raise DomainError('关联条件必须是 patient_id 等值关联')
        left, right = resolve(on.this), resolve(on.expression)
        if left['name'].casefold() != 'patient_id' or right['name'].casefold() != 'patient_id' or left['parent_id'] == right['parent_id']:
            raise DomainError('请使用两张表的 patient_id 关联')
        helper = object.__new__(IntakeFlow); helper.store = store
        if not helper._has_confirmed_patient_id_link(left['id'], right['id']):
            raise DomainError('这两张表的患者关联尚未确认，请由管理员在目录中确认')
        joins.append([left['id'], right['id']])
    outputs = [resolve(x) for x in tree.expressions]
    if not 1 <= len(outputs) <= 10 or len({x['id'] for x in outputs}) != len(outputs):
        raise DomainError('请选择 1 至 10 个不重复的输出字段')
    definitions = FieldLibrary(store).confirmed()
    for field in outputs:
        if field['id'] not in definitions:
            raise DomainError('字段定义待管理员确认：' + field['name'])
        classification = field_classification(field)
        if classification not in ('public', 'internal', 'health', 'identifier'):
            raise DomainError('字段交付分类待管理员确认：' + definitions[field['id']]['label'])
        if name_classification(field['name']) == 'identifier' and field['name'].casefold() != 'patient_id':
            raise DomainError('当前不支持直接交付该个人标识字段：' + definitions[field['id']]['label'])
    where = tree.args.get('where')
    if not where:
        raise DomainError('请至少添加一个精确筛选条件，避免无范围读取')
    from execution.query.intake_plan import _where_terms
    filters = []
    for term in _where_terms(where.this):
        left, right = term.this, term.expression
        if isinstance(left, exp.Literal): left, right = right, left
        field = resolve(left)
        if not isinstance(right, exp.Literal) or not str(right.this).strip():
            raise DomainError('精确筛选需要明确的文本或数字常量')
        filters.append({'field': field['id'], 'op': '=', 'value': str(right.this)})
    if len(filters) > 20: raise DomainError('筛选条件最多 20 项')
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
    tree = tree.limit(count)
    required = list(dict.fromkeys([x['id'] for x in outputs] + [x['field'] for x in filters]
                    + [x['field'] for x in ordering] + [x for pair in joins for x in pair]))
    return {'sql': tree.sql(dialect='duckdb'), 'table': tables[-1], 'tables': tables, 'joins': joins,
            'output_field': outputs[0], 'output_fields': outputs, 'filters': filters, 'fields': required,
            'filter_names': [store.require('object', x['field'])['name'] for x in filters],
            'max_rows': count, 'order_by': ordering, 'row_limit': count}
