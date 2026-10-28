"""Role-scoped work orders projected from immutable business records.

This is a tracking and collaboration view, not a second execution state machine.
Providers receive a deliberately small projection of their own service activity.
"""
from platform_core.access import require_resource, require_action, can_read_management
from platform_core.storage import DomainError, new_id


ACTORS = {'demo-platform': '数据服务中心', 'demo-provider': '医院数据中心',
          'demo-requester': '临床研究中心', 'demo-auditor': '数据合规岗'}
STATUS = {'draft': '待确认', 'confirmed': '待编制方案', 'planning': '待确认方案',
          'authorized': '待执行', 'running': '执行中', 'delivered': '已交付',
          'attention': '待处理', 'awaiting_authorization': '待授权'}
NEXT = {'draft': '补全需求并由平台确认', 'confirmed': '从已确认规格编制执行方案',
        'planning': '核对并确认当前方案', 'authorized': '按已授权方案执行',
        'running': '查看执行进度', 'delivered': '查看交付凭证并提交使用反馈',
        'attention': '查看异常记录，补正后重新确认或重试', 'awaiting_authorization': '申请当前版本的执行授权'}


def actor_name(value):
    return ACTORS.get(value, value or '未登记')


def stamp(item):
    return item.get('updated_at') or item.get('time') or ''


def key(item):
    return item.get('plan_id'), item.get('plan_version')


def called_services(plan, run, job):
    if key(job) != key(run):
        job = {}
    nodes = {**run.get('nodes', {}), **job.get('nodes', {})}
    invoked = {(item.get('node'), item.get('service_id')) for item in job.get('timeline', []) if item.get('stage') == 'service_invoked'}
    return {node['service_id'] for node in plan.get('nodes', [])
            if node.get('operation') == 'service' and node.get('service_id') in plan.get('service_versions', {})
            and ((node['id'], node['service_id']) in invoked or nodes.get(node['id'], {}).get('status') == 'success')}


