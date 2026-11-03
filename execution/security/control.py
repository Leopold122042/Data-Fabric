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
    def grant(self,body,actor,health_output_authorization=None,identifier_output_authorization=None):
        if self.store.environment!='demo':raise DomainError('真实授权适配器尚未接入，拒绝签发演示授权',503)
        if not all(body.get(k) for k in ('subject','purpose','plan_id','plan_version','expires_at')):raise DomainError('授权缺少主体、用途、方案版本或期限')
        timestamp(body['expires_at'])
        plan=self.store.require('plan',body['plan_id'],body['plan_version'])
        if self.plan_validator:self.plan_validator(plan)
        from execution.security.output_policy import output_authorization_requirements
        requirements=output_authorization_requirements(plan)
        if requirements is None:
            raise DomainError('方案包含不支持的健康信息输出，不能登记授权',403)
        health_fields=requirements['health'];identifier_fields=requirements['identifier']
        def validate_approval(required,supplied,label):
            if not required:return None
            supplied=supplied if isinstance(supplied,dict) else {}
            expected_ids=set(required);supplied_ids=supplied.get('field_ids')
            if (supplied.get('decision')!='approved' or supplied.get('approver_role')!='platform'
                    or supplied.get('approver_id')!=actor
                    or supplied.get('requester_id')!=body.get('subject')
                    or not isinstance(supplied.get('request_id'),str) or not supplied.get('request_id')
                    or supplied.get('plan_id')!=plan['id']
                    or supplied.get('plan_version')!=plan['version']
                    or supplied.get('purpose')!=body.get('purpose')
                    or not isinstance(supplied_ids,list)
                    or any(not isinstance(field_id,str) for field_id in supplied_ids)
                    or set(supplied_ids)!=expected_ids):
                raise DomainError(label+'须由平台方对本方案、本用途和指定字段作出批准',403)
            return {'decision':'approved','approver_role':'platform','approver_id':actor,
                    'requester_id':body['subject'],'request_id':supplied['request_id'],
                    'plan_id':plan['id'],'plan_version':plan['version'],'purpose':body['purpose'],
                    'field_ids':sorted(expected_ids),
                    'fields':[required[field_id] for field_id in sorted(expected_ids)],
                    'approved_at':now()}
        consent=validate_approval(health_fields,health_output_authorization,'健康信息原值')
        identifier_consent=validate_approval(identifier_fields,identifier_output_authorization,'原始患者标识')
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
        if consent:
            data['health_output_authorization']=consent
            data['health_output_authorized_by']=actor
        if identifier_consent:
            data['identifier_output_authorization']=identifier_consent
            data['identifier_output_authorized_by']=actor
        data['contract_id']=body.get('contract_id')
        if data['contract_id']:
            contract=self.store.require('contract',data['contract_id'])
            if (contract['plan_id'],contract['plan_version'])!=(data['plan_id'],data['plan_version']):raise DomainError('合约与方案版本不匹配')
        note=('平台方批准需求方指定健康信息字段申请并登记本地授权；绑定方案版本与用途'
              if consent else '本地授权登记；跨机构授权适配器尚未接入')
        return self.store.save('grant',new_id('GRANT'),data,0,actor,note)
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
                from execution.security.output_policy import output_authorization_requirements
                requirements=output_authorization_requirements(plan)
                if requirements is None:
                    raise DomainError('方案包含不支持的健康信息输出，拒绝执行',403)
                def verify_approval(required,consent_key,actor_key,label):
                    if not required:return
                    consent=grant.get(consent_key) or {}
                    consent_ids=consent.get('field_ids');consent_fields=consent.get('fields')
                    field_map=({item.get('field_id'):item for item in consent_fields
                                if isinstance(item,dict)} if isinstance(consent_fields,list) else {})
                    if (consent.get('decision')!='approved' or consent.get('approver_role')!='platform'
                            or consent.get('approver_id')!=grant.get(actor_key)
                            or consent.get('requester_id')!=grant.get('subject')
                            or not isinstance(consent.get('request_id'),str) or not consent.get('request_id')
                            or consent.get('plan_id')!=plan['id']
                            or consent.get('plan_version')!=plan['version']
                            or consent.get('purpose')!=grant.get('purpose')
                            or not isinstance(consent_ids,list)
                            or any(not isinstance(field_id,str) for field_id in consent_ids)
                            or set(consent_ids)!=set(required) or field_map!=required):
                        raise DomainError('缺少平台方对需求方该字段申请的有效批准：'+label,403)
                verify_approval(requirements['health'],'health_output_authorization',
                                'health_output_authorized_by','健康信息')
                verify_approval(requirements['identifier'],'identifier_output_authorization',
                                'identifier_output_authorized_by','原始患者标识')
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
