from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import medsim
from platform_core.bootstrap import create_app
from platform_core.healthcare_scenarios import (
    seed_healthcare, SEED_ID, SOURCE_ID, SOURCE_FILE, TABLES, MANAGEMENT_KINDS, EXECUTION_KINDS)
from platform_core.storage import DomainError


class HealthcareScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.data_patch = patch.object(medsim, 'DATA_DIR', str(self.data))
        self.data_patch.start()
        self.runtime = create_app(self.root / 'control', start_runtime=False).state.runtime
        self.runtime.engine = SimpleNamespace(stats={s['id']: {'tables': i + 1, 'rows': 0}
            for i, s in enumerate(medsim.SOURCE_DEFS)})
        self.e, self.m = self.runtime.execution, self.runtime.management
        self.m.catalog.ingest('his', {'domain': '医院信息', 'tables': {'visit': {
            'columns': ['dept'], 'column_details': [{'name': 'dept', 'type': 'TEXT', 'nullable': False}], 'rows': 0}}})

    def tearDown(self):
        self.e.close()
        self.runtime.external_governance.close()
        self.runtime.shutdown()
        self.data_patch.stop()
        self.temp.cleanup()

    def seed(self):
        return seed_healthcare(self.runtime)

    def test_healthcare_results_match_actual_business_filters_and_period(self):
        manifest = self.seed()
        self.assertTrue(manifest['ready'])
        self.assertEqual(set(manifest['source_rows']), set(TABLES))
        self.assertGreater(sum(manifest['source_rows'].values()), 2000)
        cases = {s['id']: s for s in manifest['scenarios']}
        expected = {
            'FOLLOWUP': ('chronic_followup', ['院区', '随访状态'], '随访日期', [('服务项目', '高血压随访')]),
            'COHORT': ('research_cohort', ['院区', '年龄段'], '入组日期', [('课题代码', 'RSP-2026-MET-09'), ('入组状态', '符合纳入')]),
            'IMAGING': ('imaging_turnaround', ['院区', '检查类型', '签发时段'], '检查日期', []),
        }
        with closing(sqlite3.connect(self.data / SOURCE_FILE)) as con:
            for key, (table, groups, date, filters) in expected.items():
                case = cases[key]
                plan = self.e.get(case['plan_id'], 1)
                spec = self.m.store.require('spec', case['spec_id'])
                self.assertEqual(spec['demand_id'], case['demand_id'])
                delivered = self.e.delivery.read(case['delivery_id'], case['subject'], plan)
                self.assertTrue(self.e.delivery.verify(delivered['receipt'], delivered['signature'])['valid'])
                cols = ','.join('"' + col + '"' for col in groups)
                where = ['"' + date + '" >= ?', '"' + date + '" <= ?'] + ['"' + col + '" = ?' for col, _ in filters]
                params = ['2026-09-01', '2026-09-30'] + [value for _, value in filters]
                rows = con.execute('SELECT ' + cols + ',COUNT(*) FROM ' + table + ' WHERE ' + ' AND '.join(where) +
                                   ' GROUP BY ' + cols + ' HAVING COUNT(*) >= 5', params).fetchall()
                self.assertEqual(delivered['result']['columns'], groups + ['count'])
                self.assertEqual(sorted(delivered['result']['rows']), sorted([list(row) for row in rows]))
                self.assertGreater(len({row[-1] for row in rows}), 1)
                self.assertTrue(all(row[-1] >= 5 for row in rows))
                if filters:
                    self.assertLess(sum(row[-1] for row in rows), manifest['source_rows'][table])
                grant = self.e.store.require('grant', case['grant_id'])
                expires_days = (datetime.fromisoformat(grant['expires_at']) - datetime.now(timezone.utc)).days
                self.assertIn(expires_days, (29, 30))

    def test_all_role_visible_subpages_have_related_records_without_business_labels(self):
        manifest = self.seed()
        from platform_core.access import resource_level, can_read_management, can_read_execution
        for role in ('platform', 'provider', 'requester', 'auditor'):
            user = {'id': 'demo-' + role, 'role': role}
            for store, kinds, visible in ((self.m.store, MANAGEMENT_KINDS, can_read_management),
                                         (self.e.store, EXECUTION_KINDS, can_read_execution)):
                for kind in kinds:
                    if resource_level(user, kind) != 'none':
                        with self.subTest(role=role, visible_subpage=kind):
                            self.assertTrue(any(visible(self.runtime, kind, row, user) for row in store.list(kind)))
        for domain, kinds in manifest['records'].items():
            store = self.m.store if domain == 'management' else self.e.store
            for kind, ids in kinds.items():
                if kind in ('node_result', 'materialization'):
                    continue
                for oid in ids:
                    value = json.dumps(store.require(kind, oid), ensure_ascii=False)
                    with self.subTest(kind=kind, record=oid):
                        self.assertNotIn('DEMO-', oid)
                        for word in ('模拟', '虚构', '演练', '样例', '演示'):
                            self.assertNotIn(word, value)
        self.assertTrue(all(not t['enabled'] for t in self.e.store.list('trigger')))

    def test_quality_repair_and_access_rejection_have_actual_execution_evidence(self):
        manifest = self.seed()
        case = next(s for s in manifest['scenarios'] if s['id'] == 'LAB-QUALITY')
        failed = self.e.store.require('job', case['failed_job_id'])
        retest = self.e.store.require('job', case['retest_job_id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertIsNone(failed['delivery_id'])
        self.assertIn('quality', failed['error'])
        self.assertEqual(retest['state'], 'success')
        self.assertEqual(retest['retry_of'], failed['id'])
        self.assertEqual(retest['plan_version'], failed['plan_version'])
        comparison = self.m.store.require('improvement', case['improvement_id'])
        self.assertEqual(comparison['baseline']['success_rate'], 0)
        self.assertEqual(comparison['retest']['success_rate'], 1)
        self.assertIn(self.m.store.require('case', case['case_id'])['status'], ('closed', 'observing'))
        self.assertTrue(any(c.get('case_id') == case['case_id'] and c['status'] == 'applied' for c in self.e.store.list('command')))
        with closing(sqlite3.connect(self.data / SOURCE_FILE)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM laboratory_reports WHERE "报告状态" IS NULL').fetchone()[0], 0)
        denied = next(s for s in manifest['scenarios'] if s['id'] == 'ACCESS-REVIEW')
        self.assertTrue(any(not d['allowed'] and d['grant_id'] == denied['expired_grant_id'] and '到期' in d['reason'] for d in self.e.store.list('decision')))
        self.assertFalse(any(j['grant_id'] == denied['expired_grant_id'] for j in self.e.store.list('job')))
        self.assertFalse(any(d['grant_id'] == denied['expired_grant_id'] for d in self.e.store.list('delivery')))

    def test_catalog_release_and_both_parties_retests_are_real(self):
        manifest = self.seed()
        self.assertTrue(manifest['published_service_ready'])
        catalog = next(s for s in manifest['scenarios'] if s['id'] == 'CATALOG')
        self.assertEqual(self.m.store.require('service', catalog['service_id'])['lifecycle'], 'published')
        service = self.m.store.require('service', catalog['service_id'])
        self.assertTrue(service['source_objects'])
        self.assertTrue(all(self.m.store.require('object', oid)['source_id'] == 'his' for oid in service['source_objects']))
        self.assertTrue(self.m.store.require('validation', catalog['validation_id'])['passed'])
        bad = next(v for v in self.m.store.list('validation') if v['service_id'] == 'SVC-CATALOG-ARRAY')
        self.assertFalse(bad['passed'])
        self.assertFalse(bad['checks']['consistency']['passed'])
        self.assertEqual(self.m.store.require('service', 'SVC-CATALOG-ARRAY')['lifecycle'], 'draft')
        for key in ('CATALOG', 'COHORT'):
            scenario = next(s for s in manifest['scenarios'] if s['id'] == key)
            comparison = self.m.store.require('improvement', scenario['improvement_id'])
            self.assertEqual(comparison['baseline']['success_rate'], 1)
            self.assertEqual(comparison['retest']['success_rate'], 1)
            self.assertFalse(comparison['success_rate_improved'])
        provider_grant = self.e.store.require('grant', catalog['provider_revoked_grant_id'])
        self.assertEqual(provider_grant['status'], 'revoked')
        self.assertTrue(any(not d['allowed'] and d['grant_id'] == provider_grant['id'] for d in self.e.store.list('decision')))
        notice = self.m.store.require('notice', 'NOTICE-FOLLOWUP-COVERAGE')
        demand = self.m.store.require('demand', notice['affected_demands'][0])
        self.assertIn(notice['service_id'], demand['service_ids'])
        self.assertEqual(demand['created_by'], 'demo-requester')
        catalog_contract = self.e.store.require('contract', catalog['contract_id'])
        self.assertNotIn('demo-requester', catalog_contract['parties'])
        self.assertIn('目录结构摘要', catalog_contract['obligations'])
        self.assertNotIn('聚合统计', catalog_contract['obligations'])
        cohort = next(s for s in manifest['scenarios'] if s['id'] == 'COHORT')
        self.assertIn('demo-requester', self.e.store.require('contract', cohort['contract_id'])['parties'])

    def test_idempotency_preserves_business_changes_and_unrelated_records(self):
        user_record = self.m.intake.save({'id': 'USER-REQ', 'title': '用户已有需求'}, 'demo-requester')
        manifest = self.seed()
        draft = self.m.store.require('service', 'SVC-FOLLOWUP-COVERAGE')
        changed = self.m.services.save({**draft, 'name': '已修订的出院随访需求', 'expected_version': draft['version']}, 'demo-provider')
        before = [(store, kind, store.list(kind)) for store, kinds in
                  ((self.m.store, MANAGEMENT_KINDS), (self.e.store, EXECUTION_KINDS)) for kind in kinds]
        source_before = (self.data / SOURCE_FILE).read_bytes()
        self.assertEqual(seed_healthcare(self.runtime), manifest)
        self.assertEqual(self.m.store.require('demand', user_record['id']), user_record)
        self.assertEqual(self.m.store.require('service', changed['id']), changed)
        self.assertEqual((self.data / SOURCE_FILE).read_bytes(), source_before)
        for store, kind, records in before:
            self.assertEqual(store.list(kind), records)

    def test_failure_rolls_back_and_owned_source_is_restored_for_retry(self):
        with patch.object(self.m.feedback, 'compare', side_effect=DomainError('comparison unavailable')):
            with self.assertRaisesRegex(DomainError, 'comparison unavailable'):
                self.seed()
        self.assertIsNone(self.m.store.get('demo_seed', SEED_ID))
        self.assertIsNone(self.m.store.get('source', SOURCE_ID))
        self.assertEqual(self.e.store.list('plan'), [])
        self.assertEqual(self.e.store.list('delivery'), [])
        self.assertTrue(self.seed()['ready'])

    def test_non_demo_and_unowned_source_are_refused(self):
        self.e.store.environment = 'production'
        with self.assertRaises(DomainError):
            self.seed()
        self.assertFalse((self.data / SOURCE_FILE).exists())
        self.e.store.environment = 'demo'
        path = self.data / SOURCE_FILE
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE important_records (value TEXT)')
            con.execute("INSERT INTO important_records VALUES ('preserve')")
        original = path.read_bytes()
        with self.assertRaisesRegex(DomainError, '未覆盖'):
            self.seed()
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(self.m.store.get('source', SOURCE_ID))


if __name__ == '__main__':
    unittest.main()
