"""Output rules compiled from trusted definitions, never from returned data.

Unknown columns and classifications are denied. Content patterns are an extra
check, not a substitute for the source classification and projection lineage.
"""
import re
import unicodedata


def normalized(value):
    return unicodedata.normalize('NFKC', str(value)).casefold().strip()


IDENTIFIER_NAMES = {
    'patientid', 'recordid', 'personid', 'name', 'patientname', 'fullname',
    'phone', 'telephone', 'mobile', 'email', 'idcard', 'identitynumber',
    '身份证', '身份证号', '姓名', '患者姓名', '手机号', '联系电话', '地址',
}
HEALTH_NAMES = {
    'diagnosis', 'diagnosisname', 'diagnosiscode', 'diagnoses', 'medicalrecord',
    'chiefcomplaint', 'presentillnesshistory', 'physicalexam', 'treatmentplan',
    '诊断', '诊断名称', '诊断编码', '病历', '病史', '检验结果', '健康信息',
}
CLASSIFICATIONS = {
    'public': 'public', '公开': 'public', '公开数据': 'public', 'l1': 'public',
    'internal': 'internal', '内部': 'internal', '内部数据': 'internal', 'l2': 'internal',
    'aggregate': 'aggregate', '汇总': 'aggregate',
    'identifier': 'identifier', '个人标识': 'identifier', '个人信息': 'identifier',
    'health': 'health', '健康信息': 'health', '医疗健康信息': 'health',
    'sensitive': 'sensitive', '敏感': 'sensitive', '敏感数据': 'sensitive',
    'l3': 'sensitive', 'l4': 'sensitive',
}
CONTENT_PATTERNS = (
    re.compile(r'(?<!\d)(?:\+?86[\s-]?)?1[3-9](?:[\s-]?\d){9}(?!\d)'),
    re.compile(r'(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dxX](?!\w)'),
    re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'),
    re.compile(r'(?:姓名|身份证号?|手机号|诊断|病史|病历|检验结果)\s*[:：=]'),
    re.compile(r'\b(?:patient\s*name|diagnosis|medical\s*history)\s*[:=]', re.I),
)
METADATA_VALUES = {
    'notice': {
        '部分分组未满足输出条件', '部分分组可能未满足输出条件',
        '本地对象级结果',
    },
    'fixture': {'新建虚构样例；仅用于本地演示'},
}


def name_classification(name):
    key = re.sub(r'[\W_]+', '', normalized(name))
    if key in IDENTIFIER_NAMES:
        return 'identifier'
    if key in HEALTH_NAMES:
        return 'health'
    return None


def field_classification(field):
    """A known sensitive name cannot be downgraded by an annotation."""
    return name_classification(field.get('name', '')) or CLASSIFICATIONS.get(
        normalized(field.get('classification', '')), 'unknown')


def expression_fields(expression):
    """Keep all source fields, including branches, when following a mapping."""
    if not isinstance(expression, dict):
        return set()
    result = {expression['field']} if 'field' in expression else set()
    for value in expression.values():
        if isinstance(value, dict):
            result.update(expression_fields(value))
        elif isinstance(value, list):
            for item in value:
                result.update(expression_fields(item))
    return result


def field_type(field):
    dtype = normalized(field.get('structure', {}).get('type', ''))
    if 'int' in dtype:
        return 'integer'
    if any(t in dtype for t in ('real', 'double', 'float', 'decimal', 'numeric')):
        return 'number'
    if 'bool' in dtype:
        return 'boolean'
    return 'string'


def medical_output_policy():
    """The fixed fictional adapter emits buckets, never raw health records."""
    return {
        'version': 1, 'origin': 'builtin:medical',
        'columns': {
            'age_group': {'type': 'string', 'classification': 'aggregate',
                          'transform': 'age_band', 'allowed_values': ['30—39岁', '40—49岁', '50—59岁'], 'source_fields': []},
            'category': {'type': 'string', 'classification': 'aggregate',
                         'transform': 'category_bucket', 'allowed_values': ['类别甲', '类别乙'], 'source_fields': []},
            'count': {'type': 'integer', 'classification': 'aggregate', 'transform': 'count', 'source_fields': []},
        },
    }


def metadata_output_policy():
    """Allow only the reviewed metadata adapter's counts and structure names."""
    columns = {}
    for name, dtype in (('source', 'string'), ('source_name', 'string'), ('table_count', 'integer')):
        columns[name] = {
            'type': dtype, 'classification': 'public',
            'source_fields': [{'id': 'metadata.' + name, 'name': name, 'classification': 'public'}],
        }
    return {'version': 1, 'origin': 'builtin:metadata', 'columns': columns}


