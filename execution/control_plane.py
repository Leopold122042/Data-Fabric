"""B local execution plane: immutable plans, guarded jobs, bounded scheduling and evidence."""
import json,threading,time
from datetime import datetime,timezone
from platform_core.storage import DomainError,new_id,now
from execution.security.control import Security,timestamp
from execution.delivery import Delivery
from execution.query.engine import Queries
from execution.medical import aggregate
from contracts.execution.constraints import fingerprint,normalize
from execution.constraints import compile_node,verify_plan_policy,metadata_result
from execution.security.output_policy import medical_output_policy,metadata_output_policy,view_output_policy,sensitive_output_allowed


class Execution:
    def __init__(self,runtime):
        self.runtime=runtime;self.management=runtime.management
        self.evidence=self.management.evidence;self.store=self.evidence.store
        self.security=Security(self.store);self.delivery=Delivery(self.store,self.security);self.queries=Queries(self.management,self.store)
        self.lock=threading.RLock();self.security.lock=self.lock;self.stop=threading.Event();self.workers=[]
        self.security.plan_validator=self.current
    def start(self):
        for job in self.store.list('job'):
            if job['state']=='running':self.update('job',job,{'state':'interrupted','error':'进程重启，需人工重试'})
        for i in range(2):
            t=threading.Thread(target=self.loop,daemon=True,name='execution-worker-'+str(i));self.workers.append(t);t.start()
        monitor=threading.Thread(target=self.monitor,daemon=True,name='execution-control-monitor');self.workers.append(monitor);monitor.start()
    def close(self):
        self.stop.set()
        for t in self.workers:t.join(timeout=6)
    def update(self,kind,obj,changes,actor='execution'):
        return self.store.save(kind,obj['id'],{**obj,**changes},obj['version'],actor,'执行状态更新')
    def get(self,plan_id,version):return self.store.require('plan',plan_id,version)
    def snapshot(self,kind,obj,dependencies):
        return self.management.register_design({'object_kind':kind,'object_id':obj['id'],'object_version':obj['version'],
            'environment':self.store.environment,'definition':obj,'dependencies':dependencies})
    def plan(self,body,actor):
        sid=body.get('spec_id');spec=None
        if sid:
            spec=self.management.store.require('spec',sid,body.get('spec_version'))
            if not spec['confirmed']:raise DomainError('规格未人工确认')
            demand=self.management.store.require('demand',spec['demand_id'])
            if actor!='demo-platform' and demand['created_by']!=actor:raise DomainError('无权使用他人的任务规格',403)
        elif body.get('scenario')!='medical':raise DomainError('需选择A侧已确认规格')
        if sid and body.get('scenario')=='medical':raise DomainError('医疗快捷样例不能替代已确认规格的执行条件')
        policy=normalize(spec['specification']) if spec else None
        mode=body.get('output_mode',policy['output_mode'] if policy else 'aggregate')
        if policy and mode!=policy['output_mode']:raise DomainError('方案输出粒度与确认规格不一致',403)
        if mode not in ('aggregate','object'):raise DomainError('输出粒度非法')
        nodes=body.get('nodes',[])
        if body.get('scenario')=='medical':
            if mode!='aggregate':raise DomainError('医疗示例仅允许汇总查询',403)
            nodes=[{'id':'medical-summary','operation':'medical','depends_on':[],'params':{}}]
        if not nodes and spec:
            nodes=[{'id':'service-'+str(i+1),'operation':'service','service_id':sid,'depends_on':[],'params':{}} for i,sid in enumerate(spec['specification'].get('service_ids') or [])]
        if not isinstance(nodes,list) or not 1<=len(nodes)<=20:raise DomainError('方案需1至20个节点')
        ids=[n.get('id') for n in nodes]
        if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):raise DomainError('节点编号为空或重复')
        services={};views={};compiled=[];output_policies={}
        for n in nodes:
            service=view=None
            op=n.get('operation')
            if op not in ('service','view','medical'):raise DomainError('未知节点操作')
            if op=='medical' and body.get('scenario')!='medical':raise DomainError('医疗示例只能使用独立虚构场景')
            refs=n.get('inputs',{})
            if not isinstance(refs,dict):raise DomainError('节点输入需为参数名到上游节点的映射')
            deps=list(dict.fromkeys(n.get('depends_on',[])+list(refs.values())))
            if any(d not in ids or d==n['id'] for d in deps):raise DomainError('节点依赖非法')
            if op=='service':
                service=self.management.store.require('service',n.get('service_id',''))
                if service['lifecycle']!='published':raise DomainError('服务尚未发布',409)
                if service.get('visibility')=='specified' and actor not in service.get('allowed_subjects',[]):raise DomainError('服务不对当前主体开放',403)
                if service.get('visibility')=='approval':raise DomainError('审批型服务缺少外部审批适配器',503)
                services[service['id']]=service['version']
            if op=='view':
                view=self.store.require('view',n.get('view_id',''));self.queries.sources(view)
                if view['output_mode']!=mode:raise DomainError('视图与方案粒度不一致')
                views[view['id']]=view['version']
            params=n.get('params',{})
            if not isinstance(params,dict):raise DomainError('节点参数需为对象')
            node={'id':n['id'],'operation':op,'service_id':n.get('service_id'),'view_id':n.get('view_id'),
                'depends_on':deps,'inputs':refs,'params':params,'retries':min(3,max(0,int(n.get('retries',0))))}
            if policy:node['query_constraints']=compile_node(policy,node,view,service)
            if op=='view':output_policies[n['id']]=view_output_policy(view,{fid:self.management.store.require('object',fid,v) for fid,v in view['field_versions'].items()})
            elif op=='medical':output_policies[n['id']]=medical_output_policy()
            else:output_policies[n['id']]=metadata_output_policy()
            compiled.append(node)
        ordered=[];pending=compiled.copy()
        while pending:
            ready=[n for n in pending if set(n['depends_on'])<=set(ordered)]
            if not ready:raise DomainError('流程包含依赖环')
            for n in ready:ordered.append(n['id']);pending.remove(n)
        output=body.get('output_node',ordered[-1])
        if output not in ids:raise DomainError('交付节点不存在')
        if not sensitive_output_allowed({'columns':list(output_policies[output]['columns']),'rows':[]},{'output_policy':output_policies[output]}):
            raise DomainError('输出包含未确认或受限字段，请确认目录分类并改为允许的输出',403)
        if spec and spec['specification'].get('service_ids') and not set(services)<=set(spec['specification']['service_ids']):
            raise DomainError('方案调用了规格未选择的服务',403)
        oid=body.get('id') or new_id('PLAN');old=self.store.get('plan',oid)
        if old and old['created_by']!=actor and actor!='demo-platform':raise DomainError('不能修改他人方案',403)
        purpose=(spec or {}).get('specification',{}).get('purpose') or '统计研究'
        if policy is None:
            policy={'version':1,'scope':{'kind':'medical'},'window_kind':'fixture','deadline_at':'9999-01-01T00:00:00+00:00',
                    'purpose':purpose,'output_mode':'aggregate','circulation_modes':['controlled_compute','api','download'],
                    'quality_min_complete':1,'max_rows':200,'min_group_size':5}
        definition={'name':body.get('name') or '本地方案','created_by':(old or {}).get('created_by',actor),'scenario':body.get('scenario','custom'),
            'workflow_id':'WF-'+oid,'workflow_version':(old or {}).get('version',0)+1,
            'spec_id':sid,'spec_version':spec['version'] if spec else None,'purpose':purpose,'output_mode':mode,'nodes':compiled,'order':ordered,
            'output_node':output,'service_versions':services,'view_versions':views,'parameters':body.get('parameters') or {'output_mode':mode},
            'quality_min_complete':float(body.get('quality_min_complete',policy['quality_min_complete'])),
            'circulation_mode':body.get('circulation_mode',policy['circulation_modes'][0]),'execution_location':'本地执行器',
            'execution_policy':policy,'execution_policy_digest':fingerprint(policy),'output_policy':output_policies[output],
            'difference':{'added_nodes':sorted(set(ids)-{n['id'] for n in (old or {}).get('nodes',[])}),'removed_nodes':sorted({n['id'] for n in (old or {}).get('nodes',[])}-set(ids)),
                          'changed_nodes':[n['id'] for n in compiled if old and n not in old['nodes']]}}
        if definition['circulation_mode'] not in ('controlled_compute','api','download'):raise DomainError('流通模式非法')
        from contracts.execution.constraints import number
        number(definition['quality_min_complete'],'完整率',0,1)
        verify_plan_policy(definition,spec['specification'] if spec else None)
        plan=self.store.save('plan',oid,definition,body.get('expected_version',0),actor,'生成待确认方案；不自动执行')
        flow=self.store.save('workflow',plan['workflow_id'],{'name':definition['name']+' · 执行流程','created_by':definition['created_by'],'plan_id':oid,'plan_version':plan['version'],'nodes':compiled,'order':ordered,'max_workers':2,'failure_strategy':'block_downstream'},(old or {}).get('version',0),actor,'编译固定版本执行DAG')
        self.snapshot('workflow',flow,list(services)+list(views))
        state=self.store.get('plan_state',oid)
        self.store.save('plan_state',oid,{'plan_version':plan['version'],'status':'draft'},state['version'] if state else 0,actor,'新版本需重新确认')
        self.snapshot('plan',plan,list(services)+list(views)+([sid] if sid else []));return plan
    def current(self,plan):
        latest=self.store.require('plan',plan['id']);state=self.store.require('plan_state',plan['id'])
        registered=self.store.require('plan',plan['id'],plan['version'])
        if plan!=registered:raise DomainError('方案定义与登记版本不一致',409)
        if latest['version']!=plan['version'] or state['plan_version']!=plan['version'] or state['status']!='confirmed':raise DomainError('方案未确认、已暂停或版本已失效',409)
        if plan.get('spec_id'):
            spec=self.management.store.require('spec',plan['spec_id']);demand=self.management.store.require('demand',spec['demand_id'])
            if spec['version']!=plan['spec_version'] or demand['state']!='confirmed' or demand.get('spec_version')!=spec['version']:raise DomainError('需求或规格变化，需重新生成并确认方案',409)
            verify_plan_policy(plan,spec['specification'])
        else:verify_plan_policy(plan)
        for sid,version in plan['service_versions'].items():
            s=self.management.store.require('service',sid)
            if s['version']!=version or s['lifecycle']!='published':raise DomainError('服务版本已变化或退出',409)
        for vid,version in plan['view_versions'].items():
            v=self.store.require('view',vid)
            if v['version']!=version:raise DomainError('视图版本变化',409)
            self.queries.sources(v)
    def confirm(self,oid,version,actor):
        plan=self.store.require('plan',oid)
        if plan['version']!=version:raise DomainError('方案版本变化',409)
        if plan['created_by']!=actor and actor!='demo-platform':raise DomainError('仅创建者或平台方可确认',403)
        state=self.store.require('plan_state',oid)
        return self.update('plan_state',state,{'status':'confirmed','confirmed_by':actor,'confirmed_at':now()},actor)
    def submit(self,oid,version,grant_id,actor,retry_of=None,priority=0):
        if type(version) is not int or version<1:raise DomainError('必须指定方案版本')
        plan=self.get(oid,version);self.current(plan)
        self.security.check(grant_id,plan,actor,plan['purpose'],'submission')
        if sum(j['state'] in ('queued','running') and j['actor']==actor for j in self.store.list('job'))>=20:raise DomainError('当前主体排队配额已满',429)
        if retry_of:
            prior=self.store.require('job',retry_of)
            if (prior['actor'],prior['plan_id'],prior['plan_version'])!=(actor,oid,version) or prior['state'] not in ('failed','interrupted','cancelled'):raise DomainError('仅能重试本人同版本失败任务')
        return self.store.save('job',new_id('JOB'),{'plan_id':oid,'plan_version':version,'grant_id':grant_id,'actor':actor,'purpose':plan['purpose'],
            'state':'queued','priority':min(9,max(0,int(priority))),'retry_of':retry_of,'nodes':{},'timeline':[{'stage':'submitted','time':now()}]},0,actor,'任务入队')
    def cancel(self,oid,actor):
        with self.lock:
            job=self.store.require('job',oid)
            if actor!=job['actor'] and actor!='demo-platform':raise DomainError('无权取消',403)
            if any(d['receipt']['run_id']==oid for d in self.store.list('delivery')):raise DomainError('结果已经交付，不能取消已提交的交付',409)
            if job['state'] not in ('queued','running'):raise DomainError('任务已经结束',409)
            return self.update('job',job,{'cancel_requested':True,'state':'cancelled' if job['state']=='queued' else 'running'},actor)
    def guard(self,job,plan,stage):
        latest=self.store.require('job',job['id'])
        if self.stop.is_set() or latest.get('cancel_requested'):raise DomainError(latest.get('cancel_reason') or '任务已中止',409)
        self.current(plan)
        return self.security.check(job['grant_id'],plan,job['actor'],job['purpose'],stage)[0]
    def run(self,job):
        start=time.perf_counter();plan=self.get(job['plan_id'],job['plan_version']);results={};node_states={};delivery=None;error=None
        timeline=list(job['timeline']);previous=self.store.get('job',job.get('retry_of','')) if job.get('retry_of') else None
        try:
            for nid in plan['order']:
                node=next(n for n in plan['nodes'] if n['id']==nid);grant=self.guard(job,plan,'node:'+nid)
                timeline.append({'stage':'node_started','node':nid,'time':now()})
                prior=(previous or {}).get('nodes',{}).get(nid)
                # Only immutable same-version local medical/service results may be reused; source views are re-read.
                if prior and prior['status']=='success' and node['operation']=='medical' and self.store.get('node_result',previous['id']+':'+nid):
                    result=self.store.require('node_result',previous['id']+':'+nid)['result'];trace={'reused_completed_node':True}
                else:
                    for attempt in range(node['retries']+1):
                        self.guard(job,plan,'attempt:'+nid)
                        try:
                            params={**node['params'],**{k:results[v] for k,v in node['inputs'].items()}}
                            if node['operation']=='medical':result=aggregate(grant['min_group_size']);trace={'fixture':'reconstructed-fictional','suppression':True}
                            elif node['operation']=='view':
                                view=self.get_view(plan,node)
                                constraints=compile_node(plan['execution_policy'],node,view)
                                if constraints!=node.get('query_constraints'):raise DomainError('节点执行范围与确认规格不一致',403)
                                result,trace=self.queries.execute(view,plan['output_mode'],grant['min_group_size'],constraints)
                            else:
                                service=self.management.store.require('service',node['service_id'])
                                compile_node(plan['execution_policy'],node,service=service)
                                if set(params)-{'previous'}:raise DomainError('运行输入试图覆盖受控参数',403)
                                # Entering a node is not evidence of a service call:
                                # attempt authorization and parameter guards run first.
                                timeline.append({'stage':'service_invoked','node':nid,'service_id':node['service_id'],
                                                 'attempt':attempt+1,'time':now()})
                                result=self.runtime._dispatch(node['service_id'],params)
                                result=metadata_result(result)
                                trace={'service_id':node['service_id'],'service_version':plan['service_versions'][node['service_id']]}
                            break
                        except DomainError:raise
                        except Exception:
                            if attempt==node['retries']:raise DomainError('节点执行失败；请检查参数、来源及服务可达性',502)
                            if self.stop.wait(.2):raise DomainError('执行器已停止',503)
                self.guard(job,plan,'node_completed:'+nid)
                if node['operation']=='medical':
                    self.store.save('node_result',job['id']+':'+nid,{'result':result,'expires_epoch':time.time()+60},0,'execution','短期保留已完成虚构节点，供局部重试')
                results[nid]=result;node_states[nid]={'status':'success','trace':trace,'finished_at':now()}
                with self.lock:
                    active=self.store.require('job',job['id']);self.update('job',active,{'nodes':node_states,'timeline':timeline})
            output=results[plan['output_node']]
            if not isinstance(output,dict) or 'columns' not in output:raise DomainError('服务未提供受控表格交付适配器，不能直接交付原始对象',409)
            self.guard(job,plan,'before_delivery')
            with self.lock,self.management.store.lock:
                self.guard(job,plan,'delivery_commit')
                delivery=self.delivery.create(output,plan,job['grant_id'],job['actor'],job['id'])
            state='success';timeline.append({'stage':'delivered','time':now(),'delivery_id':delivery['id']})
        except DomainError as exc:state='failed';error=str(exc)
        except Exception:state='failed';error='执行异常，未交付结果'
        for node in plan['nodes']:
            if node['id'] not in node_states:node_states[node['id']]={'status':'blocked','reason':error}
        timeline.append({'stage':state,'time':now()})
        evidence={'id':job['id'],'object_kind':'plan','object_id':plan['id'],'object_version':plan['version'],'plan_id':plan['id'],'plan_version':plan['version'],
            'execution_policy_digest':plan.get('execution_policy_digest'),
            'environment':self.store.environment,'status':state,'task_scope':'plan:'+plan['id'],'actor':job['actor'],'latency_ms':round((time.perf_counter()-start)*1000,3),
            'authorization':'allowed' if delivery else 'not_delivered','delivered':delivery is not None,'quality_passed':True if delivery else None,
            'delivery_id':delivery['id'] if delivery else None,'error':error,'nodes':node_states}
        if delivery:evidence.update(delivered_at=delivery['receipt']['issued_at'],authorized_until=delivery['receipt']['expires_at'])
        self.evidence.record(evidence)
        with self.lock:
            active=self.store.require('job',job['id']);return self.update('job',active,{'state':state,'error':error,'nodes':node_states,'timeline':timeline,'delivery_id':evidence['delivery_id']})
    def get_view(self,plan,node):return self.store.require('view',node['view_id'],plan['view_versions'][node['view_id']])
    def loop(self):
        while not self.stop.wait(.25):
            try:
                with self.lock:
                    queued=sorted((j for j in self.store.list('job') if j['state']=='queued'),key=lambda j:(-j['priority'],j['updated_at']))
                    job=self.update('job',queued[0],{'state':'running'}) if queued else None
                if job:self.run(job)

            except Exception:pass  # Stored jobs stay inspectable; a restart marks interrupted work.
    def consume_events(self):
        with self.lock:
            cursor=self.store.get('cursor','management')
            events=self.management.store.events((cursor or {}).get('after',0))
            if events:
                for p in self.store.list('plan'):
                    state=self.store.require('plan_state',p['id'])
                    if state['status']=='confirmed':
                        try:self.current(p)
                        except DomainError:self.update('plan_state',state,{'status':'invalidated','reason':'依赖版本或有效状态变化'})
                self.store.save('cursor','management',{'after':events[-1]['seq']},cursor['version'] if cursor else 0,'execution','A事件消费游标')
    def control(self,command):
        with self.lock,self.store.atomic():
            existing=self.store.get('command',command['command_id'])
            if existing:return existing
            plan=self.store.get('plan',command['object_id'])
            if not plan:raise DomainError('本地控制仅支持已登记方案，请人工处理其他对象',409)
            if command.get('case_id') and (type(command.get('object_version')) is not int or command['object_version']!=plan['version']):
                raise DomainError('偏差工单关联的方案版本已变化，请重新核验后处置',409)
            if command['action']=='pause':
                self.update('plan_state',self.store.require('plan_state',plan['id']),{'status':'paused'})
                result={'status':'applied','reason':'后续节点及交付入口已阻断'}
            elif command['action']=='local_fix':
                failed=[j for j in self.store.list('job') if j['plan_id']==plan['id'] and j['state']=='failed']
                if not failed:raise DomainError('没有可局部重试的失败任务',409)
                old=sorted(failed,key=lambda j:j['updated_at'])[-1]
                job=self.submit(plan['id'],plan['version'],old['grant_id'],old['actor'],old['id']);result={'status':'queued','job_id':job['id']}
            else:raise DomainError('不支持的控制命令')
            return self.store.save('command',command['command_id'],{**command,**result},0,'reconciliation','执行控制回执')

    def trigger(self,body,actor):
        p=self.get(body.get('plan_id',''),body.get('plan_version'));self.current(p)
        self.security.check(body.get('grant_id',''),p,actor,p['purpose'],'trigger_registration')
        kind=body.get('kind')
        if kind not in ('interval','metadata_change','task_completed'):raise DomainError('触发类型非法')
        if kind=='task_completed':
            if body.get('source_plan')==p['id']:raise DomainError('不能用本方案完成事件递归触发自身')
            self.store.require('plan',body.get('source_plan',''))
        cursor=0
        while True:
            events=self.management.store.events(cursor,500)
            if not events:break
            cursor=events[-1]['seq']
        return self.store.save('trigger',new_id('TRIGGER'),{'actor':actor,'plan_id':p['id'],'plan_version':p['version'],'grant_id':body['grant_id'],
            'kind':kind,'interval':max(5,int(body.get('interval',60))),'source_plan':body.get('source_plan'),'enabled':True,'next_at':time.time()+max(5,int(body.get('interval',60))),
            'event_after':cursor,'job_after':now()},0,actor,'登记显式触发器')
    def tick_triggers(self):
        with self.lock:
            for trigger in self.store.list('trigger'):
                if not trigger['enabled']:continue
                due=False;changes={}
                if trigger['kind']=='interval' and time.time()>=trigger['next_at']:
                    due=True;changes['next_at']=time.time()+trigger['interval']
                elif trigger['kind']=='metadata_change':
                    events=self.management.store.events(trigger['event_after'])
                    if events:
                        due=any(x['kind']=='object' for x in events);changes['event_after']=events[-1]['seq']
                elif trigger['kind']=='task_completed':
                    jobs=[j for j in self.store.list('job') if j['state']=='success' and j['plan_id']==trigger.get('source_plan') and j['updated_at']>trigger['job_after']]
                    if jobs:due=True;changes['job_after']=max(j['updated_at'] for j in jobs)
                if due:
                    try:
                        job=self.submit(trigger['plan_id'],trigger['plan_version'],trigger['grant_id'],trigger['actor']);changes['last_job_id']=job['id'];changes['last_error']=None
                    except DomainError as exc:changes.update(last_error=str(exc),enabled=False)
                if changes:self.update('trigger',trigger,changes)

    def cleanup(self):
        with self.lock,self.store.atomic():
            for kind in ('materialization','node_result'):
                for item in self.store.list(kind):
                    if item.get('expires_epoch',0)<time.time():
                        with self.store.connection() as con:
                            con.execute('DELETE FROM versions WHERE kind=? AND id=? AND environment=?',(kind,item['id'],self.store.environment))
            for d in self.store.list('delivery'):
                if d['state']=='available' and timestamp(d['receipt']['expires_at'])<=datetime.now(timezone.utc):
                    self.delivery.destroy(d['id'],'demo-platform')

    def monitor(self):
        while not self.stop.wait(.25):
            try:
                with self.lock:
                    for job in self.store.list('job'):
                        if job['state']=='running' and not job.get('cancel_requested'):
                            grant=self.store.get('grant',job['grant_id'])
                            if not grant or grant['status']!='active' or timestamp(grant['expires_at'])<=datetime.now(timezone.utc):
                                self.update('job',job,{'cancel_requested':True,'cancel_reason':'授权已撤销或到期','blocked_observed_at':now()})
                self.consume_events();self.tick_triggers();self.cleanup()
            except Exception:
                # No authorization decision falls back to allow. Guards recheck synchronously.
                continue
