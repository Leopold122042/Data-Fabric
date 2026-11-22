"""Shared navigation/action permissions plus server-side record ownership.

The JSON manifest is consumed by the UI and enforced here. Hiding controls is
only a presentation of this policy, never the authorization boundary itself.
"""
import json
import re
from pathlib import Path
from platform_core.storage import DomainError

POLICY = json.loads((Path(__file__).resolve().parents[1] / 'static' / 'shared' / 'access-policy.json').read_text(encoding='utf-8'))
READ_ACTIONS = {'read', 'history', 'impact', 'export', 'candidates', 'suggest',
                'resolve', 'gaps', 'metrics', 'reference', 'download', 'verify'}

# Current demo catalog scopes. Level 1 can run queries in its scope but receives
# only a thresholded count; levels 2 and 3 see de-identified rows; level 4 sees
# the full catalog and output. Per-account grants can narrow these sets later.
LEVEL_TABLES = {
    1: {'vitals', 'devices', 'imaging_turnaround', 'test_order', 'result'},
    2: {'vitals', 'devices', 'imaging_turnaround', 'test_order', 'result',
        'visit', 'orders', 'study', 'laboratory_reports', 'chronic_followup', 'research_cohort'},
    3: {'vitals', 'devices', 'imaging_turnaround', 'test_order', 'result',
        'visit', 'orders', 'study', 'laboratory_reports', 'chronic_followup', 'research_cohort',
        'patient', 'dossier'},
    4: {'*'},
}
LEVEL_VISIBILITY = {1: '仅查看满足最小样本数的查询计数',
                    2: '可查看授权范围内的脱敏明细',
                    3: '可查看全部数据表的脱敏明细',
                    4: '可查看全部字段和原始明细'}


def level_for(user):
    try:
        fallback={'requester':1,'provider':2,'auditor':3,'platform':4}.get(user.get('role'),1)
        return max(1, min(4, int(user.get('level', fallback))))
    except (AttributeError, TypeError, ValueError):
        return 4


def canonical_table_name(table):
    if isinstance(table, dict):
        value = table.get('table') or table.get('name') or ''
    else:
        value = table or ''
    text = str(value).strip().casefold()
    # Display labels such as dossier(病历文书) are normalized to the physical name.
    text = re.sub(r'\s*\([^)]*\)\s*$', '', text)
    return text


def can_access_table(user, table):
    # OpenAlex is a public scholarly-metadata source, independent of the
    # level-scoped clinical tables below.
    if isinstance(table, dict) and table.get('source_id') == 'openalex':
        return True
    allowed = LEVEL_TABLES[level_for(user)]
    return '*' in allowed or canonical_table_name(table) in allowed


def require_table_scope(user, tables):
    level=level_for(user)
    denied = [canonical_table_name(table) for table in tables if not can_access_table(user, table)]
    if denied:
        allowed=LEVEL_TABLES[level]
        scope='全部13张表' if '*' in allowed else '、'.join(sorted(allowed))
        raise DomainError(f"当前账户为{level}级，当前权限下无法访问数据表："+
                          '、'.join(dict.fromkeys(denied))+f'。当前可用数据范围：{scope}。请联系管理员核对权限。',403)
    return user


def account_permission(user):
    level = level_for(user)
    tables = sorted(LEVEL_TABLES[level]) if '*' not in LEVEL_TABLES[level] else ['全部13张表']
    return {'level': level, 'dataset_tables': tables,
            'visibility': LEVEL_VISIBILITY[level], 'can_manage_accounts': level == 4,
            'scope_note': '权限按账户等级限制；数据源合同、用途、期限及撤销规则仍生效。'}


def table_ids_for_fields(store, field_ids):
    ids = set()
    for field_id in field_ids or []:
        field = store.get('object', field_id)
        if not field:
            raise DomainError('查询引用了无效字段，已拒绝执行', 403)
        if field.get('kind') == 'table':
            ids.add(field['id'])
        else:
            parent = store.get('object', field.get('parent_id', ''))
            if not parent or parent.get('kind') != 'table':
                raise DomainError('字段没有有效的所属表，已拒绝执行', 403)
            ids.add(parent['id'])
    return ids


def require_object_scope(user, store, object_ids):
    table_ids = table_ids_for_fields(store, object_ids)
    tables = [store.require('object', oid) for oid in table_ids]
    return require_table_scope(user, tables)


