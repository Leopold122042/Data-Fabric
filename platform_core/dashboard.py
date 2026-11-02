"""Read-only, role-scoped dashboard projections. No result payloads or fabricated history."""
from collections import Counter
from datetime import datetime, timezone
import logging

from management.work_orders import WorkOrders
from platform_core.access import require_resource, can_read_management, can_read_execution

log = logging.getLogger(__name__)


def pick(row, *keys):
    return {key: row.get(key) for key in keys}


def dashboard(runtime, user):
    require_resource(user, 'overview')
    role = user['role']
    m, e = runtime.management.store, runtime.execution.store
    orders = WorkOrders(runtime).projections(user)
    services = [s for s in m.list('service') if can_read_management(runtime, 'service', s, user)]
    active_objects = [o for o in m.list('object') if o.get('status') != 'deleted']
    if role == 'provider':
        refs = {oid for s in services for oid in s.get('source_objects', [])}
        # A resource referenced by a service is not proof of resource ownership.
        active_objects = [o for o in active_objects if o['id'] in refs]
    elif role == 'requester':
        active_objects = []
    feedback = {}
    usages, exceptions = [], []
    for order in orders:
        for item in order['feedback']:
            feedback[item['id']] = {**pick(item, 'id', 'comment', 'time', 'service_id'), 'order_id': order['id'], 'title': order['title']}
        for item in order['exceptions']:
            if not item['resolved']:
                exceptions.append({**pick(item, 'id', 'title', 'detail', 'time'), 'order_id': order['id']})
        for service in order['services']:
            usages.append({**pick(service, 'id', 'name', 'usage_state', 'job_count'),
                           'order_id': order['id'], 'title': order['title'], 'requester': order['requester_name']})
    counts = Counter(o['status'] for o in orders)
    data = {
        'role': role, 'scope': {'platform': '全平台', 'provider': '本方服务及关联需求',
                              'requester': '我的需求与可见服务', 'auditor': '审计范围'}[role],
        'updated_at': datetime.now(timezone.utc).isoformat(), 'errors': [],
        'order_counts': {'total': len(orders), **counts, 'exceptions': len(exceptions)},
        'orders': [pick(o, 'id', 'title', 'status', 'status_label', 'next_action', 'counts', 'stages') for o in orders[:3]],
        'attention': [pick(o, 'id', 'title', 'counts', 'next_action') for o in sorted(
            (o for o in orders if o['status'] != 'delivered' or o['counts']['exceptions']),
            key=lambda o: bool(o['counts']['exceptions']), reverse=True)][:3],
        'services': [pick(s, 'id', 'name', 'description', 'lifecycle') for s in services[:3]],
        'service_counts': {'total': len(services), 'published': sum(s.get('lifecycle') == 'published' for s in services),
                           'by_state': dict(Counter(s.get('lifecycle') or 'draft' for s in services))},
        'source_names': {**{sid: sid.upper() if sid != 'iot' else 'IoT' for sid in ('his','lis','pacs','emr','iot')},
                         **{s['id']: s.get('name') or s['id'] for s in m.list('source') if s['id'] in {o.get('source_id') for o in active_objects}}},
        'objects': {'total': len(active_objects), 'by_source': dict(Counter(o.get('source_id') or '未分类' for o in active_objects))},
        'usages': usages[:4], 'consumer_count': len({o['requester'] for o in orders}),
        'feedback': sorted(feedback.values(), key=lambda x: x['time'] or '', reverse=True)[:2],
        'feedback_count': len(feedback), 'exceptions': sorted(exceptions, key=lambda x: x['time'] or '', reverse=True)[:2],
    }
    if role != 'provider':
        deliveries = [d for d in e.list('delivery') if can_read_execution(runtime, 'delivery', d, user)]
        data['delivery_count'] = len(deliveries)
        data['deliveries'] = []
        for d in deliveries[:2]:
            plan = e.get('plan', d.get('plan_id', ''), d.get('plan_version')) or {}
            data['deliveries'].append({**pick(d, 'id', 'state', 'circulation_mode'), 'name': plan.get('name') or d['id']})
    if role in ('platform', 'auditor'):
        # Optional legacy sources fail independently and never appear as zero on error.
        try:
            overview = runtime.overview()
            data['medical'] = overview['counts']
            data['sources'] = [pick(s, 'id', 'name', 'status', 'last_scan', 'tables') for s in overview['sources']]
        except Exception:
            log.exception('Dashboard source snapshot unavailable')
            data['errors'].append('来源与医疗统计暂不可用')
        try:
            events = runtime.events(0, 800)['events']
            data['events'] = {'total': len(events), 'by_type': dict(Counter(ev['etype'] for ev in events)),
                              'items': [pick(ev, 'seq', 'etype', 'title', 'detail', 'ts', 'source_id') for ev in sorted(events, key=lambda x: x['seq'], reverse=True)[:3]],
                              'scope': '当前留存窗口，最多 800 条；非今日累计'}
        except Exception:
            log.exception('Dashboard events unavailable')
            data['errors'].append('元数据事件暂不可用')
        try:
            sec = runtime.security_overview()
            data['risks'] = {'open': sec.get('metrics', {}).get('open_risks'),
                             'items': [pick(r, 'id', 'summary', 'level', 'target') for r in sec.get('risks', []) if r.get('status') == 'open'][:2]}
        except Exception:
            log.exception('Dashboard risks unavailable')
            data['errors'].append('风险信息暂不可用')
        decisions = e.list('decision')
        rejected = [d for d in decisions if d.get('allowed') is False]
        data['authorization'] = {'total': len(decisions), 'rejected': len(rejected), 'grants': len(e.list('grant')),
                                  'reasons': dict(Counter(d.get('reason') or '未记录原因' for d in rejected))}
        # Show actual evidence records, not a presumed completeness or signature score.
        data['evidence'] = [{**pick(r, 'id', 'plan_id', 'plan_version', 'status'),
                            'name': (e.get('plan', r.get('plan_id', ''), r.get('plan_version')) or {}).get('name') or r.get('plan_id') or r['id']}
                           for r in e.list('run')[:2]]
    return data
