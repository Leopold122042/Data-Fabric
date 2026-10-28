"""Work-order joins, historical state and provider projections are authorization boundaries."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app


class WorkOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, start_runtime=False)
        self.runtime = self.app.state.runtime
        self.m = self.runtime.management.store
        self.e = self.runtime.execution.store
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def save(self, store, kind, oid, values):
        old = store.get(kind, oid)
        return store.save(kind, oid, values, old['version'] if old else 0, 'fixture', 'Isolated work-order test')

    def get(self, path='', role='requester'):
        return self.client.get('/api/management/work-orders' + path, headers={'X-Demo-Role': role})

    def post(self, oid, body, role='requester'):
        return self.client.post('/api/management/work-orders/' + oid + '/messages', json=body, headers={'X-Demo-Role': role})

    def fixture(self, oid='REQ-ONE', owner='demo-requester', mixed=False):
        self.save(self.m, 'service', 'SVC-OWN', {'name': '本院目录覆盖核验', 'provider': 'demo-provider'})
        self.save(self.m, 'service', 'SVC-OTHER', {'name': 'PRIVATE-OTHER-SERVICE', 'provider': 'other-provider'})
        demand = self.save(self.m, 'demand', oid, {'title': '九月临床研究目录核验', 'purpose': '科研可行性评估',
            'created_by': owner, 'state': 'draft', 'service_ids': ['SVC-OWN'] + (['SVC-OTHER'] if mixed else []),
            'attachments': ['PRIVATE-ATTACHMENT'], 'execution_constraints': {'sql': 'PRIVATE-SQL'}})
        spec = self.save(self.m, 'spec', 'SPEC-' + oid, {'demand_id': oid, 'demand_version': demand['version'], 'confirmed': True})
        self.save(self.m, 'demand', oid, {**demand, 'state': 'confirmed', 'spec_id': spec['id'], 'spec_version': spec['version']})
        nodes = [{'id': 'own', 'operation': 'service', 'service_id': 'SVC-OWN'}]
        if mixed:
            nodes.append({'id': 'PRIVATE-OTHER-NODE', 'operation': 'service', 'service_id': 'SVC-OTHER'})
        plan = self.save(self.e, 'plan', 'PLAN-' + oid, {'name': '目录核验方案', 'created_by': owner, 'spec_id': spec['id'], 'spec_version': spec['version'],
            'service_versions': {'SVC-OWN': 1, **({'SVC-OTHER': 1} if mixed else {})}, 'nodes': nodes, 'circulation_mode': 'download'})
        self.save(self.e, 'plan_state', plan['id'], {'plan_version': plan['version'], 'status': 'confirmed'})
        return demand, spec, plan

    def job(self, plan, oid='JOB-ONE', state='success', retry_of=None):
        nodes = {'own': {'status': 'success', 'trace': {'sql': 'PRIVATE-NODE-SQL'}}}
        if len(plan['nodes']) > 1:
            nodes['PRIVATE-OTHER-NODE'] = {'status': 'blocked', 'reason': 'PRIVATE-OTHER-ERROR'}
        job = self.save(self.e, 'job', oid, {'actor': plan['created_by'], 'plan_id': plan['id'], 'plan_version': plan['version'],
            'state': state, 'retry_of': retry_of, 'nodes': nodes, 'error': 'PRIVATE-RAW-ERROR' if state == 'failed' else None,
            'timeline': [{'stage': 'service_invoked', 'node': 'own', 'service_id': 'SVC-OWN', 'time': '2026-10-27T00:00:00+00:00'}]})
        self.save(self.e, 'run', oid, {'actor': plan['created_by'], 'object_kind': 'plan', 'object_id': plan['id'], 'object_version': plan['version'],
            'plan_id': plan['id'], 'plan_version': plan['version'], 'status': state})
        if state == 'success':
            self.save(self.e, 'delivery', 'DEL-' + oid, {'plan_id': plan['id'], 'plan_version': plan['version'], 'state': 'available',
                'receipt': {'run_id': oid, 'issued_at': job['updated_at']}, 'result': {'rows': [['PRIVATE-RESULT']]}, 'signature': 'PRIVATE-SIGNATURE'})
        return job

    def test_requester_sees_own_full_lifecycle_and_no_other_orders(self):
        demand, spec, plan = self.fixture()
        self.job(plan)
        self.fixture('REQ-OTHER', 'other-requester')
        rows = self.get().json()['items']
        self.assertEqual(self.get().headers['cache-control'], 'no-store')
        self.assertEqual(self.get('/' + demand['id']).headers['cache-control'], 'no-store')
        self.assertEqual([r['id'] for r in rows], [demand['id']])
        self.assertEqual(self.get('/REQ-OTHER').status_code, 404)
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['status'], 'delivered')
        self.assertEqual([s['key'] for s in detail['stages']], ['application', 'confirmation', 'execution', 'delivery', 'exception'])
        self.assertEqual(len(detail['deliveries']), 1)
        self.assertEqual(detail['services'][0]['usage_state'], '已调用')
        self.assertNotIn('PRIVATE-RESULT', json.dumps(detail))
        self.assertNotIn('PRIVATE-SIGNATURE', json.dumps(detail))

    def test_provider_projection_never_exposes_other_nodes_raw_demands_or_results(self):
        demand, _, plan = self.fixture(mixed=True)
        job = self.job(plan, state='failed')
        self.save(self.m, 'feedback', 'FB-WHOLE', {'run_id': job['id'], 'comment': 'PRIVATE-WHOLE-FEEDBACK', 'ratings': {'satisfaction': 2}})
        self.save(self.m, 'feedback', 'FB-OWN', {'run_id': job['id'], 'service_id': 'SVC-OWN', 'comment': '字段字典说明需要补全', 'ratings': {'satisfaction': 3}})
        detail = self.get('/' + demand['id'], 'provider').json()
        text = json.dumps(detail)
        self.assertNotIn('PRIVATE-', text)
        self.assertEqual([s['id'] for s in detail['services']], ['SVC-OWN'])
        self.assertEqual([f['id'] for f in detail['feedback']], ['FB-OWN'])
        self.assertEqual(detail['requester_name'], '临床研究中心')
        self.assertEqual(detail['purpose'], '科研可行性评估')
        self.assertEqual(detail['plans'], [])
        self.assertEqual(detail['jobs'], [])
        raw = self.client.get('/api/management/items/demand', headers={'X-Demo-Role': 'provider'})
        self.assertEqual(raw.status_code, 403)

    def test_selected_service_is_not_reported_as_invoked(self):
        self.save(self.m, 'service', 'SVC-OWN', {'name': '随访范围核验', 'provider': 'demo-provider'})
        self.save(self.m, 'demand', 'REQ-DRAFT', {'title': '随访研究申请', 'created_by': 'demo-requester', 'state': 'draft', 'service_ids': ['SVC-OWN']})
        detail = self.get('/REQ-DRAFT', 'provider').json()
        self.assertEqual(detail['status'], 'draft')
        self.assertEqual(detail['services'][0]['usage_state'], '申请使用')
        self.assertEqual(detail['services'][0]['job_count'], 0)
        self.assertEqual(detail['counts']['jobs'], 0)

    def test_reconfirmation_keeps_historical_delivery_without_claiming_current_completion(self):
        demand, _, plan = self.fixture()
        self.job(plan)
        current = self.m.require('demand', demand['id'])
        self.save(self.m, 'demand', demand['id'], {**current, 'state': 'draft', 'title': '扩大研究范围'})
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['status'], 'draft')
        self.assertEqual(len(detail['deliveries']), 1)
        self.assertFalse(detail['plans'][0]['current'])
        self.assertEqual(detail['stages'][3]['state'], 'pending')
        # A plan referencing a nonexistent pinned specification cannot join by ID alone.
        self.save(self.e, 'plan', 'PLAN-DANGLING', {**plan, 'spec_version': 999})
        self.assertEqual(len(self.get('/' + demand['id']).json()['plans']), 1)

    def test_retry_and_post_delivery_denial_preserve_historical_evidence(self):
        demand, _, plan = self.fixture()
        failed = self.job(plan, 'JOB-FAILED', state='failed')
        self.job(plan, 'JOB-RETRY', retry_of=failed['id'])
        self.save(self.e, 'decision', 'DEC-AFTER', {'plan_id': plan['id'], 'plan_version': plan['version'], 'actor': 'demo-requester', 'allowed': False, 'reason': '授权已到期', 'stage': 'retrieve'})
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['status'], 'delivered')
        execution = next(e for e in detail['exceptions'] if e['id'] == failed['id'])
        self.assertTrue(execution['resolved'])
        self.assertTrue(any(e['kind'] == 'authorization' and not e['resolved'] for e in detail['exceptions']))
        self.assertEqual(len(detail['jobs']), 2)

    def test_authorization_rejection_without_job_is_visible_as_exception(self):
        demand, _, plan = self.fixture()
        self.save(self.e, 'decision', 'DEC-BLOCKED', {'plan_id': plan['id'], 'plan_version': plan['version'], 'actor': 'demo-requester', 'allowed': False, 'reason': '授权已到期', 'stage': 'submission'})
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['status'], 'attention')
        self.assertEqual(detail['jobs'], [])
        self.assertEqual(detail['exceptions'][0]['detail'], '授权已到期')

    def test_messages_are_authenticated_service_scoped_and_do_not_mutate_status(self):
        demand, _, _ = self.fixture(mixed=True)
        before = self.m.require('demand', demand['id'])
        self.assertEqual(self.post(demand['id'], {'body': '请核对院区字段', 'service_id': 'SVC-OWN'}).status_code, 200)
        self.assertEqual(self.post(demand['id'], {'body': '内部协调说明'}).status_code, 200)
        self.assertEqual(self.post(demand['id'], {'body': 'PRIVATE-OTHER-CHANNEL', 'service_id': 'SVC-OTHER'}).status_code, 200)
        reply = self.post(demand['id'], {'body': '院区字典已经补充', 'service_id': 'SVC-OWN'}, 'provider')
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.headers['cache-control'], 'no-store')
        self.assertEqual(reply.json()['actor'], 'demo-provider')
        provider = self.get('/' + demand['id'], 'provider').json()
        self.assertEqual(len(provider['messages']), 2)
        self.assertNotIn('PRIVATE-OTHER-CHANNEL', json.dumps(provider))
        self.assertEqual(len(self.get('/' + demand['id']).json()['messages']), 4)
        self.assertEqual(self.m.require('demand', demand['id']), before)
        for payload in ({'body': '越权', 'service_id': 'SVC-OTHER'}, {'body': '越权'}):
            self.assertEqual(self.post(demand['id'], payload, 'provider').status_code, 403)
        self.assertEqual(self.post(demand['id'], {'body': '审计写入'}, 'auditor').status_code, 403)
        self.fixture('REQ-OTHER', 'other-requester')
        self.assertEqual(self.post('REQ-OTHER', {'body': '越权'}).status_code, 404)
        self.assertEqual(self.get('/' + demand['id'], 'auditor').json()['can_message'], False)

    def test_invalid_messages_fail_without_persisting(self):
        demand, _, _ = self.fixture()
        for payload in ({'body': []}, {'body': 1}, {'body': '  '}, {'body': '字' * 2001}, {'body': '内容', 'actor': 'demo-platform'}, {'body': '内容', 'service_id': []}):
            with self.subTest(payload=str(payload)[:80]):
                self.assertEqual(self.post(demand['id'], payload).status_code, 422)
        self.assertEqual(self.m.list('work_order_message'), [])

    def test_retry_chain_resolves_all_failed_ancestors_but_not_unrelated_jobs(self):
        demand, _, plan = self.fixture()
        first = self.job(plan, 'JOB-A', state='failed')
        second = self.job(plan, 'JOB-B', state='failed', retry_of=first['id'])
        unrelated = self.job(plan, 'JOB-UNRELATED', state='failed')
        self.job(plan, 'JOB-C', retry_of=second['id'])
        exceptions = {e['id']: e for e in self.get('/' + demand['id']).json()['exceptions']}
        self.assertTrue(exceptions[first['id']]['resolved'])
        self.assertTrue(exceptions[second['id']]['resolved'])
        self.assertFalse(exceptions[unrelated['id']]['resolved'])

    def test_read_rejection_is_not_resolved_by_another_grants_submission(self):
        demand, _, plan = self.fixture()
        self.job(plan)
        base = {'plan_id': plan['id'], 'plan_version': plan['version'], 'actor': 'demo-requester'}
        self.save(self.e, 'decision', 'DEC-READ-OLD', {**base, 'grant_id': 'OLD-GRANT', 'stage': 'retrieve', 'allowed': False, 'reason': '授权已到期'})
        self.save(self.e, 'decision', 'DEC-NEW-ALLOWED', {**base, 'grant_id': 'NEW-GRANT', 'stage': 'submission', 'allowed': True})
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['status'], 'delivered')
        self.assertFalse(next(e for e in detail['exceptions'] if e['id'] == 'DEC-READ-OLD')['resolved'])

    def test_same_service_multiple_nodes_count_each_actual_invocation(self):
        demand, _, plan = self.fixture()
        plan = self.save(self.e, 'plan', plan['id'], {**plan, 'nodes': [
            {'id': 'own', 'operation': 'service', 'service_id': 'SVC-OWN'},
            {'id': 'second', 'operation': 'service', 'service_id': 'SVC-OWN'}]})
        job = self.job(plan, state='failed')
        self.save(self.e, 'job', job['id'], {**job, 'nodes': {'own': {'status': 'success'}, 'second': {'status': 'blocked'}},
            'timeline': [{'stage': 'service_invoked', 'node': 'own', 'service_id': 'SVC-OWN'}, {'stage': 'service_invoked', 'node': 'second', 'service_id': 'SVC-OWN'}]})
        service = self.get('/' + demand['id'], 'provider').json()['services'][0]
        self.assertEqual(service['job_count'], 2)
        self.assertEqual(service['success_count'], 1)
        self.assertEqual(service['failed_count'], 1)

    def test_ready_status_uses_real_contract_and_dependency_guards_without_writing_decisions(self):
        owner = 'demo-requester'
        end = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        self.save(self.m, 'service', 'SVC-META', {'name': '目录核验', 'provider': 'demo-provider',
            'handler_id': 'SVC-META-CATALOG', 'lifecycle': 'published', 'visibility': 'public'})
        demand = self.runtime.management.intake.save({'title': '科研目录申请', 'entry': 'service', 'service_ids': ['SVC-META'],
            'object_scope': '当前来源摘要', 'time_start': datetime.now(timezone.utc).isoformat(), 'time_end': end,
            'deadline': end, 'purpose': '课题目录核验', 'quality': '完整', 'delivery': '对象级',
            'execution_constraints': {'scope': {'kind': 'metadata'}, 'output_mode': 'object', 'circulation_modes': ['controlled_compute'],
                                      'quality_min_complete': 1, 'max_rows': 60, 'min_group_size': 5}}, owner)
        spec = self.runtime.management.intake.confirm(demand['id'], {'expected_version': demand['version'],
            'confirmations': {'content': True, 'attachments': True, 'gaps': True, 'execution_constraints': True}, 'note': '确认目录范围'}, 'demo-platform')
        plane = self.runtime.execution
        plan = plane.plan({'spec_id': spec['id'], 'spec_version': spec['version']}, owner)
        plane.confirm(plan['id'], plan['version'], owner)
        contract = self.save(self.e, 'contract', 'AGR-READY', {'plan_id': plan['id'], 'plan_version': plan['version'],
            'parties': ['demo-platform', owner], 'accepted': ['demo-platform', owner], 'expires_at': end})
        grant = plane.security.grant({'subject': owner, 'purpose': plan['purpose'], 'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 'contract_id': contract['id']}, 'demo-platform')
        before = self.e.list('decision')
        self.assertEqual(self.get('/' + demand['id']).json()['status'], 'authorized')
        contract = self.save(self.e, 'contract', contract['id'], {**contract, 'accepted': []})
        self.assertEqual(self.get('/' + demand['id']).json()['status'], 'awaiting_authorization')
        self.assertEqual(self.e.list('decision'), before)
        from platform_core.storage import DomainError
        with self.assertRaisesRegex(DomainError, '尚未全部确认'):
            plane.submit(plan['id'], plan['version'], grant['id'], owner)
        self.assertEqual(len(self.e.list('decision')), len(before) + 1)
        contract = self.save(self.e, 'contract', contract['id'], {**contract, 'accepted': contract['parties'],
            'expires_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
        # An existing rejection remains visible in the overall state.
        self.assertEqual(self.get('/' + demand['id']).json()['status'], 'attention')
        self.assertIn('合约已到期', plane.security.assess(grant['id'], plan, owner, plan['purpose'])[1])
        self.save(self.e, 'contract', contract['id'], {**contract, 'expires_at': end})
        svc = self.m.require('service', 'SVC-META')
        self.save(self.m, 'service', svc['id'], {**svc, 'name': '修订目录核验'})
        self.assertEqual(self.get('/' + demand['id']).json()['status'], 'attention')
        self.assertEqual(len(self.e.list('decision')), len(before) + 1)

    def test_completed_plan_does_not_hide_another_current_running_plan(self):
        demand, _, plan = self.fixture()
        second = self.save(self.e, 'plan', 'PLAN-PARALLEL', {**plan, 'name': '并行核验'})
        self.save(self.e, 'plan_state', second['id'], {'plan_version': second['version'], 'status': 'confirmed'})
        self.job(second, 'JOB-RUNNING', state='running')
        self.job(plan, 'JOB-LATER-COMPLETED')
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['status'], 'running')
        self.assertEqual(len(detail['deliveries']), 1)
        self.assertEqual(len(detail['jobs']), 2)

    def test_service_transfer_does_not_transfer_historical_feedback_or_messages(self):
        demand, _, plan = self.fixture()
        job = self.job(plan)
        self.save(self.m, 'feedback', 'FB-OLD-OWNER', {'run_id': job['id'], 'comment': '供给方历史评价'})
        self.post(demand['id'], {'body': '历史供给方协作', 'service_id': 'SVC-OWN'}, 'provider')
        service = self.m.require('service', 'SVC-OWN')
        self.save(self.m, 'service', service['id'], {**service, 'provider': 'new-provider'})
        # The original provider retains its own pinned participation history.
        old = self.get('/' + demand['id'], 'provider').json()
        self.assertEqual(len(old['feedback']), 1)
        self.assertEqual(len(old['messages']), 1)
        from management.work_orders import WorkOrders
        new = WorkOrders(self.runtime).get(demand['id'], {'role': 'provider', 'id': 'new-provider'})
        self.assertEqual(new['services'][0]['usage_state'], '申请使用')
        self.assertEqual(new['feedback'], [])
        self.assertEqual(new['messages'], [])
        self.assertEqual(new['jobs'], [])

    def test_feedback_targets_follow_terminal_own_runs_and_actual_service_calls(self):
        demand, _, plan = self.fixture(mixed=True)
        self.job(plan, state='failed')
        targets = self.get('/' + demand['id']).json()['feedback_targets']
        self.assertEqual([(t['run_id'], t['service_id']) for t in targets], [('JOB-ONE', 'SVC-OWN')])
        self.assertEqual(self.get('/' + demand['id'], 'provider').json()['feedback_targets'], [])
        self.assertEqual(self.get('/' + demand['id'], 'auditor').json()['feedback_targets'], [])

    def test_guessing_unexecuted_service_ids_does_not_reveal_private_catalog(self):
        for sid, values in [('SVC-PRIVATE', {'lifecycle': 'published', 'visibility': 'specified', 'allowed_subjects': ['another-requester']}),
                            ('SVC-DRAFT', {'lifecycle': 'draft', 'visibility': 'public'})]:
            self.save(self.m, 'service', sid, {'name': 'PRIVATE-SERVICE-NAME-' + sid, 'provider': 'demo-provider', **values})
        self.save(self.m, 'demand', 'REQ-GUESSED', {'title': '自行输入服务编号的申请', 'created_by': 'demo-requester',
            'state': 'draft', 'service_ids': ['SVC-PRIVATE', 'SVC-DRAFT']})
        detail = self.get('/REQ-GUESSED').json()
        self.assertEqual(detail['services'], [])
        self.assertEqual(detail['message_scopes'], [])
        self.assertNotIn('PRIVATE-SERVICE-NAME', json.dumps(detail))
        self.assertEqual(self.get().json()['items'][0]['service_ids'], [])
        self.assertEqual(self.post('REQ-GUESSED', {'body': '猜测服务', 'service_id': 'SVC-PRIVATE'}).status_code, 403)
        self.assertEqual(len(self.get('/REQ-GUESSED', 'provider').json()['services']), 2)

    def test_historical_provider_does_not_inherit_later_unrelated_demand_revisions(self):
        demand, _, plan = self.fixture()
        self.job(plan)
        before = self.get('/' + demand['id'], 'provider').json()
        current = self.m.require('demand', demand['id'])
        self.save(self.m, 'demand', demand['id'], {**current, 'state': 'draft', 'service_ids': ['SVC-OTHER'],
            'title': 'PRIVATE-NEW-PROJECT', 'purpose': 'PRIVATE-NEW-PURPOSE'})
        historical = self.get('/' + demand['id'], 'provider').json()
        self.assertNotIn('PRIVATE-', json.dumps(historical))
        self.assertTrue(historical['historical_scope'])
        self.assertEqual(historical['title'], before['title'])
        self.assertEqual(historical['purpose'], before['purpose'])
        self.assertEqual(historical['status'], 'delivered')
        self.assertEqual(len(historical['timeline']), len(before['timeline']))
        self.assertEqual(self.get('/' + demand['id']).json()['title'], 'PRIVATE-NEW-PROJECT')

    def executable_service_order(self):
        owner = 'demo-requester'
        end = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        self.save(self.m, 'service', 'SVC-CALL', {'name': '实际调用核验', 'provider': 'demo-provider',
            'handler_id': 'SVC-META-CATALOG', 'lifecycle': 'published', 'visibility': 'public'})
        demand = self.runtime.management.intake.save({'title': '目录调用边界', 'entry': 'service', 'service_ids': ['SVC-CALL'],
            'object_scope': '当前来源摘要', 'time_start': datetime.now(timezone.utc).isoformat(), 'time_end': end,
            'deadline': end, 'purpose': '目录核验', 'quality': '完整', 'delivery': '对象级',
            'execution_constraints': {'scope': {'kind': 'metadata'}, 'output_mode': 'object', 'circulation_modes': ['controlled_compute'],
                                      'quality_min_complete': 1, 'max_rows': 60, 'min_group_size': 5}}, owner)
        spec = self.runtime.management.intake.confirm(demand['id'], {'expected_version': demand['version'],
            'confirmations': {'content': True, 'attachments': True, 'gaps': True, 'execution_constraints': True}, 'note': '确认目录范围'}, 'demo-platform')
        plane = self.runtime.execution
        plan = plane.plan({'spec_id': spec['id'], 'spec_version': spec['version']}, owner)
        plane.confirm(plan['id'], plan['version'], owner)
        grant = plane.security.grant({'subject': owner, 'purpose': plan['purpose'], 'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, 'demo-platform')
        return demand, plan, grant

    def test_attempt_guard_rejection_has_zero_calls_and_cannot_be_rated_as_service_use(self):
        demand, plan, grant = self.executable_service_order()
        plane = self.runtime.execution
        job = plane.submit(plan['id'], plan['version'], grant['id'], 'demo-requester')
        original = plane.guard
        def revoke_before_attempt(active, definition, stage):
            if stage.startswith('attempt:'):
                plane.security.revoke(grant['id'], 'demo-platform')
            return original(active, definition, stage)
        with patch.object(plane, 'guard', side_effect=revoke_before_attempt), patch.object(self.runtime, '_dispatch') as dispatch:
            result = plane.run(job)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(dispatch.call_count, 0)
        self.assertTrue(any(t['stage'] == 'node_started' for t in result['timeline']))
        self.assertFalse(any(t['stage'] == 'service_invoked' for t in result['timeline']))
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['services'][0]['job_count'], 0)
        self.assertEqual(detail['services'][0]['failed_count'], 0)
        self.assertEqual(detail['feedback_targets'], [])

    def test_dispatch_failure_has_actual_invocation_evidence_and_can_receive_feedback(self):
        demand, plan, grant = self.executable_service_order()
        plane = self.runtime.execution
        job = plane.submit(plan['id'], plan['version'], grant['id'], 'demo-requester')
        with patch.object(self.runtime, '_dispatch', side_effect=RuntimeError('handler unavailable')) as dispatch:
            result = plane.run(job)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(dispatch.call_count, 1)
        invoked = [t for t in result['timeline'] if t['stage'] == 'service_invoked']
        self.assertEqual(len(invoked), 1)
        self.assertEqual(invoked[0]['service_id'], 'SVC-CALL')
        self.assertEqual(invoked[0]['attempt'], 1)
        detail = self.get('/' + demand['id']).json()
        self.assertEqual(detail['services'][0]['job_count'], 1)
        self.assertEqual(detail['services'][0]['failed_count'], 1)
        self.assertEqual([(t['run_id'], t['service_id']) for t in detail['feedback_targets']], [(job['id'], 'SVC-CALL')])


if __name__ == '__main__':
    unittest.main()
