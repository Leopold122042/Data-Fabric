import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import medsim
from platform_core.bootstrap import create_app
from platform_core.storage import DomainError
from management.catalog.sources import SQLiteSource


class ExecutionConstraintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'data'
        self.source.mkdir()
        self.data_patch = patch.object(medsim, 'DATA_DIR', str(self.source))
        self.data_patch.start()
        con = sqlite3.connect(self.source / 'his.db')
        con.execute('CREATE TABLE observations(cohort TEXT, observed_at TEXT, region TEXT, value INTEGER)')
        # A within January has exactly six rows, including timezone/date boundaries.
        rows = [('A', stamp, 'north', 1) for stamp in
                ['2026-01-01', '2026-01-01T08:00:00+08:00', '2026-01-10',
                 '2026-01-20', '2026-01-31T23:59:59Z', '2026-02-01T07:59:59+08:00']]
        rows += [('A', '2026-02-01T00:00:00Z', 'north', 1)] * 6
        rows += [('B', '2026-01-15', 'north', 1)] * 7
        rows += [('A', 'invalid-time', 'north', 1)]
        con.executemany('INSERT INTO observations VALUES(?,?,?,?)', rows)
        con.commit()
        con.close()
        self.app = create_app(self.root / 'control', start_runtime=False)
        self.e = self.app.state.runtime.execution
        self.m = self.e.management
        self.m.catalog.ingest('his', SQLiteSource(self.source / 'his.db').snapshot())
        self.fields = {}
        for field in self.m.catalog.objects(kind='field'):
            field = self.m.catalog.annotate(field['id'], {'classification': 'public',
                'expected_version': field['version']}, 'demo-platform')
            self.fields[field['name']] = field['id']
        self.view = self.e.queries.define({'fields': list(self.fields.values()),
            'group_by': [self.fields['region']]}, 'demo-platform')

    def tearDown(self):
        self.e.close()
        self.app.state.runtime.external_governance.close()
        self.app.state.runtime.shutdown()
        self.data_patch.stop()
        self.tmp.cleanup()

    def demand_body(self, cohort='A', **overrides):
        return {'title': 'January cohort counts', 'object_scope': 'Only cohort ' + cohort,
            'time_start': '2026-01-01', 'time_end': '2026-01-31', 'purpose': 'research',
            'quality': '完整', 'deadline': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            'delivery': '汇总', 'execution_constraints': {
                'scope': {'kind': 'rows', 'filters': [{'field': self.fields['cohort'], 'op': '=', 'value': cohort}]},
                'time_field': self.fields['observed_at'], 'output_mode': 'aggregate',
                'circulation_modes': ['controlled_compute'], 'quality_min_complete': 1,
                'max_rows': 20, 'min_group_size': 5}, **overrides}

    def spec(self, cohort='A', **overrides):
        demand = self.m.intake.save(self.demand_body(cohort, **overrides), 'demo-platform')
        return self.m.intake.confirm(demand['id'], {'expected_version': demand['version'],
            'confirmations': {'content': True, 'attachments': True, 'gaps': True, 'execution_constraints': True},
            'note': 'Description, field bindings and delivery rules reviewed'}, 'demo-platform')

    def plan(self, spec=None, **overrides):
        spec = spec or self.spec()
        return self.e.plan({'spec_id': spec['id'], 'spec_version': spec['version'],
            'nodes': [{'id': 'count', 'operation': 'view', 'view_id': self.view['id']}], **overrides}, 'demo-platform')

    def grant(self, plan, **overrides):
        self.e.confirm(plan['id'], plan['version'], 'demo-platform')
        return self.e.security.grant({'subject': 'demo-platform', 'purpose': plan['purpose'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), **overrides}, 'demo-platform')

    def execute(self, plan, grant):
        return self.e.run(self.e.submit(plan['id'], plan['version'], grant['id'], 'demo-platform'))

    def test_confirmed_scope_and_dates_filter_source_before_aggregation(self):
        plan = self.plan()
        grant = self.grant(plan)
        job = self.execute(plan, grant)
        self.assertEqual(job['state'], 'success', job)
        delivered = self.e.delivery.read(job['delivery_id'], 'demo-platform', plan)
        self.assertEqual(delivered['result']['rows'], [['north', 6]])
        trace = job['nodes']['count']['trace']
        self.assertEqual(trace['sources'][0]['rows_read'], 6)
        self.assertEqual(trace['sources'][0]['pushed_filters'], 3)
        self.assertEqual(delivered['receipt']['execution_policy_digest'], plan['execution_policy_digest'])

    def test_cache_cannot_reuse_a_different_cohort(self):
        a = self.plan()
        first = self.execute(a, self.grant(a))
        b = self.plan(self.spec('B'))
        second = self.execute(b, self.grant(b))
        self.assertEqual(second['state'], 'success', second)
        self.assertFalse(second['nodes']['count']['trace']['cache_hit'])
        self.assertNotEqual(first['nodes']['count']['trace']['materialization_id'], second['nodes']['count']['trace']['materialization_id'])
        self.assertEqual(self.e.delivery.read(second['delivery_id'], 'demo-platform', b)['result']['rows'], [['north', 7]])
        repeated = self.execute(b, self.e.store.require('grant', second['grant_id']))
        self.assertTrue(repeated['nodes']['count']['trace']['cache_hit'])

    def test_plan_cannot_weaken_delivery_or_quality(self):
        spec = self.spec()
        for override in ({'output_mode': 'object'}, {'circulation_mode': 'download'},
                         {'circulation_mode': 'api'}, {'quality_min_complete': 0}):
            with self.subTest(override=override), self.assertRaises(DomainError):
                self.plan(spec, **override)
        self.assertEqual(self.e.store.list('plan'), [])

    def test_missing_or_unsupported_executable_rules_are_not_silently_ignored(self):
        for changes in ({'execution_constraints': None}, {'cost': 10}, {'quality': '模型准确率至少90%'}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                self.spec(**changes)
        spec = self.spec()
        with patch.object(self.e.runtime, '_dispatch') as handler, self.assertRaises(DomainError):
            self.plan(spec, nodes=[{'id': 'legacy', 'operation': 'service', 'service_id': 'SVC-FED-QUERY'}])
        handler.assert_not_called()

    def test_executable_conditions_need_separate_human_confirmation(self):
        demand = self.m.intake.save(self.demand_body(), 'demo-platform')
        with self.assertRaisesRegex(DomainError, '执行条件'):
            self.m.intake.confirm(demand['id'], {'expected_version': demand['version'],
                'confirmations': {'content': True, 'attachments': True, 'gaps': True}, 'note': 'reviewed'}, 'demo-platform')
        self.assertEqual(self.m.store.list('spec'), [])

    def test_node_params_and_upstream_inputs_cannot_override_filters(self):
        spec = self.spec()
        for node in ({'id': 'n', 'operation': 'view', 'view_id': self.view['id'], 'params': {'object_scope': 'all'}},
                     {'id': 'n', 'operation': 'view', 'view_id': self.view['id'], 'inputs': {'time_start': 'prior'}}):
            nodes = [{'id': 'prior', 'operation': 'view', 'view_id': self.view['id']}, node]
            with self.subTest(node=node), self.assertRaises(DomainError):
                self.plan(spec, nodes=nodes)
        with self.assertRaises(DomainError):
            self.plan(spec, scenario='medical')

    def test_view_must_contain_actual_scope_and_time_fields(self):
        other = self.e.queries.define({'fields': [self.fields['region']],
            'group_by': [self.fields['region']]}, 'demo-platform')
        with self.assertRaisesRegex(DomainError, '范围字段或时间字段'):
            self.plan(nodes=[{'id': 'n', 'operation': 'view', 'view_id': other['id']}])

    def test_grant_cannot_relax_specification(self):
        plan = self.plan()
        for changes in ({'max_rows': 21}, {'min_group_size': 4}, {'mode': 'object'},
                        {'purpose': 'other'}, {'expires_at': (datetime.now(timezone.utc)+timedelta(days=3)).isoformat()}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                self.grant(plan, **changes)
        self.assertEqual(self.e.store.list('grant'), [])

    def test_plan_and_policy_tampering_cannot_create_or_read_delivery(self):
        plan = self.plan()
        grant = self.grant(plan)
        job = self.execute(plan, grant)
        for key, value in [('output_policy', {'version': 1, 'columns': {}}),
                           ('quality_min_complete', 0), ('circulation_mode', 'download')]:
            altered = {**plan, key: value}
            with self.subTest(key=key), self.assertRaises(DomainError):
                self.e.delivery.read(job['delivery_id'], 'demo-platform', altered)
        denied=[d for d in self.e.store.list('decision') if d['stage']=='retrieve' and not d['allowed']]
        self.assertEqual(len(denied),3)

    def test_demand_change_blocks_cached_execution_and_delivery(self):
        plan = self.plan()
        grant = self.grant(plan)
        job = self.execute(plan, grant)
        spec = self.m.store.require('spec', plan['spec_id'])
        demand = self.m.store.require('demand', spec['demand_id'])
        self.m.intake.save({**demand, 'object_scope': 'cohort B', 'expected_version': demand['version']}, 'demo-platform')
        with self.assertRaises(DomainError):
            self.e.delivery.read(job['delivery_id'], 'demo-platform', plan)
        with self.assertRaises(DomainError):
            self.e.submit(plan['id'], plan['version'], grant['id'], 'demo-platform')

    def test_deadline_is_rechecked_after_computation_and_on_retrieval(self):
        plan = self.plan()
        grant = self.grant(plan)
        after = datetime.fromisoformat(plan['execution_policy']['deadline_at']) + timedelta(seconds=1)
        real_execute = self.e.queries.execute
        clock_patch = patch('execution.constraints.datetime')
        def run_then_expire(*args):
            result = real_execute(*args)
            clock = clock_patch.start()
            clock.now.return_value = after
            return result
        try:
            with patch.object(self.e.queries, 'execute', side_effect=run_then_expire):
                job = self.execute(plan, grant)
            self.assertEqual(job['state'], 'failed', job)
            self.assertIsNone(job['delivery_id'])
        finally:
            clock_patch.stop()
        good = self.execute(plan, grant)
        self.assertEqual(good['state'], 'success', good)
        with patch('execution.constraints.datetime') as clock:
            clock.now.return_value = after
            with self.assertRaisesRegex(DomainError, '交付时限'):
                self.e.delivery.read(good['delivery_id'], 'demo-platform', plan)


if __name__ == '__main__':
    unittest.main()
