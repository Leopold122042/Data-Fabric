"""Server-side delivery checks and locally signed receipts, never trusted timestamps."""
import hashlib,hmac,json,os,secrets,math
from pathlib import Path
from datetime import datetime,timezone
from platform_core.storage import DomainError,new_id,now
from execution.security.control import timestamp
from execution.security.output_policy import sensitive_output_allowed,policy_for


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


class Delivery:
    def __init__(self,store,security):
        self.store,self.security=store,security
        path=Path(store.path).parent/'receipt.key'
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as f:f.write(secrets.token_bytes(32))
        except FileExistsError:pass
        self.key=path.read_bytes()
    def checks(self,result,plan,grant,quality=None):
        rows=result.get('rows') if isinstance(result,dict) else None
        columns=result.get('columns') if isinstance(result,dict) else None
        shape=isinstance(rows,list) and isinstance(columns,list) and bool(columns) and all(isinstance(c,str) for c in columns) and len(set(columns))==len(columns) and all(isinstance(r,list) and len(r)==len(columns) for r in rows)
        def finite(value):
            if isinstance(value,float):return math.isfinite(value)
            if isinstance(value,list):return all(finite(x) for x in value)
            if isinstance(value,dict):return all(finite(x) for x in value.values())
            return True
        shape=shape and finite(rows)
        sensitive=sensitive_output_allowed(result,plan)
        cells=[v for row in rows for v in row] if shape else []
        measured_quality=shape and (not cells or sum(v is not None for v in cells)/len(cells)>=plan.get('quality_min_complete',1))
        quality=measured_quality and (quality is None or quality is True)
        aggregate=plan['output_mode']=='aggregate'
        granularity=not aggregate or (shape and 'count' in columns and all(type(r[columns.index('count')]) is int and r[columns.index('count')]>=grant['min_group_size'] for r in rows))
        return {'format':shape,'quality':quality is True,'authorization':grant['status']=='active' and timestamp(grant['expires_at'])>datetime.now(timezone.utc),
                'sensitive_information':sensitive,'granularity':granularity,'subsequent_purpose':bool(plan['purpose']) and plan['purpose']==grant['purpose']}
    def create(self,result,plan,grant_id,actor,run_id,quality=None):
        grant,decision=self.security.check(grant_id,plan,actor,plan['purpose'],'delivery')
        checks=self.checks(result,plan,grant,quality)
        if not all(checks.values()):raise DomainError('交付复核未通过：'+', '.join(k for k,v in checks.items() if not v),403)
        if len(result['rows'])>grant['max_rows']:raise DomainError('交付行数超出授权',403)
        oid=new_id('DEL')
        receipt={'schema':'local-demo-receipt-v1','delivery_id':oid,'run_id':run_id,'plan_id':plan['id'],'plan_version':plan['version'],
                 'spec_id':plan.get('spec_id'),'spec_version':plan.get('spec_version'),'service_versions':plan.get('service_versions',{}),
                 'grant_id':grant_id,'decision_id':decision['id'],'actor':actor,'issued_at':now(),'expires_at':grant['expires_at'],
                 'result_digest':digest(result),'output_policy_digest':digest(policy_for(plan)),
                 'execution_policy_digest':plan.get('execution_policy_digest'),'checks':checks,'assurance':'本地HMAC校验；非第三方存证或可信时间戳'}
        signature=hmac.new(self.key,canonical(receipt),hashlib.sha256).hexdigest()
        return self.store.save('delivery',oid,{'actor':actor,'plan_id':plan['id'],'plan_version':plan['version'],'grant_id':grant_id,
            'state':'available','result':result,'receipt':receipt,'signature':signature},0,actor,'六项复核通过，生成本地凭证')
    def read(self,oid,actor,plan):
        d=self.store.require('delivery',oid)
        if d['actor']!=actor:raise DomainError('无权读取该交付物',403)
        self.security.check(d['grant_id'],plan,actor,plan['purpose'],'retrieve')
        if d['state']!='available':raise DomainError('平台副本已销毁',410)
        if not isinstance(d.get('receipt'),dict) or not isinstance(d.get('signature'),str):
            raise DomainError('交付缺少可验证凭证，请重新执行',403)
        if not self.verify(d['receipt'],d['signature'])['valid'] or d['receipt'].get('result_digest')!=digest(d['result']):
            raise DomainError('交付结果或凭证完整性校验未通过',403)
        if d['receipt'].get('output_policy_digest')!=digest(policy_for(plan)) or d['receipt'].get('execution_policy_digest')!=plan.get('execution_policy_digest'):
            raise DomainError('交付凭证未绑定当前输出及执行条件，请重新执行',403)
        # Previously stored results must not bypass the current output boundary.
        grant=self.store.require('grant',d['grant_id'])
        checks=self.checks(d['result'],plan,grant)
        if not all(checks.values()):raise DomainError('交付复核未通过：'+', '.join(k for k,v in checks.items() if not v),403)
        if len(d['result']['rows'])>grant['max_rows']:raise DomainError('交付行数超出授权',403)
        return d
    def destroy(self,oid,actor):
        d=self.store.require('delivery',oid)
        if actor!=d['actor'] and actor!='demo-platform':raise DomainError('无权处置副本',403)
        with self.store.atomic():
            with self.store.connection() as con:
                rows=con.execute('SELECT version,payload FROM versions WHERE kind=? AND id=? AND environment=?',('delivery',oid,self.store.environment)).fetchall()
                for row in rows:
                    payload=json.loads(row['payload']);payload['result']=None
                    con.execute('UPDATE versions SET payload=? WHERE kind=? AND id=? AND version=? AND environment=?',(json.dumps(payload,ensure_ascii=False),'delivery',oid,row['version'],self.store.environment))
            return self.store.save('delivery',oid,{**d,'result':None,'state':'destroyed','destroyed_at':now()},d['version'],actor,'平台逻辑副本清除，保留凭证；不声明磁盘取证擦除或撤回已下载副本')
    def verify(self,receipt,signature):
        expected=hmac.new(self.key,canonical(receipt),hashlib.sha256).hexdigest()
        return {'valid':hmac.compare_digest(expected,str(signature)),'scope':'local-demo','third_party_verified':False}
