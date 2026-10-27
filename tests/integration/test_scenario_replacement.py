from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import medsim
from platform_core.storage import Store, DomainError
from platform_core.scenario_replacement import (
    LEGACY_ID, LEGACY_RETEST_ID, REPLACEMENT_ID, MIGRATION_ID,
    replace_legacy_scenarios, _delete_records,
)


class ScenarioReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.m = Store(self.root / 'management' / 'control.db')
        self.e = Store(self.root / 'execution' / 'control.db')
        self.runtime = SimpleNamespace(management=SimpleNamespace(store=self.m),
                                       execution=SimpleNamespace(store=self.e, lock=threading.RLock()))

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self):
        self.m.save('source', 'source-v1', {'path': 'old.sqlite', 'name': '旧来源'})
        self.m.save('object', 'table-v1', {'kind': 'table', 'source_id': 'source-v1', 'name': 'observations'})
        self.m.save('object', 'field-v1', {'kind': 'field', 'source_id': 'source-v1', 'parent_id': 'table-v1'})
        self.m.save('scan', 'source-v1', {'status': 'success'})
        self.m.save('demand', 'demand-v1', {'title': '旧需求'})
        self.m.save('spec', 'spec-v1', {'demand_id': 'demand-v1'})
        self.e.save('view', 'view-v1', {'field_versions': {'field-v1': 1}})
        self.e.save('plan', 'plan-v1', {'spec_id': 'spec-v1', 'view_versions': {'view-v1': 1}})
        self.e.save('plan_state', 'plan-v1', {'status': 'confirmed'})
        self.e.save('workflow', 'flow-v1', {'plan_id': 'plan-v1'})
        self.e.save('grant', 'grant-v1', {'plan_id': 'plan-v1'})
        self.e.save('job', 'job-v1', {'plan_id': 'plan-v1', 'grant_id': 'grant-v1'})
        self.e.save('run', 'job-v1', {'plan_id': 'plan-v1'})
        self.e.save('delivery', 'delivery-v1', {'plan_id': 'plan-v1', 'grant_id': 'grant-v1'})
        records = {}
        for domain, store in [('management', self.m), ('execution', self.e)]:
            with store.connection() as con:
                kinds = [r[0] for r in con.execute('SELECT DISTINCT kind FROM versions')]
            records[domain] = {kind: [r['id'] for r in store.list(kind)] for kind in kinds}
        self.m.save('demo_seed', LEGACY_ID, {'records': records, 'demo': True})
        # These real system side-effects were not listed in the V1 manifest.
        self.m.save('external_design', 'design-v1', {'object_id': 'plan-v1', 'object_kind': 'plan',
                    'definition': {'id': 'plan-v1', 'spec_id': 'spec-v1'}}, actor='execution', reason='接收外部设计快照')
        self.e.save('decision', 'decision-read', {'plan_id': 'plan-v1', 'grant_id': 'grant-v1'},
                    actor='demo-requester', reason='执行点授权核验')
        self.m.save('pair', 'pair-v1', {'run_id': 'job-v1'}, reason='设计运行配对')
        self.m.save('demo_seed', REPLACEMENT_ID, {'ready': True})
        return records

    def raw(self):
        result = {}
        for domain, store in [('management', self.m), ('execution', self.e)]:
            with store.connection() as con:
                result[domain] = {table: [dict(r) for r in con.execute('SELECT * FROM ' + table + ' ORDER BY 1,2,3')]
                                  for table in ('versions', 'events')}
        return result

    def test_exact_retirement_archives_versions_and_events_and_is_idempotent(self):
        self.fixture()
        user = self.m.save('demand', 'DEMO-USER-KEPT', {'title': '用户需求'})
        source = self.root / 'old.sqlite'
        source.write_bytes(b'preserve source contents')
        before = self.raw()
        result = replace_legacy_scenarios(self.runtime)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['protected'], [])
        self.assertEqual(result['removed']['management']['external_design'], 1)
        self.assertIsNone(self.e.get('decision', 'decision-read'))
        self.assertEqual(self.m.history('demand', 'demand-v1'), [])
        self.assertEqual(self.m.require('demand', user['id']), user)
        self.assertIsNotNone(self.m.get('demo_seed', LEGACY_ID))
        self.assertEqual(source.read_bytes(), b'preserve source contents')
        archive = json.loads(Path(result['archive_path']).read_text(encoding='utf-8'))
        for domain in ('management', 'execution'):
            removed_ids = {(r['kind'], r['id']) for r in archive['records'][domain]}
            self.assertTrue(removed_ids)
            self.assertEqual(archive['records'][domain], sorted(
                [row for row in before[domain]['versions'] if (row['kind'], row['id']) in removed_ids],
                key=lambda row: (row['kind'], row['id'], row['version'])))
            self.assertTrue(all(row in before[domain]['events'] for row in archive['events'][domain]))
        after = self.raw()
        self.assertEqual(replace_legacy_scenarios(self.runtime), result)
        self.assertEqual(self.raw(), after)
        self.assertEqual(len(list((self.root / 'scenario_archives').glob('*.json'))), 1)

    def test_user_edits_and_historical_references_keep_dependency_chain(self):
        self.fixture()
        edited = self.m.save('demand', 'demand-v1', {'title': '用户修改'}, 1, 'demo-requester')
        self.m.save('service', 'user-service', {'source_objects': ['field-v1']}, actor='demo-provider')
        self.m.save('service', 'user-service', {'source_objects': []}, 1, 'demo-provider')
        result = replace_legacy_scenarios(self.runtime)
        kept = {(r['domain'], r['kind'], r['id']) for r in result['protected']}
        self.assertIn(('management', 'demand', edited['id']), kept)
        for key in [('management', 'source', 'source-v1'), ('management', 'object', 'field-v1'),
                    ('management', 'object', 'table-v1'), ('management', 'scan', 'source-v1')]:
            self.assertIn(key, kept)
        self.assertEqual(self.m.require('demand', edited['id']), edited)
        self.assertEqual(len(self.m.history('service', 'user-service')), 2)
        self.assertIsNone(self.e.get('plan', 'plan-v1'))

    def test_user_created_job_keeps_its_plan_grant_and_source_dependencies(self):
        self.fixture()
        self.e.save('job', 'user-job', {'plan_id': 'plan-v1', 'grant_id': 'grant-v1'})
        result = replace_legacy_scenarios(self.runtime)
        self.assertTrue(result['protected'])
        self.assertIsNotNone(self.e.get('plan', 'plan-v1'))
        self.assertIsNotNone(self.e.get('plan_state', 'plan-v1'))
        self.assertIsNotNone(self.e.get('grant', 'grant-v1'))
        self.assertIsNotNone(self.m.get('spec', 'spec-v1'))
        self.assertIsNotNone(self.m.get('object', 'field-v1'))
        self.assertIsNotNone(self.e.get('job', 'user-job'))

    def test_failure_after_both_database_deletes_rolls_back_and_can_retry(self):
        self.fixture()
        before = self.raw()
        def fail(con, removed, archive, environment):
            _delete_records(con, removed, archive, environment)
            raise RuntimeError('injected delete failure')
        with patch('platform_core.scenario_replacement._delete_records', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                replace_legacy_scenarios(self.runtime)
        self.assertEqual(self.raw(), before)
        self.assertIsNone(self.m.get('scenario_replacement', MIGRATION_ID))
        self.assertTrue(list((self.root / 'scenario_archives').glob('*.json')))
        self.assertEqual(replace_legacy_scenarios(self.runtime)['status'], 'completed')

    def test_requires_completed_replacement_and_rejects_real_environment(self):
        self.m.save('demo_seed', LEGACY_ID, {'records': {}})
        for ready in (None, False):
            if ready is False:
                self.m.save('demo_seed', REPLACEMENT_ID, {'ready': False})
            with self.assertRaisesRegex(DomainError, '新业务场景尚未完成'):
                replace_legacy_scenarios(self.runtime)
        self.assertFalse((self.root / 'scenario_archives').exists())
        self.e.environment = 'production'
        with self.assertRaisesRegex(DomainError, '本机数据环境'):
            replace_legacy_scenarios(self.runtime)

    def test_actual_v1_seed_and_append_are_fully_retired_without_touching_builtin_data(self):
        from platform_core.bootstrap import create_app
        from platform_core.demo_scenarios import seed_demo
        data = self.root / 'source-data'
        data.mkdir()
        with patch.object(medsim, 'DATA_DIR', str(data)):
            app = create_app(self.root / 'actual', start_runtime=False)
            runtime = app.state.runtime
            try:
                runtime.engine = SimpleNamespace(stats={s['id']: {'tables': i + 1, 'rows': 0}
                    for i, s in enumerate(medsim.SOURCE_DEFS)})
                originals = runtime.management.store.list('service')
                seed_demo(runtime)
                runtime.management.store.save('demo_seed', REPLACEMENT_ID, {'ready': True})
                result = replace_legacy_scenarios(runtime)
                self.assertEqual(result['protected'], [])
                self.assertGreater(result['removed']['execution']['decision'], 70)
                self.assertEqual(runtime.execution.store.list('job'), [])
                self.assertEqual(runtime.execution.store.list('delivery'), [])
                self.assertEqual(runtime.management.store.list('external_design'), [])
                self.assertEqual(runtime.management.store.list('service'), originals)
                self.assertEqual(runtime.management.store.list('source'), [])
                self.assertEqual(runtime.management.store.list('object'), [])
                self.assertTrue((data / 'demo_scenarios_v1' / 'fictional_records.sqlite').exists())
            finally:
                runtime.execution.close()
                runtime.external_governance.close()
                runtime.shutdown()

    def test_real_v1_and_v2_replacement_preserves_every_new_record_and_user_data(self):
        from platform_core.bootstrap import create_app
        from platform_core.demo_scenarios import seed_demo
        from platform_core.healthcare_scenarios import seed_healthcare
        data = self.root / 'combined-source-data'
        data.mkdir()
        with patch.object(medsim, 'DATA_DIR', str(data)):
            runtime = create_app(self.root / 'combined', start_runtime=False).state.runtime
            try:
                runtime.engine = SimpleNamespace(stats={s['id']: {'tables': i + 1, 'rows': 0}
                    for i, s in enumerate(medsim.SOURCE_DEFS)})
                m, e = runtime.management, runtime.execution
                m.catalog.ingest('his', {'domain': '医院信息', 'tables': {'visit': {
                    'columns': ['dept'], 'column_details': [{'name': 'dept', 'type': 'TEXT', 'nullable': False}], 'rows': 0}}})
                original_services = m.store.list('service')
                original_objects = m.store.list('object')
                user = m.store.save('demand', 'USER-CLINICAL-REQ', {'title': '科室自建统计需求'}, actor='demo-requester')
                legacy = seed_demo(runtime)
                legacy_append = m.store.require('demo_seed_migration', LEGACY_RETEST_ID)
                replacement = seed_healthcare(runtime)
                self.assertTrue(replacement['ready'])
                new_records = []
                for domain, kinds in replacement['records'].items():
                    store = m.store if domain == 'management' else e.store
                    for kind, ids in kinds.items():
                        for oid in ids:
                            new_records.append((store, kind, oid, store.require(kind, oid), store.history(kind, oid)))
                self.assertTrue(new_records)

                result = replace_legacy_scenarios(runtime)

                self.assertEqual(result['protected'], [])
                self.assertEqual(m.store.require('demo_seed', REPLACEMENT_ID), replacement)
                for store, kind, oid, record, history in new_records:
                    with self.subTest(new_kind=kind, new_id=oid):
                        self.assertEqual(store.require(kind, oid), record)
                        self.assertEqual(store.history(kind, oid), history)
                for domain, kinds in legacy['records'].items():
                    store = m.store if domain == 'management' else e.store
                    for kind, ids in kinds.items():
                        for oid in ids:
                            with self.subTest(retired_kind=kind, retired_id=oid):
                                self.assertIsNone(store.get(kind, oid))
                                self.assertEqual(store.history(kind, oid), [])
                self.assertIsNone(e.store.get('job', legacy_append['job_id']))
                self.assertIsNone(e.store.get('delivery', legacy_append['delivery_id']))
                self.assertIsNone(m.store.get('improvement', legacy_append['improvement_id']))
                for record in original_services:
                    self.assertEqual(m.store.require('service', record['id']), record)
                for record in original_objects:
                    self.assertEqual(m.store.require('object', record['id']), record)
                self.assertEqual(m.store.require('demand', user['id']), user)
                self.assertTrue(Path(result['archive_path']).is_file())
            finally:
                runtime.execution.close()
                runtime.external_governance.close()
                runtime.shutdown()


if __name__ == '__main__':
    unittest.main()
