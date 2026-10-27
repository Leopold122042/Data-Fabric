from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import medsim
from platform_core.bootstrap import create_app
from platform_core.demo_scenarios import seed_demo, SEED_ID, SOURCE_FILE, SOURCE_ID, REQUESTER_RETEST_ID, MANAGEMENT_KINDS, EXECUTION_KINDS
from platform_core.storage import DomainError


class DemoScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.data_patch = patch.object(medsim, 'DATA_DIR', str(self.data))
        self.data_patch.start()
        self.app = create_app(self.root / 'control', start_runtime=False)
        self.runtime = self.app.state.runtime
        self.e, self.m = self.runtime.execution, self.runtime.management

    def tearDown(self):
        self.e.close()
        self.runtime.external_governance.close()
        self.runtime.shutdown()
        self.data_patch.stop()
        self.temp.cleanup()

    def seed(self, with_engine=False):
        if with_engine:
            # Feed known in-memory metadata through the actual legacy handler,
            # schema validator, lifecycle, plan and delivery pipeline.
            self.runtime.engine = SimpleNamespace(stats={s['id']: {'tables': i + 1, 'rows': 0}
                for i, s in enumerate(medsim.SOURCE_DEFS)})
        return seed_demo(self.runtime)

    def test_every_requested_record_kind_has_linked_fictional_examples(self):
        manifest = self.seed()
        for domain, counts in manifest['counts'].items():
            for kind, count in counts.items():
                with self.subTest(domain=domain, kind=kind):
                    self.assertGreater(count, 0)
        self.assertTrue(manifest['demo'])
        self.assertEqual(manifest['role_labels']['demo-requester'], '研究组B（模拟）')
        for scenario in manifest['scenarios']:
            if scenario['status'] != 'success':
                continue
            plan = self.e.get(scenario['plan_id'], 1)
            spec = self.m.store.require('spec', plan['spec_id'])
            self.assertEqual(spec['demand_id'], scenario['demand_id'])
            delivered = self.e.delivery.read(scenario['delivery_id'], scenario['subject'], plan)
            self.assertTrue(self.e.delivery.verify(delivered['receipt'], delivered['signature'])['valid'])
            self.assertEqual(sorted(delivered['result']['rows']), [['东区（模拟）', 6], ['西区（模拟）', 6]])
        self.assertTrue(all(not t['enabled'] for t in self.e.store.list('trigger')))

    def test_actual_metadata_validation_publication_and_delivery(self):
        manifest = self.seed(with_engine=True)
        self.assertTrue(manifest['published_service_ready'])
        scenario = next(s for s in manifest['scenarios'] if s['id'] == 'CATALOG')
        service = self.m.store.require('service', scenario['service_id'])
        validation = self.m.store.require('validation', scenario['validation_id'])
        self.assertEqual(service['lifecycle'], 'published')
        self.assertTrue(validation['passed'])
        self.assertEqual([v['lifecycle'] for v in reversed(self.m.store.history('service', service['id']))],
                         ['draft', 'review', 'trial', 'published'])
        plan = self.e.get(scenario['plan_id'], 1)
        delivery = self.e.delivery.read(scenario['delivery_id'], 'demo-platform', plan)
        self.assertEqual(delivery['result']['columns'], ['source', 'source_name', 'table_count'])
        self.assertEqual(len(delivery['result']['rows']), len(medsim.SOURCE_DEFS))
        self.assertEqual([r[2] for r in delivery['result']['rows']], list(range(1, len(medsim.SOURCE_DEFS) + 1)))
        bad = self.m.store.require('service', 'DEMO-SERVICE-PENDING')
        self.assertEqual(bad['lifecycle'], 'draft')
        bad_validation = next(v for v in self.m.store.list('validation') if v['service_id'] == bad['id'])
        self.assertFalse(bad_validation['passed'])
        self.assertFalse(bad_validation['checks']['consistency']['passed'])
        comparison = self.m.store.require('improvement', scenario['improvement_id'])
        self.assertEqual(comparison['baseline']['success_rate'], 1)
        self.assertEqual(comparison['retest']['success_rate'], 1)
        self.assertFalse(comparison['success_rate_improved'])
        from platform_core.access import can_read_execution, can_read_management
        provider = {'id': 'demo-provider', 'role': 'provider'}
        for kind in ('service', 'validation', 'notice', 'feedback', 'improvement', 'metric_definition'):
            with self.subTest(provider_management=kind):
                self.assertTrue(any(can_read_management(self.runtime, kind, r, provider) for r in self.m.store.list(kind)))
        for kind in ('view', 'grant', 'decision', 'run', 'contract', 'facility'):
            with self.subTest(provider_execution=kind):
                self.assertTrue(any(can_read_execution(self.runtime, kind, r, provider) for r in self.e.store.list(kind)))
        provider_grant = self.e.store.require('grant', scenario['provider_revoked_grant_id'])
        self.assertEqual(provider_grant['subject'], provider['id'])
        self.assertEqual(provider_grant['status'], 'revoked')
        self.assertFalse(any(j['grant_id'] == provider_grant['id'] for j in self.e.store.list('job')))
        requester = {'id': 'demo-requester', 'role': 'requester'}
        notice = self.m.store.require('notice', 'DEMO-NOTICE-SERVICE')
        self.assertTrue(can_read_management(self.runtime, 'notice', notice, requester))
        self.assertEqual(notice['affected_demands'], ['DEMO-DEMAND-DRAFT'])
        draft_demand = self.m.store.require('demand', notice['affected_demands'][0])
        self.assertIn(notice['service_id'], draft_demand['service_ids'])
        from platform_core.access import resource_level
        for role in ('platform', 'provider', 'requester', 'auditor'):
            user = {'id': 'demo-' + role, 'role': role}
            for store, kinds, visible in ((self.m.store, MANAGEMENT_KINDS, can_read_management),
                                           (self.e.store, EXECUTION_KINDS, can_read_execution)):
                for kind in kinds:
                    if resource_level(user, kind) != 'none':
                        with self.subTest(role=role, visible_subpage=kind):
                            self.assertTrue(any(visible(self.runtime, kind, row, user) for row in store.list(kind)))

    def test_requester_retest_migration_appends_without_editing_v1_and_runs_once(self):
        with patch('platform_core.demo_scenarios._migrate_requester_retest'):
            manifest = self.seed()
        original = [(store, kind, row) for store, kinds in ((self.m.store, MANAGEMENT_KINDS), (self.e.store, EXECUTION_KINDS))
                    for kind in kinds for row in store.list(kind)]
        result = seed_demo(self.runtime)
        self.assertEqual(result, manifest)
        marker = self.m.store.require('demo_seed_migration', REQUESTER_RETEST_ID)
        self.assertEqual(marker['status'], 'completed')
        for store, kind, record in original:
            with self.subTest(preserved_kind=kind, record=record['id']):
                self.assertEqual(store.require(kind, record['id']), record)
        job = self.e.store.require('job', marker['job_id'])
        self.assertEqual(job['state'], 'success')
        self.assertEqual(job['actor'], 'demo-requester')
        comparison = self.m.store.require('improvement', marker['improvement_id'])
        self.assertEqual(comparison['baseline']['success_rate'], 1)
        self.assertEqual(comparison['retest']['success_rate'], 1)
        self.assertFalse(comparison['success_rate_improved'])
        from platform_core.access import can_read_management
        self.assertTrue(can_read_management(self.runtime, 'improvement', comparison,
                                           {'id': 'demo-requester', 'role': 'requester'}))
        counts = (len(self.e.store.list('job')), len(self.e.store.list('delivery')), len(self.m.store.list('improvement')))
        self.assertEqual(seed_demo(self.runtime), manifest)
        self.assertEqual(counts, (len(self.e.store.list('job')), len(self.e.store.list('delivery')), len(self.m.store.list('improvement'))))
        self.assertEqual(self.m.store.require('demo_seed_migration', REQUESTER_RETEST_ID), marker)

    def test_requester_migration_keeps_revoked_user_state_and_skips_once(self):
        with patch('platform_core.demo_scenarios._migrate_requester_retest'):
            manifest = self.seed()
        scenario = next(s for s in manifest['scenarios'] if s['id'] == 'RESEARCH-B')
        revoked = self.e.security.revoke(scenario['grant_id'], 'demo-platform')
        original_jobs = self.e.store.list('job')
        self.assertEqual(seed_demo(self.runtime), manifest)
        marker = self.m.store.require('demo_seed_migration', REQUESTER_RETEST_ID)
        self.assertEqual(marker['status'], 'skipped')
        self.assertIn('修改', marker['reason'])
        self.assertEqual(self.e.store.require('grant', revoked['id']), revoked)
        self.assertEqual(self.e.store.list('job'), original_jobs)
        self.assertEqual(seed_demo(self.runtime), manifest)
        self.assertEqual(self.m.store.require('demo_seed_migration', REQUESTER_RETEST_ID), marker)

    def test_requester_migration_failure_rolls_back_only_additions_and_does_not_block_startup(self):
        with patch('platform_core.demo_scenarios._migrate_requester_retest'):
            manifest = self.seed()
        original = [(store, kind, store.list(kind))
                    for store, kinds in ((self.m.store, MANAGEMENT_KINDS), (self.e.store, EXECUTION_KINDS)) for kind in kinds]
        with patch.object(self.m.feedback, 'compare', side_effect=DomainError('optional comparison failed')):
            self.assertEqual(seed_demo(self.runtime), manifest)
        marker = self.m.store.require('demo_seed_migration', REQUESTER_RETEST_ID)
        self.assertEqual(marker['status'], 'skipped')
        self.assertEqual(marker['reason'], 'optional comparison failed')
        for store, kind, records in original:
            with self.subTest(rolled_back_kind=kind):
                self.assertEqual(store.list(kind), records)

    def test_denied_requests_never_create_a_job_or_delivery(self):
        manifest = self.seed()
        scenario = next(s for s in manifest['scenarios'] if s['id'] == 'DENIED')
        denied = [d for d in self.e.store.list('decision') if not d['allowed']]
        self.assertTrue(any(d['grant_id'] == scenario['expired_grant_id'] and '到期' in d['reason'] for d in denied))
        self.assertTrue(any(d['actor'] == 'demo-provider' and '主体' in d['reason'] for d in denied))
        self.assertFalse(any(j['grant_id'] == scenario['expired_grant_id'] for j in self.e.store.list('job')))
        self.assertFalse(any(d['grant_id'] == scenario['expired_grant_id'] for d in self.e.store.list('delivery')))

    def test_quality_failure_pause_repair_and_retest_use_actual_records(self):
        manifest = self.seed()
        scenario = next(s for s in manifest['scenarios'] if s['id'] == 'RECOVERY')
        failed = self.e.store.require('job', scenario['failed_job_id'])
        retest = self.e.store.require('job', scenario['retest_job_id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertIsNone(failed['delivery_id'])
        self.assertIn('quality', failed['error'])
        self.assertEqual(retest['state'], 'success')
        self.assertEqual(retest['retry_of'], failed['id'])
        self.assertEqual(retest['plan_version'], failed['plan_version'])
        case = self.m.store.require('case', scenario['case_id'])
        self.assertIn(case['status'], ('closed', 'observing'))
        self.assertTrue(any(c.get('case_id') == case['id'] and c['status'] == 'applied'
                            for c in self.e.store.list('command')))
        comparison = self.m.store.require('improvement', scenario['improvement_id'])
        self.assertEqual(comparison['baseline']['success_rate'], 0)
        self.assertEqual(comparison['retest']['success_rate'], 1)
        with closing(sqlite3.connect(self.data / SOURCE_FILE)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_observations WHERE region IS NULL').fetchone()[0], 0)

    def test_repeated_seed_preserves_user_edits_and_all_existing_versions(self):
        unrelated = self.m.intake.save({'id': 'USER-demand', 'title': '用户自己的需求'}, 'demo-requester')
        manifest = self.seed()
        service = self.m.store.require('service', 'DEMO-SERVICE-DRAFT')
        edited = self.m.services.save({**service, 'name': '用户修改后的演示草稿', 'expected_version': service['version']}, 'demo-provider')
        events = (self.m.store.events(0, 500)[-1]['seq'], self.e.store.events(0, 500)[-1]['seq'])
        before = (self.data / SOURCE_FILE).read_bytes()
        repeated = seed_demo(self.runtime)
        self.assertEqual(manifest, repeated)
        self.assertEqual(self.m.store.require('demand', unrelated['id']), unrelated)
        self.assertEqual(self.m.store.require('service', edited['id']), edited)
        self.assertEqual(before, (self.data / SOURCE_FILE).read_bytes())
        self.assertEqual(events, (self.m.store.events(0, 500)[-1]['seq'], self.e.store.events(0, 500)[-1]['seq']))

    def test_non_demo_and_unrelated_source_file_are_not_modified(self):
        self.e.store.environment = 'production'
        with self.assertRaises(DomainError):
            seed_demo(self.runtime)
        self.assertFalse((self.data / SOURCE_FILE).exists())
        self.e.store.environment = 'demo'
        path = self.data / SOURCE_FILE
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE user_records (value TEXT)')
            con.execute("INSERT INTO user_records VALUES ('keep me')")
        original = path.read_bytes()
        with self.assertRaisesRegex(DomainError, '未覆盖'):
            seed_demo(self.runtime)
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(self.m.store.get('source', SOURCE_ID))
        self.assertIsNone(self.m.store.get('demo_seed', SEED_ID))

    def test_failed_generation_rolls_back_control_records_and_can_retry(self):
        with patch.object(self.m.feedback, 'compare', side_effect=DomainError('injected failure')):
            with self.assertRaisesRegex(DomainError, 'injected failure'):
                self.seed()
        self.assertIsNone(self.m.store.get('demo_seed', SEED_ID))
        self.assertIsNone(self.m.store.get('source', SOURCE_ID))
        self.assertEqual(self.e.store.list('plan'), [])
        self.assertEqual(self.e.store.list('delivery'), [])
        self.assertEqual(self.m.store.list('demand'), [])
        manifest = self.seed()
        self.assertTrue(manifest['demo'])


if __name__ == '__main__':
    unittest.main()
