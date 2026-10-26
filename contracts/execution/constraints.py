"""Executable, human-confirmed constraints shared by intake and execution.

Free-text scope descriptions are retained for review; filters are the executable
meaning explicitly confirmed with that description. Unsupported rules fail closed.
"""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from platform_core.storage import DomainError


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def instant(value, end_of_date=False):
    try:
        if not isinstance(value, str):
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10:
            parsed = parsed.replace(tzinfo=timezone.utc)
            if end_of_date:
                parsed += timedelta(days=1)
        elif parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise DomainError('执行时间需为ISO日期或带时区的时间；日期按UTC日界计算')


def number(value, label, low, high, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise DomainError(label + '超出支持范围')
    return value


def normalize(specification):
    raw = specification.get('execution_constraints')
    if not isinstance(raw, dict) or not raw:
        raise DomainError('规格缺少已确认的执行条件，请在需求受理页补充范围、时间字段和交付规则后重新确认', 409)
    allowed = {'scope', 'time_field', 'output_mode', 'circulation_modes',
               'quality_min_complete', 'max_rows', 'min_group_size'}
    if set(raw) - allowed:
        raise DomainError('存在尚不支持的执行条件，不能忽略后继续执行')
    scope = raw.get('scope')
    if not isinstance(scope, dict) or set(scope) - {'kind', 'filters', 'all_rows'}:
        raise DomainError('对象范围需明确选择数据行范围或当前元数据快照')
    kind = scope.get('kind')
    if kind not in ('rows', 'metadata'):
        raise DomainError('对象范围类型不支持')
    filters = scope.get('filters', [])
    if not isinstance(filters, list) or len(filters) > 20:
        raise DomainError('对象范围最多20个且为同时成立的筛选条件')
    checked = []
    for condition in filters:
        if not isinstance(condition, dict) or set(condition) != {'field', 'op', 'value'}:
            raise DomainError('范围条件必须包含字段、比较方式和值')
        if not isinstance(condition['field'], str) or not condition['field'] or condition['op'] not in ('=', '!=', '<', '<=', '>', '>='):
            raise DomainError('范围条件字段或比较方式非法')
        value = condition['value']
        if type(value) not in (str, int, float) or (isinstance(value, float) and not math.isfinite(value)):
            raise DomainError('范围条件只接受文本或有限数值')
        checked.append(dict(condition))
    if 'all_rows' in scope and type(scope['all_rows']) is not bool:
        raise DomainError('全部对象选项必须是明确的布尔值')
    if kind == 'rows':
        if not checked and scope.get('all_rows') is not True:
            raise DomainError('数据范围不能为空；全部对象也必须明确确认')
        if not isinstance(raw.get('time_field'), str) or not raw['time_field']:
            raise DomainError('数据行查询必须绑定时间字段')
    elif checked or raw.get('time_field') or scope.get('all_rows'):
        raise DomainError('元数据快照不接受患者范围条件；请使用数据行查询')
    mode = raw.get('output_mode')
    if mode not in ('aggregate', 'object'):
        raise DomainError('需明确汇总或对象级交付')
    labels = {'统计': 'aggregate', '汇总': 'aggregate', '汇总统计': 'aggregate',
              'summary': 'aggregate', 'aggregate': 'aggregate', '对象级': 'object', 'object': 'object'}
    if labels.get(str(specification.get('delivery', '')).strip().lower()) != mode:
        raise DomainError('交付形态说明与执行规则不一致或尚不支持，请明确选择汇总/对象级')
    if kind == 'metadata' and mode != 'object':
        raise DomainError('元数据快照返回目录对象，请选择对象级交付；不查询业务明细')
    modes = raw.get('circulation_modes')
    if not isinstance(modes, list) or not modes or any(m not in ('controlled_compute', 'api', 'download') for m in modes):
        raise DomainError('需明确允许的交付方式')
    quality = number(raw.get('quality_min_complete', 1), '完整率', 0, 1)
    quality_label = str(specification.get('quality', '')).strip().lower()
    if quality_label not in ('完整', '无缺失', 'complete', 'no missing values', '完整率', 'completeness'):
        raise DomainError('当前仅支持完整率质量规则；其他质量要求需接入校验器后执行')
    if quality_label in ('完整', '无缺失', 'complete', 'no missing values') and quality != 1:
        raise DomainError('无缺失的质量要求不能降低完整率阈值')
    if specification.get('cost') not in (None, ''):
        raise DomainError('成本预算执行适配器尚未接入，不能忽略已确认的成本约束', 409)
    start = instant(specification.get('time_start'))
    end = instant(specification.get('time_end'), end_of_date=True)
    if start >= end:
        raise DomainError('执行时间窗必须非空且起点早于终点')
    deadline = instant(specification.get('deadline'), end_of_date=True)
    if kind == 'metadata':
        deadline = min(deadline, end)
    policy = {'version': 1, 'scope': {'kind': kind, 'filters': checked,
              'all_rows': scope.get('all_rows', False)},
              'time_field': raw.get('time_field'), 'time_start': start.isoformat(),
              'time_end_exclusive': end.isoformat(), 'window_kind': 'data' if kind == 'rows' else 'execution',
              'deadline_at': deadline.isoformat(), 'purpose': specification['purpose'],
              'output_mode': mode, 'circulation_modes': sorted(set(modes)),
              'quality_min_complete': quality,
              'max_rows': number(raw.get('max_rows', 200), '最大交付行数', 1, 200, True),
              'min_group_size': number(raw.get('min_group_size', 5), '最小分组记录数', 5, 100000, True),
              'source_specification_digest': fingerprint(specification)}
    return policy
