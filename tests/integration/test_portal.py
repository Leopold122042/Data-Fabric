import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
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
        for sql in ['SELECT value FROM observations',"SELECT value FROM observations WHERE cohort='A'; DELETE FROM observations", "SELECT DISTINCT ON (value) value FROM observations WHERE cohort='A'", "SELECT value FROM observations WHERE cohort='A' LIMIT 500", "SELECT value FROM observations WHERE cohort='A' OFFSET 1", "SELECT value FROM observations WHERE cohort='A' OR cohort='B'"]:
            with self.subTest(sql=sql):
                response=self.client.post('/api/portal/professional/preview',json={'sql':sql})
                self.assertGreaterEqual(response.status_code,400,response.text)
    def test_read_only_preview_does_not_create_flow(self):
        before=len(self.m.store.list('intake_flow'))
        self.post('/api/portal/professional/preview',{'sql':"SELECT value FROM observations WHERE cohort='A' LIMIT 2"})
        self.assertEqual(len(self.m.store.list('intake_flow')),before)

    def test_count_delivery_distinguishes_records_people_and_small_samples(self):
        with closing(sqlite3.connect(self.data/'his.db')) as db, db:
            db.execute('ALTER TABLE observations ADD COLUMN patient_id TEXT')
            db.executemany('INSERT INTO observations VALUES (?,?,?,?)', [
                ('C',5,'2026-01-05',person) for person in
                ('p1','p1','p2','p3','p4','p5',' ',None)])
        self.m.catalog.ingest('his',SQLiteSource(self.data/'his.db').snapshot())
        library=FieldLibrary(self.m.store)
        patient_id=next(f for f in library.items() if f['name']=='patient_id')
        saved=library.save(patient_id['id'],{'expected_version':patient_id['version'],'label':'患者标识',
            'definition':'测试患者标识','basis':'Synthetic test fixture'},'demo-platform')
        library.save(patient_id['id'],{'expected_version':saved['version'],'confirmation_note':'Fixture reviewed'},
                     'demo-platform',confirm=True)
        for expression,cohort,kind,rows in (
            ('COUNT(*)','C','row_count',[[8]]),
            ('COUNT(DISTINCT patient_id)','C','person_count',[[5]]),
            ('COUNT(*)','B','row_count',[[1]]),
            ('COUNT(*)','missing','row_count',[[0]]),
        ):
            with self.subTest(expression=expression,cohort=cohort):
                sql=f"SELECT {expression} FROM observations WHERE cohort='{cohort}'"
                f=self.post('/api/portal/professional',{'sql':sql,'confirmed':True})['flow']
                url='/api/portal/queries/'+f['id']
                f=self.post(url+'/execute',{'expected_version':f['version'],'confirmed':True})['flow']
                job=self.e.run(self.e.store.require('job',f['job_id']))
                self.assertEqual(job['state'],'success',job.get('error'))
                current=self.client.get(url,headers={'X-Demo-Role':'platform'})
                self.assertEqual(current.json()['flow']['state'],'query_completed')
                result=self.post(url+'/result',{})['result']
                self.assertEqual(result['columns'],['count'])
                self.assertEqual(result['calculation'],kind)
                self.assertEqual(result['rows'],rows)
                self.assertEqual(result['suppressed'],not rows)

    def test_result_link_reads_only_its_bound_delivery(self):
        sql="SELECT COUNT(*) FROM observations WHERE cohort='A'"
        flow=self.post('/api/portal/professional',{'sql':sql,'confirmed':True})['flow']
        url='/api/portal/queries/'+flow['id']
        running=self.post(url+'/execute',{'expected_version':flow['version'],'confirmed':True})['flow']
        job=self.e.run(self.e.store.require('job',running['job_id']))
        self.assertEqual(job['state'],'success',job.get('error'))
        headers={'X-Demo-Role':'platform'}
        completed=self.client.get(url,headers=headers).json()['flow']
        delivery_id=completed['delivery_id']
        endpoint=url+'/result?delivery_id='+delivery_id
        for _ in range(2):
            result=self.client.post(endpoint,json={},headers=headers)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.headers['cache-control'],'no-store')
            self.assertEqual(result.json()['result']['rows'],[[3]])
        invalid=self.client.post(url+'/result?delivery_id=DEL-another',json={},headers=headers)
        self.assertEqual(invalid.status_code,410)
        denied=self.client.post(endpoint,json={},headers={'X-Demo-Role':'auditor'})
        self.assertEqual(denied.status_code,403)
        self.e.security.revoke(running['grant_id'],'demo-platform')
        self.assertEqual(self.client.post(endpoint,json={},headers=headers).status_code,403)

    def test_diabetes_service_delivers_zero_and_other_small_counts_to_level_four(self):
        from management.intake.calculations import DIABETES_COUNTS
        from platform_core.access import project_result

        self.m.catalog.ingest('doris-nju',{'tables':{DIABETES_COUNTS:{'columns':['cohort','patient_count']}}})
        library=FieldLibrary(self.m.store)
        for field in library.items():
            if field['name'] not in ('cohort','patient_count'):
                continue
            saved=library.save(field['id'],{'expected_version':field['version'],'label':field['name'],
                'definition':'测试诊断汇总','basis':'Synthetic test fixture'},'demo-platform')
            library.save(field['id'],{'expected_version':saved['version'],'confirmation_note':'Fixture reviewed'},
                         'demo-platform',confirm=True)

        counts={'diabetes_all':3,'diabetes_type1':0}
        @contextmanager
        def cursor():
            def execute(sql, values):
                self.assertIn('MAX(`patient_count`)',sql)
                self.assertIn('`cohort` = %s',sql)
                current.result=(counts[values[0]],)
            current=SimpleNamespace(execute=execute,fetchone=lambda:current.result)
            yield current
        source=SimpleNamespace(cursor=cursor,table_sql=lambda name: name)
        self.m.catalog.doris=SimpleNamespace(for_table=lambda *_:source,status=lambda _: 'connected')

        for scope,count in counts.items():
            with self.subTest(scope=scope):
                with patch('management.intake.semantic_query.interpret',side_effect=AssertionError('登记口径由平台提供')):
                    f=self.post('/api/portal/queries',{'text':'糖尿病的患者有多少人','provider':'deepseek'})['flow']
                self.assertEqual(f['state'],'awaiting_chain_confirmation')
                options=f['field_bindings'][0]['options']
                self.assertEqual([item['id'] for item in options],['diabetes_all','diabetes_type2','diabetes_type1'])
                self.assertTrue(options[0]['recommended'])
                url='/api/portal/queries/'+f['id']
                f=self.post(url+'/clarify',{'expected_version':f['version'],
                    'answers':[{'intent_id':f['field_bindings'][0]['intent_id'],'option_id':scope}]})['flow']
                self.assertEqual(f['draft']['execution_constraints']['min_group_size'],0)
                f=self.post(url+'/execute',{'expected_version':f['version'],'confirmed':True})['flow']
                grant=self.e.store.require('grant',f['grant_id'])
                self.assertEqual((grant['min_group_size'],grant['account_level']),(0,4))
                job=self.e.run(self.e.store.require('job',f['job_id']))
                self.assertEqual(job['state'],'success',job.get('error'))
                headers={'X-Demo-Role':'platform'}
                self.assertEqual(self.client.get(url,headers=headers).json()['flow']['state'],'query_completed')
                result=self.post(url+'/result',{})['result']
                self.assertEqual(result['rows'],[[count]])
                self.assertFalse(result['suppressed'])
                projected=project_result({'columns':['count'],'rows':[[count]]},{'level':3},0,
                    calculation={'kind':'diabetes_count'})
                self.assertEqual(projected['rows'],[[count]])
                denied=self.client.get(url,headers={'X-Demo-Role':'auditor'})
                self.assertEqual(denied.status_code,403)

    def test_people_count_reuses_the_saved_name_and_rechecks_current_permissions(self):
        from management.intake.professional import workbench_policy

        with closing(sqlite3.connect(self.data/'his.db')) as db, db:
            db.execute('CREATE TABLE patient (patient_id TEXT, name TEXT)')
            db.executemany('INSERT INTO patient VALUES (?,?)',[('a','唐军'),('a','唐军'),('b','测试')])
        self.m.catalog.ingest('his',SQLiteSource(self.data/'his.db').snapshot())
        library=FieldLibrary(self.m.store)
        for field in library.items():
            if field['status']=='confirmed':
                continue
            saved=library.save(field['id'],{'expected_version':field['version'],'label':field['name'],
                'definition':'测试字段','basis':'Synthetic test fixture'},'demo-platform')
            library.save(field['id'],{'expected_version':saved['version'],'confirmation_note':'Fixture reviewed'},
                         'demo-platform',confirm=True)
        intent={'selection':'count_people','entity':'patient','summary':'统计姓名为唐军的去重人数',
            'outputs':[],'filters':[{'field':'patient.name','op':'=','value':'唐军'}],
            'limit':1,'order_by':[],'questions':[]}
        with patch('management.intake.semantic_query.interpret',return_value=intent), patch(
                'management.intake.semantic_query.workbench_policy',
                side_effect=lambda shape, **kwargs: {**workbench_policy(shape),'min_group_size':5}):
            old=self.post('/api/portal/queries',{'text':'唐军有多少人'})['flow']

        def complete(flow):
            url='/api/portal/queries/'+flow['id']
            started=self.post(url+'/execute',{'expected_version':flow['version'],'confirmed':True})['flow']
            job=self.e.run(self.e.store.require('job',started['job_id']))
            self.assertEqual(job['state'],'success',job.get('error'))
            headers={'X-Demo-Role':'platform'}
            status=self.client.get(url,headers=headers).json()
            return status,self.post(url+'/result',{})

        state,delivery=complete(old)
        self.assertTrue(state['can_recount'])
        self.assertTrue(delivery['result']['suppressed'])
        url='/api/portal/queries/'+old['id']+'/recount'
        with patch('management.intake.semantic_query.interpret',side_effect=AssertionError('已保存口径无需请求模型')):
            revised=self.post(url,{'expected_version':delivery['flow']['version']})['flow']
        self.assertNotEqual(revised['id'],old['id'])
        self.assertEqual(revised['query_draft'],old['query_draft'])
        self.assertEqual(revised['state'],'waiting_manual_spec_confirmation')
        self.assertNotIn('job_id',revised)
        state,delivery=complete(revised)
        self.assertFalse(state['can_recount'])
        self.assertEqual(delivery['result']['rows'],[[1]])

    def test_missing_values_require_a_new_explicit_query_policy(self):
        with closing(sqlite3.connect(self.data/'his.db')) as db, db:
            db.execute('INSERT INTO observations VALUES (?,?,?)', ('M',None,'2026-01-02'))
        headers={'X-Demo-Role':'platform'}
        body={'sql':"SELECT value, cohort FROM observations WHERE cohort='M' LIMIT 2",'confirmed':True}
        strict=self.post('/api/portal/professional',body)['flow']
        strict_url='/api/portal/queries/'+strict['id']
        running=self.post(strict_url+'/execute',{'expected_version':strict['version'],'confirmed':True})['flow']
        failed=self.e.run(self.e.store.require('job',running['job_id']))
        self.assertEqual(failed['state'],'failed')
        self.assertEqual(failed['error_kind'],'delivery_quality')
        self.assertIn('50.0%',failed['error'])
        self.assertIn('value（1 条）',failed['error'])
        self.assertIsNone(failed['delivery_id'])
        self.assertEqual(self.client.get(strict_url,headers=headers).json()['flow']['query_error_kind'],'delivery_quality')

        revised=self.post('/api/portal/professional',{**body,'allow_missing':True})['flow']
        self.assertNotEqual(revised['id'],strict['id'])
        self.assertEqual(revised['state'],'waiting_manual_spec_confirmation')
        self.assertFalse(revised.get('job_id'))
        self.assertEqual(revised['query_draft'],strict['query_draft'])
        revised_url='/api/portal/queries/'+revised['id']
        running=self.post(revised_url+'/execute',{'expected_version':revised['version'],'confirmed':True})['flow']
        completed=self.e.run(self.e.store.require('job',running['job_id']))
        self.assertEqual(completed['state'],'success',completed.get('error'))
        self.client.get(revised_url,headers=headers)
        delivery=self.post(revised_url+'/result',{})
        self.assertEqual(delivery['result']['rows'],[[None,'M']])
        self.assertTrue(all(delivery['checks'].values()))
        self.assertEqual(self.e.get(failed['plan_id'],failed['plan_version'])['quality_min_complete'],1)

    def test_provider_cannot_start_query(self):
        response=self.client.post('/api/portal/queries',json={'text':'test'},headers={'X-Demo-Role':'provider'})
        self.assertEqual(response.status_code,403)
