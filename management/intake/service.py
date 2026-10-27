from datetime import datetime
from management.store import DomainError,new_id

REQUIRED=('object_scope','time_start','time_end','purpose','quality','deadline','delivery')


class Intake:
    def __init__(self,store):self.store=store

    def save(self,body,actor):
        if not body.get('title'):raise DomainError('需求标题必填')
        if body.get('entry','goal') not in ('goal','service'):raise DomainError('入口应为goal或service')
        if body.get('time_start') and body.get('time_end'):
            try:
                if datetime.fromisoformat(body['time_start'])>datetime.fromisoformat(body['time_end']):raise DomainError('时间窗起点不得晚于终点')
            except (ValueError,TypeError):raise DomainError('时间窗格式或范围非法')
        oid=body.get('id') or new_id('DEM');old=self.store.get('demand',oid)
        if old and old.get('state')=='confirmed':
            # New demand version invalidates confirmation; immutable specs remain historical.
            body={**body,'supersedes_version':old['version']}
        data={k:v for k,v in body.items() if k not in ('confirmed_by','confirmation','spec_id','state')}
        data['state']='draft';data['created_by']=(old or {}).get('created_by',actor)
        return self.store.save('demand',oid,data,body.get('expected_version',0),actor,'需求草稿')

    def gaps(self,oid):
        d=self.store.require('demand',oid)
        result={'fields':[k for k in REQUIRED if not d.get(k)],'services':[], 'authorization':[], 'quality':[]}
        if d.get('entry')=='service' and not d.get('service_ids'):result['services'].append('未选择服务')
        for sid in d.get('service_ids',[]):
            s=self.store.get('service',sid)
            if not s or s.get('lifecycle')!='published':result['services'].append(sid+'未发布或已退出')
        if not d.get('authorization_evidence'):result['authorization'].append('尚无外部授权证据；受理确认不等于执行授权')
        if not d.get('quality'):result['quality'].append('未明确质量要求')
        if not d.get('execution_constraints'):
            result['fields'].append('execution_constraints')
        return result

    def confirm(self,oid,body,actor):
        with self.store.atomic():
            d=self.store.require('demand',oid)
            if d['version']!=body.get('expected_version'):raise DomainError('需求版本已变化',409)
            if d['state']=='confirmed':raise DomainError('该版本已经确认',409)
            gaps=self.gaps(oid)
            if gaps['fields']:raise DomainError('必填规格尚不完整：'+', '.join(gaps['fields']))
            confirmations=body.get('confirmations',{})
            if not all(confirmations.get(k) is True for k in ('content','attachments','gaps')):raise DomainError('需逐项确认内容、附件及缺口')
            if not body.get('note'):raise DomainError('需填写确认及修改说明')
            from contracts.execution.constraints import normalize
            normalize(d)
            if confirmations.get('execution_constraints') is not True:
                raise DomainError('需核对并确认范围筛选、时间字段和交付执行条件')
            spec_id='SPEC-'+oid;previous=self.store.get('spec',spec_id)
            spec=self.store.save('spec',spec_id,{'name':d['title']+' · 任务规格','demand_id':oid,'demand_version':d['version'],
                'specification':{k:d.get(k) for k in (*REQUIRED,'service_ids','cost','attachments','execution_constraints')},
                'gaps':gaps,'confirmed_by':actor,'confirmation_note':body['note'],
                'confirmed':True,'execution_authorized':False},previous['version'] if previous else 0,actor,'人工确认任务规格')
            self.store.save('demand',oid,{**d,'state':'confirmed','spec_id':spec_id,'spec_version':spec['version']},d['version'],actor,'人工受理完成')
            return spec
