"""The same three-state policy drives UI capabilities and real API guards."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app
from platform_core.access import POLICY, access_snapshot, require_action, require_resource
from platform_core.storage import DomainError


def user(role):
    return {'id': 'demo-' + role, 'role': role, 'demo': True}


class AccessManifestTests(unittest.TestCase):
    def test_all_modules_have_four_explicit_roles_and_three_states(self):
        for resource, matrix in POLICY['resources'].items():
            with self.subTest(resource=resource):
                self.assertEqual(set(matrix), {'platform', 'provider', 'requester', 'auditor'})
                self.assertTrue(set(matrix.values()) <= {'write', 'read', 'none'})
        requester = access_snapshot(user('requester'))
        self.assertEqual(requester['resources']['demand'], 'write')
        self.assertEqual(requester['resources']['service'], 'read')
        self.assertEqual(requester['resources']['case'], 'none')
        self.assertFalse(requester['actions']['grant.create'])
        self.assertFalse(requester['actions']['service.update'])
        self.assertTrue(requester['actions']['plan.run'])
        for action, roles in POLICY['actions'].items():
            for role in roles:
                with self.subTest(action=action, role=role):
                    require_action(user(role), action)

    def test_unknown_identity_and_unknown_capability_fail_closed(self):
        unknown = access_snapshot({'id': 'unknown', 'role': 'unknown'})
        self.assertEqual(set(unknown['resources'].values()), {'none'})
        self.assertFalse(any(unknown['actions'].values()))
        for role in ('platform', 'provider', 'requester', 'auditor'):
            with self.subTest(role=role), self.assertRaises(DomainError):
                require_action(user(role), 'unregistered.write')
        with self.assertRaises(DomainError):
            require_resource(user('requester'), 'service', write=True)
        with self.assertRaises(DomainError):
            require_resource(user('requester'), 'case')


class AccessRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, start_runtime=False)
        self.runtime = self.app.state.runtime
        self.e, self.m = self.runtime.execution, self.runtime.management
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def get(self, path, role):
        return self.client.get(path, headers={'X-Demo-Role': role})

    def post(self, path, role, data=None):
        return self.client.post(path, headers={'X-Demo-Role': role}, json=data or {})

    def save(self, store, kind, oid, data):
        return store.save(kind, oid, data, 0, 'fixture', 'Isolated fictional permission fixture')

    def test_access_endpoint_matches_server_policy(self):
        for role in ('platform', 'provider', 'requester', 'auditor'):
            response = self.get('/api/access', role)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), access_snapshot(user(role)))
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_hidden_modules_reject_direct_api_and_history(self):
        for role, domain, kinds in [
                ('provider', 'management', ['demand', 'spec', 'case', 'pair']),
                ('requester', 'management', ['case', 'pair', 'scan', 'external_design']),
                ('provider', 'execution', ['plan', 'workflow', 'job', 'delivery', 'trigger'])]:
            for kind in kinds:
                with self.subTest(role=role, kind=kind):
                    self.assertEqual(self.get('/api/' + domain + '/items/' + kind, role).status_code, 403)
        demand = self.m.intake.save({'title': 'Private fictional request'}, 'demo-requester')
        self.assertEqual(self.get('/api/management/items/demand/' + demand['id'] + '/history', 'provider').status_code, 403)

    def test_auditor_read_only_actions_never_reach_mutations(self):
        paths = ['/api/management/demands', '/api/management/services', '/api/management/concepts',
                 '/api/management/mappings', '/api/management/sources', '/api/management/feedback',
                 '/api/management/improvements', '/api/management/metric-definitions',
                 '/api/management/reconciliation/run', '/api/management/reconciliation/experiment',
                 '/api/execution/plans', '/api/execution/views', '/api/execution/grants',
                 '/api/execution/control', '/api/execution/triggers', '/api/execution/contracts',
                 '/api/execution/facilities']
        before = (self.m.store.events(), self.e.store.events())
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.post(path, 'auditor').status_code, 403)
        self.assertEqual((self.m.store.events(), self.e.store.events()), before)

    def test_requester_reads_services_but_cannot_edit_or_issue_authorization(self):
        self.assertEqual(self.get('/api/management/items/service', 'requester').status_code, 200)
        for path in ('/api/management/services', '/api/management/catalog/unknown/annotate',
                     '/api/execution/grants', '/api/execution/grants/unknown/revoke',
                     '/api/management/demands/unknown/confirm'):
            self.assertEqual(self.post(path, 'requester').status_code, 403, path)

    def test_provider_can_edit_own_service_but_not_other_provider(self):
        created = self.post('/api/management/services', 'provider', {'name': 'Own draft', 'provider': 'someone-else'})
        self.assertEqual(created.status_code, 200, created.text)
        own = created.json()
        self.assertEqual(own['provider'], 'demo-provider')
        changed = self.post('/api/management/services', 'provider', {**own, 'expected_version': own['version'], 'name': 'Own edited draft'})
        self.assertEqual(changed.status_code, 200, changed.text)
        other = self.m.services.save({'name': 'Other draft', 'provider': 'other-provider'}, 'other-provider')
        denied = self.post('/api/management/services', 'provider', {**other, 'expected_version': other['version'], 'name': 'Unauthorized edit'})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.m.store.require('service', other['id'])['name'], 'Other draft')

    def test_requester_ownership_follows_immutable_plan_for_workflow_and_run(self):
        for owner in ('demo-requester', 'other-requester'):
            plan = self.save(self.e.store, 'plan', owner + '-plan', {'created_by': owner})
            self.save(self.e.store, 'workflow', owner + '-workflow', {'plan_id': plan['id'], 'plan_version': plan['version']})
            self.save(self.e.store, 'run', owner + '-run', {'plan_id': plan['id'], 'plan_version': plan['version'], 'object_kind': 'plan'})
            self.save(self.e.store, 'grant', owner + '-grant', {'subject': owner, 'created_by': 'demo-platform'})
            self.save(self.e.store, 'decision', owner + '-decision', {'actor': owner})
        for kind in ('workflow', 'run', 'grant', 'decision'):
            with self.subTest(kind=kind):
                response = self.get('/api/execution/items/' + kind, 'requester')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([row['id'] for row in response.json()['items']], ['demo-requester-' + kind])

    def test_provider_validation_evidence_and_feedback_stay_with_own_service(self):
        for suffix, provider in [('own', 'demo-provider'), ('other', 'other-provider')]:
            svc = self.save(self.m.store, 'service', suffix + '-service', {'provider': provider, 'lifecycle': 'published'})
            self.save(self.m.store, 'validation', suffix + '-validation', {'service_id': svc['id']})
            run = self.save(self.e.store, 'run', suffix + '-run', {'object_kind': 'service', 'object_id': svc['id'], 'actor': 'demo-platform'})
            self.save(self.m.store, 'feedback', suffix + '-feedback', {'run_id': run['id'], 'evaluated_by': 'demo-requester'})
            self.save(self.m.store, 'improvement', suffix + '-improvement', {'before': [run['id']], 'after': []})
        for domain, kind in [('management', 'validation'), ('management', 'feedback'), ('management', 'improvement'), ('execution', 'run')]:
            response = self.get('/api/' + domain + '/items/' + kind, 'provider')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([row['id'] for row in response.json()['items']], ['own-' + kind])

    def test_auditor_sees_receipt_but_only_delivery_subject_reads_result(self):
        plan = self.e.plan({'scenario': 'medical'}, 'demo-requester')
        self.e.confirm(plan['id'], plan['version'], 'demo-requester')
        grant = self.e.security.grant({'subject': 'demo-requester', 'purpose': plan['purpose'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}, 'demo-platform')
        job = self.e.run(self.e.submit(plan['id'], plan['version'], grant['id'], 'demo-requester'))
        self.assertEqual(job['state'], 'success', job)
        delivery_id = job['delivery_id']
        metadata = self.get('/api/execution/items/delivery', 'auditor').json()['items'][0]
        self.assertNotIn('result', metadata)
        self.assertEqual(metadata['circulation_mode'], 'controlled_compute')
        verified = self.post('/api/execution/receipts/verify', 'auditor', {'receipt': metadata['receipt'], 'signature': metadata['signature']})
        self.assertTrue(verified.json()['valid'])
        for role in ('auditor', 'provider', 'platform'):
            self.assertEqual(self.get('/api/execution/deliveries/' + delivery_id, role).status_code, 403, role)
        own_result = self.get('/api/execution/deliveries/' + delivery_id, 'requester')
        self.assertEqual(own_result.status_code, 200, own_result.text)
        self.assertEqual(own_result.json()['result']['rows'], [['40—49岁', '类别甲', 5]])

    def test_provider_participating_in_one_node_cannot_read_entire_mixed_plan(self):
        self.save(self.m.store, 'service', 'own-service', {'provider': 'demo-provider'})
        self.save(self.m.store, 'service', 'other-service', {'provider': 'other-provider'})
        own_node = {'operation': 'service', 'service_id': 'own-service'}
        scenarios = [
            ('own', {'own-service': 1}, [own_node]),
            ('mixed', {'own-service': 1, 'other-service': 1}, [own_node, {'operation': 'service', 'service_id': 'other-service'}]),
            ('private-view', {'own-service': 1}, [own_node, {'operation': 'view', 'view_id': 'private-view'}])]
        for key, services, nodes in scenarios:
            plan = self.save(self.e.store, 'plan', key + '-plan', {'created_by': 'demo-requester', 'service_versions': services, 'nodes': nodes})
            self.save(self.e.store, 'run', key + '-run', {'object_kind': 'plan', 'plan_id': plan['id'], 'plan_version': 1, 'actor': 'demo-requester'})
        rows = self.get('/api/execution/items/run', 'provider').json()['items']
        self.assertEqual([r['id'] for r in rows], ['own-run'])

    def test_legacy_auditor_reads_governance_but_cannot_change_or_read_patients(self):
        for path in ('/api/security/overview', '/api/security/audit', '/api/security/risks', '/api/orchestrator', '/api/events'):
            self.assertEqual(self.get(path, 'auditor').status_code, 200, path)
            self.assertEqual(self.get(path, 'requester').status_code, 403, path)
        for path in ('/api/security/risks/unknown/resolve', '/api/rules/unknown/toggle', '/api/orchestrator/patrol'):
            self.assertEqual(self.post(path, 'auditor').status_code, 403, path)
        self.assertEqual(self.get('/api/patients', 'auditor').status_code, 403)
        self.assertEqual(self.post('/api/query/patient_360', 'auditor', {'patient_id': 'example'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
