"""Dashboard projections must preserve ownership and never imply result authorization."""
import unittest
from unittest.mock import patch
from tests.integration import test_work_orders as fixtures


class DashboardTests(unittest.TestCase):
    setUp = fixtures.WorkOrderTests.setUp
    tearDown = fixtures.WorkOrderTests.tearDown
    save = fixtures.WorkOrderTests.save
    fixture = fixtures.WorkOrderTests.fixture
    job = fixtures.WorkOrderTests.job

    def dashboard(self, role):
        return self.client.get('/api/dashboard', headers={'X-Demo-Role': role})

    def test_provider_projection_excludes_other_nodes_and_global_statistics(self):
        _, _, plan = self.fixture(mixed=True)
        self.job(plan)
        with patch.object(self.runtime, 'overview', side_effect=AssertionError('must not read global snapshot')):
            response = self.dashboard('provider')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        for key in ('medical', 'events', 'sources', 'deliveries', 'authorization', 'evidence'):
            self.assertNotIn(key, data)
        self.assertEqual(data['service_counts']['total'], sum(s.get('provider') == 'demo-provider' for s in self.m.list('service')))
        self.assertEqual(data['usages'][0]['usage_state'], '已调用')
        for marker in ('PRIVATE-RESULT', 'PRIVATE-OTHER', 'PRIVATE-ATTACHMENT', 'PRIVATE-SQL', 'PRIVATE-SIGNATURE'):
            self.assertNotIn(marker, response.text)

    def test_requester_counts_and_receipts_only_include_self(self):
        _, _, mine = self.fixture()
        self.job(mine)
        _, _, other = self.fixture('REQ-OTHER', 'other-requester')
        self.job(other, 'JOB-OTHER')
        response = self.dashboard('requester')
        data = response.json()
        self.assertEqual(data['order_counts']['total'], 1)
        self.assertEqual(data['delivery_count'], 0)  # fixture receipts have no actor; fail closed
        delivery = self.e.get('delivery', 'DEL-JOB-ONE')
        self.save(self.e, 'delivery', delivery['id'], {**delivery, 'actor': 'demo-requester'})
        response = self.dashboard('requester')
        self.assertEqual(response.json()['delivery_count'], 1)
        for marker in ('PRIVATE-RESULT', 'PRIVATE-SIGNATURE', 'REQ-OTHER'):
            self.assertNotIn(marker, response.text)
        self.assertNotIn('readable', response.json()['deliveries'][0])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_provider_counts_only_explicitly_referenced_live_objects(self):
        self.fixture()
        svc = self.m.get('service', 'SVC-OWN')
        self.save(self.m, 'service', svc['id'], {**svc, 'source_objects': ['own', 'deleted']})
        for oid, status in [('own', 'active'), ('other', 'active'), ('deleted', 'deleted')]:
            self.save(self.m, 'object', oid, {'source_id': 'his', 'status': status})
        data = self.dashboard('provider').json()
        self.assertEqual(data['objects'], {'total': 1, 'by_source': {'his': 1}})

    def test_missing_source_snapshot_does_not_become_zero(self):
        with patch.object(self.runtime, 'overview', side_effect=RuntimeError('unavailable')), self.assertLogs('platform_core.dashboard', level='ERROR'):
            data = self.dashboard('platform').json()
        self.assertNotIn('medical', data)
        self.assertIn('来源与医疗统计暂不可用', data['errors'])
        self.assertIn('非今日累计', data['events']['scope'])

    def test_auditor_dashboard_is_read_only_and_denials_are_not_risks(self):
        self.save(self.e, 'decision', 'DEC-1', {'allowed': False, 'reason': '授权过期'})
        before = self.e.events()
        with patch.object(self.runtime, 'overview', return_value={'counts': {}, 'sources': []}):
            data = self.dashboard('auditor').json()
        self.assertEqual(data['authorization']['rejected'], 1)
        self.assertEqual(data['risks']['open'], 0)
        self.assertEqual(before, self.e.events())

    def test_unknown_role_and_direct_global_routes_reject(self):
        self.assertEqual(self.dashboard('unknown').status_code, 403)
        for role in ('provider', 'requester'):
            for path in ('/api/overview', '/api/cockpit', '/api/events', '/api/medical-overview'):
                self.assertEqual(self.client.get(path, headers={'X-Demo-Role': role}).status_code, 403, (role, path))
        self.assertEqual(self.client.get('/api/medical-overview', headers={'X-Demo-Role': 'auditor'}).status_code, 403)

    def test_attention_is_selected_before_recent_order_truncation(self):
        _, _, plan = self.fixture('OLDER-EXCEPTION')
        self.job(plan, state='failed')
        for i in range(5):
            self.save(self.m, 'demand', 'D-'+str(i), {'title': '范围待补充 '+str(i), 'created_by': 'demo-requester', 'state': 'draft'})
        response = self.dashboard('requester').json()
        self.assertEqual(response['order_counts']['total'], 6)
        self.assertEqual(len(response['attention']), 3)
        self.assertEqual(len(response['orders']), 3)
        self.assertNotIn('OLDER-EXCEPTION', [o['id'] for o in response['orders']])
        self.assertEqual(response['attention'][0]['id'], 'OLDER-EXCEPTION')
