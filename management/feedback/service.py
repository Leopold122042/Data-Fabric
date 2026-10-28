from statistics import mean
from management.store import DomainError,new_id


class Feedback:
    def __init__(self,store,evidence):self.store,self.evidence=store,evidence

    def evaluate(self,body,actor):
        run=self.evidence.get(body.get('run_id',''))
        if run['status'] not in ('success','failed','blocked'):raise DomainError('任务尚未结束')
        scope={}
        service_id=body.get('service_id')
        if service_id is not None and not isinstance(service_id,str):raise DomainError('评价服务范围格式错误')
        if service_id:
            service_id=service_id.strip()
            plan=self.evidence.store.get('plan',run.get('plan_id',''),run.get('plan_version'))
            if not plan or not run.get('plan_version') or service_id not in plan.get('service_versions',{}):
                raise DomainError('评价服务不属于本次运行的固定方案版本')
            version=plan['service_versions'][service_id]
            if not self.store.get('service',service_id,version):raise DomainError('本次运行的服务版本不存在')
            job=self.evidence.store.get('job',run['id']) or {}
            if (job.get('plan_id'),job.get('plan_version'))!=(plan['id'],plan['version']):job={}
            invoked=False
            for node in plan.get('nodes',[]):
                if node.get('operation')!='service' or node.get('service_id')!=service_id:continue
                node_id=node.get('id')
                completed=any(source.get('nodes',{}).get(node_id,{}).get('status')=='success' for source in (run,job))
                dispatched=any(item.get('stage')=='service_invoked' and item.get('node')==node_id
                               and item.get('service_id')==service_id for item in job.get('timeline',[]))
                invoked=invoked or completed or dispatched
            if not invoked:raise DomainError('该服务在本次运行中尚未调用，不能提交服务评价')
            # A scoped comment is deliberately addressed to this service. An
            # unscoped task comment never acquires a service audience implicitly.
            scope={'service_id':service_id,'service_version':version}
        ratings=body.get('ratings',{})
        if set(ratings)!={'usability','explainability','timeliness','satisfaction'} or any(type(v)!=int or not 1<=v<=5 for v in ratings.values()):
            raise DomainError('四项评价均须为1到5的整数')
        return self.store.save('feedback',new_id('FB'),{'run_id':run['id'],'object_id':run['object_id'],
            'object_version':run['object_version'],'plan_id':run.get('plan_id'),'ratings':ratings,'comment':body.get('comment',''),
            'evaluated_by':actor,**scope},0,actor,'用户评价')

    def compare(self,body,actor):
        before=[self.evidence.get(x) for x in body.get('before',[])]
        after=[self.evidence.get(x) for x in body.get('after',[])]
        if not before or not after:raise DomainError('基线和复测均需运行证据')
        if len({r['id'] for r in before+after})!=len(before+after):raise DomainError('基线与复测证据不可重复')
        if len({r['task_scope'] for r in before+after})!=1:raise DomainError('必须在相同任务范围复测')
        if max(r['updated_at'] for r in before)>=min(r['updated_at'] for r in after):raise DomainError('复测必须晚于基线')
        if not body.get('change_note'):raise DomainError('需填写修订内容')
        def stats(rows):
            latencies=[r['latency_ms'] for r in rows if r.get('latency_ms') is not None]
            missing=[r['missing_rate'] for r in rows if r.get('missing_rate') is not None]
            manual=[r['manual_count'] for r in rows if r.get('manual_count') is not None]
            return {'denominator':len(rows),'success_rate':sum(r['status']=='success' for r in rows)/len(rows),
                    'mean_latency_ms':mean(latencies) if latencies else None,'latency_samples':len(latencies),
                    'missing_rate':mean(missing) if missing else None,'missing_samples':len(missing),
                    'manual_count':sum(manual) if manual else None,'manual_samples':len(manual),
                    'window_start':min(r['updated_at'] for r in rows),'window_end':max(r['updated_at'] for r in rows)}
        b,a=stats(before),stats(after)
        return self.store.save('improvement',new_id('IMP'),{'before':body['before'],'after':body['after'],
            'task_scope':before[0]['task_scope'],'baseline':b,'retest':a,'change_note':body['change_note'],
            'success_rate_improved':a['success_rate']>b['success_rate'],'status':'reviewed'},0,actor,'同范围复测，保留所有结果')

    def definition(self,body,actor):
        for k in ('name','definition','source','denominator','window'):
            if not body.get(k):raise DomainError('指标口径缺少：'+k)
        return self.store.save('metric_definition',body.get('id') or new_id('METRIC'),body,body.get('expected_version',0),actor,'评价口径版本')
