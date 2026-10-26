import copy
import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from execution.delivery import Delivery, canonical, digest
from execution.medical import aggregate
from execution.security.output_policy import (
    medical_output_policy, metadata_output_policy, view_output_policy,
    sensitive_output_allowed,
)
from platform_core.storage import DomainError


class DeliverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.delivery = Delivery.__new__(Delivery)
        self.plan = {
            'id': 'PLAN-test', 'version': 1, 'scenario': 'medical',
            'purpose': 'research', 'output_mode': 'aggregate',
            'quality_min_complete': 1, 'output_policy': medical_output_policy(),
        }
        self.grant = {
            'id': 'GRANT-test', 'status': 'active', 'purpose': 'research',
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'min_group_size': 5,
        }

    def check(self, result, plan=None):
        return self.delivery.checks(result, plan or self.plan, self.grant)

    def public_view(self, classification='public', name='region', projection=False):
        field = {'id': 'FIELD-1', 'name': name, 'classification': classification,
                 'structure': {'type': 'TEXT'}}
        view = {'fields': [field['id']], 'output_fields': [field['id']],
                'group_by': [field['id']], 'output_mode': 'aggregate', 'projections': {}}
        if projection:
            view['projections'] = {'CON-1': {'name': 'category', 'expression': {'field': field['id']}}}
            view['group_by'] = ['CON-1']
        return {**self.plan, 'scenario': 'custom',
                'output_policy': view_output_policy(view, {field['id']: field})}

    def test_medical_aggregate_is_preserved(self):
        result = aggregate()
        self.assertEqual(result['rows'], [['40—49岁', '类别甲', 5]])
        self.assertTrue(all(self.check(result).values()))

    def test_previous_alias_and_nested_bypasses_are_rejected(self):
        examples = [
            {'columns': ['patient_name', 'count'], 'rows': [['虚构姓名', 5]]},
            {'columns': ['diagnosis', 'count'], 'rows': [['虚构诊断', 5]]},
            {'columns': ['value', 'count'], 'rows': [['虚构姓名、虚构诊断、13800138000', 5]]},
            {'columns': ['category', 'count'], 'rows': [[{'patient_name': '虚构姓名', 'diagnosis': '虚构诊断'}, 5]]},
        ]
        for result in examples:
            with self.subTest(result=result):
                checks = self.check(result)
                self.assertFalse(checks['sensitive_information'])
                self.assertFalse(all(checks.values()))

    def test_health_lineage_cannot_be_declassified_by_alias(self):
        plan = self.public_view('health', 'test_result', projection=True)
        self.assertFalse(self.check({'columns': ['category', 'count'], 'rows': [['A', 5]]}, plan)['sensitive_information'])
        self.assertEqual(plan['output_policy']['columns']['category']['source_fields'][0]['id'], 'FIELD-1')

    def test_identifier_name_cannot_be_downgraded_as_public(self):
        plan = self.public_view('public', 'patient_name', projection=True)
        self.assertFalse(self.check({'columns': ['category', 'count'], 'rows': [['虚构姓名', 5]]}, plan)['sensitive_information'])

    def test_unknown_classification_is_denied_but_count_is_allowed(self):
        plan = self.public_view('待确认')
        self.assertFalse(self.check({'columns': ['region', 'count'], 'rows': [['A', 5]]}, plan)['sensitive_information'])
        count_view = {'fields': ['FIELD-1'], 'group_by': [], 'output_mode': 'aggregate', 'projections': {}}
        field = {'id': 'FIELD-1', 'name': 'region', 'classification': '待确认', 'structure': {'type': 'TEXT'}}
        plan['output_policy'] = view_output_policy(count_view, {'FIELD-1': field})
        self.assertTrue(self.check({'columns': ['count'], 'rows': [[5]]}, plan)['sensitive_information'])

    def test_public_group_with_valid_classification_is_allowed(self):
        plan = self.public_view()
        self.assertTrue(all(self.check({'columns': ['region', 'count'], 'rows': [['North', 5]]}, plan).values()))

    def test_content_is_checked_even_for_declared_public_columns(self):
        plan = self.public_view()
        for value in ('13800138000', '138-0013-8000', 'ｔｅｓｔ＠ｅｘａｍｐｌｅ．ｃｏｍ',
                      '姓名：虚构姓名', 'diagnosis: fictional condition', '诊断：虚构诊断'):
            with self.subTest(value=value):
                self.assertFalse(self.check({'columns': ['region', 'count'], 'rows': [[value, 5]]}, plan)['sensitive_information'])

    def test_nested_values_require_reviewed_schema(self):
        plan = self.public_view()
        for value in ({'value': 'benign'}, ['A'], {'nested': {'phone': '13800138000'}}):
            self.assertFalse(self.check({'columns': ['region', 'count'], 'rows': [[value, 5]]}, plan)['sensitive_information'])

    def test_extra_fields_cannot_bypass_table_checks(self):
        result = aggregate()
        for extra in ({'raw': {'patient_name': '虚构姓名'}}, {'notice': '姓名：虚构姓名'}, {'notice': {'raw': 'value'}}, {'output_policy': medical_output_policy()}):
            with self.subTest(extra=extra):
                self.assertFalse(self.check({**result, **extra})['sensitive_information'])

    def test_result_cannot_supply_its_own_policy(self):
        plan = {**self.plan, 'scenario': 'custom'}
        plan.pop('output_policy')
        result = {'columns': ['count'], 'rows': [[5]], 'output_policy': medical_output_policy()}
        self.assertFalse(sensitive_output_allowed(result, plan))

    def test_derived_expression_inherits_every_source_branch(self):
        fields = {
            'public': {'id': 'public', 'name': 'region', 'classification': 'public', 'structure': {'type': 'TEXT'}},
            'private': {'id': 'private', 'name': 'lab_result', 'classification': 'health', 'structure': {'type': 'TEXT'}},
        }
        view = {'fields': list(fields), 'group_by': ['concept'], 'output_mode': 'aggregate',
                'projections': {'concept': {'name': 'bucket', 'expression': {
                    'operator': 'case', 'args': [{'literal': False}, {'field': 'private'}, {'field': 'public'}]}}}}
        policy = view_output_policy(view, fields)
        self.assertEqual({s['id'] for s in policy['columns']['bucket']['source_fields']}, set(fields))
        self.assertFalse(sensitive_output_allowed({'columns': ['bucket', 'count'], 'rows': [['North', 5]]}, {**self.plan, 'output_policy': policy}))

    def test_count_boolean_duplicate_columns_and_unknown_columns_fail(self):
        for result in ({'columns': ['count'], 'rows': [[True]]},
                       {'columns': ['count', 'count'], 'rows': [[5, 5]]},
                       {'columns': ['unknown', 'count'], 'rows': [['A', 5]]}):
            self.assertFalse(all(self.check(result).values()))

    def test_metadata_adapter_has_a_fixed_structure_only_schema(self):
        plan = {**self.plan, 'scenario': 'custom', 'output_mode': 'object', 'output_policy': metadata_output_policy()}
        result = {'columns': ['source', 'source_name', 'table_count'], 'rows': [['his', '医院信息系统', 3]]}
        self.assertTrue(all(self.check(result, plan).values()))
        self.assertFalse(self.check({**result, 'rows': [['his', {'patient_name': '虚构姓名'}, 3]]}, plan)['sensitive_information'])
        self.assertFalse(self.check({'columns': ['source', 'patient_name'], 'rows': [['his', '虚构姓名']]}, plan)['sensitive_information'])

    def test_existing_deliveries_are_rechecked_on_read(self):
        record = {'id': 'DEL-old', 'actor': 'researcher', 'grant_id': self.grant['id'],
                  'state': 'available', 'result': {'columns': ['patient_name', 'count'], 'rows': [['虚构姓名', 5]]}}
        # Model an old delivery which was signed by the former permissive gate.
        # Integrity is valid, so the new output boundary must still reject it.
        self.delivery.key = b'local-test-receipt-key'
        record['receipt'] = {
            'delivery_id': record['id'], 'actor': record['actor'], 'grant_id': record['grant_id'],
            'plan_id': self.plan['id'], 'plan_version': self.plan['version'], 'run_id': 'RUN-old',
            'result_digest': digest(record['result']),
            'output_policy_digest': digest(self.plan['output_policy']),
            'execution_policy_digest': self.plan.get('execution_policy_digest'),
        }
        record['signature'] = hmac.new(self.delivery.key, canonical(record['receipt']), hashlib.sha256).hexdigest()
        self.delivery.store = SimpleNamespace(require=lambda kind, oid: record if kind == 'delivery' else self.grant)
        self.delivery.security = SimpleNamespace(check=lambda *args: (self.grant, {}))
        with self.assertRaisesRegex(DomainError, 'sensitive_information'):
            self.delivery.read(record['id'], 'researcher', self.plan)

    def test_tampered_policy_does_not_allow_health_lineage(self):
        plan = self.public_view('health', 'result_value', projection=True)
        plan = copy.deepcopy(plan)
        plan['output_policy']['columns']['category']['classification'] = 'public'
        self.assertFalse(self.check({'columns': ['category', 'count'], 'rows': [['A', 5]]}, plan)['sensitive_information'])


if __name__ == '__main__':
    unittest.main()