def require_plan_scope(user, runtime, plan):
    store = runtime.management.store
    table_ids = set()
    scoped_nodes = 0
    for node in plan.get('nodes', []):
        if node.get('operation') == 'view':
            scoped_nodes += 1
            view = runtime.execution.store.get('view', node.get('view_id', ''))
            if not view:
                raise DomainError('方案引用的逻辑视图不存在', 403)
            table_ids.update(table_ids_for_fields(store, view.get('fields', [])))
        elif node.get('operation') == 'service':
            scoped_nodes += 1
            service = store.get('service', node.get('service_id', ''))
            if not service:
                raise DomainError('方案引用的数据服务不存在', 403)
            sources=service.get('source_objects', [])
            if not sources and level_for(user)<4:
                raise DomainError('该数据服务没有登记来源字段，当前等级不能执行',403)
            table_ids.update(table_ids_for_fields(store, sources))
        elif node.get('operation') == 'medical' and level_for(user) < 4:
            raise DomainError('该示例服务仅对4级账户开放', 403)
    if scoped_nodes and not table_ids and level_for(user)<4:
        raise DomainError('方案未能关联到当前账户范围内的数据表，已拒绝执行',403)
    return require_table_scope(user, [store.require('object', oid) for oid in table_ids])


def filter_catalog(tables, user):
    return [table for table in tables if can_access_table(user, table)]


def project_result(result, user, min_group_size=5):
    """Apply the account's output tier at every result read boundary."""
    level = level_for(user)
    if level >= 4:
        return result
    if not isinstance(result, dict) or not isinstance(result.get('rows'), list):
        return {'columns': [], 'rows': [], 'message': '查询可能已执行，但当前权限不允许展示该结果结构；请联系管理员核对结果查看范围。'}
    rows = result['rows']
    columns = result.get('columns', [])
    if level == 1:
        count = len(rows)
        return {'columns': ['符合条件的记录数'],
                'rows': [[count]] if count >= min_group_size else [],
                'message': '' if count >= min_group_size else
                    f'查询已执行成功；当前权限只允许展示达到最小样本阈值的计数，本次未达到阈值，因此隐藏具体数量和明细。',
                'suppressed': count < min_group_size}
    from execution.security.output_policy import name_classification
    identifier_indexes = [i for i, name in enumerate(columns)
                          if name_classification(name) == 'identifier']
    per_result = {}
    projected = []
    for row_index, row in enumerate(rows):
        values = list(row) if isinstance(row, (list, tuple)) else [row.get(name) for name in columns] if isinstance(row, dict) else []
        for index in identifier_indexes:
            if index >= len(values) or values[index] is None:
                continue
            raw = str(values[index])
            # Stable only within this returned result; no cross-query identity linkage.
            if raw not in per_result:
                per_result[raw] = f'记录-{len(per_result) + 1:03d}'
            values[index] = per_result[raw]
        projected.append(values)
    return {**result, 'rows': projected}


def resource_level(user, resource):
    return POLICY['resources'].get(resource, {}).get(user.get('role'), 'none')


def require_resource(user, resource, write=False):
    level = resource_level(user, resource)
    if level == 'none' or write and level != 'write':
        labels={'overview':'平台总览','demand':'查询工作台','object':'数据目录','delivery':'查询结果',
                'execution':'执行管理','service':'数据服务','account':'账户管理'}
        operation='修改' if write else '查看'
        raise DomainError(f"当前账户为{level_for(user)}级，当前权限下无权{operation}{labels.get(resource,resource)}。"
                          '如需使用此功能，请联系平台管理员核对账户权限。',403)
    return user


def require_action(user, action):
    if user.get('role') not in POLICY['actions'].get(action, []):
        level=level_for(user)
        if action=='demand.create':
            message=f'当前账户为{level}级，没有新建查询的权限；请联系平台管理员核对账户权限。'
        elif action=='demand.understand':
            message=f'当前账户为{level}级，没有使用自然语言理解的权限；请联系平台管理员调整权限。'
        elif action=='demand.confirm':
            message=f'当前账户为{level}级，没有确认查询方案的权限；请联系管理员处理。'
        elif action=='delivery.read':
            message=f'查询状态与结果查看权限相互独立；当前账户为{level}级，当前权限下无法查看这次查询结果。请联系管理员核对结果查看范围。'
        elif action=='delivery.download':
            message=f'当前账户为{level}级，当前权限下无法导出查询结果；可以查看允许展示的结果，或联系管理员核对导出权限。'
        else:
            message=f'当前账户为{level}级，当前权限下无权执行“{action}”操作；请联系平台管理员核对权限。'
        raise DomainError(message, 403)
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
        requester_id=item.get('requester_id') or ('demo-requester' if item.get('created_by')=='demo-platform' else None)
        return item.get('created_by') == user['id'] or requester_id == user['id']
    if kind == 'spec':
        demand = store.get('demand', item.get('demand_id', ''))
        requester_id=(demand or {}).get('requester_id') or (
            'demo-requester' if (demand or {}).get('created_by')=='demo-platform' else None)
        return bool(demand and (demand.get('created_by') == user['id'] or requester_id == user['id']))
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
