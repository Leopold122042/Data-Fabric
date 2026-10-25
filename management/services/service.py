import hashlib
import json
from management.store import DomainError,new_id

FIELDS=('purpose','output','audience','conditions','provider','source_scope','update_period','quality_commitment','restrictions','metering')
TYPES=('query','metric','processing','model','verification')
TRANSITIONS={'draft':['review'],'review':['trial','draft'],'trial':['published','draft'],
             'published':['adjusting','retired'],'adjusting':['review','retired'],'retired':[]}


def fingerprint(data):
    return hashlib.sha256(json.dumps({k:v for k,v in data.items() if k not in
        {'id','version','updated_at','updated_by','change_reason','environment','lifecycle','validation_id'}},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class Services:
    def __init__(self,store):self.store=store;self.validator=None;self.plan_reader=None;self.evidence=None

    def save(self,body,actor):
        if not str(body.get('name','')).strip():raise DomainError('服务名称必填')
        oid=body.get('id') or new_id('SVC');old=self.store.get('service',oid)
        if old and old.get('lifecycle') in ('published','review','trial','retired'):raise DomainError('请先进入调整状态；已退出服务不可修改',409)
        data={k:v for k,v in body.items() if k not in ('validation_id','validated','legacy_seed')}
        data['lifecycle']='draft';data['legacy_seed']=False
        result=self.store.save('service',oid,data,body.get('expected_version',0),actor,'编辑服务草稿')
        return result

    def check_definition(self,data):
        missing=[k for k in FIELDS if not data.get(k)]
        if missing:raise DomainError('发布说明缺少：'+', '.join(missing))
        if data.get('service_type') not in TYPES:raise DomainError('请选择五类服务类型之一')
        if data.get('mode') not in ('regular','demand','joint'):raise DomainError('发布模式非法')
        if data.get('mode')=='demand' and not data.get('demand_id'):raise DomainError('需求牵引服务需关联需求单')
        if data.get('mode')=='joint' and (not data.get('lead_provider') or not data.get('responsibilities')):raise DomainError('联合发布需牵头方及责任分工')
        if data.get('visibility') not in ('internal','specified','approval'):raise DomainError('开放范围非法')
        if data.get('visibility')=='specified' and not data.get('allowed_subjects'):raise DomainError('指定主体共享需列出主体')
        if not isinstance(data.get('input_schema'),dict) or not isinstance(data.get('output_schema'),dict):raise DomainError('输入输出模式必填')
        for oid in data.get('source_objects',[]):self.store.require('object',oid)

    def validate(self,oid,actor):
        with self.store.lock:
            data=self.store.require('service',oid);self.check_definition(data)
            if self.validator is None:raise DomainError('B侧样例执行接口未接入，不能生成通过记录',503)
            checks=self.validator(data)
            required={'semantic','schema','sample','permission','reachable','consistency'}
            passed=set(checks)==required and all(x.get('passed') is True for x in checks.values())
            result=self.store.save('validation',new_id('VAL'),{'service_id':oid,'service_version':data['version'],
                'fingerprint':fingerprint(data),'checks':checks,'passed':passed,'scope':'local-demo'},0,actor,'发布前验证')
            return result

    def transition(self,oid,body,actor):
        with self.store.lock:
            data=self.store.require('service',oid);target=body.get('target')
            if target not in TRANSITIONS.get(data.get('lifecycle','draft'),[]):raise DomainError('不允许的生命周期流转',409)
            if not str(body.get('reason','')).strip():raise DomainError('请填写流转理由')
            if target in ('review','trial','published'):self.check_definition(data)
            if target in ('trial','published'):
                val=self.store.require('validation',body.get('validation_id',''))
                if val['service_id']!=oid or not val['passed'] or val['fingerprint']!=fingerprint(data):raise DomainError('缺少匹配当前定义的通过验证记录',409)
                data['validation_id']=val['id']
            data['lifecycle']=target
            result=self.store.save('service',oid,data,body.get('expected_version'),actor,body['reason'])
            if target in ('adjusting','retired'):
                self.store.save('notice',new_id('NOTICE'),{'service_id':oid,'service_version':result['version'],
                    'reason':body['reason'],'requires_confirmation':True,
                    'affected_demands':[d['id'] for d in self.store.list('demand') if oid in d.get('service_ids',[])]},0,actor,'服务变更影响')
            return result

    def regularize(self,oid,body,actor):
        old=self.store.require('service',oid)
        if old.get('mode')!='demand':raise DomainError('仅定制服务可转常态')
        return self.store.save('service',oid,{**old,'mode':'regular','lifecycle':'draft','source_demand_id':old.get('demand_id')},body.get('expected_version'),actor,'定制转常态，需重新审核')

    def compose(self,body,actor):
        if self.plan_reader is None or self.evidence is None:raise DomainError('B侧方案快照接口未接入，不能登记未经核实的组合',503)
        plan_id,version=body.get('plan_id'),body.get('plan_version')
        plan=self.plan_reader.get(plan_id,version)
        successful=[r for r in self.evidence.list() if r.get('plan_id')==plan_id and r.get('plan_version')==version and r['status']=='success']
        if len(successful)<3:raise DomainError('同一版本组合需至少三次成功运行证据')
        if not plan.get('nodes') or not plan.get('parameters'):raise DomainError('组合缺少子图或可复用参数')
        return self.save({**body,'composition':{'plan_id':plan_id,'version':version,'nodes':plan['nodes'],
            'parameters':plan['parameters'],'evidence_ids':[r['id'] for r in successful]}},actor)
