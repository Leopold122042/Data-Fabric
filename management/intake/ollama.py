"""Small Ollama client used only for untrusted intake extraction proposals."""
import json
import os
import re
import socket
import urllib.error
import urllib.request

from management.store import DomainError

FIELDS = ('title', 'entry', 'object_scope', 'time_start', 'time_end', 'purpose', 'quality',
          'deadline', 'delivery', 'service_ids', 'cost', 'attachments', 'execution_constraints')
CAPABILITIES = ('catalog_search', 'connected_source_discovery', 'data_query', 'trend_analysis')
CAPABILITY_ALIASES = {
    'catalog_search': 'catalog_search', 'catalog search': 'catalog_search', '目录检索': 'catalog_search',
    '检索平台目录': 'catalog_search', '字段匹配': 'catalog_search', '资源检索': 'catalog_search',
    'connected_source_discovery': 'connected_source_discovery',
    'connected source discovery': 'connected_source_discovery', '数据源发现': 'connected_source_discovery',
    '检查已连接数据源结构': 'connected_source_discovery', '数据源结构发现': 'connected_source_discovery',
    'data_query': 'data_query', 'data query': 'data_query', '查询数据': 'data_query', '数据查询': 'data_query',
    'trend_analysis': 'trend_analysis', 'trend analysis': 'trend_analysis',
    '趋势分析': 'trend_analysis', '分析指标趋势': 'trend_analysis',
}
COST_MENTION = re.compile(
    r'预算(?=.{0,8}(?:上限|约束|控制|不超过|不能超过|不得超过|不高于|以内|最多|封顶|(?:为|是)?\s*\d))|'
    r'经费预算|成本(?:预算|约束|上限|控制)|费用(?:预算|约束|上限|限制)|'
    r'花费(?:上限|不超过|不能超过|不得超过|不高于)|最高(?:成本|费用|花费)|'
    r'(?:成本|费用|经费|花费)\s*(?:(?:不超过|不能超过|不得超过|不高于|以内|封顶|上限|最多|最高|控制在|为|是)|<=|≤|<)?\s*\d+(?:\.\d+)?|'
    r'(?:成本|费用|预算|经费).{0,8}(?:不超过|不能超过|不得超过|不高于|以内|封顶|上限|限制|控制)|免费|零成本|'
    r'(?:\d+(?:\.\d+)?\s*)(?:元|块|万元|人民币|美元|美金|usd|cny|rmb)|'
    r'[$¥￥]\s*\d+(?:\.\d+)?|'
    r'\b(?:budget\s+(?:limit|cap|under|below)|spending cap|cost limit|cost cap|fee limit|price cap|cost under)\b', re.I)


def explicitly_mentions_cost(text):
    return bool(COST_MENTION.search(str(text or '')))


def _as_string_list(value, *, limit=100):
    if isinstance(value, str):
        values=[value]
    elif isinstance(value, (list, tuple)):
        values=value
    else:
        return []
    return list(dict.fromkeys(item.strip() for item in values[:limit]
                              if isinstance(item, str) and item.strip()))


def _normalize_capability(value):
    if not isinstance(value, str):
        return None
    value=' '.join(value.strip().casefold().replace('-', ' ').replace('_', ' ').split())
    aliases={ ' '.join(key.casefold().replace('-', ' ').replace('_', ' ').split()): capability
              for key,capability in CAPABILITY_ALIASES.items() }
    return aliases.get(value)


def _normalize_capability_steps(value):
    """Treat model capability steps as hints; repair common shape variations."""
    if isinstance(value, dict):
        value=value.get('steps', value.get('items', value))
        if isinstance(value, dict):
            value=[{'capability':key, **(item if isinstance(item,dict) else {})}
                   for key,item in value.items()]
    if not isinstance(value, list):
        return []
    normalized=[]
    seen=set()
    for item in value[:20]:
        if isinstance(item, str):
            capability=_normalize_capability(item)
            reason='模型建议的平台能力步骤；执行前仍由平台规则校验。'
            inputs=[]
        elif isinstance(item, dict):
            capability=_normalize_capability(item.get('capability') or item.get('name')
                or item.get('type') or item.get('step'))
            reason=item.get('reason') or item.get('description') or item.get('why')
            if not isinstance(reason, str) or not reason.strip():
                reason='模型建议的平台能力步骤；执行前仍由平台规则校验。'
            inputs=_as_string_list(item.get('inputs', item.get('input', [])))
        else:
            continue
        if capability and capability not in seen:
            normalized.append({'capability':capability,'reason':reason.strip()[:1000], 'inputs':inputs})
            seen.add(capability)
    return normalized


