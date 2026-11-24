import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app
from management.catalog.sources import SQLiteSource
from management.catalog.field_library import FieldLibrary


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.data=root/'data';self.data.mkdir()
        con=sqlite3.connect(self.data/'his.db')
        con.execute('CREATE TABLE observations (cohort TEXT, value REAL, observed_at TEXT)')
        con.executemany('INSERT INTO observations VALUES (?,?,?)',[
            ('A',1,'2026-01-01'),('A',3,'2026-01-03'),('A',2,'2026-01-02'),('B',4,'2026-01-04')])
        con.commit();con.close()
        self.patch=patch('medsim.DATA_DIR',self.data);self.patch.start()
        self.app=create_app(root/'control',start_runtime=False);self.runtime=self.app.state.runtime
        self.m=self.runtime.management;self.e=self.runtime.execution;self.client=TestClient(self.app)
        self.m.catalog.ingest('his',SQLiteSource(self.data/'his.db').snapshot())
        for field in self.m.catalog.objects(kind='field'):
            self.m.catalog.annotate(field['id'],{'expected_version':field['version'],'classification':'public'},'demo-platform')
        library=FieldLibrary(self.m.store);library.sync()
        for field in library.items():
            saved=library.save(field['id'],{'expected_version':field['version'],'label':field['name'],
                'definition':'Test '+field['name'],'basis':'Synthetic test fixture'},'demo-platform')
            library.save(field['id'],{'expected_version':saved['version'],'confirmation_note':'Fixture reviewed'},'demo-platform',confirm=True)
    def tearDown(self):
        self.e.close();self.runtime.external_governance.close();self.runtime.shutdown();self.client.close();self.patch.stop();self.tmp.cleanup()
    def post(self,path,data,role='platform'):
        response=self.client.post(path,json=data,headers={'X-Demo-Role':role})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_scanned_source_in_permission_scoped_graph(self):
        # A new file registration and a rescan use the same routes as the workbench.
        with closing(sqlite3.connect(self.data/'new-feed.sqlite')) as db:
            db.execute('CREATE TABLE sensor_samples (sample_id INTEGER, reading REAL)')
        with closing(sqlite3.connect(self.data/'his.db')) as db:
            db.execute('CREATE TABLE vitals (sample_id INTEGER)')
            db.execute('CREATE TABLE visit (sample_id INTEGER)')
            db.execute('CREATE TABLE patient (sample_id INTEGER)')
        self.runtime.engine = SimpleNamespace(adapters={'his': SQLiteSource(self.data/'his.db')})
        registration = {'source_id': 'Ward-East', 'connector': 'sqlite',
                        'path': 'new-feed.sqlite', 'name': '东区设备记录'}
        self.post('/api/management/sources', registration)
        endpoint = '/api/portal/catalog?include_relations=true'

        def catalog(role):
            response = self.client.get(endpoint, headers={'X-Demo-Role': role})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        self.assertNotIn('Ward-East', {table['source'] for table in catalog('platform')['tables']})
        self.post('/api/management/catalog/scan', {})
        graph = catalog('platform')
        sensor, = [table for table in graph['tables'] if table['source'] == 'Ward-East']
        self.assertEqual(sensor['name'], 'sensor_samples')
        self.assertEqual(sensor['source_label'], '东区设备记录')
        self.assertEqual({field['name'] for field in sensor['fields']}, {'sample_id', 'reading'})
        self.assertEqual({field['status'] for field in sensor['fields']}, {'draft'})
        self.assertNotIn(registration['path'], str(graph))
        self.assertTrue(any(member['table_id'] == sensor['id'] for relation in graph['relations']
                            for member in relation['members']))
        for role, expected in [('requester', {'vitals'}), ('provider', {'vitals', 'visit'}),
                               ('auditor', {'vitals', 'visit', 'patient'})]:
            with self.subTest(role=role):
                graph = catalog(role)
                self.assertEqual({table['name'] for table in graph['tables']}, expected)
                self.assertTrue(all(table['source_label'] is None for table in graph['tables']))
                self.assertNotIn('东区设备记录', str(graph))
                visible = {field['id'] for table in graph['tables'] for field in table['fields']}
                self.assertTrue(all(member['field_id'] in visible for relation in graph['relations']
                                    for member in relation['members']))
    def execute(self,cohort):
        sql=f"SELECT value FROM observations WHERE cohort = '{cohort}' ORDER BY observed_at DESC LIMIT 2"
        preview=self.post('/api/portal/professional/preview',{'sql':sql})
        self.assertEqual(preview['max_rows'],2)
        f=self.post('/api/portal/professional',{'sql':sql,'confirmed':True})['flow']
        denied=self.client.post('/api/portal/professional/'+f['id']+'/confirm',json={'confirmed':True,'expected_version':f['version']},headers={'X-Demo-Role':'requester'})
        self.assertEqual(denied.status_code,403)
        f=self.post('/api/portal/professional/'+f['id']+'/confirm',{'confirmed':True,'expected_version':f['version']})['flow']
        prefix='/api/management/intake/flows/'+f['id']
        f=self.post(prefix+'/confirm-execution-plan',{'confirmed':True,'expected_version':f['version']})['flow']
        f=self.post(prefix+'/execution-authorization',{'direct':True,'decision':'yes','expected_version':f['version']})['flow']
        f=self.post(prefix+'/submit-query',{'confirmed':True,'expected_version':f['version']})['flow']
        job=self.e.run(self.e.store.require('job',f['job_id']))
        self.assertEqual(job['state'],'success',job.get('error'))
        current=self.client.get('/api/portal/queries/'+f['id']).json()['flow']
        self.assertEqual(current['state'],'query_completed')
        result=self.post(prefix+'/read-delivery',{})
        return result['result']['rows']
    def test_order_limit_survive_plan_and_delivery(self):
        self.assertEqual(self.execute('A'),[[3.0],[2.0]])
    def test_fewer_than_limit_returns_existing_rows(self):
        self.assertEqual(self.execute('B'),[[4.0]])
    def test_unsupported_sql_is_rejected_not_silently_changed(self):
        for sql in ['SELECT value FROM observations',"SELECT value FROM observations WHERE cohort='A'; DELETE FROM observations", "SELECT DISTINCT value FROM observations WHERE cohort='A'", "SELECT value FROM observations WHERE cohort='A' LIMIT 500", "SELECT value FROM observations WHERE cohort='A' OFFSET 1", "SELECT value FROM observations WHERE cohort='A' OR cohort='B'"]:
            with self.subTest(sql=sql):
                response=self.client.post('/api/portal/professional/preview',json={'sql':sql})
                self.assertGreaterEqual(response.status_code,400,response.text)
    def test_read_only_preview_does_not_create_flow(self):
        before=len(self.m.store.list('intake_flow'))
        self.post('/api/portal/professional/preview',{'sql':"SELECT value FROM observations WHERE cohort='A' LIMIT 2"})
        self.assertEqual(len(self.m.store.list('intake_flow')),before)
    def test_provider_cannot_start_query(self):
        response=self.client.post('/api/portal/queries',json={'text':'test'},headers={'X-Demo-Role':'provider'})
        self.assertEqual(response.status_code,403)
