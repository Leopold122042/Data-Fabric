"""Exercise trusted output rules through persisted plans, grants and deliveries."""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from execution.medical import aggregate
from execution.security.output_policy import medical_output_policy
from platform_core.bootstrap import create_app
from platform_core.storage import DomainError


class DeliveryPolicyBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, start_runtime=False)
        self.e = self.app.state.runtime.execution

    def tearDown(self):
        self.e.close()
        self.app.state.runtime.external_governance.close()
        self.app.state.runtime.shutdown()
        self.tmp.cleanup()

    def authorized(self, **body):
        plan = self.e.plan({'scenario': 'medical', **body}, 'demo-platform')
        self.e.confirm(plan['id'], plan['version'], 'demo-platform')
        grant = self.e.security.grant({
            'subject': 'demo-platform', 'purpose': plan['purpose'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }, 'demo-platform')
        return plan, grant

    def run_plan(self, plan, grant):
        return self.e.run(self.e.submit(plan['id'], plan['version'], grant['id'], 'demo-platform'))

    def forged_policy(self):
        return {'version': 1, 'origin': 'catalog', 'columns': {
            'value': {'type': 'string', 'classification': 'public',
                      'source_fields': [{'id': 'invented', 'name': 'region', 'classification': 'public'}]},
            'count': {'type': 'integer', 'classification': 'aggregate', 'transform': 'count', 'source_fields': []},
        }}

    def test_normal_medical_delivery_preserves_result_and_bound_policy(self):
        plan, grant = self.authorized()
        job = self.run_plan(plan, grant)
        self.assertEqual(job['state'], 'success', job)
        delivery = self.e.delivery.read(job['delivery_id'], 'demo-platform', plan)
        self.assertEqual(delivery['result']['columns'], ['age_group', 'category', 'count'])
        self.assertEqual(delivery['result']['rows'], [['40—49岁', '类别甲', 5]])
        self.assertTrue(all(delivery['receipt']['checks'].values()))
        self.assertTrue(delivery['receipt']['output_policy_digest'])
        self.assertTrue(self.e.delivery.verify(delivery['receipt'], delivery['signature'])['valid'])

    def test_request_output_policy_cannot_replace_compiled_policy(self):
        plan, grant = self.authorized(output_policy=self.forged_policy())
        self.assertEqual(plan['output_policy'], medical_output_policy())
        self.assertEqual(self.e.store.require('plan', plan['id'])['output_policy'], medical_output_policy())
        with patch('execution.control_plane.aggregate', return_value={'columns': ['value', 'count'], 'rows': [['未授权内容', 5]]}):
            job = self.run_plan(plan, grant)
        self.assertEqual(job['state'], 'failed')
        self.assertIn('sensitive_information', job['error'])
        self.assertIsNone(job['delivery_id'])
        self.assertEqual(self.e.store.list('delivery'), [])

    def test_returned_self_declared_policy_is_rejected(self):
        plan, grant = self.authorized()
        with patch('execution.control_plane.aggregate', return_value={**aggregate(), 'output_policy': self.forged_policy()}):
            job = self.run_plan(plan, grant)
        self.assertEqual(job['state'], 'failed')
        self.assertIn('sensitive_information', job['error'])
        self.assertIsNone(job['delivery_id'])

    def test_direct_create_rejects_modified_plan_even_with_valid_grant(self):
        plan, grant = self.authorized()
        modified = copy.deepcopy(plan)
        modified['output_policy'] = self.forged_policy()
        with self.assertRaisesRegex(DomainError, '方案定义'):
            self.e.delivery.create(aggregate(), modified, grant['id'], 'demo-platform', 'RUN-test')
        self.assertEqual(self.e.store.list('delivery'), [])
        self.assertTrue(any(d['stage'] == 'delivery' and not d['allowed'] for d in self.e.store.list('decision')))

    def test_read_rejects_modified_plan_even_after_valid_delivery(self):
        plan, grant = self.authorized()
        job = self.run_plan(plan, grant)
        self.assertEqual(job['state'], 'success', job)
        modified = copy.deepcopy(plan)
        modified['output_policy'] = self.forged_policy()
        with self.assertRaisesRegex(DomainError, '方案定义'):
            self.e.delivery.read(job['delivery_id'], 'demo-platform', modified)
        self.assertTrue(any(d['stage'] == 'retrieve' and not d['allowed'] for d in self.e.store.list('decision')))

    def test_registered_new_plan_version_invalidates_old_delivery_access(self):
        plan, grant = self.authorized()
        job = self.run_plan(plan, grant)
        self.assertEqual(job['state'], 'success', job)
        changed = self.e.plan({'id': plan['id'], 'expected_version': plan['version'], 'scenario': 'medical'}, 'demo-platform')
        self.assertGreater(changed['version'], plan['version'])
        with self.assertRaises(DomainError):
            self.e.delivery.read(job['delivery_id'], 'demo-platform', plan)

    def test_quality_true_cannot_override_missing_data(self):
        plan, grant = self.authorized()
        result = {'columns': ['age_group', 'category', 'count'], 'rows': [['40—49岁', None, 5]]}
        with self.assertRaisesRegex(DomainError, 'quality'):
            self.e.delivery.create(result, plan, grant['id'], 'demo-platform', 'RUN-quality', quality=True)
        self.assertEqual(self.e.store.list('delivery'), [])

    def test_read_rejects_safe_shaped_result_that_no_longer_matches_receipt(self):
        plan, grant = self.authorized()
        job = self.run_plan(plan, grant)
        original = self.e.store.require('delivery', job['delivery_id'])
        modified = copy.deepcopy(original)
        modified['result']['rows'][0][2] = 6  # Still meets schema and minimum-group checks.
        self.e.store.save('delivery', original['id'], modified, original['version'], 'test', 'Simulated corrupt stored result')
        with self.assertRaises(DomainError):
            self.e.delivery.read(original['id'], 'demo-platform', plan)

    def test_read_rejects_invalid_receipt_signature(self):
        plan, grant = self.authorized()
        job = self.run_plan(plan, grant)
        original = self.e.store.require('delivery', job['delivery_id'])
        self.e.store.save('delivery', original['id'], {**original, 'signature': '0' * 64},
                          original['version'], 'test', 'Simulated corrupt receipt signature')
        with self.assertRaises(DomainError):
            self.e.delivery.read(original['id'], 'demo-platform', plan)

    def test_read_rechecks_a_narrowed_row_limit(self):
        plan, grant = self.authorized()
        result = {'columns': ['age_group', 'category', 'count'],
                  'rows': [['40—49岁', '类别甲', 5], ['50—59岁', '类别乙', 6]]}
        delivery = self.e.delivery.create(result, plan, grant['id'], 'demo-platform', 'RUN-row-limit')
        self.e.store.save('grant', grant['id'], {**grant, 'max_rows': 1}, grant['version'], 'test', 'Narrow result access')
        with self.assertRaises(DomainError):
            self.e.delivery.read(delivery['id'], 'demo-platform', plan)

    def test_output_shape_and_scalar_attacks_are_not_delivered(self):
        attacks = {
            'missing_columns': {'columns': ['count'], 'rows': [[5]]},
            'boolean_count': {'columns': ['age_group', 'category', 'count'], 'rows': [['40—49岁', '类别甲', True]]},
            'nonfinite_count': {'columns': ['age_group', 'category', 'count'], 'rows': [['40—49岁', '类别甲', float('inf')]]},
            'nested_category': {'columns': ['age_group', 'category', 'count'], 'rows': [['40—49岁', {'diagnosis': '虚构诊断'}, 5]]},
            'unreviewed_metadata': {**aggregate(), 'raw': {'patient_name': '虚构姓名'}},
        }
        for label, output in attacks.items():
            with self.subTest(attack=label):
                plan, grant = self.authorized()
                before = len(self.e.store.list('delivery'))
                with patch('execution.control_plane.aggregate', return_value=output):
                    job = self.run_plan(plan, grant)
                self.assertEqual(job['state'], 'failed', job)
                self.assertIsNone(job['delivery_id'])
                self.assertEqual(len(self.e.store.list('delivery')), before)


if __name__ == '__main__':
    unittest.main()
