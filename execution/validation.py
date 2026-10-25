"""Local B validation bridge; only existing handlers, fixed demo identity, no remote endpoints."""
import time
from platform_core.storage import DomainError


def check_schema(value,schema):
    if not isinstance(schema,dict):return False
    supported={'type','properties','required','items','enum','description','additionalProperties'}
    if set(schema)-supported:raise DomainError('本地schema检查不支持该关键字；请接入完整校验器')
    types={'object':dict,'array':list,'string':str,'number':(int,float),'integer':int,'boolean':bool,'null':type(None)}
    kind=schema.get('type')
    if kind not in types or not isinstance(value,types[kind]) or (kind in ('integer','number') and isinstance(value,bool)):return False
    if 'enum' in schema and value not in schema['enum']:return False
    if isinstance(value,dict):
        if any(k not in value for k in schema.get('required',[])):return False
        props=schema.get('properties',{})
        if schema.get('additionalProperties') is False and set(value)-set(props):return False
        return all(k not in value or check_schema(value[k],v) for k,v in props.items())
    if isinstance(value,list) and 'items' in schema:return all(check_schema(x,schema['items']) for x in value)
    return True


def validator(runtime):
    def validate(service):
        sid=service.get('handler_id')
        reachable=any(s['id']==sid for s in runtime.SERVICES)
        mappings=service.get('concept_ids',[])
        semantic=bool(service.get('source_objects') or mappings)
        reasons=[]
        for cid in mappings:
            try:runtime.management.semantic.resolve(cid)
            except DomainError as exc:semantic=False;reasons.append(str(exc))
        for oid in service.get('source_objects',[]):
            obj=runtime.management.store.get('object',oid)
            if not obj or obj.get('status')!='active':semantic=False
        params=service.get('sample_input')
        schema=check_schema(params,service['input_schema'])
        result=None;failure=None;t0=time.perf_counter()
        actor={'id':'demo-platform','role':'clinician','org':'local-demo'}
        auth=runtime.gov.authorize(actor,runtime.SERVICE_ASSET.get(sid,'clinical_visit'),'validation','care',False)
        allowed=auth['allowed']
        if reachable and schema and semantic and allowed:
            try:result=runtime._execute_handler(sid,params)
            except Exception as exc:failure=str(exc)
        else:failure='语义、输入模式、权限或可达性未通过，未调用后端'
        sample=result is not None and failure is None
        consistency=sample and check_schema(result,service['output_schema'])
        checks={'semantic':{'passed':semantic,'reason':'; '.join(reasons) or '目录引用与已确认映射核对'},
                'schema':{'passed':schema,'reason':'输入模式检查'},'sample':{'passed':sample,'reason':failure or '样例实际执行成功'},
                'permission':{'passed':allowed,'reason':'仅验证本地演示策略边界，未接入课题四'},
                'reachable':{'passed':reachable,'reason':'已注册本地handler'},'consistency':{'passed':consistency,'reason':'实际输出与声明模式比对'}}
        runtime.management.evidence.record({'object_kind':'service','object_id':service['id'],'object_version':service['version'],
            'environment':runtime.management.store.environment,'status':'success' if all(v['passed'] for v in checks.values()) else 'failed',
            'task_scope':'service-validation:'+service['id'],'actor':'demo-platform','latency_ms':round((time.perf_counter()-t0)*1000,3),
            'authorization':'allowed' if allowed else 'denied','delivered':False,'quality_passed':consistency,'checks':checks})
        return checks
    return validate
