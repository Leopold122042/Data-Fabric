"""Small declarative expression language. No Python evaluation or caller SQL."""
from platform_core.storage import DomainError


def compile_expression(expr,fields,values,depth=0):
    if depth>8 or not isinstance(expr,dict):raise DomainError('表达式必须为有界结构化对象')
    if set(expr)=={'field'}:
        if expr['field'] not in fields:raise DomainError('表达式引用未声明字段')
        return fields[expr['field']]
    if set(expr)=={'literal'}:
        if isinstance(expr['literal'],(dict,list)):raise DomainError('常量必须为标量')
        values.append(expr['literal']);return '?'
    op=expr.get('operator');args=expr.get('args',[])
    if not isinstance(args,list) or len(args)>8:raise DomainError('表达式参数非法')
    sql=[compile_expression(x,fields,values,depth+1) for x in args]
    binary={'add':'+','subtract':'-','multiply':'*','divide':'/','eq':'=','ne':'!=','gt':'>','ge':'>=','lt':'<','le':'<='}
    if op in binary and len(sql)==2:
        right='NULLIF('+sql[1]+',0)' if op=='divide' else sql[1]
        return '('+sql[0]+' '+binary[op]+' '+right+')'
    if op in ('lower','upper','abs') and len(sql)==1:return op.upper()+'('+sql[0]+')'
    if op=='coalesce' and len(sql)>=2:return 'COALESCE('+','.join(sql)+')'
    if op=='case' and len(sql)==3:return '(CASE WHEN '+sql[0]+' THEN '+sql[1]+' ELSE '+sql[2]+' END)'
    raise DomainError('表达式运算符或参数数量不支持')


def mapping_expression(mapping):
    kind=mapping['mapping_type'];first={'field':mapping['fields'][0]};expr=mapping.get('expression',{})
    if kind in ('direct','join'):return {'field':expr.get('field',mapping['fields'][0])}
    if kind=='derived':return {'operator':expr['operator'],'args':expr.get('args',[{'field':f} for f in mapping['fields']])}
    if kind=='conditional':return {'operator':'case','args':[expr['predicate'],expr.get('then',first),expr.get('else',{'literal':None})]}
    raise DomainError('未知映射类型')
