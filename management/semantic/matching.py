"""Explainable metadata-only ranking; scores are heuristics, never probabilities."""
import math
import re
from difflib import SequenceMatcher


def similarity(left,right):
    norm=lambda s:re.sub(r'[\W_]+','',str(s).casefold())
    a,b=norm(left),norm(right)
    if not a or not b:return 0.0
    return SequenceMatcher(None,a,b).ratio()


def profile_columns(columns,rows):
    profiles={}
    for i,name in enumerate(columns):
        values=[r[i] for r in rows if i<len(r) and r[i] is not None and str(r[i]).strip()]
        numbers=[]
        for value in values:
            try:
                n=float(value)
                if math.isfinite(n):numbers.append(n)
            except (ValueError,TypeError,OverflowError):pass
        lengths=[len(str(v)) for v in values]
        p={'count':len(rows),'non_null':len(values),'null_rate':round(1-len(values)/len(rows),4) if rows else None,
           'numeric_ratio':round(len(numbers)/len(values),4) if values else None,
           'min_length':min(lengths) if lengths else None,'max_length':max(lengths) if lengths else None,
           'evidence_source':'external-monitor','contains_raw_values':False}
        if numbers:p.update(min=min(numbers),max=max(numbers))
        profiles[name]=p
    return profiles


def non_null_observations(profile):
    """Read current and historical profiles without treating total rows as evidence."""
    # An explicit zero/unknown canonical value must not fall back to an old alias.
    value=profile.get('non_null') if 'non_null' in profile else profile.get('non_null_count')
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or int(value)!=value:
        return 0
    total=profile.get('count') if 'count' in profile else profile.get('row_count')
    if total is not None and (isinstance(total,bool) or not isinstance(total,(int,float)) or not math.isfinite(total) or total<value or int(total)!=total):
        return 0
    return int(value)


def rank(concept,field,table,peers,family):
    aliases=[concept['name'],*concept.get('aliases',[])]
    name=max(similarity(a,field['name']) for a in aliases)
    actual=family(field.get('structure',{}).get('type','unknown'));expected=family(concept['data_type'])
    structural=None if 'unknown' in (actual,expected) else float(actual==expected)
    profile=field.get('profile',{});bounds=concept.get('range',{}) or {}
    value=None
    if non_null_observations(profile):
        ratio=profile.get('numeric_ratio',1.0)
        ratio_known=not isinstance(ratio,bool) and isinstance(ratio,(int,float)) and math.isfinite(ratio) and 0<=ratio<=1
        if expected=='number' and profile.get('min') is not None and profile.get('max') is not None and ratio_known:
            lo,hi=bounds.get('min'),bounds.get('max')
            if lo is not None or hi is not None:
                value=float((lo is None or profile['min']>=lo) and (hi is None or profile['max']<=hi))*ratio
            else:value=ratio
        elif expected=='text' and profile.get('max_length') is not None:
            value=float((bounds.get('min_length') is None or profile['min_length']>=bounds['min_length']) and (bounds.get('max_length') is None or profile['max_length']<=bounds['max_length']))
    contexts=[]
    if field.get('meaning'):contexts.append(similarity(concept['meaning'],field['meaning']))
    if field.get('domain') and field['domain'] not in ('外部数据','医疗演示'):contexts.append(similarity(concept['domain'],field['domain']))
    keywords=concept.get('context_keywords',[])
    if keywords:
        corpus=' '.join([field.get('table',''),table.get('meaning',''),table.get('name',''),*peers]).casefold()
        contexts.append(sum(str(k).casefold() in corpus for k in keywords)/len(keywords))
    context=max(contexts) if contexts else None
    components={'name':name,'structure':structural,'values':value,'context':context}
    weights={'name':.35,'structure':.2,'values':.25,'context':.2}
    coverage=sum(weights[k] for k,v in components.items() if v is not None)
    score=sum(weights[k]*v for k,v in components.items() if v is not None)
    return round(score,3),round(coverage,3),{k:{'score':round(v,3) if v is not None else None,'weight':weights[k],'available':v is not None} for k,v in components.items()}


def expression_type(expr,fields,family):
    from management.store import DomainError
    if 'field' in expr:return family(fields[expr['field']].get('structure',{}).get('type','unknown'))
    if 'literal' in expr:
        v=expr['literal']
        return 'unknown' if v is None else 'boolean' if isinstance(v,bool) else 'number' if isinstance(v,(int,float)) else 'text'
    op=expr['operator'];types=[expression_type(a,fields,family) for a in expr.get('args',[])]
    if op in ('add','subtract','multiply','divide','abs'):
        if any(t not in ('number','unknown') for t in types):raise DomainError('计算表达式需要数值字段，不能直接计算文本字段')
        return 'number'
    if op in ('lower','upper'):
        if any(t not in ('text','unknown') for t in types):raise DomainError('文本表达式需要文本字段')
        return 'text'
    if op in ('eq','ne','gt','ge','lt','le'):
        if len(set(types)-{'unknown'})>1:raise DomainError('条件两侧的数据类型不兼容')
        return 'boolean'
    if op=='case':
        if types[0] not in ('boolean','unknown'):raise DomainError('条件表达式必须返回布尔值')
        types=types[1:]
    known=set(types)-{'unknown'}
    if len(known)>1:raise DomainError('输出分支的数据类型不兼容')
    return next(iter(known),'unknown')
