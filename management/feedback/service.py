from statistics import mean
from management.store import DomainError,new_id


class Feedback:
    def __init__(self,store,evidence):self.store,self.evidence=store,evidence

    def evaluate(self,body,actor):
        run=self.evidence.get(body.get('run_id',''))
        if run['status'] not in ('success','failed','blocked'):raise DomainError('任务尚未结束')
        ratings=body.get('ratings',{})
        if set(ratings)!={'usability','explainability','timeliness','satisfaction'} or any(type(v)!=int or not 1<=v<=5 for v in ratings.values()):
            raise DomainError('四项评价均须为1到5的整数')
        return self.store.save('feedback',new_id('FB'),{'run_id':run['id'],'object_id':run['object_id'],
            'object_version':run['object_version'],'plan_id':run.get('plan_id'),'ratings':ratings,'comment':body.get('comment',''),
            'evaluated_by':actor},0,actor,'用户评价')

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