def view_output_policy(view, fields_by_id):
    """Compile the same names as the query executor and retain projection lineage.

    Grouping does not declassify health or identifier columns. Only the generated
    COUNT column is aggregate output; unclassified grouping keys remain denied.
    """
    projections = view.get('projections', {})
    selected = view.get('group_by', []) if view['output_mode'] == 'aggregate' else view.get('output_fields', view['fields'])
    names = [projections[fid]['name'] if fid in projections else fields_by_id[fid]['name'] for fid in selected]
    names = [name if names.count(name) == 1 else name + '_' + str(i) for i, name in enumerate(names)]
    if view['output_mode'] == 'aggregate':
        names = ['count_group' if name == 'count' else name for name in names]
    columns = {}
    for fid, name in zip(selected, names):
        source_ids = expression_fields(projections[fid]['expression']) if fid in projections else {fid}
        sources = [fields_by_id[oid] for oid in sorted(source_ids)]
        classifications = {field_classification(f) for f in sources}
        classification = next((c for c in ('identifier', 'health', 'sensitive', 'unknown', 'internal', 'public') if c in classifications), 'unknown')
        columns[name] = {
            'type': field_type(sources[0]) if len(sources) == 1 and fid not in projections else 'scalar',
            'classification': classification,
            'source_fields': [{'id': f['id'], 'name': f['name'], 'classification': field_classification(f)} for f in sources],
        }
    if view['output_mode'] == 'aggregate':
        columns['count'] = {'type': 'integer', 'classification': 'aggregate', 'transform': 'count', 'source_fields': []}
    return {'version': 1, 'origin': 'catalog', 'columns': columns}


def policy_for(plan):
    policy = plan.get('output_policy')
    # Existing fixed medical plans may predate the policy field. No fallback is
    # allowed for arbitrary services, queries, or user-supplied result metadata.
    if policy is None and plan.get('scenario') == 'medical':
        policy = medical_output_policy()
    return policy


def _safe_content(value):
    if not isinstance(value, str):
        return True
    value = normalized(value)
    return not any(pattern.search(value) for pattern in CONTENT_PATTERNS)


def _safe_column(name, rule, origin):
    if not isinstance(rule, dict) or name_classification(name):
        return False
    classification = CLASSIFICATIONS.get(normalized(rule.get('classification', '')), 'unknown')
    sources = rule.get('source_fields')
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        return False
    if classification == 'aggregate':
        transform = rule.get('transform')
        if transform == 'count':
            return name == 'count' and rule.get('type') == 'integer'
        return (origin == 'builtin:medical' and transform in ('age_band', 'category_bucket')
                and bool(rule.get('allowed_values')) and rule.get('type') == 'string')
    if classification not in ('public', 'internal') or not sources:
        return False
    return all(field_classification(source) in ('public', 'internal') for source in sources)


def sensitive_output_allowed(result, plan):
    policy = policy_for(plan)
    if not isinstance(policy, dict) or policy.get('version') != 1:
        return False
    origin = policy.get('origin')
    if origin not in ('catalog', 'builtin:medical', 'builtin:metadata'):
        return False
    rules = policy.get('columns')
    if not isinstance(result, dict) or not isinstance(rules, dict):
        return False
    columns, rows = result.get('columns'), result.get('rows')
    if not isinstance(columns, list) or not columns or any(not isinstance(c, str) for c in columns):
        return False
    if len(set(columns)) != len(columns) or set(columns) != set(rules):
        return False
    if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
        return False
    if not all(_safe_column(name, rules[name], origin) for name in columns):
        return False
    # Additional result keys are delivered too, so they need the same boundary.
    for key in set(result) - {'columns', 'rows'}:
        if key not in METADATA_VALUES or not isinstance(result[key],str) or result[key] not in METADATA_VALUES[key]:
            return False
    for row in rows:
        for name, value in zip(columns, row):
            rule = rules[name]
            if isinstance(value, (dict, list, tuple, set)):
                return False  # Nested values require a separately reviewed schema.
            if value is None:
                continue  # Completeness is independently checked by the quality gate.
            dtype = rule.get('type')
            valid = (dtype == 'string' and isinstance(value, str)
                     or dtype == 'integer' and type(value) is int
                     or dtype == 'number' and type(value) in (int, float)
                     or dtype == 'boolean' and type(value) is bool
                     or dtype == 'scalar' and type(value) in (str, int, float, bool))
            if not valid or not _safe_content(value):
                return False
            if 'allowed_values' in rule and value not in rule['allowed_values']:
                return False
    return True
