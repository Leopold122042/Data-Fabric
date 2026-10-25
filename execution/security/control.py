"""Explicit local demo authorizations; replace this adapter for real institutions."""
from datetime import datetime, timezone
import threading
from platform_core.storage import DomainError, new_id, now


def timestamp(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError()
        return result
    except (ValueError,AttributeError):raise DomainError('期限必须为带时区的ISO时间')


class Security:
    def __init__(self,store):self.store=store;self.lock=threading.RLock()
    def grant(self,body,actor):
        if self.store.environment!='demo':raise DomainError('真实授权适配器尚未接入，拒绝签发演示授权',503)
        if not all(body.get(k) for k in ('subject','purpose','plan_id','plan_version','expires_at')):raise DomainError('授权缺少主体、用途、方案版本或期限')
        timestamp(body['expires_at'])
        if body.get('mode','aggregate') not in ('aggregate','object'):raise DomainError('输出粒度非法')
        data={k:body[k] for k in ('subject','purpose','plan_id','plan_version','expires_at')}
        data.update(mode=body.get('mode','aggregate'),status='active',scope='local-demo',min_group_size=max(5,int(body.get('min_group_size',5))),max_rows=min(200,max(1,int(body.get('max_rows',200)))))
        data['contract_id']=body.get('contract_id')
        if data['contract_id']:
            contract=self.store.require('contract',data['contract_id'])
            if (contract['plan_id'],contract['plan_version'])!=(data['plan_id'],data['plan_version']):raise DomainError('合约与方案版本不匹配')
        return self.store.save('grant',new_id('GRANT'),data,0,actor,'本地演示授权；非课题四真实决定')
    def revoke(self,oid,actor):
        with self.lock:
            g=self.store.require('grant',oid)
            return self.store.save('grant',oid,{**g,'status':'revoked','revoked_at':now()},g['version'],actor,'撤销授权')
    def check(self,gid,plan,actor,purpose,stage):
        grant=self.store.get('grant',gid);reason=None
        if not grant:reason='缺少授权'
        elif grant['status']!='active':reason='授权已撤销'
        elif timestamp(grant['expires_at'])<=datetime.now(timezone.utc):reason='授权已到期'
        elif grant['subject']!=actor or grant['purpose']!=purpose:reason='主体或用途超出授权范围'
        elif grant['plan_id']!=plan['id'] or grant['plan_version']!=plan['version']:reason='授权不适用当前方案版本'
        elif grant['mode']!=plan['output_mode']:reason='仅允许汇总查询' if grant['mode']=='aggregate' else '输出粒度不匹配'
        if grant and grant.get('contract_id') and reason is None:
            contract=self.store.require('contract',grant['contract_id'])
            if set(contract['accepted'])!=set(contract['parties']):reason='合约参与方尚未全部确认'
            elif timestamp(contract['expires_at'])<=datetime.now(timezone.utc):reason='合约已到期'
        decision=self.store.save('decision',new_id('DEC'),{'grant_id':gid,'plan_id':plan['id'],'plan_version':plan['version'],'actor':actor,'purpose':purpose,'stage':stage,'allowed':reason is None,'reason':reason or '本地演示授权通过','scope':'local-demo'},0,actor,'执行点授权核验')
        if reason:raise DomainError(reason,403)
        return grant,decision
