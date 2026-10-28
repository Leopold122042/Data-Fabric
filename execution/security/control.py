"""Explicit local demo authorizations; replace this adapter for real institutions."""
from datetime import datetime, timezone
import threading
from platform_core.storage import DomainError, new_id, now
from contracts.execution.constraints import fingerprint,instant,number


def timestamp(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError()
        return result
    except (ValueError,AttributeError):raise DomainError('期限必须为带时区的ISO时间')


class Security:
    def __init__(self,store):self.store=store;self.lock=threading.RLock();self.plan_validator=None
    def grant(self,body,actor):
        if self.store.environment!='demo':raise DomainError('真实授权适配器尚未接入，拒绝签发演示授权',503)
        if not all(body.get(k) for k in ('subject','purpose','plan_id','plan_version','expires_at')):raise DomainError('授权缺少主体、用途、方案版本或期限')
        timestamp(body['expires_at'])
        plan=self.store.require('plan',body['plan_id'],body['plan_version'])
        if self.plan_validator:self.plan_validator(plan)
        policy=plan.get('execution_policy')
        if not policy:raise DomainError('方案缺少执行条件，请重新生成',409)
        mode=body.get('mode',plan['output_mode'])
        if mode not in ('aggregate','object'):raise DomainError('输出粒度非法')
        if mode!=policy['output_mode'] or body['purpose']!=policy['purpose']:raise DomainError('授权不得放宽规格的用途或输出粒度',403)
        if timestamp(body['expires_at'])>instant(policy['deadline_at']):raise DomainError('授权期限不得超过规格交付时限',403)
        min_group=number(body.get('min_group_size',policy['min_group_size']),'最小分组记录数',5,100000,True)
        max_rows=number(body.get('max_rows',policy['max_rows']),'最大交付行数',1,200,True)
        if min_group<policy['min_group_size'] or max_rows>policy['max_rows']:raise DomainError('授权不得放宽规格的分组或行数限制',403)
        data={k:body[k] for k in ('subject','purpose','plan_id','plan_version','expires_at')}
        data.update(mode=mode,status='active',scope='local-demo',min_group_size=min_group,max_rows=max_rows,
                    execution_policy_digest=plan['execution_policy_digest'],plan_digest=fingerprint(plan))
        data['contract_id']=body.get('contract_id')
        if data['contract_id']:
            contract=self.store.require('contract',data['contract_id'])
            if (contract['plan_id'],contract['plan_version'])!=(data['plan_id'],data['plan_version']):raise DomainError('合约与方案版本不匹配')
        return self.store.save('grant',new_id('GRANT'),data,0,actor,'本地授权登记；跨机构授权适配器尚未接入')
    def revoke(self,oid,actor):
        with self.lock:
            g=self.store.require('grant',oid)
            return self.store.save('grant',oid,{**g,'status':'revoked','revoked_at':now()},g['version'],actor,'撤销授权')
    def assess(self,gid,plan,actor,purpose):
        """Evaluate the same execution boundary without writing a decision record."""
        grant=self.store.get('grant',gid);reason=None
        if not grant:reason='缺少授权'
        elif grant['status']!='active':reason='授权已撤销'
        elif timestamp(grant['expires_at'])<=datetime.now(timezone.utc):reason='授权已到期'
        elif grant['subject']!=actor or grant['purpose']!=purpose:reason='主体或用途超出授权范围'
        elif grant['plan_id']!=plan['id'] or grant['plan_version']!=plan['version']:reason='授权不适用当前方案版本'
        elif grant['mode']!=plan['output_mode']:reason='仅允许汇总查询' if grant['mode']=='aggregate' else '输出粒度不匹配'
        if reason is None:
            try:
                registered=self.store.require('plan',plan['id'],plan['version'])
                if plan!=registered or grant.get('plan_digest')!=fingerprint(registered):raise DomainError('授权绑定的方案定义已变化，请重新确认并授权',403)
                if grant.get('execution_policy_digest')!=plan.get('execution_policy_digest'):raise DomainError('授权缺少匹配的执行条件，请重新授权',403)
                if self.plan_validator:self.plan_validator(plan)
                from execution.constraints import verify_plan_policy
                policy=verify_plan_policy(plan)
                if timestamp(grant['expires_at'])>instant(policy['deadline_at']) or grant['max_rows']>policy['max_rows'] or grant['min_group_size']<policy['min_group_size']:
                    raise DomainError('授权放宽了规格的期限或输出限制',403)
            except DomainError as exc:reason=str(exc)
        if grant and grant.get('contract_id') and reason is None:
            contract=self.store.require('contract',grant['contract_id'])
            if set(contract['accepted'])!=set(contract['parties']):reason='合约参与方尚未全部确认'
            elif timestamp(contract['expires_at'])<=datetime.now(timezone.utc):reason='合约已到期'
        return grant,reason
    def check(self,gid,plan,actor,purpose,stage):
        grant,reason=self.assess(gid,plan,actor,purpose)
        decision=self.store.save('decision',new_id('DEC'),{'grant_id':gid,'plan_id':plan['id'],'plan_version':plan['version'],'actor':actor,'purpose':purpose,'stage':stage,'allowed':reason is None,'reason':reason or '本地授权核验通过','scope':'local-demo'},0,actor,'执行点授权核验')
        if reason:raise DomainError(reason,403)
        return grant,decision