class WorkOrders:
    def __init__(self, runtime):
        self.runtime = runtime
        self.m = runtime.management.store
        self.e = runtime.execution.store

    def _histories(self, store, kind):
        return [version for row in store.list(kind) for version in store.history(kind, row['id'])]

    def _context(self):
        return {'plans': self._histories(self.e, 'plan'),
                'specs': self._histories(self.m, 'spec'),
                **{kind: self.e.list(kind) for kind in ('job', 'delivery', 'decision', 'run', 'grant', 'plan_state')},
                **{kind: self.m.list(kind) for kind in ('feedback', 'case', 'work_order_message')}}

    def _related(self, demand, context):
        demand_versions = {d['version'] for d in self.m.history('demand', demand['id'])}
        specs = {(s['id'], s['version']): s for s in context['specs'] if s.get('demand_id') == demand['id']
                 and s.get('demand_version') in demand_versions}
        plans = [p for p in context['plans'] if (p.get('spec_id'), p.get('spec_version')) in specs]
        return specs, plans

    def _services(self, demand, plans, user):
        # Selected, planned and actually executed services are distinct states.
        services = {}
        for sid in demand.get('service_ids', []):
            service = self.m.get('service', sid)
            if service and (user['role'] != 'requester' or can_read_management(self.runtime, 'service', service, user)):
                services[sid] = service
        for plan in plans:
            for sid, version in plan.get('service_versions', {}).items():
                service = self.m.get('service', sid, version)
                if service:
                    if user['role'] == 'provider' and service.get('provider') == user['id']:
                        services[sid] = service
                    else:
                        services.setdefault(sid, service)
        return services

    def _visible(self, demand, services, user):
        if user['role'] in ('platform', 'auditor'):
            return True
        if user['role'] == 'requester':
            return demand.get('created_by') == user['id']
        return user['role'] == 'provider' and any(s.get('provider') == user['id'] for s in services.values())

    def list(self, user, q='', status=''):
        require_resource(user, 'work_order')
        context = self._context()
        items = []
        for demand in self.m.list('demand'):
            specs, plans = self._related(demand, context)
            services = self._services(demand, plans, user)
            if not self._visible(demand, services, user):
                continue
            detail = self._project(demand, specs, plans, services, context, user)
            summary = {k: detail[k] for k in ('id', 'title', 'purpose', 'requester', 'requester_name', 'status', 'status_label', 'next_action', 'updated_at', 'counts')}
            summary['service_ids'] = [s['id'] for s in detail['services']]
            if (not q or q.lower() in str(summary).lower()) and (not status or status == summary['status']):
                items.append(summary)
        items.sort(key=lambda x: x['updated_at'], reverse=True)
        return {'items': items, 'counts': {'total': len(items),
                'active': sum(x['status'] not in ('delivered', 'attention') for x in items),
                'attention': sum(x['status'] == 'attention' or x['counts']['exceptions'] > 0 for x in items),
                'delivered': sum(x['status'] == 'delivered' for x in items)}}

    def get(self, oid, user):
        require_resource(user, 'work_order')
        demand = self.m.get('demand', oid)
        if not demand:
            raise DomainError('工单不存在或当前身份不可查看', 404)
        context = self._context()
        specs, plans = self._related(demand, context)
        services = self._services(demand, plans, user)
        if not self._visible(demand, services, user):
            raise DomainError('工单不存在或当前身份不可查看', 404)
        return self._project(demand, specs, plans, services, context, user)

    def _project(self, demand, specs, plans, services, context, user):
        provider = user['role'] == 'provider'
        historical_scope = False
        if provider:
            # Participation in an earlier revision must not disclose a later,
            # unrelated project's title, purpose or changes under the same ID.
            current_participant = any((self.m.get('service', sid) or {}).get('provider') == user['id']
                                      for sid in demand.get('service_ids', []))
            plans = [p for p in plans if any(n.get('operation') == 'service'
                     and (self.m.get('service', n.get('service_id', ''), p.get('service_versions', {}).get(n.get('service_id'))) or {}).get('provider') == user['id']
                     for n in p.get('nodes', []))]
            related_specs = {(p.get('spec_id'), p.get('spec_version')) for p in plans}
            if current_participant:
                related_specs.add((demand.get('spec_id'), demand.get('spec_version')))
            specs = {reference: spec for reference, spec in specs.items() if reference in related_specs}
            if not current_participant and specs:
                last_spec = max(specs.values(), key=stamp)
                history = self.m.history('demand', demand['id'])
                confirmed = next((d for d in history if d.get('state') == 'confirmed' and d.get('spec_id') == last_spec['id']
                                  and d.get('spec_version') == last_spec['version']), None)
                bound = self.m.require('demand', demand['id'], last_spec['demand_version'])
                demand = confirmed or {**bound, 'state': 'confirmed', 'spec_id': last_spec['id'], 'spec_version': last_spec['version']}
                historical_scope = True
        own_services = {sid: s for sid, s in services.items() if not provider or s.get('provider') == user['id']}
        plan_map = {(p['id'], p['version']): p for p in plans}
        jobs = sorted([j for j in context['job'] if key(j) in plan_map], key=stamp)
        job_ids = {j['id'] for j in jobs}
        runs = {r['id']: r for r in context['run'] if key(r) in plan_map}
        deliveries = [d for d in context['delivery'] if key(d) in plan_map and d.get('receipt', {}).get('run_id') in job_ids]
        decisions = [d for d in context['decision'] if key(d) in plan_map]
        states = {s['id']: s for s in context['plan_state']}
        current_plans = [p for p in plans if demand.get('state') == 'confirmed'
                         and p.get('spec_id') == demand.get('spec_id') and p.get('spec_version') == demand.get('spec_version')
                         and (historical_scope or (self.e.get('plan', p['id']) or {}).get('version') == p['version'])]
        current_keys = {(p['id'], p['version']) for p in current_plans}
        current_jobs = [j for j in jobs if key(j) in current_keys]
        latest_job = max(current_jobs, key=stamp, default=None)
        current_decisions = [d for d in decisions if key(d) in current_keys and d.get('actor') == demand.get('created_by')]
        latest_decision = max(current_decisions, key=stamp, default=None)
        state = 'draft' if demand.get('state') != 'confirmed' else 'confirmed'
        if current_plans:
            state = 'planning'
            if any(states.get(p['id'], {}).get('status') == 'confirmed' and states.get(p['id'], {}).get('plan_version') == p['version'] for p in current_plans):
                state = 'awaiting_authorization'
                valid_plans = set()
                for plan in current_plans:
                    try:
                        self.runtime.execution.current(plan)
                        valid_plans.add((plan['id'], plan['version']))
                    except (DomainError, KeyError, TypeError, AttributeError):
                        # Invalid historical definitions never appear runnable.
                        pass
                if not valid_plans:
                    state = 'attention'
                for grant in context['grant']:
                    if key(grant) not in valid_plans or grant.get('subject') != demand.get('created_by'):
                        continue
                    try:
                        plan = plan_map[key(grant)]
                        _, reason = self.runtime.execution.security.assess(grant['id'], plan, demand['created_by'], plan['purpose'])
                        if reason is None:
                            state = 'authorized'
                    except (DomainError, KeyError, TypeError, AttributeError):
                        pass
            if any(states.get(p['id'], {}).get('status') in ('paused', 'invalidated') and states.get(p['id'], {}).get('plan_version') == p['version'] for p in current_plans):
                state = 'attention'
        if latest_job:
            state = {'queued': 'running', 'running': 'running', 'failed': 'attention', 'interrupted': 'attention',
                     'cancelled': 'attention', 'success': 'delivered'}.get(latest_job.get('state'), state)
            if latest_job.get('state') == 'success' and not any(d.get('receipt', {}).get('run_id') == latest_job['id'] for d in deliveries):
                state = 'attention'
        # Completion in one plan must not hide another plan that is still running.
        if any(j.get('state') in ('queued', 'running') for j in current_jobs):
            state = 'running'
        if latest_decision and not latest_decision.get('allowed') and state not in ('delivered', 'running') and (not latest_job or stamp(latest_decision) > stamp(latest_job)):
            state = 'attention'
        timeline = []
        def event(oid, stage, title, detail, time, status='done'):
            timeline.append({'id': oid, 'stage': stage, 'title': title, 'detail': detail, 'time': time, 'status': status})
        for version in reversed(self.m.history('demand', demand['id'])):
            if historical_scope and version['version'] > demand['version']:
                continue
            event('demand:' + str(version['version']), 'application', '需求已登记' if version['version'] == 1 else '需求版本更新',
                  '第 ' + str(version['version']) + ' 版 · ' + ('已确认' if version.get('state') == 'confirmed' else '待确认'), stamp(version))
        for spec in specs.values():
            event('spec:' + spec['id'] + ':' + str(spec['version']), 'confirmation', '任务规格已确认',
                  '规格第 ' + str(spec['version']) + ' 版', stamp(spec))
        service_rows = []
        provider_jobs = set()
        allowed_nodes = {}
        for sid, service in own_services.items():
            linked = [(p, n) for p in plans for n in p.get('nodes', []) if n.get('operation') == 'service' and n.get('service_id') == sid
                      and (self.m.get('service', sid, p.get('service_versions', {}).get(sid)) or {}).get('provider') == service.get('provider')]
            invoked, success, failed = set(), set(), set()
            for plan, node in linked:
                for job in jobs:
                    if key(job) != (plan['id'], plan['version']):
                        continue
                    provider_jobs.add(job['id'])
                    allowed_nodes.setdefault(job['id'], set()).add(node['id'])
                    result = job.get('nodes', {}).get(node['id'], {})
                    started = any(t.get('node') == node['id'] and t.get('service_id') == sid and t.get('stage') == 'service_invoked' for t in job.get('timeline', []))
                    invocation = (job['id'], node['id'])
                    if started or result.get('status') == 'success':
                        invoked.add(invocation)
                    if result.get('status') == 'success':
                        success.add(invocation)
                    elif started and job.get('state') in ('failed', 'interrupted', 'cancelled'):
                        failed.add(invocation)
                    if provider and (started or result):
                        event('node:' + job['id'] + ':' + node['id'], 'execution', service.get('name', sid),
                              {'success': '本方服务节点执行完成', 'blocked': '本方服务节点未完成，请联系平台核查' if started else '本方服务尚未调用，所在任务已中止'}.get(result.get('status'), '本方服务正在执行或等待结果'),
                              result.get('finished_at') or stamp(job), 'done' if result.get('status') == 'success' else 'attention')
            service_rows.append({'id': sid, 'name': service.get('name', sid), 'provider': service.get('provider'),
                                 'provider_name': actor_name(service.get('provider')),
                                 'usage_state': '已调用' if invoked else '已编入方案' if linked else '申请使用',
                                 'job_count': len(invoked), 'success_count': len(success), 'failed_count': len(failed)})
        visible_jobs = [j for j in jobs if not provider or j['id'] in provider_jobs]
        plan_rows = []
        if not provider:
            for plan in plans:
                plan_state = states.get(plan['id'], {})
                row = {'id': plan['id'], 'name': plan.get('name', plan['id']), 'version': plan['version'], 'spec_version': plan.get('spec_version'),
                       'status': plan_state.get('status', 'draft') if plan_state.get('plan_version') == plan['version'] else 'superseded',
                       'current': (plan['id'], plan['version']) in current_keys}
                plan_rows.append(row)
                event('plan:' + plan['id'] + ':' + str(plan['version']), 'confirmation', '执行方案已编制',
                      row['name'] + ' · 第 ' + str(row['version']) + ' 版', stamp(plan))
                for transition in self.e.history('plan_state', plan['id']):
                    if transition.get('plan_version') != plan['version'] or transition.get('status') == 'draft':
                        continue
                    labels = {'confirmed': '执行方案已确认', 'paused': '执行方案已暂停', 'invalidated': '依赖已变化，方案需复核'}
                    event('plan-state:' + plan['id'] + ':' + str(transition['version']), 'confirmation', labels.get(transition.get('status'), '方案状态更新'),
                          row['name'] + ' · 第 ' + str(row['version']) + ' 版', stamp(transition),
                          'attention' if transition.get('status') in ('paused', 'invalidated') else 'done')
            for grant in context['grant']:
                if key(grant) not in plan_map or grant.get('subject') != demand.get('created_by'):
                    continue
                for version in self.e.history('grant', grant['id']):
                    event('grant:' + grant['id'] + ':' + str(version['version']), 'confirmation',
                          '执行授权已撤销' if version.get('status') == 'revoked' else '执行授权已登记',
                          '适用方案第 ' + str(version.get('plan_version', '')) + ' 版 · 有效期至 ' + str(version.get('expires_at', '未登记')),
                          stamp(version), 'attention' if version.get('status') == 'revoked' else 'done')
            for job in jobs:
                event('job:' + job['id'], 'execution', '任务执行' + (' · 重试' if job.get('retry_of') else ''),
                      {'success': '执行成功', 'failed': '执行失败', 'queued': '等待执行', 'running': '执行中', 'interrupted': '执行中断', 'cancelled': '任务取消'}.get(job.get('state'), job.get('state', '')),
                      stamp(job), 'attention' if job.get('state') in ('failed', 'interrupted', 'cancelled') else 'done')
        delivery_rows = []
        for delivery in deliveries:
            plan = plan_map[key(delivery)]
            row = {'id': delivery['id'], 'job_id': delivery.get('receipt', {}).get('run_id'), 'state': delivery.get('state'),
                   'mode': plan.get('circulation_mode'), 'time': delivery.get('receipt', {}).get('issued_at', stamp(delivery))}
            if not provider:
                delivery_rows.append(row)
                event('delivery:' + delivery['id'], 'delivery', '结果已交付', '交付凭证已生成', row['time'])
                if delivery.get('state') == 'destroyed':
                    event('delivery-destroyed:' + delivery['id'], 'delivery', '平台结果副本已清除', '交付凭证保留，结果读取需重新执行', delivery.get('destroyed_at') or stamp(delivery))
        feedback_rows = []
        jobs_by_id = {j['id']: j for j in jobs}
        feedback_targets = []
        try:
            require_action(user, 'feedback.create')
            can_evaluate = True
        except DomainError:
            can_evaluate = False
        if can_evaluate:
            for run in runs.values():
                if run.get('status') not in ('success', 'failed', 'blocked') or user['role'] != 'platform' and run.get('actor') != user['id']:
                    continue
                plan = plan_map[key(run)]
                for sid in sorted(called_services(plan, run, jobs_by_id.get(run['id'], {}))):
                    service = self.m.get('service', sid, plan.get('service_versions', {}).get(sid))
                    feedback_targets.append({'run_id': run['id'], 'service_id': sid,
                                             'service_name': (service or {}).get('name', sid), 'time': stamp(run)})
        for feedback in context['feedback']:
            run = runs.get(feedback.get('run_id'))
            if not run:
                continue
            plan = plan_map[key(run)]
            node_services = set(plan.get('service_versions', {}))
            scoped_sid = feedback.get('service_id')
            called = called_services(plan, run, jobs_by_id.get(run['id'], {}))
            if scoped_sid and scoped_sid not in called:
                continue
            owned_at_version = {sid for sid, version in plan.get('service_versions', {}).items()
                                if (self.m.get('service', sid, version) or {}).get('provider') == user['id']}
            pure_own = bool(node_services) and node_services <= owned_at_version and all(n.get('operation') == 'service' for n in plan.get('nodes', []))
            if provider and not (scoped_sid in owned_at_version and scoped_sid in called or not scoped_sid and pure_own and bool(called)):
                continue
            row = {'id': feedback['id'], 'service_id': scoped_sid, 'run_id': feedback.get('run_id'), 'ratings': feedback.get('ratings', {}),
                   'comment': feedback.get('comment', ''), 'time': stamp(feedback)}
            feedback_rows.append(row)
            event('feedback:' + feedback['id'], 'feedback', '收到质量与使用反馈', row['comment'], row['time'])
        exceptions = []
        # A successful second or later retry resolves every failed ancestor in
        # the same immutable execution scope, not unrelated attempts.
        recovered_jobs = set()
        for job in jobs:
            if job.get('state') != 'success':
                continue
            ancestor = job.get('retry_of')
            visited = set()
            while ancestor and ancestor not in visited:
                visited.add(ancestor)
                previous = jobs_by_id.get(ancestor)
                if not previous or key(previous) != key(job) or previous.get('actor') != job.get('actor'):
                    break
                recovered_jobs.add(ancestor)
                ancestor = previous.get('retry_of')
        for job in visible_jobs:
            if job.get('state') not in ('failed', 'interrupted', 'cancelled'):
                continue
            recovered = job['id'] in recovered_jobs
            exceptions.append({'id': job['id'], 'kind': 'execution', 'status': 'recovered' if recovered else 'open',
                               'title': '执行异常' if not recovered else '执行异常已重试恢复',
                               'detail': '本方服务所在任务未完成，请通过协作记录联系平台。' if provider else job.get('error') or '任务未完成',
                               'time': stamp(job), 'resolved': recovered})
        if not provider:
            for decision in decisions:
                if decision.get('allowed') or decision.get('actor') != demand.get('created_by'):
                    continue
                resolved = any(d.get('allowed') and key(d) == key(decision) and d.get('actor') == decision.get('actor')
                               and d.get('grant_id') == decision.get('grant_id') and d.get('stage') == decision.get('stage')
                               and stamp(d) > stamp(decision) for d in decisions)
                if decision.get('stage') == 'submission':
                    resolved = resolved or any(j.get('state') == 'success' and key(j) == key(decision)
                                               and j.get('actor') == decision.get('actor') and stamp(j) > stamp(decision) for j in jobs)
                exceptions.append({'id': decision['id'], 'kind': 'authorization', 'status': 'resolved' if resolved else 'open',
                                   'title': '授权核验未通过', 'detail': decision.get('reason', ''), 'time': stamp(decision), 'resolved': resolved})
            for case in context['case']:
                if (case.get('object_id'), case.get('object_version')) not in plan_map or case.get('object_kind') != 'plan':
                    continue
                exceptions.append({'id': case['id'], 'kind': 'quality', 'status': case.get('status'), 'title': '质量偏差处置',
                                   'detail': case.get('reason', '') + ('；' + case['note'] if case.get('note') else ''), 'time': stamp(case), 'resolved': case.get('status') == 'closed'})
        for item in exceptions:
            event('exception:' + item['id'], 'exception', item['title'], item['detail'], item['time'], 'done' if item['resolved'] else 'attention')
        message_rows = []
        for message in context['work_order_message']:
            if message.get('demand_id') != demand['id']:
                continue
            sid = message.get('service_id')
            if provider and (sid not in own_services or message.get('provider') != user['id']):
                continue
            message_rows.append(self._message(message))
        message_rows.sort(key=lambda x: x['time'])
        timeline.sort(key=lambda x: (x['time'], x['id']))
        stages = [
            {'key': 'application', 'label': '申请', 'state': 'done', 'detail': '需求已登记，历次修订保留'},
            {'key': 'confirmation', 'label': '确认', 'state': 'done' if demand.get('state') == 'confirmed' else 'current', 'detail': '当前需求已确认' if demand.get('state') == 'confirmed' else '当前版本等待确认'},
            {'key': 'execution', 'label': '执行', 'state': 'attention' if state == 'attention' else 'current' if state == 'running' else 'done' if latest_job and latest_job.get('state') == 'success' else 'pending', 'detail': STATUS[state]},
            {'key': 'delivery', 'label': '交付', 'state': 'done' if state == 'delivered' else 'pending', 'detail': '已有交付凭证；读取结果时仍需核验授权' if state == 'delivered' else '等待当前版本执行与交付'},
            {'key': 'exception', 'label': '异常与反馈', 'state': 'attention' if any(not x['resolved'] for x in exceptions) else 'done' if exceptions or feedback_rows else 'pending', 'detail': '保留异常、处置和反馈记录'}]
        updates = [stamp(demand)] + [x['time'] for x in timeline + message_rows]
        return {'id': demand['id'], 'title': demand.get('title', demand['id']), 'purpose': demand.get('purpose', '待明确用途'),
                'historical_scope': historical_scope,
                'requester': demand.get('created_by'), 'requester_name': actor_name(demand.get('created_by')),
                'status': state, 'status_label': STATUS[state], 'next_action': NEXT[state], 'updated_at': max(updates),
                'counts': {'services': len(service_rows), 'jobs': len(visible_jobs), 'deliveries': len(delivery_rows),
                           'exceptions': sum(not x['resolved'] for x in exceptions), 'feedback': len(feedback_rows)},
                'stages': stages, 'services': service_rows, 'plans': plan_rows,
                'jobs': [] if provider else [{'id': j['id'], 'plan_id': j['plan_id'], 'plan_version': j['plan_version'], 'state': j.get('state'), 'retry_of': j.get('retry_of'), 'time': stamp(j)} for j in jobs],
                'deliveries': delivery_rows, 'exceptions': exceptions, 'feedback': feedback_rows, 'feedback_targets': feedback_targets, 'timeline': timeline,
                'messages': message_rows, 'message_scopes': [{'id': sid, 'name': s.get('name', sid)} for sid, s in own_services.items()],
                'can_message': user['role'] in ('platform', 'provider', 'requester')}

    def _message(self, message):
        return {'id': message['id'], 'actor': message['actor'], 'actor_name': actor_name(message['actor']),
                'service_id': message.get('service_id'), 'service_name': message.get('service_name'),
                'body': message['body'], 'time': stamp(message)}

    def message(self, oid, body, user):
        require_action(user, 'work_order.message')
        if not isinstance(body, dict) or set(body) - {'body', 'service_id'}:
            raise DomainError('协作记录仅接受内容与服务范围')
        text = body.get('body')
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2000:
            raise DomainError('协作内容须为 1 至 2000 字')
        sid = body.get('service_id')
        if sid is not None and not isinstance(sid, str):
            raise DomainError('服务范围格式错误')
        sid = sid or None
        with self.m.atomic():
            order = self.get(oid, user)
            scopes = {s['id']: s for s in order['message_scopes']}
            if sid and sid not in scopes or user['role'] == 'provider' and not sid:
                raise DomainError('当前身份不能向此服务范围发送协作记录', 403)
            service = next((s for s in order['services'] if s['id'] == sid), None)
            result = self.m.save('work_order_message', new_id('MSG'), {'demand_id': oid, 'body': text.strip(), 'actor': user['id'],
                'service_id': sid, 'service_name': scopes[sid]['name'] if sid else None, 'provider': service.get('provider') if service else None},
                0, user['id'], '工单协作记录')
        return self._message(result)