def _normalize_field_intents(value):
    if not isinstance(value, list):
        return []
    roles={'output':'output','filter':'filter','group':'group','time':'time',
           'join':'join','metric':'metric','unknown':'unknown','select':'output',
           '筛选':'filter','过滤':'filter','分组':'group','时间':'time',
           '关联':'join','指标':'metric','返回':'output'}
    normalized=[]
    for item in value[:40]:
        if not isinstance(item, dict):
            continue
        phrase=item.get('phrase') or item.get('field') or item.get('name')
        meaning=item.get('meaning') or item.get('definition') or item.get('description') or ''
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        if not isinstance(meaning, str):
            meaning=str(meaning) if isinstance(meaning, (int,float)) else ''
        role=item.get('role') or item.get('usage') or 'unknown'
        role=roles.get(role.strip().casefold(), 'unknown') if isinstance(role,str) else 'unknown'
        normalized.append({'phrase':phrase.strip()[:200], 'meaning':meaning[:500],
                           'aliases':_as_string_list(item.get('aliases', item.get('synonyms', [])),limit=30),
                           'role':role})
    return normalized


class OllamaIntake:
    def __init__(self, base_url=None, model=None, timeout=None):
        self.base_url=(base_url or os.getenv('MEDFABRIC_OLLAMA_URL','http://127.0.0.1:11434')).rstrip('/')
        self.model=model or os.getenv('MEDFABRIC_OLLAMA_MODEL','cjl-model:latest')
        self.timeout=float(timeout or os.getenv('MEDFABRIC_OLLAMA_TIMEOUT','300'))

    def extract(self, text, catalog=None):
        if not isinstance(text,str) or not text.strip():raise DomainError('请提供自然语言需求')
        schema={'type':'object','properties':{
            'title':{'type':['string','null']},'entry':{'type':['string','null'],'enum':['goal','service',None]},
            'object_scope':{'type':['string','null']},'time_start':{'type':['string','null']},
            'time_end':{'type':['string','null']},'purpose':{'type':['string','null']},
            'quality':{'type':['string','null']},'deadline':{'type':['string','null']},
            'delivery':{'type':['string','null']},'service_ids':{'type':['array','null'],'items':{'type':'string'}},
            'cost':{'type':['string','number','null']},'attachments':{'type':['array','null'],'items':{'type':'string'}},
            'execution_constraints':{'type':['object','null']},
            'field_intents':{'type':'array','items':{'type':'object','properties':{
                'phrase':{'type':'string'},'meaning':{'type':'string'},
                'aliases':{'type':'array','items':{'type':'string'}},
                'role':{'type':'string','enum':['output','filter','group','time','join','metric','unknown']}},
                'required':['phrase','meaning','aliases','role'],'additionalProperties':False}},
            'capability_steps':{'type':'array','items':{'type':'object','properties':{
                'capability':{'type':'string','enum':list(CAPABILITIES)},'reason':{'type':'string'},
                'inputs':{'type':'array','items':{'type':'string'}}},'required':['capability','reason','inputs'],'additionalProperties':False}},
            'resource_recommendations':{'type':'array','items':{'type':'object','properties':{
                'id':{'type':'string'},'reason':{'type':'string'}},'required':['id','reason'],'additionalProperties':False}},
            'query_draft':{'type':['string','null'],'maxLength':10000},
            'uncertain_fields':{'type':'array','items':{'type':'string'}}},
            'required':list(FIELDS)+['field_intents','capability_steps','resource_recommendations','query_draft','uncertain_fields'],'additionalProperties':False}
        catalog_rows=(catalog or [])[:300]
        catalog_text=json.dumps(catalog_rows,ensure_ascii=False)
        prompt=('你是需求分析器。只能输出字段抽取和 capability_steps 能力链路建议；不可编造平台服务/逻辑表ID，'
                '可以为 data_query 或 trend_analysis 提出只读 SELECT 查询草案，但不可生成规格、授权或调用服务；'
                'SQL中的逻辑表名和字段必须来自给定目录元数据，缺少信息时 query_draft 填 null；meta_catalog 是平台内部目录，不是可查询的业务数据表，禁止把它写进SQL。'
                '步骤能力只能选 catalog_search（平台已有目录检索）、'
                'connected_source_discovery（已连接数据源的结构发现）、data_query（数据查询）、trend_analysis（指标趋势分析）。'
                '简短表达也可能是查询，例如“唐军的性别”“李明年龄”；识别姓名和目标属性，即使没有“查询/查找”等动词也要提出 catalog_search、'
                'connected_source_discovery、data_query 三步。先将需求拆成 field_intents：每个输出、筛选、分组、时间、关联或指标字段各一项，'
                '保留原文短语，用 meaning 解释业务含义，用 aliases 列出自然语言同义说法，并标注 role。'
                '必须在给定目录中按已确认字段定义、名称、别名、单位、业务域和所属表语义匹配目标字段，'
                '当原文并列多个目标属性（如“某人的ID和科室”）时，必须逐项匹配并全部放入查询输出列与资源推荐；不得只返回最后一个或最显著的属性。'
                '字段库的 definition_status=confirmed 表示人工确认的定义；优先结合 field_label、meaning、aliases、unit 与所属来源表匹配，遇到同名字段保留歧义，不自行确认。'
                '将目标表、目标字段和仅用于精确姓名筛选的姓名字段列入 resource_recommendations；推荐理由要说明“原文目标属性→目录字段”的匹配依据。'
                '只有目录元数据明确支持需求主题时才能推荐该资源；不要把生命体征表当作无关指标的数据源。如果目录中没有匹配项或有多个同等候选，不要猜字段，query_draft 填 null 并把缺口或歧义写入 uncertain_fields。'
                '生成的SQL必须覆盖每个 field_intent；若任一字段未匹配、定义未确认、存在同等候选或SQL漏掉字段，query_draft 必须为 null，并在 uncertain_fields 写出需要向用户确认的具体问题。'
                'resource_recommendations只能从提供的目录元数据中选择ID，不得推断未列出的数据源。这里只提供结构元数据，不含数据行。'
                '成本字段只在原文明确提出预算、费用或金额约束时填写；原文没有明确提及则必须为null，禁止猜测或补默认值。'
                '链路只是建议，之后由规则校验和人工确认。输出一个JSON对象，并严格遵循此结构约定：'
                +json.dumps(schema,ensure_ascii=False)+'\n目录元数据：'+catalog_text+'\n原文：'+text)
        # Ollama JSON-schema constrained decoding fails to load the vocabulary for
        # some locally imported GGUF models. Use its compatible JSON mode and
        # enforce the full application contract below before accepting anything.
        payload=json.dumps({'model':self.model,'stream':False,'format':'json','keep_alive':'10m',
            'messages':[{'role':'user','content':prompt}]},ensure_ascii=False).encode()
        req=urllib.request.Request(self.base_url+'/api/chat',data=payload,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response: raw=json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try: detail=json.loads(exc.read()).get('error',str(exc))
            except (ValueError,AttributeError): detail=str(exc)
            if exc.code==404 and 'not found' in str(detail).lower():
                raise DomainError(f'Ollama 中没有模型“{self.model}”。已安装模型请配置 MEDFABRIC_OLLAMA_MODEL（当前可用：调用 /api/tags 查看）',503)
            raise DomainError(f'Ollama 请求失败（HTTP {exc.code}）：{detail}',502)
        except (urllib.error.URLError,TimeoutError,socket.timeout) as exc:
            reason=getattr(exc,'reason',exc)
            if isinstance(reason,(TimeoutError,socket.timeout)) or 'timed out' in str(reason).lower():
                raise DomainError(f'Ollama 模型仍在推理，超过等待时间 {self.timeout:g} 秒；请稍后重试，或设置 MEDFABRIC_OLLAMA_TIMEOUT 延长等待时间。',504)
            raise DomainError('无法连接本地 Ollama，请检查服务状态和地址：'+str(exc),503)
        except (ValueError,KeyError) as exc:raise DomainError('Ollama 返回格式无效：'+str(exc),502)
        try: proposal=json.loads(raw['message']['content'])
        except (KeyError,TypeError,ValueError):raise DomainError('模型没有返回有效JSON草案',502)
        keys=FIELDS+('field_intents','capability_steps','resource_recommendations','query_draft','uncertain_fields')
        if not isinstance(proposal,dict):
            raise DomainError('模型草案未通过结构校验',502)
        # Some models wrap their JSON object in a conventional envelope. Unwrap
        # only when the outer object has no fields from our contract.
        if not set(proposal).intersection(keys):
            for wrapper in ('proposal','result','output','data'):
                if isinstance(proposal.get(wrapper),dict) and set(proposal[wrapper]).intersection(keys):
                    proposal=proposal[wrapper]
                    break
        # JSON mode guarantees syntax, not a fixed schema. Ignore harmless extra
        # keys and let the application rules supply optional/omitted fields.
        proposal={key:value for key,value in proposal.items() if key in keys}
        for key in FIELDS:
            proposal.setdefault(key,None)
        proposal.setdefault('field_intents',[])
        proposal.setdefault('capability_steps',[])
        proposal.setdefault('resource_recommendations',[])
        proposal.setdefault('query_draft',None)
        proposal.setdefault('uncertain_fields',[])
        nullable_strings=('title','object_scope','time_start','time_end','purpose','quality','deadline','delivery')
        for key in nullable_strings:
            value=proposal.get(key)
            if value is not None and not isinstance(value,str):
                proposal[key]=str(value) if isinstance(value,(int,float)) else None
                value=proposal[key]
            if isinstance(value,str) and value.strip().casefold() in {
                    '', 'null', 'none', 'nil', 'undefined', 'n/a', 'na'}:
                proposal[key]=None
        entry=proposal.get('entry')
        proposal['entry']=entry.strip().casefold() if isinstance(entry,str) and entry.strip().casefold() in {
            'goal','service'} else None
        proposal['service_ids']=None if proposal.get('service_ids') is None else _as_string_list(proposal['service_ids'])
        proposal['attachments']=None if proposal.get('attachments') is None else _as_string_list(proposal['attachments'])
        if proposal['cost'] is not None and type(proposal['cost']) not in (str,int,float):
            proposal['cost']=None
        # Model output is untrusted: never let a guessed number become an execution
        # constraint when the requester did not ask for a cost limit.
        cost_was_explicit=explicitly_mentions_cost(text)
        if not cost_was_explicit:
            proposal['cost']=None
        elif isinstance(proposal['cost'],str) and proposal['cost'].strip().casefold() in {
                '', 'null', 'none', 'nil', 'undefined', 'n/a', 'na'}:
            proposal['cost']=None
        if cost_was_explicit and proposal['cost'] is None:
            # Preserve the fact that a cost condition was requested even when the
            # model failed to extract its amount. This remains a hard blocker until
            # the requester fills in the condition or a budget adapter is available.
            proposal['cost']='原文提到预算/成本约束，但模型未能确定具体条件；请人工补充金额和单位'
        if proposal['execution_constraints'] is not None and not isinstance(proposal['execution_constraints'],dict):
            proposal['execution_constraints']=None
        proposal['uncertain_fields']=_as_string_list(proposal.get('uncertain_fields'))
        field_intents=_normalize_field_intents(proposal.get('field_intents'))
        if proposal.get('field_intents') and not field_intents:
            proposal['uncertain_fields'].append('模型未能按项输出字段语义；需要补充字段说明后重新匹配。')
        proposal['uncertain_fields']=list(dict.fromkeys(proposal['uncertain_fields']))
        # Keep only the explicit extraction contract. Domain rules remain authoritative downstream.
        steps=_normalize_capability_steps(proposal.get('capability_steps'))
        recommendations=proposal.get('resource_recommendations',[])
        if isinstance(recommendations,dict):
            recommendations=recommendations.get('items',[])
        if not isinstance(recommendations,list):
            recommendations=[]
        recommendations=[{'id':item.get('id') or item.get('resource_id'),
                           'reason':item.get('reason') or item.get('description') or item.get('why') or ''}
                         for item in recommendations[:100] if isinstance(item,dict)
                         and isinstance(item.get('id') or item.get('resource_id'),str)]
        recommendations=[item for item in recommendations if isinstance(item['reason'],str)]
        allowed_ids={str(row.get('id')) for row in catalog_rows}
        invalid_recommendations=[item for item in recommendations if item['id'] not in allowed_ids]
        if invalid_recommendations:
            # Resource recommendations are untrusted hints, not the field binding
            # itself. Drop IDs outside the supplied catalog and let the semantic
            # matcher rebuild valid table/field candidates from field_intents.
            recommendations=[item for item in recommendations if item['id'] in allowed_ids]
        query=proposal.get('query_draft')
        if query is not None and (not isinstance(query,str) or len(query)>10000):
            query=None
            proposal['uncertain_fields'].append('模型未返回可校验的查询草案；字段匹配完成后需补充查询范围与筛选条件。')
        return {k:proposal.get(k) for k in FIELDS}|{'field_intents':field_intents,'capability_steps':steps,
            'resource_recommendations':recommendations,'query_draft':query,
            'uncertain_fields':list(dict.fromkeys(proposal.get('uncertain_fields',[])))}
