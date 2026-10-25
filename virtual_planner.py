"""Conservative pruning: only proven literal constraints in a single-table SELECT."""
from external_sources import SourceError


def constraints(sql, view_id):
    import sqlglot
    from sqlglot import exp
    try:
        statements=sqlglot.parse(sql,read='duckdb')
    except sqlglot.errors.ParseError:
        raise SourceError('SQL 无法解析，请检查语句。') from None
    if len(statements)!=1 or not isinstance(statements[0],exp.Query):
        raise SourceError('虚拟表仅允许一条只读查询。')
    tree=statements[0]
    if not isinstance(tree,exp.Select):return {}
    tables=list(tree.find_all(exp.Table))
    if (len(tables)!=1 or tables[0].name!=view_id or tables[0].db or
        tree.args.get('with_') or tree.args.get('joins') or list(tree.find_all(exp.Subquery))):return {}
    if any(a.alias in ('指标','口径') for a in tree.find_all(exp.Alias)):return {}
    where=tree.args.get('where')
    if where is None:return {}
    def walk(node):
        if isinstance(node,exp.Paren):return walk(node.this)
        if isinstance(node,exp.And):
            a,b=walk(node.this),walk(node.expression)
            for key,values in b.items():a[key]=a[key]&values if key in a else values
            return a
        if isinstance(node,exp.EQ):
            a,b=node.this,node.expression
            if isinstance(b,exp.Column):a,b=b,a
            if isinstance(a,exp.Column) and a.name in ('指标','口径') and isinstance(b,exp.Literal) and b.is_string:
                return {a.name:{b.this}}
        if isinstance(node,exp.In) and isinstance(node.this,exp.Column) and node.this.name in ('指标','口径') and not node.args.get('query'):
            if node.expressions and all(isinstance(v,exp.Literal) and v.is_string for v in node.expressions):
                return {node.this.name:{v.this for v in node.expressions}}
        # OR, NOT, functions and uncertain predicates cannot safely prune.
        return {}
    return walk(where.this)


def choose_sources(config,view,sql):
    limits=constraints(sql,view['id'])
    selected=[]
    for source_id in view['sources']:
        definition=next(d for d in config['sources'] if d['id']==source_id)
        maps=definition.get('sheet_capabilities',{})
        sheets=[]
        for sheet in definition['sheets']:
            capability=maps.get(sheet,{})
            if any(key in capability and not (set(capability[key]) & values) for key,values in limits.items()):continue
            sheets.append(sheet)
        if sheets:selected.append({**definition,'sheets':sheets})
    return selected,limits
