import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from execution.medical import aggregate
from platform_core.bootstrap import create_app
from platform_core.storage import DomainError


class ReconciliationControlRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, start_runtime=False)
        self.e = self.app.state.runtime.execution
        self.m = self.e.management
        self.plan = self.e.plan({'scenario': 'medical'}, 'demo-platform')
        self.e.confirm(self.plan['id'], self.plan['version'], 'demo-platform')
        self.grant = self.e.security.grant({'subject': 'demo-platform', 'purpose': self.plan['purpose'],
            'plan_id': self.plan['id'], 'plan_version': self.plan['version'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}, 'demo-platform')
        self.case = self.m.store.save('case', 'CASE-pause-regression', {'object_kind': 'plan',
            'object_id': self.plan['id'], 'object_version': self.plan['version'],
            'category': 'execution', 'status': 'open', 'run_ids': []}, 0, 'audit', 'Synthetic execution deviation')

    def tearDown(self):
        self.e.close()
        self.app.state.runtime.external_governance.close()
        self.app.state.runtime.shutdown()
        self.temp.cleanup()

    def pause_body(self):
        return {'action': 'pause_review', 'expected_version': self.case['version'], 'note': 'Review execution deviation'}

    def job(self):
        return self.e.submit(self.plan['id'], self.plan['version'], self.grant['id'], 'demo-platform')

    def test_management_api_pause_blocks_already_queued_job_before_node(self):
        job = self.job()
        with TestClient(self.app) as client:
            response = client.post('/api/management/cases/' + self.case['id'] + '/action', json=self.pause_body())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['last_action'], 'pause_review')
            self.assertEqual(response.json()['command_result']['action'], 'pause')
            self.assertEqual(response.json()['command_result']['status'], 'applied')
            with patch('execution.control_plane.aggregate') as execute:
                failed = self.e.run(job)
        execute.assert_not_called()
        self.assertEqual(self.e.store.require('plan_state', self.plan['id'])['status'], 'paused')
        self.assertEqual(failed['state'], 'failed')
        self.assertIsNone(failed['delivery_id'])
        self.assertEqual(self.e.store.list('delivery'), [])

    def test_pause_after_computation_blocks_result_delivery(self):
        job = self.job()
        def pause_during_node(min_group):
            result = aggregate(min_group)
            self.m.reconciliation.action(self.case['id'], self.pause_body(), 'demo-platform')
            return result
        with patch('execution.control_plane.aggregate', side_effect=pause_during_node) as execute:
            failed = self.e.run(job)
        execute.assert_called_once()
        self.assertEqual(failed['state'], 'failed')
        self.assertIsNone(failed['delivery_id'])
        self.assertEqual(self.e.store.list('delivery'), [])

    def test_stale_case_is_rejected_before_control(self):
        self.m.reconciliation.action(self.case['id'], {**self.pause_body(), 'action': 'observe'}, 'demo-platform')
        state = self.e.store.require('plan_state', self.plan['id'])
        with self.assertRaises(DomainError) as caught:
            self.m.reconciliation.action(self.case['id'], self.pause_body(), 'demo-platform')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.e.store.require('plan_state', self.plan['id']), state)
        self.assertEqual(self.e.store.list('command'), [])

    def test_receipt_write_failure_rolls_back_pause_and_events(self):
        state = self.e.store.require('plan_state', self.plan['id'])
        events = self.e.store.events()
        save = self.e.store.save
        def fail_command_receipt(kind, *args, **kwargs):
            if kind == 'command':
                raise RuntimeError('Injected command receipt failure')
            return save(kind, *args, **kwargs)
        with patch.object(self.e.store, 'save', side_effect=fail_command_receipt):
            with self.assertRaisesRegex(RuntimeError, 'command receipt failure'):
                self.m.reconciliation.action(self.case['id'], self.pause_body(), 'demo-platform')
        self.assertEqual(self.e.store.require('plan_state', self.plan['id']), state)
        self.assertEqual(self.e.store.events(), events)
        self.assertEqual(self.e.store.list('command'), [])
        self.assertEqual(self.m.store.require('case', self.case['id']), self.case)
        self.e.current(self.plan)
        # A failed transaction must release locks and leave the same case retryable.
        result = self.m.reconciliation.action(self.case['id'], self.pause_body(), 'demo-platform')
        self.assertEqual(result['command_result']['status'], 'applied')

    def test_old_plan_case_cannot_pause_replacement_plan_version(self):
        current = self.e.plan({'id': self.plan['id'], 'expected_version': self.plan['version'], 'scenario': 'medical'}, 'demo-platform')
        self.e.confirm(current['id'], current['version'], 'demo-platform')
        state = self.e.store.require('plan_state', current['id'])
        with self.assertRaises(DomainError) as caught:
            self.m.reconciliation.action(self.case['id'], self.pause_body(), 'demo-platform')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.e.store.require('plan_state', current['id']), state)
        self.assertEqual(self.e.store.list('command'), [])
        self.assertEqual(self.m.store.require('case', self.case['id']), self.case)

    def test_concurrent_same_version_actions_do_not_control_twice(self):
        entered = threading.Event()
        second_started = threading.Event()
        release = threading.Event()
        original = self.m.reconciliation.controller
        def held_controller(command):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Timed out waiting for test release')
            return original(command)
        def second_action():
            second_started.set()
            return self.m.reconciliation.action(self.case['id'], self.pause_body(), 'demo-platform')
        with patch.object(self.m.reconciliation, 'controller', side_effect=held_controller) as controller:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.m.reconciliation.action, self.case['id'], self.pause_body(), 'demo-platform')
                try:
                    self.assertTrue(entered.wait(5))
                    second = pool.submit(second_action)
                    self.assertTrue(second_started.wait(5))
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5)['command_result']['status'], 'applied')
                with self.assertRaises(DomainError) as caught:
                    second.result(timeout=5)
                self.assertEqual(caught.exception.status, 409)
            controller.assert_called_once()
        self.assertEqual(len(self.e.store.list('command')), 1)


if __name__ == '__main__':
    unittest.main()
