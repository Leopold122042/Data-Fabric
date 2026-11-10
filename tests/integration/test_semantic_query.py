import sqlite3
from unittest.mock import patch
from tests.integration.test_portal import PortalTest
from management.intake.semantic_query import compile_intent
from platform_core.storage import DomainError
from management.catalog.sources import SQLiteSource
from management.catalog.field_library import FieldLibrary


class SemanticQueryTest(PortalTest):
    def intent(self, **changes):
        return dict({'summary':'筛选 A 组、日期早于 2026-01-03 的记录，按数值降序最多返回两条',
            'entity':'observations','outputs':['observations.value'],
            'filters':[{'field':'observations.cohort','op':'=','value':'A'},
                       {'field':'observations.observed_at','op':'<','value':'2026-01-03'}],
            'order_by':[{'field':'observations.value','direction':'desc'}],'limit':2,'questions':[]},**changes)

    def finish(self,f):
        f=self.post('/api/portal/queries/'+f['id']+'/confirm',{'confirmed':True,'expected_version':f['version']})['flow']
        prefix='/api/management/intake/flows/'+f['id']
        f=self.post(prefix+'/confirm-execution-plan',{'confirmed':True,'expected_version':f['version']})['flow']
        f=self.post(prefix+'/execution-authorization',{'direct':True,'decision':'yes','expected_version':f['version']})['flow']
        f=self.post(prefix+'/submit-query',{'confirmed':True,'expected_version':f['version']})['flow']
        job=self.e.run(self.e.store.require('job',f['job_id']))
        self.assertEqual(job['state'],'success',job.get('error'))
        self.client.get('/api/portal/queries/'+f['id'])
        return self.post(prefix+'/read-delivery',{})['result']['rows']

    def ask(self,intent,text='查询A组、日期早于2026年1月3日的记录，按数值降序取两条'):
        with patch('management.intake.semantic_query.interpret',return_value=intent):
            return self.post('/api/portal/queries',{'text':text})['flow']

    def test_range_date_and_order_reach_actual_result(self):
        f=self.ask(self.intent())
        self.assertEqual(f['query_mode'],'semantic')
        self.assertEqual(f['field_bindings'],[])
        self.assertTrue(all(r['review_status']=='pending' for r in f['resources']))
        self.assertEqual(self.finish(f),[[2.0],[1.0]])

    def test_numeric_comparison_not_lexical(self):
        db=sqlite3.connect(self.data/'his.db')
        db.execute('INSERT INTO observations VALUES (?,?,?)',('A',12,'2026-01-05'));db.commit();db.close()
        intent=self.intent(filters=[{'field':'observations.value','op':'>=','value':3}],limit=10)
        self.assertEqual(self.finish(self.ask(intent)),[[12.0],[4.0],[3.0]])

    def test_question_preserves_original_request(self):
        f=self.ask(self.intent(questions=['缺少排序时间依据，是否返回全部匹配记录？']))
        self.assertIsNone(f['query_draft'])
        self.assertEqual(f['state'],'awaiting_chain_confirmation')
        with patch('management.intake.semantic_query.interpret',return_value=self.intent()) as model:
            updated=self.post('/api/portal/queries/'+f['id']+'/clarify',{
                'expected_version':f['version'],'answers':[{'intent_id':'operation-0','answer':'保留A组条件，按数值降序'}]})['flow']
        self.assertEqual(updated['id'],f['id'])
        self.assertEqual(updated['demand_id'],f['demand_id'])
        self.assertIn(f['request_text'],model.call_args.args[0])
        self.assertIn('保留A组条件',model.call_args.args[0])
        self.assertEqual(self.finish(updated),[[2.0],[1.0]])

    def test_unsupported_operation_never_dropped(self):
        f=self.ask(self.intent(filters=[{'field':'observations.value','op':'OR','value':2}]))
        self.assertIsNone(f['query_draft'])
        self.assertTrue(f['clarification_questions'])
        self.assertFalse(self.e.store.list('job'))

    def test_unknown_fields_are_not_executable(self):
        with self.assertRaises(DomainError):
            compile_intent(self.intent(outputs=['observations.invented']),self.m.store)

    def test_filter_value_cannot_inject_sql(self):
        f=self.ask(self.intent(filters=[{'field':'observations.cohort','op':'=',
                                      'value':"A' OR 1=1 --"}]))
        self.assertEqual(self.finish(f),[])

    def test_latest_cannot_sort_by_value_instead_of_time(self):
        f=self.ask(self.intent(selection='latest',summary='查询最近两条记录'))
        self.assertIsNone(f['query_draft'])
        self.assertIn('时间依据',f['clarification_questions'][0])

    def test_latest_uses_record_time_and_returns_fewer_when_needed(self):
        f=self.ask(self.intent(selection='latest',summary='查询B组最近两条记录',
            filters=[{'field':'observations.cohort','op':'=','value':'B'}],
            order_by=[{'field':'observations.observed_at','direction':'desc'}]))
        self.assertEqual(self.finish(f),[[4.0]])

    def test_old_draft_recent_summary_also_checks_time(self):
        with self.assertRaises(DomainError):
            compile_intent(self.intent(summary='查询最近两条记录'),self.m.store)

    def test_reversed_comparison_sql(self):
        result=self.post('/api/portal/professional/preview',{
            'sql':'SELECT value FROM observations WHERE 2 < value ORDER BY value DESC LIMIT 10'})
        self.assertEqual(result['filters'][0]['op'],'>')

    def test_male_born_before_1995_patient_list(self):
        db=sqlite3.connect(self.data/'his.db')
        db.execute('CREATE TABLE patient (patient_id TEXT, gender TEXT, birth_date TEXT)')
        db.executemany('INSERT INTO patient VALUES (?,?,?)',[
            ('P1','男','1994-12-31'),('P2','男','1995-01-01'),
            ('P3','女','1990-01-01'),('P4','男',None),('P5','男','1980-05-06')])
        db.commit();db.close()
        self.m.catalog.ingest('his',SQLiteSource(self.data/'his.db').snapshot())
        for f in self.m.catalog.objects(kind='field'):
            if f.get('table')=='patient':
                self.m.catalog.annotate(f['id'],{'expected_version':f['version'],
                    'classification':'identifier' if f['name']=='patient_id' else 'public'},'demo-platform')
        library=FieldLibrary(self.m.store);library.sync()
        for f in library.items():
            if f['status']=='confirmed':continue
            saved=library.save(f['id'],{'expected_version':f['version'],'label':f['name'],
                'definition':'Synthetic patient '+f['name'],'basis':'Fixture'},'demo-platform')
            library.save(f['id'],{'expected_version':saved['version'],'confirmation_note':'Reviewed'},'demo-platform',confirm=True)
        intent=self.intent(entity='patient',outputs=['patient.patient_id','patient.gender','patient.birth_date'],
            filters=[{'field':'patient.gender','op':'=','value':'男'},
                     {'field':'patient.birth_date','op':'<','value':'1995-01-01'}],
            order_by=[{'field':'patient.birth_date','direction':'asc'}],limit=200)
        f=self.ask(intent,'查询性别为男，出生日期在1995年之前的人')
        self.assertEqual(self.finish(f),[['P5','男','1980-05-06'],['P1','男','1994-12-31']])
