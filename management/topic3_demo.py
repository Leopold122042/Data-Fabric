"""Persisted synthetic showcase for Project 3 service supply scenarios.

The runner operates only on deterministic fictional rows. It records real
server-side plans, node states, decisions, datasets and evidence without
claiming that the demo handlers are clinical models or external institutions.
"""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from management.store import DomainError, new_id, now


SERVICE_CATALOG = {
    'T3-SVC-QUERY': ('队列筛选服务', '按已确认对象范围和队列条件选取合成样例。'),
    'T3-SVC-FEATURES': ('特征提取服务', '仅提取 T0 之前且位于约定时间窗内的合成特征。'),
    'T3-SVC-RISK': ('风险计算服务', '使用固定规则演示计算风险分层；不作为临床模型。'),
    'T3-SVC-AGGREGATE': ('域内聚合服务', '在来源域内按风险分层聚合合成记录。'),
    'T3-SVC-SUPPRESS': ('小样本抑制服务', '按已确认最小分组规模抑制小样本结果。'),
    'T3-SVC-QUALITY': ('质量验收服务', '按逐字段缺失率、单位和时点检查数据集。'),
    'T3-SVC-SNAPSHOT': ('固定版本生成服务', '将合成数据结果、质量报告和血缘绑定为版本。'),
    'T3-SVC-REVIEW': ('交付复核服务', '核对用途、对象范围、输出粒度和交付条件。'),
}
OUTCOME_SERVICE = ('T3-SVC-OUTCOME', '随访结局服务', '按项目定义生成 T0 后 12 个月的合成结局标签。')
ALLOWED_FIELDS = {'age_years', 'bmi', 'fasting_glucose', 'hba1c', 'family_history', 'followup_outcome'}
ALLOWED_CIRCULATION = {'intra_hospital', 'cross_institution', 'source_domain_compute'}
ALLOWED_DELIVERY = {'controlled_access', 'api_reference', 'approved_file'}


def _json_digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _parse_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise DomainError('期限必须是带时区的 ISO 时间')


def _demo_rows():
    """Thirty stable, synthetic cohort members with a second-source backup."""
    rows = []
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for index in range(30):
        n = index + 1
        glucose = round(4.5 + ((n * 17) % 80) / 10, 1)
        a1c = round(5.1 + ((n * 13) % 42) / 10, 1)
        bmi = round(20 + ((n * 19) % 140) / 10, 1)
        t0 = base + timedelta(days=n * 7)
        rows.append({
            'sample_id': f'S{n:03d}', 'cohort_id': 'C01', 'age_years': 40 + ((n * 7) % 26),
            'sex': '女' if n % 2 else '男', 'bmi': bmi, 'backup_bmi': bmi,
            'fasting_glucose': glucose, 'hba1c': a1c, 'family_history': n % 4 == 0,
            't0': t0.isoformat(), 'feature_days_before': (n * 11) % 91,
            'feature_source': '合成医疗样例 / 风险特征虚拟表',
            'followup_days': 210 if n % 16 == 0 else 365,
            'followup_outcome': bool(glucose >= 8.0 or a1c >= 7.0) if n % 16 else None,
        })
    return rows


