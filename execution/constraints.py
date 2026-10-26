"""Compile confirmed rules into supported executors; unsupported handlers never run."""
from datetime import datetime, timezone
from contracts.execution.constraints import fingerprint, instant, normalize
from platform_core.storage import DomainError


def check_window(policy, stage):
    now = datetime.now(timezone.utc)
    if now >= instant(policy['deadline_at']):
        raise DomainError('已超过规格约定的交付时限', 403)
    if policy['window_kind'] == 'execution' and now < instant(policy['time_start']):
        raise DomainError('尚未进入规格约定的执行时间窗', 403)


def compile_node(policy, node, view=None, service=None):
    if policy['scope']['kind'] == 'rows':
        if node['operation'] != 'view':
            raise DomainError('该服务尚不能落实对象范围和时间窗，请使用绑定字段的逻辑视图', 409)
        if node['params'] or node['inputs']:
            raise DomainError('受控视图不接受覆盖范围的节点参数或上游输入')
        required = {c['field'] for c in policy['scope']['filters']} | {policy['time_field']}
        if not required <= set(view['field_versions']):
            raise DomainError('视图缺少规格要求的范围字段或时间字段，不能执行', 409)
        return {'filters': policy['scope']['filters'], 'time_field': policy['time_field'],
                'time_start': policy['time_start'], 'time_end_exclusive': policy['time_end_exclusive']}
    if node['operation'] != 'service' or (service or {}).get('handler_id', node.get('service_id')) != 'SVC-META-CATALOG':
        raise DomainError('当前元数据快照规则只能调用目录服务，不能替代业务数据查询', 409)
    if node['params'] or set(node['inputs']) - {'previous'}:
        raise DomainError('目录服务不接受范围覆盖参数')
    return None


def metadata_result(result):
    # Only structural source names and table counts cross the delivery boundary.
    if not isinstance(result, dict) or not isinstance(result.get('sources'), list):
        raise DomainError('目录服务未返回支持的结构摘要', 409)
    rows = []
    for source in result['sources']:
        if not isinstance(source, dict) or not isinstance(source.get('id'), str) or not isinstance(source.get('name'), str) or type(source.get('tables')) is not int or source['tables'] < 0:
            raise DomainError('目录结构摘要格式不正确', 409)
        rows.append([source['id'], source['name'], source['tables']])
    return {'columns': ['source', 'source_name', 'table_count'], 'rows': rows}


def verify_plan_policy(plan, specification=None):
    policy = plan.get('execution_policy')
    if not policy:
        raise DomainError('方案缺少执行条件，请重新生成并确认方案', 409)
    if plan.get('execution_policy_digest') != fingerprint(policy):
        raise DomainError('方案执行条件摘要不一致', 409)
    if specification is not None and policy != normalize(specification):
        raise DomainError('方案与确认规格的执行条件不一致', 409)
    if plan['purpose'] != policy['purpose'] or plan['output_mode'] != policy['output_mode'] or plan['circulation_mode'] not in policy['circulation_modes']:
        raise DomainError('方案放宽了用途、粒度或交付限制', 403)
    if plan['quality_min_complete'] < policy['quality_min_complete']:
        raise DomainError('方案降低了质量阈值', 403)
    check_window(policy, 'execution')
    return policy
