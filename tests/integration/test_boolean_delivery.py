"""SQLite BOOLEAN values preserve their type through execution and delivery."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import medsim
from management.catalog.sources import SQLiteSource
from platform_core.bootstrap import create_app


class BooleanDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'data'
        self.source.mkdir()
        self.data_patch = patch.object(medsim, 'DATA_DIR', str(self.source))
        self.data_patch.start()
        self.db_path = self.source / 'his.db'
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute('CREATE TABLE observations(enabled BOOLEAN, observed_at TEXT)')
            con.executemany('INSERT INTO observations VALUES (?, ?)',
                            [(value, '2026-01-15') for value in [0] * 5 + [1] * 5])
        self.app = create_app(self.root / 'control', start_runtime=False)
        self.e = self.app.state.runtime.execution
        self.m = self.e.management
        self.m.catalog.ingest('his', SQLiteSource(self.db_path).snapshot())
        self.fields = {}
        for field in self.m.catalog.objects(kind='field'):
            annotated = self.m.catalog.annotate(field['id'], {
                'classification': 'public', 'expected_version': field['version'],
            }, 'demo-platform')
            self.fields[field['name']] = annotated['id']

    def tearDown(self):
        self.e.close()
        self.app.state.runtime.external_governance.close()
        self.app.state.runtime.shutdown()
        self.data_patch.stop()
        self.tmp.cleanup()

    def authorized(self, mode='aggregate', completeness=1):
        view = self.e.queries.define({
            'fields': list(self.fields.values()), 'output_mode': mode,
            'group_by': [self.fields['enabled']] if mode == 'aggregate' else [],
        }, 'demo-platform')
        demand = self.m.intake.save({
            'title': 'Reviewed switch states', 'object_scope': 'All rows in January',
            'time_start': '2026-01-01', 'time_end': '2026-01-31', 'purpose': 'research',
            'quality': '完整率', 'deadline': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            'delivery': '汇总' if mode == 'aggregate' else '对象级',
            'execution_constraints': {
                'scope': {'kind': 'rows', 'all_rows': True},
                'time_field': self.fields['observed_at'], 'output_mode': mode,
                'circulation_modes': ['controlled_compute'], 'quality_min_complete': completeness,
                'max_rows': 20, 'min_group_size': 5,
            },
        }, 'demo-platform')
        spec = self.m.intake.confirm(demand['id'], {
            'expected_version': demand['version'],
            'confirmations': {'content': True, 'attachments': True, 'gaps': True,
                              'execution_constraints': True},
            'note': 'Reviewed field classification, source types and row constraints',
        }, 'demo-platform')
        plan = self.e.plan({'spec_id': spec['id'], 'spec_version': spec['version'],
            'nodes': [{'id': 'switches', 'operation': 'view', 'view_id': view['id']}]}, 'demo-platform')
        self.e.confirm(plan['id'], plan['version'], 'demo-platform')
        grant = self.e.security.grant({
            'subject': 'demo-platform', 'purpose': plan['purpose'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }, 'demo-platform')
        return plan, grant

    def execute(self, plan, grant):
        return self.e.run(self.e.submit(plan['id'], plan['version'], grant['id'], 'demo-platform'))

    def test_aggregate_booleans_survive_delivery_and_cache(self):
        plan, grant = self.authorized()
        self.assertEqual(plan['output_policy']['columns']['enabled']['type'], 'boolean')
        for cache_hit in (False, True):
            with self.subTest(cache_hit=cache_hit):
                job = self.execute(plan, grant)
                self.assertEqual(job['state'], 'success', job)
                self.assertEqual(job['nodes']['switches']['trace']['cache_hit'], cache_hit)
                delivery = self.e.delivery.read(job['delivery_id'], 'demo-platform', plan)
                self.assertEqual(delivery['result']['columns'], ['enabled', 'count'])
                self.assertEqual(sorted(delivery['result']['rows']), [[False, 5], [True, 5]])
                self.assertTrue(all(type(row[0]) is bool for row in delivery['result']['rows']))
                self.assertTrue(all(delivery['receipt']['checks'].values()))

    def test_object_booleans_and_null_remain_distinct(self):
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute('INSERT INTO observations VALUES (NULL, ?)', ('2026-01-15',))
        plan, grant = self.authorized('object', completeness=0.9)
        job = self.execute(plan, grant)
        self.assertEqual(job['state'], 'success', job)
        delivery = self.e.delivery.read(job['delivery_id'], 'demo-platform', plan)
        index = delivery['result']['columns'].index('enabled')
        values = [row[index] for row in delivery['result']['rows']]
        self.assertEqual(values.count(False), 5)
        self.assertEqual(values.count(True), 5)
        self.assertEqual(values.count(None), 1)
        self.assertTrue(all(value is None or type(value) is bool for value in values))

    def test_invalid_sqlite_boolean_is_not_coerced_or_delivered(self):
        plan, grant = self.authorized()
        for invalid in ('false', 'true', 'arbitrary text', 2, -1, 0.5):
            with self.subTest(value=invalid):
                with closing(sqlite3.connect(self.db_path)) as con, con:
                    con.execute('DELETE FROM observations')
                    con.executemany('INSERT INTO observations VALUES (?, ?)',
                                    [(invalid, '2026-01-15')] * 5)
                job = self.execute(plan, grant)
                self.assertEqual(job['state'], 'failed', job)
                self.assertIn('布尔字段', job['error'])
                self.assertIsNone(job['delivery_id'])
        self.assertEqual(self.e.store.list('delivery'), [])
        self.assertEqual(self.e.store.list('materialization'), [])


if __name__ == '__main__':
    unittest.main()