class Topic3Demo:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.management.store
        self.evidence = runtime.management.evidence
        self.environment = self.store.environment
        self._ensure_services()

    def _ensure_services(self):
        for service_id, (name, description) in SERVICE_CATALOG.items():
            if self.store.get('topic3_service', service_id):
                continue
            self.store.save('topic3_service', service_id, {
                'name': name, 'description': description, 'handler': service_id,
                'provider': 'A医院合成演示供给方', 'lifecycle': 'published',
                'source_scope': '仅合成医疗样例', 'synthetic_only': True,
                'change_note': '课题三本地合成演示服务基线',
            }, 0, 'system', '登记课题三合成演示服务')

    def _record_event(self, case_id, actor, action, detail, result='已记录'):
        return self.store.save('topic3_event', new_id('T3E'), {
            'case_id': case_id, 'actor': actor, 'action': action,
            'detail': detail, 'result': result, 'environment': self.environment,
        }, 0, actor, action)

    def _can_access(self, case, user, write=False):
        if case.get('created_by') == user.get('id'):
            return True
        if user.get('role') == 'platform':
            return True
        if case.get('scenario') == 'dataset' and user.get('role') == 'provider':
            return True
        if not write and user.get('role') == 'auditor':
            return True
        return False

    def _case(self, case_id, user, write=False):
        case = self.store.require('topic3_case', case_id)
        if not self._can_access(case, user, write):
            raise DomainError('无权访问这项课题三演示任务', 403)
        if write and user.get('role') == 'auditor':
            raise DomainError('审计身份只能查看课题三演示记录', 403)
        return case

    def _update_case(self, case, changes, actor, reason):
        return self.store.save('topic3_case', case['id'], {**case, **changes},
                               case['version'], actor, reason)

    @staticmethod
    def _project_result_for_user(result, user):
        from platform_core.access import level_for, project_result
        level = level_for(user)
        if level >= 4:
            return result
        if level < 2:
            return {'columns': [], 'rows': [], 'message': '当前账户仅可发起服务，不能查看结果明细。'}
        projected = project_result(result, user, 5)
        if not isinstance(projected, dict) or not isinstance(projected.get('rows'), list):
            return projected
        columns = projected.get('columns', [])
        identifier_indexes = [i for i, name in enumerate(columns)
                              if str(name).casefold() in ('sample_id', 'dataset_id')]
        aliases = {}
        rows = []
        for row in projected['rows']:
            values = list(row) if isinstance(row, (list, tuple)) else row
            if isinstance(values, list):
                for index in identifier_indexes:
                    if index >= len(values) or values[index] is None:
                        continue
                    raw = str(values[index])
                    if raw not in aliases:
                        aliases[raw] = f'合成记录-{len(aliases)+1:03d}'
                    values[index] = aliases[raw]
            rows.append(values)
        return {**projected, 'rows': rows}

    def _default_spec(self, scenario):
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0).isoformat()
        common = {
            'purpose': '', 'scope_id': '', 'fields': ['age_years', 'bmi', 'fasting_glucose', 'hba1c', 'family_history'],
            'feature_window_days': 90, 'outcome_window_days': 365, 'quality_min_complete': 0.95,
            'min_group_size': 5, 'expires_at': expires, 'circulation_mode': 'source_domain_compute',
            'delivery_methods': ['controlled_access', 'api_reference', 'approved_file'],
            'delivery_method': 'controlled_access', 'output_mode': 'aggregate',
        }
        if scenario == 'object':
            return {**common, 'purpose': 'A医院院内诊疗辅助（合成风险评估演示）',
                    'scope_id': 'S001', 'output_mode': 'object', 'circulation_mode': 'intra_hospital'}
        if scenario == 'group':
            return {**common, 'purpose': 'P-DEMO-01 项目内人群风险分布研究', 'scope_id': 'C01',
                    'output_mode': 'aggregate', 'circulation_mode': 'cross_institution'}
        if scenario == 'dataset':
            return {**common, 'purpose': 'P-DEMO-01 疾病风险研究数据集生成', 'scope_id': 'P-DEMO-01',
                    'fields': ['age_years', 'bmi', 'fasting_glucose', 'hba1c', 'family_history', 'followup_outcome'],
                    'output_mode': 'dataset', 'circulation_mode': 'cross_institution',
                    'delivery_method': 'controlled_access'}
        raise DomainError('课题三场景无效')

    def create_case(self, body, user):
        scenario = body.get('scenario')
        spec = self._default_spec(scenario)
        request_text = str(body.get('request_text') or '').strip()
        if not request_text:
            request_text = {
                'object': 'A医院医生申请对合成样例对象 S001 做 T0 时点的 2 型糖尿病风险辅助评估，输出评估结果和质量说明。',
                'group': 'B机构研究员为 P-DEMO-01 申请 C01 队列的人群风险分布，仅接收按风险分层汇总的结果。',
                'dataset': 'P-DEMO-01 申请 40 至 65 岁合成样例首次合格记录队列，提取 T0 前90天特征并生成 T0 后12个月结局数据集，缺失率不超过5%，期限30天。',
            }[scenario]
        case_id = new_id('T3CASE')
        case = self.store.save('topic3_case', case_id, {
            'scenario': scenario, 'created_by': user['id'], 'created_role': user.get('role'),
            'request_text': request_text, 'draft': deepcopy(spec), 'spec_id': case_id + '-SPEC',
            'spec_version': None, 'spec_confirmed': False, 'plan_id': case_id + '-PLAN',
            'plan_version': None, 'grant_id': None, 'run_ids': [], 'delivery_id': None,
            'file_delivery_approved': False, 'repair_strategy': None,
            'gap_service_id': OUTCOME_SERVICE[0] if scenario == 'dataset' else None,
            'state': 'request_draft', 'synthetic_only': True,
        }, 0, user['id'], '创建课题三合成演示需求')
        self._record_event(case_id, user['id'], '需求提交', request_text, '建立待确认需求草案；未生成方案或执行')
        return self.state(case_id, user)

    def _owned_lists(self, kind, case_id):
        return sorted((x for x in self.store.list(kind) if x.get('case_id') == case_id),
                      key=lambda x: x.get('updated_at', ''))

    def list_cases(self, user):
        cases = []
        for case in self.store.list('topic3_case'):
            if self._can_access(case, user):
                cases.append({k: case.get(k) for k in ('id', 'scenario', 'state', 'created_by',
                                                       'created_role', 'updated_at', 'spec_confirmed', 'plan_version')})
        return sorted(cases, key=lambda x: x.get('updated_at', ''), reverse=True)

    def state(self, case_id, user):
        from platform_core.access import level_for, project_result
        case = self._case(case_id, user)
        level = level_for(user)
        spec = self.store.get('topic3_spec', case.get('spec_id', ''), case.get('spec_version')) if case.get('spec_version') else None
        contract = self.store.get('topic3_contract', case.get('contract_id', ''), case.get('contract_version')) if case.get('contract_id') else None
        plan = self.store.get('topic3_plan', case.get('plan_id', ''), case.get('plan_version')) if case.get('plan_version') else None
        grant = self.store.get('topic3_grant', case.get('grant_id', '')) if case.get('grant_id') else None
        runs = [r for r in self.evidence.list() if r.get('object_kind') == 'topic3_case' and r.get('object_id') == case_id]
        runs.sort(key=lambda x: x.get('updated_at', ''))
        visible_runs = []
        for run in runs:
            clean = {k: v for k, v in run.items() if k not in ('result', 'output')}
            if level >= 4:
                clean['result_preview'] = run.get('result')
                clean['output_preview'] = run.get('output')
            elif level >= 2 and run.get('result'):
                clean['result_preview'] = self._project_result_for_user(run['result'], user)
            else:
                clean['result_preview'] = {'message': '当前账户仅可发起服务，不能查看结果明细。'}
            visible_runs.append(clean)
        deliveries = self._owned_lists('topic3_delivery', case_id)
        datasets = self._owned_lists('topic3_dataset', case_id)
        visible_datasets = []
        for item in datasets:
            clean = {k: v for k, v in item.items() if k != 'rows'}
            if level >= 4:
                clean['rows'] = item.get('rows', [])
            visible_datasets.append(clean)
        return {
            'case': case, 'spec': spec, 'contract': contract, 'plan': plan, 'grant': grant,
            'decisions': self._owned_lists('topic3_decision', case_id),
            'events': self._owned_lists('topic3_event', case_id),
            'runs': visible_runs, 'latest_run': visible_runs[-1] if visible_runs else None,
            'deliveries': deliveries, 'dataset': visible_datasets[-1] if visible_datasets else None,
            'improvement': self.store.get('improvement', case.get('improvement_id', '')) if case.get('improvement_id') else None,
            'services': [s for s in self.store.list('topic3_service') if s.get('lifecycle') == 'published'],
            'permission': {'level': level, 'can_view_rows': level >= 2,
                           'can_view_source_values': level >= 4},
        }

    def interpret(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        from management.intake.ollama import IntakeLLM
        provider = body.get('provider')
        llm = IntakeLLM(provider=provider)
        schema = {'type': 'object', 'properties': {
            'purpose': {'type': 'string'}, 'scope_id': {'type': 'string'},
            'fields': {'type': 'array', 'items': {'type': 'string'}},
            'feature_window_days': {'type': 'integer'}, 'outcome_window_days': {'type': 'integer'},
            'quality_min_complete': {'type': 'number'}, 'min_group_size': {'type': 'integer'},
            'circulation_mode': {'type': 'string', 'enum': sorted(ALLOWED_CIRCULATION)},
            'delivery_method': {'type': 'string', 'enum': sorted(ALLOWED_DELIVERY)},
            'output_mode': {'type': 'string', 'enum': ['object', 'aggregate', 'dataset']},
            'open_questions': {'type': 'array', 'items': {'type': 'string'}},
        }, 'required': ['purpose', 'scope_id', 'fields', 'feature_window_days', 'outcome_window_days',
                       'quality_min_complete', 'min_group_size', 'circulation_mode', 'delivery_method', 'output_mode']}
        messages = [
            {'role': 'system', 'content': '你是医疗数据服务需求草案助手。只输出符合JSON结构的建议；不得批准授权、确认规格或触发执行。只允许使用用户明确提供的信息；缺少信息写入 open_questions。当前场景为合成演示。'},
            {'role': 'user', 'content': json.dumps({'scenario': case['scenario'], 'request': body.get('request_text') or case['request_text'],
                'allowed_scope': case['scenario'], 'default_spec': case['draft'], 'json': True}, ensure_ascii=False)},
        ]
        proposal = llm.complete_json(messages, schema=schema)
        if not isinstance(proposal, dict):
            raise DomainError('AI 草案格式无效，请改用结构化表单填写', 502)
        allowed = set(schema['properties'])
        proposal = {k: v for k, v in proposal.items() if k in allowed}
        draft = {**self._default_spec(case['scenario']), **case.get('draft', {}), **proposal,
                 'open_questions': proposal.get('open_questions', []), 'draft_only': True,
                 'ai_provider': llm.provider_id, 'ai_model': llm.model}
        updated = self._update_case(case, {'draft': draft, 'request_text': body.get('request_text') or case['request_text'],
                                           'state': 'request_draft'}, user['id'], 'AI 生成待确认需求草案')
        self._record_event(case_id, user['id'], '需求草案', f"{llm.provider_name} / {llm.model}", '草案待人工核对；未授权、未生成方案、未执行')
        return self.state(updated['id'], user)

    def confirm_spec(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        raw = body.get('spec') if isinstance(body.get('spec'), dict) else case.get('draft', {})
        spec = {**self._default_spec(case['scenario']), **raw}
        purpose = str(spec.get('purpose') or '').strip()
        scope = str(spec.get('scope_id') or '').strip()
        if len(purpose) < 6 or len(purpose) > 300:
            raise DomainError('请填写具体用途（6–300字）')
        expected_scope = {'object': 'S001', 'group': 'C01', 'dataset': 'P-DEMO-01'}[case['scenario']]
        if scope != expected_scope:
            raise DomainError('当前演示范围固定为 '+expected_scope+'；不得扩大到其他对象')
        fields = spec.get('fields')
        if not isinstance(fields, list) or not fields or any(x not in ALLOWED_FIELDS for x in fields):
            raise DomainError('字段需从课题三合成字段字典中选择')
        if case['scenario'] == 'dataset' and 'followup_outcome' not in fields:
            raise DomainError('科研数据集须明确包含项目定义的随访结局字段')
        try:
            feature_days = int(spec.get('feature_window_days', 90))
            outcome_days = int(spec.get('outcome_window_days', 365))
            quality = float(spec.get('quality_min_complete', .95))
            min_group = int(spec.get('min_group_size', 5))
        except (TypeError, ValueError):
            raise DomainError('时间窗、质量阈值或最小分组数格式无效')
        if not 1 <= feature_days <= 365 or not 1 <= outcome_days <= 1095:
            raise DomainError('时间窗须在允许的演示范围内')
        if quality < .95 or quality > 1:
            raise DomainError('本案例质量约定固定为缺失率不高于5%；不能通过降低阈值验收')
        if min_group < 5 or min_group > 100:
            raise DomainError('最小分组人数不得低于5')
        circulation = spec.get('circulation_mode')
        delivery = spec.get('delivery_method')
        delivery_methods = spec.get('delivery_methods', ['controlled_access', 'api_reference', 'approved_file'])
        output_mode = {'object': 'object', 'group': 'aggregate', 'dataset': 'dataset'}[case['scenario']]
        if circulation not in ALLOWED_CIRCULATION or delivery not in ALLOWED_DELIVERY:
            raise DomainError('请分别选择有效的流通模式和交付方式')
        if not isinstance(delivery_methods, list) or not delivery_methods or any(x not in ALLOWED_DELIVERY for x in delivery_methods):
            raise DomainError('交付方式清单无效')
        if delivery not in delivery_methods:
            raise DomainError('当前交付方式不在获准方式清单中')
        expires_at = spec.get('expires_at') or self._default_spec(case['scenario'])['expires_at']
        expires = _parse_time(expires_at)
        if expires <= datetime.now(timezone.utc) or expires > datetime.now(timezone.utc) + timedelta(days=365):
            raise DomainError('有效期须晚于当前时间且不超过一年')
        normalized = {
            'purpose': purpose, 'scope_id': scope, 'fields': fields,
            'feature_window_days': feature_days, 'outcome_window_days': outcome_days,
            'quality_min_complete': quality, 'min_group_size': min_group,
            'circulation_mode': circulation, 'delivery_method': delivery,
            'delivery_methods': delivery_methods, 'output_mode': output_mode,
            'expires_at': expires.isoformat(), 'cohort_age_min': 40 if case['scenario'] == 'dataset' else None,
            'cohort_age_max': 65 if case['scenario'] == 'dataset' else None,
            'confirmed_by': user['id'], 'confirmed_at': now(), 'source': '人工核对的合成演示规格',
        }
        spec_id = case['spec_id']
        previous = self.store.get('topic3_spec', spec_id)
        saved_spec = self.store.save('topic3_spec', spec_id, normalized, previous['version'] if previous else 0,
                                     user['id'], '人工确认课题三任务规格')
        contract_id = case['id'] + '-CONTRACT'
        contract = {'case_id': case['id'], 'spec_id': spec_id, 'spec_version': saved_spec['version'],
                    'principal': user['id'], 'purpose': purpose, 'scope_id': scope,
                    'circulation_mode': circulation, 'delivery_methods': delivery_methods,
                    'output_mode': output_mode, 'expires_at': expires.isoformat(),
                    'restrictions': ['禁止跨用途', '禁止扩大对象范围', '交付前复核', '期限届满停止访问'],
                    'source': '合成演示条款转策略对象'}
        old_contract = self.store.get('topic3_contract', contract_id)
        saved_contract = self.store.save('topic3_contract', contract_id, contract,
                                         old_contract['version'] if old_contract else 0,
                                         user['id'], '由已确认规格生成合约策略')
        updated = self._update_case(case, {'spec_version': saved_spec['version'], 'spec_confirmed': True,
            'contract_id': contract_id, 'contract_version': saved_contract['version'], 'grant_id': None,
            'state': 'spec_confirmed'}, user['id'], '确认课题三任务规格')
        self._record_event(case_id, user['id'], '规格确认', f"规格 v{saved_spec['version']}；合约策略 v{saved_contract['version']}",
                           '仅确认需求，不签发授权或执行')
        return self.state(updated['id'], user)

    def supply_outcome_service(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        if case['scenario'] != 'dataset' or not case.get('spec_confirmed'):
            raise DomainError('请先确认科研数据集需求，再由供给方补充随访结局服务')
        service_id, name, description = OUTCOME_SERVICE
        previous = self.store.get('topic3_service', service_id)
        definition = {'name': name, 'description': description,
                      'handler': service_id, 'provider': user['id'], 'lifecycle': 'published',
                      'source_scope': 'P-DEMO-01 合成随访表', 'synthetic_only': True,
                      'change_note': str(body.get('change_note') or '补充并发布项目定义的12个月随访结局服务'),
                      'validated': True}
        saved = self.store.save('topic3_service', service_id, definition,
                                previous['version'] if previous else 0, user['id'], definition['change_note'])
        updated = self._update_case(case, {'gap_service_id': None, 'state': 'supply_ready'}, user['id'], '补充随访结局服务')
        self._record_event(case_id, user['id'], '供给补充', f"{name} v{saved['version']}", '缺口已补齐；需求范围与质量阈值未放宽')
        return self.state(updated['id'], user)

    def _graph(self, case, spec):
        sid = case['scenario']
        nodes_by_case = {
            'object': [
                ('query', 'T3-SVC-QUERY', [], {'scope_id': 'S001'}),
                ('features', 'T3-SVC-FEATURES', ['query'], {'time_anchor': 'T0', 'days_before': spec['feature_window_days']}),
                ('risk', 'T3-SVC-RISK', ['features'], {'model_version': 'demo-rule-risk-v1'}),
                ('delivery_review', 'T3-SVC-REVIEW', ['risk'], {'output_mode': 'object', 'purpose': spec['purpose']}),
            ],
            'group': [
                ('query', 'T3-SVC-QUERY', [], {'scope_id': 'C01'}),
                ('features', 'T3-SVC-FEATURES', ['query'], {'time_anchor': 'T0', 'days_before': spec['feature_window_days']}),
                ('risk', 'T3-SVC-RISK', ['features'], {'model_version': 'demo-rule-risk-v1'}),
                ('aggregate', 'T3-SVC-AGGREGATE', ['risk'], {'group_by': 'risk_band', 'location': 'A医院来源域'}),
                ('suppress', 'T3-SVC-SUPPRESS', ['aggregate'], {'min_group_size': spec['min_group_size']}),
                ('delivery_review', 'T3-SVC-REVIEW', ['suppress'], {'output_mode': 'aggregate', 'purpose': spec['purpose']}),
            ],
            'dataset': [
                ('cohort', 'T3-SVC-QUERY', [], {'scope_id': 'P-DEMO-01', 'age': [40, 65], 'deduplicate': 'first_eligible_T0'}),
                ('features', 'T3-SVC-FEATURES', ['cohort'], {'time_anchor': 'T0', 'days_before': spec['feature_window_days']}),
                ('outcome', OUTCOME_SERVICE[0], ['cohort'], {'days_after': spec['outcome_window_days'], 'definition': 'project_confirmed'}),
                ('quality', 'T3-SVC-QUALITY', ['features', 'outcome'], {'max_missing_rate': round(1-spec['quality_min_complete'], 4), 'unit_check': True, 'future_feature_check': True}),
                ('snapshot', 'T3-SVC-SNAPSHOT', ['quality'], {'field_dictionary': True, 'source_lineage': True}),
                ('delivery_review', 'T3-SVC-REVIEW', ['snapshot'], {'output_mode': 'dataset', 'purpose': spec['purpose']}),
            ],
        }
        definitions = []
        service_versions = {}
        for node_id, service_id, dependencies, params in nodes_by_case[sid]:
            service = self.store.require('topic3_service', service_id)
            service_versions[service_id] = service['version']
            definitions.append({'id': node_id, 'service_id': service_id, 'service_name': service['name'],
                                'service_version': service['version'], 'depends_on': dependencies,
                                'parameters': params, 'status': 'pending'})
        why = {
            'object': ['对象范围明确为 S001，保留查询、特征、风险和交付复核四个必要节点。',
                       '对象级输出与院内职责场景相配；不进入人群聚合节点。'],
            'group': ['复用对象级需求的查询、特征和风险基础服务。',
                      '新增来源域内聚合与小样本抑制，跨机构只交付汇总结果。'],
            'dataset': ['按 P-DEMO-01 年龄队列与首次合格 T0 去重。',
                        '随访结局服务补齐后才生成完整图；质量不达标时快照节点被阻断。'],
        }[sid]
        return definitions, service_versions, why

    def generate_plan(self, case_id, user):
        case = self._case(case_id, user, write=True)
        if not case.get('spec_confirmed'):
            raise DomainError('请先人工确认任务规格')
        if case['scenario'] == 'dataset' and not self.store.get('topic3_service', OUTCOME_SERVICE[0]):
            updated = self._update_case(case, {'state': 'supply_gap'}, user['id'], '检测到随访结局服务缺口')
            self._record_event(case_id, user['id'], '缺口诊断', '缺少项目定义的12个月随访结局服务', '方案被阻断；等待供给方补充并发布')
            return {'state': self.state(updated['id'], user), 'gaps': [
                {'service_id': OUTCOME_SERVICE[0], 'name': OUTCOME_SERVICE[1], 'reason': '目录中尚无项目定义的随访结局服务'}]}
        spec = self.store.require('topic3_spec', case['spec_id'], case['spec_version'])
        nodes, versions, reasons = self._graph(case, spec)
        plan_id = case['plan_id']
        old = self.store.get('topic3_plan', plan_id)
        definition = {'case_id': case_id, 'scenario': case['scenario'],
                      'spec_id': case['spec_id'], 'spec_version': case['spec_version'],
                      'purpose': spec['purpose'], 'scope_id': spec['scope_id'],
                      'output_mode': spec['output_mode'], 'circulation_mode': spec['circulation_mode'],
                      'delivery_methods': spec['delivery_methods'], 'nodes': nodes,
                      'order': [n['id'] for n in nodes], 'service_versions': versions,
                      'selection_reasons': reasons, 'synthetic_only': True,
                      'graph_digest': _json_digest({'nodes': nodes, 'spec_version': spec['version'], 'services': versions}),
                      'status': 'draft', 'created_by': old.get('created_by', user['id']) if old else user['id'],
                      'confirmed_by': None, 'confirmed_at': None}
        plan = self.store.save('topic3_plan', plan_id, definition, old['version'] if old else 0,
                               user['id'], '按确认规格与服务版本生成课题三服务图')
        updated = self._update_case(case, {'plan_version': plan['version'], 'grant_id': None,
                                           'state': 'plan_draft'}, user['id'], '生成课题三方案草案')
        self._record_event(case_id, user['id'], '服务图生成', f"方案 v{plan['version']}；{len(nodes)} 个实际节点", '节点和参数已保存；等待人工确认')
        return {'state': self.state(updated['id'], user), 'gaps': []}

    def confirm_plan(self, case_id, user):
        case = self._case(case_id, user, write=True)
        if not case.get('plan_version'):
            raise DomainError('请先生成服务图')
        plan = self.store.require('topic3_plan', case['plan_id'], case['plan_version'])
        if plan['status'] != 'draft':
            raise DomainError('当前服务图不处于待确认状态', 409)
        updated_plan = self.store.save('topic3_plan', plan['id'], {**plan, 'status': 'confirmed',
            'confirmed_by': user['id'], 'confirmed_at': now()}, plan['version'], user['id'], '人工确认课题三服务图')
        updated_case = self._update_case(case, {'plan_version': updated_plan['version'], 'state': 'plan_confirmed',
                                                'grant_id': None}, user['id'], '确认方案版本')
        self._record_event(case_id, user['id'], '方案确认', f"方案 v{updated_plan['version']}", '尚未授权或执行')
        return self.state(updated_case['id'], user)

    def check_access(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        plan = self.store.require('topic3_plan', case.get('plan_id', ''), case.get('plan_version'))
        spec = self.store.require('topic3_spec', case['spec_id'], case['spec_version'])
        kind = body.get('kind', 'allow')
        requested_purpose = body.get('purpose') or spec['purpose']
        requested_scope = body.get('scope_id') or spec['scope_id']
        requested_mode = body.get('output_mode') or spec['output_mode']
        reason = None
        if plan.get('status') != 'confirmed': reason = '方案尚未人工确认'
        elif kind == 'purpose': requested_purpose = '商业营销'; reason = '请求用途与合约不一致'
        elif kind == 'expired': reason = '模拟的授权期限已届满'
        elif kind == 'detail' and spec['output_mode'] != 'object':
            requested_mode = 'object'; reason = '群体或数据集规格不允许返回逐人明细'
        elif requested_purpose != spec['purpose']: reason = '请求用途与已确认规格不一致'
        elif requested_scope != spec['scope_id']: reason = '请求对象超出已确认范围'
        elif requested_mode != spec['output_mode']: reason = '请求输出粒度超出已确认范围'
        elif _parse_time(spec['expires_at']) <= datetime.now(timezone.utc): reason = '任务授权期限已届满'
        elif body.get('kind') == 'file' and not case.get('file_delivery_approved'):
            reason = '文件出域尚未获得供给方单独批准'
        allowed = reason is None and kind == 'allow'
        if kind not in ('allow', 'purpose', 'expired', 'detail', 'file'):
            raise DomainError('访问检查类型无效')
        if kind == 'allow' and reason is None:
            allowed = True
        decision = self.store.save('topic3_decision', new_id('T3DEC'), {
            'case_id': case_id, 'plan_id': plan['id'], 'plan_version': plan['version'],
            'actor': user['id'], 'kind': kind, 'allowed': allowed,
            'requested_purpose': requested_purpose, 'requested_scope': requested_scope,
            'requested_output_mode': requested_mode, 'reason': reason or '主体、用途、范围、粒度和期限均匹配',
            'policy_version': case.get('contract_version'), 'created_at': now(),
        }, 0, user['id'], '合约策略访问决定')
        updated_case = case
        if allowed:
            grant = self.store.save('topic3_grant', new_id('T3GRANT'), {
                'case_id': case_id, 'plan_id': plan['id'], 'plan_version': plan['version'],
                'subject': user['id'], 'purpose': spec['purpose'], 'scope_id': spec['scope_id'],
                'output_mode': spec['output_mode'], 'circulation_mode': spec['circulation_mode'],
                'delivery_methods': spec['delivery_methods'], 'expires_at': spec['expires_at'],
                'status': 'active', 'policy_version': case.get('contract_version'),
                'authorization_basis': '本地合成演示；仍按当前账户和方案范围校验',
            }, 0, user['id'], '确认意图并登记课题三演示授权')
            updated_case = self._update_case(case, {'grant_id': grant['id'], 'state': 'authorized'},
                                             user['id'], '登记限期课题三演示授权')
        self._record_event(case_id, user['id'], '策略决定', decision['reason'], '放行' if allowed else '拦截')
        return self.state(updated_case['id'], user)

    def approve_file(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        if case['scenario'] != 'dataset':
            raise DomainError('获准文件交付仅用于科研数据集场景')
        if not case.get('plan_version') or not case.get('spec_confirmed'):
            raise DomainError('请先确认科研任务规格和服务图')
        if not str(body.get('reason') or '').strip():
            raise DomainError('请记录文件出域批准依据')
        updated = self._update_case(case, {'file_delivery_approved': True,
            'file_delivery_approval': {'approver': user['id'], 'role': user.get('role'),
                'reason': str(body['reason'])[:300], 'approved_at': now(),
                'recipient': str(body.get('recipient') or 'P-DEMO-01 研究员')[:120]},
        }, user['id'], '单独批准合成演示文件交付')
        self._record_event(case_id, user['id'], '文件交付审批', updated['file_delivery_approval']['reason'], '仅限当前合成演示任务和指定接收方')
        return self.state(updated['id'], user)

    def revoke(self, case_id, user):
        case = self._case(case_id, user, write=True)
        grant = self.store.require('topic3_grant', case.get('grant_id', ''))
        updated_grant = self.store.save('topic3_grant', grant['id'], {**grant, 'status': 'revoked',
            'revoked_at': now(), 'revoked_by': user['id']}, grant['version'], user['id'], '撤销课题三演示授权')
        updated_case = self._update_case(case, {'grant_id': updated_grant['id'], 'state': 'authorization_revoked'},
                                         user['id'], '撤销方案授权')
        self._record_event(case_id, user['id'], '授权撤销', updated_grant['id'], '后续节点和结果读取将被阻断')
        return self.state(updated_case['id'], user)

    def repair_quality(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        failed = [r for r in self.evidence.list() if r.get('object_kind') == 'topic3_case'
                  and r.get('object_id') == case_id and r.get('status') == 'failed'
                  and r.get('error_code') == 'quality_threshold']
        if not failed:
            raise DomainError('没有缺失率超限的失败证据，不能登记该项质量改进')
        if not str(body.get('change_note') or '').strip():
            raise DomainError('请说明如何补齐来源或修复质量问题')
        service = self.store.require('topic3_service', 'T3-SVC-QUALITY')
        self.store.save('topic3_service', service['id'], {**service,
            'change_note': str(body['change_note'])[:300],
            'repair_strategy': '合并经核对的合成补充来源；不降低5%质量阈值',
            'provider': user['id'],
        }, service['version'], user['id'], '依据质量失败证据改进质量服务')
        updated = self._update_case(case, {'repair_strategy': 'approved_source_supplement',
            'quality_failure_run_id': failed[-1]['id'], 'state': 'quality_rule_updated', 'grant_id': None},
            user['id'], '修订质量服务并要求同范围复测')
        self._record_event(case_id, user['id'], '供给改进', str(body['change_note']), '质量阈值保持缺失率不高于5%；需生成新方案并重新确认')
        return self.state(updated['id'], user)

    def _execute_node(self, node, data, spec, failure_mode, case):
        handler = node['service_id']
        if handler == 'T3-SVC-QUERY':
            rows = _demo_rows()
            deduplicated = 0
            if case['scenario'] == 'object':
                rows = [row for row in rows if row['sample_id'] == spec['scope_id']]
            elif case['scenario'] == 'dataset':
                # Add a later synthetic visit per subject, then actually select
                # the earliest eligible T0 rather than merely labeling the plan.
                later_rows = []
                for row in rows:
                    later = deepcopy(row)
                    later['t0'] = (datetime.fromisoformat(row['t0']) + timedelta(days=180)).isoformat()
                    later['feature_days_before'] = (row['feature_days_before'] + 17) % 91
                    later_rows.append(later)
                rows.extend(later_rows)
                rows = [row for row in rows if spec['cohort_age_min'] <= row['age_years'] <= spec['cohort_age_max']]
                rows.sort(key=lambda row: (row['sample_id'], row['t0']))
                first_eligible = {}
                for row in rows:
                    first_eligible.setdefault(row['sample_id'], row)
                deduplicated = len(rows) - len(first_eligible)
                rows = list(first_eligible.values())
            return {'rows': rows, 'source': 'synthetic-risk-feature-view', 'scope_id': spec['scope_id'],
                    'later_records_removed': deduplicated}
        if handler == 'T3-SVC-FEATURES':
            rows = deepcopy(data['rows'])
            rows = [row for row in rows if row['feature_days_before'] <= spec['feature_window_days']]
            for row in rows:
                row['t0_feature_window'] = f"T0前{spec['feature_window_days']}天"
                row['feature_time_valid'] = row['feature_days_before'] <= spec['feature_window_days']
            return {**data, 'rows': rows, 'feature_count': len(rows)}
        if handler == 'T3-SVC-RISK':
            rows = deepcopy(data['rows'])
            for row in rows:
                if any(row.get(k) is None for k in ('bmi', 'fasting_glucose', 'hba1c')):
                    row['risk_score'] = None; row['risk_band'] = '不可评估'; continue
                score = max(0, min(1, .35 * (row['hba1c'] - 5.5) / 3 +
                                   .30 * (row['fasting_glucose'] - 5.0) / 8 +
                                   .20 * (row['bmi'] - 25) / 15 + .15 * bool(row['family_history'])))
                row['risk_score'] = round(score, 3)
                row['risk_band'] = '较高' if score >= .55 else '中等' if score >= .30 else '较低'
            return {**data, 'rows': rows, 'model_version': 'demo-rule-risk-v1',
                    'model_note': '合成规则演示，不是临床预测模型'}
        if handler == 'T3-SVC-AGGREGATE':
            counts = {}
            for row in data['rows']:
                band = row.get('risk_band', '不可评估')
                counts[band] = counts.get(band, 0) + 1
            return {'groups': [{'risk_band': k, 'count': v} for k, v in sorted(counts.items())],
                    'denominator': len(data['rows']), 'location': 'A医院来源域（合成演示）'}
        if handler == 'T3-SVC-SUPPRESS':
            min_group = spec['min_group_size']
            groups = [{'risk_band': x['risk_band'], 'count': x['count']} for x in data['groups'] if x['count'] >= min_group]
            return {'columns': ['risk_band', 'count'], 'rows': [[x['risk_band'], x['count']] for x in groups],
                    'suppressed_groups': len(data['groups']) - len(groups), 'min_group_size': min_group,
                    'denominator': data['denominator']}
        if handler == OUTCOME_SERVICE[0]:
            rows = deepcopy(data['rows'])
            for row in rows:
                if row['followup_days'] < spec['outcome_window_days']:
                    row['followup_outcome'] = None
                    row['outcome_status'] = '随访不足，不默认记为阴性'
                else:
                    row['outcome_status'] = '项目定义合成标签'
            return {**data, 'rows': rows, 'outcome_window_days': spec['outcome_window_days']}
        if handler == 'T3-SVC-QUALITY':
            rows = deepcopy(data['rows'])
            if failure_mode == 'quality_fail' and not case.get('repair_strategy'):
                for row in rows[:2]:
                    row['bmi'] = None
            repaired = 0
            if case.get('repair_strategy') == 'approved_source_supplement':
                for row in rows:
                    if row.get('bmi') is None and row.get('backup_bmi') is not None:
                        row['bmi'] = row['backup_bmi']; repaired += 1
            fields = [x for x in spec['fields'] if x in ALLOWED_FIELDS]
            field_rates = {field: sum(row.get(field) is not None for row in rows) / max(1, len(rows)) for field in fields}
            min_complete = min(field_rates.values(), default=1.0)
            missing_rate = 1 - min_complete
            quality = {'rows': len(rows), 'field_completeness': field_rates,
                       'max_missing_rate': round(missing_rate, 4),
                       'threshold': round(1-spec['quality_min_complete'], 4),
                       'passed': min_complete >= spec['quality_min_complete'],
                       'units_normalized': True, 'future_features': sum(not r.get('feature_time_valid', True) for r in rows),
                       'insufficient_followup_not_negative': sum(r.get('followup_outcome') is None for r in rows),
                       'supplemented_rows': repaired,
                       'later_records_removed': data.get('later_records_removed', 0)}
            return {'rows': rows, 'quality': quality}
        if handler == 'T3-SVC-SNAPSHOT':
            return {'rows': deepcopy(data['rows']), 'quality': deepcopy(data.get('quality') or {}),
                    'field_dictionary': {f: {'type': 'numeric' if f in ('age_years', 'bmi', 'fasting_glucose', 'hba1c') else 'categorical',
                                             'unit': {'age_years': 'year', 'bmi': 'kg/m2', 'fasting_glucose': 'mmol/L', 'hba1c': '%'} .get(f, 'category'),
                                             'meaning': f} for f in spec['fields']},
                    'lineage': {'source': data.get('source', '合成来源'), 'service_versions': node.get('service_version'),
                                'filter': {'age': [40, 65], 'scope': spec['scope_id']},
                                'record_selection': '每个合成对象选择最早合格 T0',
                                'later_records_removed': (data.get('quality') or {}).get('later_records_removed', 0),
                                'feature_window_days': spec['feature_window_days'],
                                'outcome_window_days': spec['outcome_window_days']}}
        if handler == 'T3-SVC-REVIEW':
            if case['scenario'] == 'object':
                rows = data.get('rows', [])
                if rows and 'risk_band' in rows[0]:
                    result = {'columns': ['sample_id', 'risk_score', 'risk_band', 'model_version'],
                              'rows': [[r['sample_id'], r.get('risk_score'), r.get('risk_band'), data.get('model_version')] for r in rows],
                              'quality_note': '仅合成输入完整性；规则不具有临床效度'}
                else:
                    result = {'columns': ['sample_id'], 'rows': [[r['sample_id']] for r in rows]}
                return {'result': result, 'checks': {'purpose': True, 'scope': True, 'output': True, 'expiry': True}}
            if case['scenario'] == 'group':
                return {'result': data, 'checks': {'purpose': True, 'scope': True, 'output': True, 'expiry': True}}
            quality = data.get('quality') or {}
            if not quality.get('passed'):
                raise DomainError(f"质量验收未通过：最高字段缺失率 {quality.get('max_missing_rate', 0):.1%}，约定上限 5%", 409)
            return {'snapshot': data, 'checks': {'purpose': True, 'scope': True, 'output': True, 'expiry': True}}
        raise DomainError('演示方案引用了未知服务节点', 409)

    def run(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        if not case.get('plan_version'):
            raise DomainError('请先生成服务图')
        plan = self.store.require('topic3_plan', case['plan_id'], case['plan_version'])
        if plan.get('status') != 'confirmed':
            raise DomainError('请先确认当前服务图版本')
        grant = self.store.require('topic3_grant', case.get('grant_id', ''))
        if (grant.get('status') != 'active' or grant.get('subject') != user['id']
                or grant.get('plan_id') != plan['id'] or grant.get('plan_version') != plan['version']
                or grant.get('purpose') != plan['purpose']
                or _parse_time(grant['expires_at']) <= datetime.now(timezone.utc)):
            raise DomainError('当前方案没有有效授权；请重新核验授权条件', 403)
        failure_mode = body.get('failure_mode', 'normal')
        if failure_mode not in ('normal', 'quality_fail') or (failure_mode == 'quality_fail' and case['scenario'] != 'dataset'):
            raise DomainError('该场景不支持此故障注入')
        start = time.perf_counter(); node_states = {}; data = {}; failed = None; output = None; quality_report = None
        for node in plan['nodes']:
            if any(node_states.get(dep, {}).get('status') != 'success' for dep in node['depends_on']):
                node_states[node['id']] = {'status': 'blocked', 'detail': '上游节点未成功'}
                continue
            current_case = self.store.require('topic3_case', case_id)
            current_grant = self.store.require('topic3_grant', grant['id'])
            if current_grant.get('status') != 'active' or _parse_time(current_grant['expires_at']) <= datetime.now(timezone.utc):
                node_states[node['id']] = {'status': 'blocked', 'detail': '执行中授权撤销或到期'}
                failed = '执行中授权失效'; break
            service = self.store.require('topic3_service', node['service_id'])
            if service['version'] != plan['service_versions'].get(node['service_id']):
                node_states[node['id']] = {'status': 'blocked', 'detail': '服务版本与确认方案不一致'}
                failed = '方案服务版本已变化'; break
            tick = time.perf_counter()
            try:
                data = self._execute_node(node, data, self.store.require('topic3_spec', case['spec_id'], plan['spec_version']),
                                          failure_mode, current_case)
                if node['service_id'] == 'T3-SVC-QUALITY':
                    quality_report = deepcopy(data.get('quality'))
                if node['service_id'] == 'T3-SVC-QUALITY':
                    data['source'] = '合成补充源' if current_case.get('repair_strategy') else '合成风险特征虚拟表'
                node_states[node['id']] = {'status': 'success', 'service_id': node['service_id'],
                    'service_version': service['version'], 'duration_ms': round((time.perf_counter()-tick)*1000, 3),
                    'parameters': deepcopy(node['parameters'])}
                if node['service_id'] == 'T3-SVC-REVIEW':
                    output = data
                if node['service_id'] == 'T3-SVC-QUALITY' and not data['quality']['passed']:
                    node_states[node['id']].update({'status': 'failed',
                        'detail': f"最高字段缺失率 {data['quality']['max_missing_rate']:.1%} 超过约定上限 {data['quality']['threshold']:.1%}"})
                    failed = '质量验收未通过'; break
            except DomainError as exc:
                node_states[node['id']] = {'status': 'failed', 'service_id': node['service_id'],
                    'service_version': service['version'], 'duration_ms': round((time.perf_counter()-tick)*1000, 3),
                    'detail': str(exc)}
                failed = str(exc); break
        for node in plan['nodes']:
            node_states.setdefault(node['id'], {'status': 'blocked', 'detail': failed or '上游未完成'})
        status = 'failed' if failed else 'success'
        quality = quality_report or (data.get('quality') if isinstance(data, dict) else None)
        result = (output or {}).get('result') if isinstance(output, dict) else None
        dataset = (output or {}).get('snapshot') if isinstance(output, dict) else None
        if status == 'success' and case['scenario'] == 'dataset':
            existing = self._owned_lists('topic3_dataset', case_id)
            dataset_version = f"v1.{len(existing)}"
            dataset_rows = dataset['rows']
            dataset_record = self.store.save('topic3_dataset', new_id('T3DATASET'), {
                'case_id': case_id, 'dataset_version': dataset_version, 'rows': dataset_rows,
                'record_count': len(dataset_rows), 'content_digest': _json_digest(dataset_rows),
                'field_dictionary': dataset['field_dictionary'], 'quality_report': dataset['quality'],
                'lineage': {**dataset['lineage'], 'service_versions': plan['service_versions'], 'plan_id': plan['id'], 'plan_version': plan['version']},
                'spec_id': case['spec_id'], 'spec_version': plan['spec_version'],
                'synthetic_only': True, 'state': 'accepted',
            }, 0, user['id'], '质量验收通过，登记固定版本合成数据集')
            result = {'columns': ['dataset_id', 'dataset_version', 'record_count', 'content_digest'],
                      'rows': [[dataset_record['id'], dataset_version, len(dataset_rows), dataset_record['content_digest']]],
                      'quality': dataset['quality']}
            output = {'dataset_id': dataset_record['id'], 'dataset_version': dataset_version}
        run_id = new_id('RUN-T3')
        missing_rate = (quality or {}).get('max_missing_rate') if quality else None
        run_record = self.evidence.record({
            'id': run_id, 'object_kind': 'topic3_case', 'object_id': case_id,
            'object_version': case.get('spec_version') or 1, 'environment': self.environment,
            'status': status, 'task_scope': 'topic3:'+case_id, 'actor': user['id'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'service_versions': plan['service_versions'], 'latency_ms': round((time.perf_counter()-start)*1000, 3),
            'missing_rate': missing_rate, 'manual_count': 0,
            'authorization': 'allowed' if status == 'success' else 'blocked',
            'delivered': False, 'quality_passed': quality.get('passed') if quality else (status == 'success'),
            'error': failed, 'error_code': 'quality_threshold' if quality and not quality.get('passed') else None,
            'nodes': node_states, 'result': result, 'output': output,
            'failure_mode': failure_mode, 'synthetic_only': True,
        })
        case = self.store.require('topic3_case', case_id)
        updated = self._update_case(case, {'run_ids': [*case.get('run_ids', []), run_record['id']],
            'state': 'run_success' if status == 'success' else 'run_failed',
            'last_run_id': run_record['id']}, user['id'], '课题三后端服务图执行')
        self._record_event(case_id, user['id'], '执行完成' if status == 'success' else '执行失败',
                           f"{len(plan['nodes'])}节点；{run_record['latency_ms']} ms", failed or '结果已写入服务运行证据')
        return self.state(updated['id'], user)

    def deliver(self, case_id, body, user):
        case = self._case(case_id, user, write=True)
        method = body.get('method')
        if method not in ALLOWED_DELIVERY:
            raise DomainError('请选择受控访问、授权接口或获准文件交付')
        grant = self.store.require('topic3_grant', case.get('grant_id', ''))
        if grant.get('status') != 'active' or _parse_time(grant['expires_at']) <= datetime.now(timezone.utc):
            raise DomainError('交付前复核失败：授权已撤销或到期', 403)
        plan = self.store.require('topic3_plan', grant['plan_id'], grant['plan_version'])
        if plan.get('status') != 'confirmed' or method not in grant.get('delivery_methods', []):
            raise DomainError('交付方式不在确认方案或有效授权范围内', 403)
        if method == 'approved_file' and not case.get('file_delivery_approved'):
            raise DomainError('文件出域需供给方单独批准')
        runs = [self.evidence.get(rid) for rid in case.get('run_ids', [])]
        latest_success = next((r for r in reversed(runs) if r['status'] == 'success'), None)
        if not latest_success:
            raise DomainError('当前没有成功运行结果可交付')
        if latest_success['plan_version'] != plan['version']:
            raise DomainError('运行结果与当前方案版本不一致，请重新执行', 409)
        delivery = self.store.save('topic3_delivery', new_id('T3DEL'), {
            'case_id': case_id, 'plan_id': plan['id'], 'plan_version': plan['version'],
            'run_id': latest_success['id'], 'grant_id': grant['id'], 'method': method,
            'recipient': case.get('file_delivery_approval', {}).get('recipient') if method == 'approved_file' else grant['subject'],
            'purpose': grant['purpose'], 'expires_at': grant['expires_at'],
            'state': 'available', 'result_digest': _json_digest(latest_success.get('result')),
            'synthetic_only': True,
        }, 0, user['id'], '交付前策略复核通过')
        updated = self._update_case(case, {'delivery_id': delivery['id'], 'state': 'delivered'},
                                    user['id'], '登记课题三受控交付')
        self._record_event(case_id, user['id'], '交付复核', f"{method} · {delivery['id']}", '方案、用途、范围、授权、期限已核对')
        return self.state(updated['id'], user)

    def compare(self, case_id, user):
        case = self._case(case_id, user, write=True)
        runs = [self.evidence.get(rid) for rid in case.get('run_ids', [])]
        before = next((r for r in runs if r.get('error_code') == 'quality_threshold'), None)
        after = next((r for r in reversed(runs) if r['status'] == 'success'), None)
        if not before or not after:
            raise DomainError('需先保留一条质量失败基线，再完成同范围成功复测')
        improvement = self.runtime.management.feedback.compare({
            'before': [before['id']], 'after': [after['id']],
            'change_note': '质量失败触发补充来源和质量服务版本升级；保持缺失率不高于5%，同一任务范围复测。',
        }, user['id'])
        updated = self._update_case(case, {'improvement_id': improvement['id'], 'state': 'improved'},
                                    user['id'], '记录失败触发的同范围复测')
        self._record_event(case_id, user['id'], '同范围复测', f"{before['id']} → {after['id']}", improvement['change_note'])
        return self.state(updated['id'], user)

    def result(self, case_id, user):
        from platform_core.access import level_for, project_result
        case = self._case(case_id, user)
        delivery = self.store.require('topic3_delivery', case.get('delivery_id', ''))
        if delivery['state'] != 'available':
            raise DomainError('交付物已撤销', 410)
        grant = self.store.require('topic3_grant', delivery['grant_id'])
        if grant['status'] != 'active' or _parse_time(grant['expires_at']) <= datetime.now(timezone.utc):
            self._record_event(case_id, user['id'], '交付访问拦截', delivery['id'], '授权已撤销或到期')
            raise DomainError('再次访问被拦截：授权已撤销或到期', 403)
        if delivery['method'] == 'approved_file' and not case.get('file_delivery_approved'):
            self._record_event(case_id, user['id'], '文件交付拦截', delivery['id'], '文件出域批准已撤销')
            raise DomainError('文件交付批准已撤销', 403)
        run = self.evidence.get(delivery['run_id'])
        result = run.get('result')
        dataset = None
        if case['scenario'] == 'dataset':
            dataset_id = (run.get('output') or {}).get('dataset_id')
            source = self.store.require('topic3_dataset', dataset_id or '')
            rows = source.get('rows', [])
            columns = list(source.get('field_dictionary', {}).keys())
            raw = {'columns': columns, 'rows': [[row.get(name) for name in columns] for row in rows]}
            if level_for(user) >= 4:
                projected = raw
            elif level_for(user) >= 2:
                projected = project_result(raw, user, 5)
            else:
                projected = {'columns': [], 'rows': [], 'message': '当前账户仅可发起服务，不能查看结果明细。'}
            dataset = {k: v for k, v in source.items() if k != 'rows'}
            dataset['columns'] = projected.get('columns', [])
            dataset['rows'] = projected.get('rows', [])
            if projected.get('message'):
                dataset['message'] = projected['message']
        elif level_for(user) < 2:
            result = {'columns': [], 'rows': [], 'message': '当前账户仅可发起服务，不能查看结果明细。'}
        elif level_for(user) < 4 and isinstance(result, dict):
            result = self._project_result_for_user(result, user)
        self._record_event(case_id, user['id'], '读取交付结果', delivery['id'],
                           '已按账户等级投影结果' if level_for(user) < 4 else '按4级账户权限读取合成结果')
        return {'delivery': delivery, 'result': result, 'dataset': dataset, 'synthetic_only': True}

    def action(self, case_id, action, body, user):
        methods = {
            'draft': self.interpret, 'confirm_spec': self.confirm_spec,
        }
        if action == 'draft': return self.interpret(case_id, body, user)
        if action == 'confirm_spec': return self.confirm_spec(case_id, body, user)
        if action == 'supply_service': return self.supply_outcome_service(case_id, body, user)
        if action == 'generate_plan': return self.generate_plan(case_id, user)
        if action == 'confirm_plan': return self.confirm_plan(case_id, user)
        if action == 'check_access': return self.check_access(case_id, body, user)
        if action == 'approve_file': return self.approve_file(case_id, body, user)
        if action == 'revoke': return self.revoke(case_id, user)
        if action == 'repair_quality': return self.repair_quality(case_id, body, user)
        if action == 'run': return self.run(case_id, body, user)
        if action == 'deliver': return self.deliver(case_id, body, user)
        if action == 'compare': return self.compare(case_id, user)
        raise DomainError('未知的课题三操作', 404)
