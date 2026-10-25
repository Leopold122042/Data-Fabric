from datetime import datetime,timedelta,timezone
from statistics import mean
from management.store import DomainError,new_id


class Reconciliation:
    def __init__(self,store,evidence,catalog):
        self.store,self.evidence,self.catalog=store,evidence,catalog
        self.controller=None

    def assess(self,run):
        kind,oid,version=run['object_kind'],run['object_id'],run['object_version']
        design=self.store.get(kind,oid,version)
        if design is None:
            design=next((d['definition'] for d in self.store.list('external_design') if d['object_kind']==kind and
                         d['object_id']==oid and d['object_version']==version and d['environment']==run['environment']),None)
        if run['environment']!=self.store.environment or design is None:
            return None,{'unpaired':'缺少对应对象、版本或环境的设计快照'}
        deviations={};observed=run.get('observed',{})
        if observed.get('structure') is not None and design.get('structure')!=observed['structure']:
            deviations['structure']='结构或类型不符合加载版本'
        baseline=design.get('semantic_baseline')
        if baseline and observed.get('distribution_distance') is not None and observed.get('mapping_hit_rate') is not None:
            if observed['distribution_distance']>baseline.get('max_distribution_distance',1) and observed['mapping_hit_rate']<baseline.get('min_hit_rate',0):
                deviations['semantic']='取值分布与映射命中率同时偏离'
        # Use a recent observed baseline, not a hard-coded simulated latency.
        previous=[r for r in self.evidence.list() if r['id']!=run['id'] and r['object_id']==oid and r['object_version']==version
                  and r['environment']==run['environment'] and r['updated_at']<run['updated_at'] and r.get('latency_ms') is not None]
        previous=sorted(previous,key=lambda x:x['updated_at'])[-20:]
        if len(previous)>=3 and run.get('latency_ms') is not None:
            avg=mean(r['latency_ms'] for r in previous)
            fail=mean(r['status']=='failed' for r in previous)
            if run['latency_ms']>max(avg*2,1) or (run['status']=='failed' and fail<.2):deviations['execution']='相对近期执行基线耗时或失败表现异常'
        if run.get('authorization') in ('denied','expired','revoked') and run.get('delivered'):
            deviations['fulfillment']='授权不允许但已交付'
        if run.get('authorized_until') and run.get('delivered_at') and run['delivered_at']>=run['authorized_until']:
            deviations['fulfillment']='授权期限已过仍然交付'
        if run.get('quality_passed') is False and run.get('delivered'):deviations['fulfillment']='质量复核未通过但已交付'
        return design,deviations

    def run(self):
        results=[]
        for run in self.evidence.list():
            key='PAIR-'+run['id'];existing=self.store.get('pair',key)
            design,deviations=self.assess(run)
            if existing and existing.get('paired') and existing.get('deviations')==deviations:continue
            pair=self.store.save('pair',key,{'run_id':run['id'],'object_id':run['object_id'],'object_version':run['object_version'],
                'paired':design is not None,'deviations':deviations},existing['version'] if existing else 0,reason='设计运行配对')
            results.append(pair)
            if design is None:continue
            for category,reason in deviations.items():
                cid='CASE-'+run['object_id']+'-'+str(run['object_version'])+'-'+category
                old=self.store.get('case',cid)
                if old and run['id'] in old.get('run_ids',[]):continue
                self.store.save('case',cid,{**(old or {}),'category':category,'object_id':run['object_id'],
                    'object_kind':run['object_kind'],'object_version':run['object_version'],'reason':reason,
                    'status':'open','opened_at':(old or {}).get('opened_at',run['updated_at']),
                    'run_ids':(old or {}).get('run_ids',[])+[run['id']],
                    'reopen_count':(old or {}).get('reopen_count',0)+int(bool(old and old.get('status')=='closed')),
                    'impact':self.catalog.impact(run['object_id'])['downstream']},old['version'] if old else 0,reason='偏差发现')
        return results

    def action(self,oid,body,actor):
        case=self.store.require('case',oid)
        action=body.get('action')
        if not body.get('note'):raise DomainError('处置说明必填')
        if action not in ('observe','local_fix','pause_review','design_change','close'):raise DomainError('未知处置动作')
        data={**case,'last_action':action,'note':body['note']}
        if action=='close':
            evidence=self.evidence.get(body.get('evidence_id',''))
            if evidence['object_id']!=case['object_id'] or evidence['object_version']!=case['object_version'] or evidence['environment']!=self.store.environment:
                raise DomainError('关闭证据必须匹配对象、版本和环境')
            _,deviations=self.assess(evidence)
            if 'unpaired' in deviations or case['category'] in deviations or evidence['status']!='success':raise DomainError('复测仍存在该类偏差，不能关闭')
            original=[self.evidence.get(x) for x in case['run_ids']]
            if evidence['updated_at']<=max(r['updated_at'] for r in original):raise DomainError('需使用偏差发生后的复测证据')
            if case['category']=='structure' and 'structure' not in evidence.get('observed',{}):raise DomainError('缺少结构复核证据')
            if case['category']=='semantic' and not {'distribution_distance','mapping_hit_rate'}<=set(evidence.get('observed',{})):raise DomainError('缺少口径复核证据')
            if case['category']=='execution' and evidence.get('latency_ms') is None:raise DomainError('缺少执行时延复核证据')
            if case['category']=='fulfillment' and (evidence.get('authorization')!='allowed' or evidence.get('quality_passed') is not True):raise DomainError('缺少履约复核证据')
            from management.store import now
            data.update(status='closed',closed_at=now(),closure_evidence=evidence['id'])
        elif action in ('pause_review','local_fix'):
            if self.controller is None:raise DomainError('B侧执行控制接口未接入，不能宣称已暂停或修复',503)
            result=self.controller({'command_id':new_id('CMD'),'case_id':oid,'action':action,'object_id':case['object_id']})
            data.update(status='pending_execution',command_result=result)
        elif action=='design_change':data['status']='design_review'
        else:data['status']='observing'
        return self.store.save('case',oid,data,body.get('expected_version'),actor,'偏差处置')

    def metrics(self,days=30):
        cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        runs=[r for r in self.evidence.list() if r['updated_at']>=cutoff]
        ids={r['id'] for r in runs}
        pairs=[p for p in self.store.list('pair') if p['run_id'] in ids]
        matched=[p for p in pairs if p['paired']]
        errors=sum(bool(p['deviations']) for p in matched)
        cases=[c for c in self.store.list('case') if c.get('opened_at','')>=cutoff]
        durations=[(datetime.fromisoformat(c['closed_at'])-datetime.fromisoformat(c['opened_at'])).total_seconds() for c in cases if c.get('status')=='closed']
        return {'window_days':days,'run_count':len(runs),'paired_count':len(matched),
                'coverage':len(matched)/len(runs) if runs else None,
                'deviation_density':errors/len(matched) if matched else None,
                'mean_resolution_seconds':mean(durations) if durations else None,
                'closed_count':len(durations),'reopen_count':sum(c.get('reopen_count',0) for c in cases),
                'interpretation':'需比较连续同口径窗口；单个窗口不足以判断负载或设计问题',
                'definitions':{'coverage':'已配对运行/全部运行','deviation_density':'存在偏差的已配对运行/已配对运行',
                               'mean_resolution_seconds':'窗口内已关闭偏差从首次发现到最近关闭的平均秒数'}}
