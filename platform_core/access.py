"""Shared navigation/action permissions plus server-side record ownership.

The JSON manifest is consumed by the UI and enforced here. Hiding controls is
only a presentation of this policy, never the authorization boundary itself.
"""
import json
from pathlib import Path
from platform_core.storage import DomainError

POLICY = json.loads((Path(__file__).resolve().parents[1] / 'static' / 'shared' / 'access-policy.json').read_text(encoding='utf-8'))
READ_ACTIONS = {'read', 'history', 'impact', 'export', 'candidates', 'suggest',
                'resolve', 'gaps', 'metrics', 'reference', 'download', 'verify'}


def resource_level(user, resource):
    return POLICY['resources'].get(resource, {}).get(user.get('role'), 'none')


def require_resource(user, resource, write=False):
    level = resource_level(user, resource)
    if level == 'none' or write and level != 'write':
        raise DomainError('当前身份无权修改该模块' if write else '当前身份无权查看该模块', 403)
    return user


def require_action(user, action):
    if user.get('role') not in POLICY['actions'].get(action, []):
        raise DomainError('当前身份无权执行该操作', 403)
    resource, operation = action.split('.', 1)
    # Creating a plan from a specification never edits that immutable record.
    require_resource(user, resource, write=operation not in READ_ACTIONS and action != 'spec.plan')
    return user


def access_snapshot(user):
    return {'version': POLICY['version'], 'principal': dict(user),
            'resources': {key: resource_level(user, key) for key in POLICY['resources']},
            'actions': {key: user.get('role') in roles for key, roles in POLICY['actions'].items()}}


def _provider_service(runtime, service_id, user):
    service = runtime.management.store.get('service', service_id or '')
    return bool(service and service.get('provider') == user['id'])


def can_read_execution(runtime, kind, item, user):
    if resource_level(user, kind) == 'none':
        return False
    if user['role'] in ('platform', 'auditor'):
        return True
    if kind == 'contract':
        return user['id'] in item.get('parties', []) or item.get('created_by') == user['id']
    if kind == 'facility' and user['role'] == 'requester':
        return True  # Published local facility descriptions contain no credentials.
    owner = item.get('subject') if kind == 'grant' else item.get('actor') or item.get('created_by')
    if owner == user['id']:
        return True
    if kind == 'workflow' or kind == 'run' and not owner:
        plan = runtime.execution.store.get('plan', item.get('plan_id', ''), item.get('plan_version'))
        if plan and plan.get('created_by') == user['id']:
            return True
    if kind == 'run' and user['role'] == 'provider':
        if item.get('object_kind') == 'service':
            return _provider_service(runtime, item.get('object_id'), user)
        plan = runtime.execution.store.get('plan', item.get('plan_id', ''), item.get('plan_version'))
        # A provider's participation in one node does not expose another
        # provider's node trace or a private view in the same plan.
        service_ids = list((plan or {}).get('service_versions', {}))
        nodes = (plan or {}).get('nodes', [])
        return bool(service_ids and nodes
                    and all(_provider_service(runtime, sid, user) for sid in service_ids)
                    and all(n.get('operation') == 'service' and n.get('service_id') in service_ids for n in nodes))
    return False


def can_read_management(runtime, kind, item, user):
    if resource_level(user, kind) == 'none':
        return False
    if user['role'] in ('platform', 'auditor'):
        return True
    store = runtime.management.store
    if kind == 'demand':
        return item.get('created_by') == user['id']
    if kind == 'spec':
        demand = store.get('demand', item.get('demand_id', ''))
        return bool(demand and demand.get('created_by') == user['id'])
    if kind == 'service':
        if user['role'] == 'provider':
            return item.get('provider') == user['id']
        return item.get('lifecycle') == 'published' and (item.get('visibility') != 'specified' or user['id'] in item.get('allowed_subjects', []))
    if kind == 'validation':
        return _provider_service(runtime, item.get('service_id'), user)
    if kind == 'notice':
        if user['role'] == 'provider':
            return _provider_service(runtime, item.get('service_id'), user)
        return any((store.get('demand', did) or {}).get('created_by') == user['id'] for did in item.get('affected_demands', []))
    if kind == 'feedback':
        if item.get('evaluated_by') == user['id']:
            return True
        run = runtime.execution.store.get('run', item.get('run_id', ''))
        return bool(run and can_read_execution(runtime, 'run', run, user))
    if kind == 'improvement':
        runs = [runtime.execution.store.get('run', rid) for rid in item.get('before', []) + item.get('after', [])]
        return bool(runs) and all(run and can_read_execution(runtime, 'run', run, user) for run in runs)
    return True


def management_record(runtime, kind, item, user):
    if kind == 'notice' and user['role'] == 'requester':
        return {**item, 'affected_demands': [did for did in item.get('affected_demands', [])
                if (runtime.management.store.get('demand', did) or {}).get('created_by') == user['id']]}
    return item
