from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

import medsim
from platform_core.bootstrap import create_app
from platform_core.storage import DomainError
from management.work_orders import WorkOrders
from platform_core.work_order_scenarios import (
    seed_work_orders, SEED_ID, SERVICE_ID, DEMAND_ID, PENDING_ID, PLAN_ID, REQUESTER, PROVIDER, PLATFORM)


class WorkOrderScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = create_app(Path(self.temp.name) / 'control', start_runtime=False).state.runtime
        self.e, self.m = self.runtime.execution, self.runtime.management
        self.runtime.engine = SimpleNamespace(stats={s['id']: {'tables': index + 1, 'rows': 0}
            for index, s in enumerate(medsim.SOURCE_DEFS)})
        self.m.catalog.ingest('his', {'domain': '医院信息', 'tables': {'visit': {
            'columns': ['dept'], 'column_details': [{'name': 'dept', 'type': 'TEXT', 'nullable': False}], 'rows': 0}}})
        source = next(obj['id'] for obj in self.m.catalog.objects(source_id='his') if obj['kind'] == 'table')
        service = self.m.services.save({'id': SERVICE_ID, 'name': '科研数据目录覆盖核验',
            'service_type': 'verification', 'mode': 'regular', 'visibility': 'specified',
            'allowed_subjects': [PLATFORM, REQUESTER], 'purpose': '科研用数前目录覆盖核验',
            'output': '来源名称与表数量', 'audience': '临床研究中心', 'conditions': '已确认的需求与授权',
            'provider': PROVIDER, 'source_scope': '已接入来源目录摘要', 'update_period': '按需读取',
            'quality_commitment': '结构字段齐全', 'restrictions': '不含病例明细', 'metering': '按请求记录',
            'handler_id': 'SVC-META-CATALOG', 'source_objects': [source], 'input_schema': {'type': 'object'},
            'output_schema': {'type': 'object'}, 'sample_input': {}}, PROVIDER)
        validation = self.m.services.validate(service['id'], PROVIDER)
        self.assertTrue(validation['passed'])
        for target in ('review', 'trial', 'published'):
            service = self.m.services.transition(service['id'], {'target': target,
                'expected_version': service['version'], 'validation_id': validation['id'],
                'reason': '实际目录核验请求通过，按发布流程流转'}, PLATFORM)

    def tearDown(self):
        self.e.close()
        self.runtime.external_governance.close()
        self.runtime.shutdown()
        self.temp.cleanup()

    def test_requester_application_runs_owned_service_and_receives_real_feedback_chain(self):
        result = seed_work_orders(self.runtime)
        demand = self.m.store.require('demand', result['demand_id'])
        spec = self.m.store.require('spec', result['spec_id'])
        plan = self.e.get(result['plan_id'], result['plan_version'])
        job = self.e.store.require('job', result['job_id'])
        self.assertEqual(demand['created_by'], REQUESTER)
        self.assertEqual(demand['service_ids'], [SERVICE_ID])
        self.assertEqual(spec['demand_id'], demand['id'])
        self.assertEqual(plan['spec_version'], spec['version'])
        self.assertEqual(set(plan['service_versions']), {SERVICE_ID})
        self.assertEqual(job['state'], 'success')
        self.assertEqual(job['actor'], REQUESTER)
        data = self.e.delivery.read(result['delivery_id'], REQUESTER, plan)
        self.assertTrue(self.e.delivery.verify(data['receipt'], data['signature'])['valid'])
        self.assertEqual(data['result']['columns'], ['source', 'source_name', 'table_count'])
        self.assertEqual(len(data['result']['rows']), len(medsim.SOURCE_DEFS))
        self.assertEqual([row[2] for row in data['result']['rows']], list(range(1, len(medsim.SOURCE_DEFS) + 1)))
        feedback = self.m.store.require('feedback', result['feedback_id'])
        self.assertEqual(feedback['run_id'], job['id'])
        self.assertEqual(feedback['evaluated_by'], REQUESTER)
        contract = self.e.store.require('contract', result['contract_id'])
        self.assertEqual(set(contract['parties']), set(contract['accepted']))
        self.assertEqual(set(contract['parties']), {REQUESTER, PROVIDER, PLATFORM})
        self.assertFalse(any(j['grant_id'] == result['expired_grant_id'] for j in self.e.store.list('job')))
        self.assertTrue(any(d['grant_id'] == result['expired_grant_id'] and not d['allowed'] and
            '已到期' in d['reason'] for d in self.e.store.list('decision')))
        messages = self.m.store.list('work_order_message')
        self.assertEqual(len(messages), 6)
        self.assertTrue(all(item['service_id'] == SERVICE_ID for item in messages))
        self.assertEqual({item['actor'] for item in messages}, {REQUESTER, PROVIDER, PLATFORM})
        for role in ('requester', 'provider'):
            view = WorkOrders(self.runtime).get(DEMAND_ID, {'id': 'demo-' + role, 'role': role})
            self.assertEqual(view['id'], DEMAND_ID)
            self.assertTrue(view['messages'])

    def test_pending_application_has_real_gaps_and_no_spec_plan_or_job(self):
        result = seed_work_orders(self.runtime)
        demand = self.m.store.require('demand', result['pending_demand_id'])
        self.assertEqual(demand['state'], 'draft')
        self.assertEqual(demand['service_ids'], [SERVICE_ID])
        gaps = self.m.intake.gaps(demand['id'])
        self.assertIn('object_scope', gaps['fields'])
        self.assertIn('execution_constraints', gaps['fields'])
        self.assertFalse(any(spec['demand_id'] == demand['id'] for spec in self.m.store.list('spec')))
        self.assertEqual(len(self.e.store.list('job')), 1)
        for store, kinds in ((self.m.store, ('demand', 'spec', 'feedback')),
                             (self.e.store, ('plan', 'job', 'grant', 'delivery', 'contract'))):
            for kind in kinds:
                for record in store.list(kind):
                    content = json.dumps(record, ensure_ascii=False)
                    for word in ('模拟', '虚构', '样例', '演示', '演练'):
                        self.assertNotIn(word, content)

    def test_idempotency_preserves_user_edit_and_does_not_repeat_runs(self):
        result = seed_work_orders(self.runtime)
        draft = self.m.store.require('demand', PENDING_ID)
        changed = self.m.intake.save({**draft, 'expected_version': draft['version'],
            'description': '研究中心已补充院区范围，等待受理岗复核'}, REQUESTER)
        before = [self.m.store.list('demand'), self.e.store.list('job'), self.e.store.list('decision'),
                  self.m.store.list('work_order_message')]
        self.assertEqual(seed_work_orders(self.runtime), result)
        self.assertEqual(self.m.store.require('demand', PENDING_ID), changed)
        self.assertEqual(before, [self.m.store.list('demand'), self.e.store.list('job'), self.e.store.list('decision'),
                                 self.m.store.list('work_order_message')])

    def test_unpublished_service_leaves_no_records_and_can_retry_after_publication(self):
        published = self.m.store.require('service', SERVICE_ID)
        adjusting = self.m.services.transition(SERVICE_ID, {'target': 'adjusting',
            'expected_version': published['version'], 'reason': '正在核对目录更新'}, PLATFORM)
        pending = seed_work_orders(self.runtime)
        self.assertFalse(pending['ready'])
        self.assertEqual(pending['status'], 'waiting_service')
        self.assertIsNone(self.m.store.get('demo_seed', SEED_ID))
        self.assertIsNone(self.m.store.get('demand', DEMAND_ID))
        validation = self.m.services.validate(SERVICE_ID, PROVIDER)
        for target in ('review', 'trial', 'published'):
            adjusting = self.m.services.transition(SERVICE_ID, {'target': target,
                'expected_version': adjusting['version'], 'validation_id': validation['id'],
                'reason': '实际目录核验通过'}, PLATFORM)
        self.assertTrue(seed_work_orders(self.runtime)['ready'])

    def test_failed_execution_rolls_back_and_reserved_ids_are_never_overwritten(self):
        with patch.object(self.e, 'run', side_effect=DomainError('执行器暂不可用')):
            with self.assertRaisesRegex(DomainError, '执行器暂不可用'):
                seed_work_orders(self.runtime)
        self.assertIsNone(self.m.store.get('demand', DEMAND_ID))
        self.assertIsNone(self.m.store.get('demo_seed', SEED_ID))
        self.assertIsNone(self.e.store.get('plan', PLAN_ID))
        self.assertEqual(self.e.store.list('decision'), [])
        self.assertEqual(self.m.store.list('work_order_message'), [])
        owned = self.m.intake.save({'id': PENDING_ID, 'title': '既有研究申请'}, REQUESTER)
        with self.assertRaisesRegex(DomainError, '未覆盖'):
            seed_work_orders(self.runtime)
        self.assertEqual(self.m.store.require('demand', PENDING_ID), owned)
        self.assertIsNone(self.m.store.get('demand', DEMAND_ID))

    def test_non_local_environment_refuses_initialization(self):
        self.m.store.environment = 'production'
        with self.assertRaises(DomainError):
            seed_work_orders(self.runtime)
        self.m.store.environment = 'demo'
        self.assertIsNone(self.m.store.get('demo_seed', SEED_ID))


if __name__ == '__main__':
    unittest.main()
