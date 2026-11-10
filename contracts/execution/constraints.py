"""Executable, human-confirmed constraints shared by intake and execution.

Free-text scope descriptions are retained for review; filters are the executable
meaning explicitly confirmed with that description. Unsupported rules fail closed.
"""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
            parsed = parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
            if end_of_date:
                parsed += timedelta(days=1)
        elif parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise DomainError('执行时间需为ISO日期或带时区的时间；日期按北京时间日界计算')


def number(value, label, low, high, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise DomainError(label + '超出支持范围')
    return value


def has_cost_constraint(value):
    """Return whether a cost value represents an actual user constraint.

    Ollama sometimes serializes JSON null as the literal string "null". Treat
    those empty sentinels as absent, while keeping numeric zero as a real cap.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().casefold() not in {'', 'null', 'none', 'nil', 'undefined', 'n/a', 'na'}
    return True


def normalize(specification):
    raw = specification.get('execution_constraints')
    if not isinstance(raw, dict) or not raw:
        raise DomainError('规格缺少已确认的执行条件，请在需求受理页补充范围、时间字段和交付规则后重新确认', 409)
    allowed = {'scope', 'time_field', 'output_mode', 'circulation_modes',
               'quality_min_complete', 'max_rows', 'min_group_size', 'identifier_output', 'field_output',
               'field_outputs'}
    if set(raw) - allowed:
        raise DomainError('存在尚不支持的执行条件，不能忽略后继续执行')
    scope = raw.get('scope')
    if not isinstance(scope, dict) or set(scope) - {'kind', 'filters', 'all_rows'}:
        raise DomainError('对象范围需明确选择数据行范围或当前元数据快照')
    kind = scope.get('kind')
    if kind not in ('rows', 'lookup', 'metadata'):
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
    elif kind == 'lookup':
        if (not checked or scope.get('all_rows') is True
                or any(isinstance(condition['value'], str) and not condition['value'].strip() for condition in checked)):
            raise DomainError('对象查询必须使用一个或多个非空比较条件，且不能查询全部对象')
        if raw.get('time_field'):
            raise DomainError('对象查询的日期条件应放在 filters 中，不绑定独立执行时间字段')
    elif checked or raw.get('time_field') or scope.get('all_rows'):
        raise DomainError('元数据快照不接受患者范围条件；请使用数据行查询')
    mode = raw.get('output_mode')
    if mode not in ('aggregate', 'object'):
        raise DomainError('需明确汇总或对象级交付')
    labels = {'统计': 'aggregate', '汇总': 'aggregate', '汇总统计': 'aggregate',
              'summary': 'aggregate', 'aggregate': 'aggregate', '对象级': 'object', 'object': 'object'}
    if labels.get(str(specification.get('delivery', '')).strip().lower()) != mode:
        raise DomainError('交付形态说明与执行规则不一致或尚不支持，请明确选择汇总/对象级')
    if kind == 'lookup' and mode != 'object':
        raise DomainError('精确查询必须使用对象级受控输出')
    field_output=raw.get('field_output')
    field_outputs=raw.get('field_outputs')
    identifier_output=raw.get('identifier_output')
    if field_output is not None and field_outputs is not None:
        raise DomainError('普通输出字段请使用field_output或field_outputs之一')
    if kind == 'lookup':
        if identifier_output=='pseudonymized':
            if field_output is not None or field_outputs is not None:raise DomainError('患者伪名查询不能同时声明普通字段输出')
        elif isinstance(field_output,str) and field_output:
            if identifier_output is not None:raise DomainError('普通字段查询不能声明患者伪名输出')
        elif (isinstance(field_outputs,list) and 1<=len(field_outputs)<=10
                and all(isinstance(item,str) and item for item in field_outputs)
                and len(set(field_outputs))==len(field_outputs)):
            if identifier_output is not None:raise DomainError('目录字段查询不能声明患者伪名输出')
        else:raise DomainError('精确查询需绑定患者伪名输出或一个目录字段')
    if kind != 'lookup' and (identifier_output is not None or field_output is not None or field_outputs is not None):
        raise DomainError('患者伪名输出声明只能用于精确查询')
    if kind == 'metadata' and mode != 'object':
        raise DomainError('元数据快照返回目录对象，请选择对象级交付；不查询业务明细')
    modes = raw.get('circulation_modes')
    if not isinstance(modes, list) or not modes or any(m not in ('controlled_compute', 'api', 'download') for m in modes):
        raise DomainError('需明确允许的交付方式')
    if kind == 'lookup' and set(modes) != {'controlled_compute'}:
        raise DomainError('患者精确查询结果只允许受控计算查看')
    quality = number(raw.get('quality_min_complete', 1), '完整率', 0, 1)
    quality_requirement = str(specification.get('quality', '')).strip()
    quality_label = quality_requirement.rstrip('。.!！?？ ').strip().lower()
    if not quality_requirement:
        raise DomainError('必须填写质量要求')
    completeness_rules = ('完整', '无缺失', 'complete', 'no missing values', '完整率', 'completeness')
    quality_is_completeness = quality_label in completeness_rules
    if quality_is_completeness and quality_label in ('完整', '无缺失', 'complete', 'no missing values') and quality != 1:
        raise DomainError('无缺失的质量要求不能降低完整率阈值')
    if has_cost_constraint(specification.get('cost')):
        raise DomainError('成本预算执行适配器尚未接入，不能忽略已确认的成本约束', 409)
    start = instant(specification.get('time_start'))
    end = instant(specification.get('time_end'), end_of_date=True)
    if start >= end:
        raise DomainError('执行时间窗必须非空且起点早于终点')
    deadline = instant(specification.get('deadline'), end_of_date=True)
    if kind in ('metadata', 'lookup'):
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
    if not quality_is_completeness:
        # Preserve the request in the immutable execution policy while making clear
        # that the current executor does not measure accuracy, freshness, or other
        # free-text quality claims.
        policy['quality_requirement'] = quality_requirement
        policy['quality_verification'] = 'unverified'
    if raw.get('identifier_output') is not None:
        policy['identifier_output'] = raw['identifier_output']
    if field_output is not None:
        policy['field_output'] = field_output
    if field_outputs is not None:
        policy['field_outputs'] = list(field_outputs)
    return policy
