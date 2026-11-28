import sqlite3
from unittest.mock import patch
from tests.integration.test_portal import PortalTest
from management.intake.calculations import DIABETES_COUNTS, DORIS_PEOPLE, SUMMARY_TABLES
from management.intake.semantic_query import compile_intent, diabetes_question
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

    def test_collection_range_and_prefix_bind_to_the_source(self):
        intent=self.intent(filters=[
            {'field':'observations.cohort','op':'IN','value':['A','B']},
            {'field':'observations.value','op':'between','value':[2,4]},
            {'field':'observations.observed_at','op':'prefix','value':'2026-01'}],limit=10)
        flow=self.ask(intent)
        self.assertEqual(flow['state'],'waiting_manual_spec_confirmation')
        self.assertEqual(self.finish(flow),[[4.0],[3.0],[2.0]])

    def test_age_details_use_a_derived_value_and_deduplicate_diagnoses(self):
        db=sqlite3.connect(self.data/'his.db')
        db.executescript("""
        CREATE TABLE patient(patient_id TEXT PRIMARY KEY,birth_date TEXT);
        CREATE TABLE visit(visit_id INTEGER PRIMARY KEY,patient_id TEXT,diagnosis_code TEXT);
        INSERT INTO patient VALUES ('P001','1980-'), ('P002','1984-12-08');
        INSERT INTO visit VALUES (1,'P001','E11.9'),(2,'P001','E11.1'),(3,'P002','I10');
        """)
        db.close()
        self.m.catalog.ingest('his',SQLiteSource(self.data/'his.db').snapshot())
        library=FieldLibrary(self.m.store)
        for field in library.items():
            if field['status']=='confirmed':
                continue
            row=self.m.store.require('object',field['id'])
            self.m.catalog.annotate(field['id'],{'expected_version':row['version'],'classification':'internal'},'demo-platform')
            saved=library.save(field['id'],{'expected_version':field['version'],'label':field['name'],
                'definition':'模拟医疗字段','basis':'测试源'},'demo-platform')
            library.save(field['id'],{'expected_version':saved['version'],'confirmation_note':'已核对'},'demo-platform',confirm=True)
        flow=self.post('/api/portal/queries',{'text':'得糖尿病的人，他们各自的年龄和编号'})['flow']
        self.assertTrue(flow['field_bindings'][0]['options'][0]['label'].startswith('模拟 HIS'))
        url='/api/portal/queries/'+flow['id']
        flow=self.post(url+'/clarify',{'expected_version':flow['version'],
            'answers':[{'intent_id':'operation-0','option_id':'diabetes_all'}]})['flow']
        self.assertEqual(len(flow['professional_preview']['join_path']),1)
        self.assertTrue(flow['professional_preview']['distinct'])
        running=self.post(url+'/execute',{'expected_version':flow['version'],'confirmed':True})['flow']
        job=self.e.run(self.e.store.require('job',running['job_id']))
        self.assertEqual(job['state'],'success',job.get('error'))
        self.client.get(url,headers={'X-Demo-Role':'platform'})
        outputs=self.post(url+'/result',{})['result']['rows']
        self.assertEqual(len(outputs),1)
        patient,age,basis=outputs[0]
        self.assertEqual((patient,basis),('P001','按出生年份估算'))
        self.assertIsInstance(age,int)

    def test_question_preserves_original_request(self):
        f=self.ask(self.intent(questions=['缺少排序时间依据，是否返回全部匹配记录？']))
        self.assertIsNone(f['query_draft'])
        self.assertEqual(f['state'],'awaiting_chain_confirmation')
        with patch('management.intake.semantic_query.interpret',return_value=self.intent()) as model:
            updated=self.post('/api/portal/queries/'+f['id']+'/clarify',{
                'expected_version':f['version'],'answers':[{'intent_id':'operation-0','answer':'保留A组条件，按数值降序'}]})['flow']
        self.assertEqual(updated['id'],f['id'])
        self.assertEqual(updated['demand_id'],f['demand_id'])
        self.assertEqual(f['request_text'],model.call_args.args[0])
        self.assertEqual(model.call_args.kwargs['clarifications'][0]['answer'],'保留A组条件，按数值降序')
        self.assertEqual(self.finish(updated),[[2.0],[1.0]])

    def test_executable_choice_enters_confirmation_without_another_model_call(self):
        candidate=self.intent()
        draft=self.intent(questions=['按哪个时间口径？'],
            choices=[[{'label':'早于 1 月 3 日','intent':candidate}]])
        flow=self.ask(draft)
        option=flow['field_bindings'][0]['options'][0]
        self.assertIn('intent',option)
        with patch('management.intake.semantic_query.interpret',side_effect=AssertionError('口径已确定')):
            ready=self.post('/api/portal/queries/'+flow['id']+'/clarify',{'expected_version':flow['version'],
                'answers':[{'intent_id':'operation-0','option_id':option['id']}]})['flow']
        self.assertEqual(ready['state'],'waiting_manual_spec_confirmation')
        self.assertEqual(ready['clarification_answers'][0]['answer'],'早于 1 月 3 日')
        self.assertEqual(ready['semantic_intent']['filters'],candidate['filters'])
        self.assertFalse(self.e.store.list('job'))

    def test_answered_question_is_not_asked_again_even_when_reworded(self):
        flow=self.ask(self.intent(questions=['用哪个口径？'],question_keys=['filter.status']))
        repeated=self.intent(questions=['请确认统计采用哪种状态'],question_keys=['filter.status'])
        with patch('management.intake.semantic_query.interpret',return_value=repeated) as model:
            url='/api/portal/queries/'+flow['id']
            stalled=self.post(url+'/clarify',{'expected_version':flow['version'],
                'answers':[{'intent_id':'operation-0','answer':'已确认的状态'}]})['flow']
        self.assertEqual(model.call_count,2)
        self.assertEqual(stalled['state'],'attention')
        self.assertFalse(stalled['field_bindings'])
        self.assertEqual(stalled['clarification_answers'][0]['answer'],'已确认的状态')
        with patch('management.intake.semantic_query.interpret',return_value=self.intent()) as model:
            ready=self.post(url+'/retry-understanding',{'expected_version':stalled['version']})['flow']
        self.assertEqual(model.call_args.kwargs['clarifications'],stalled['clarification_answers'])
        self.assertEqual(ready['state'],'waiting_manual_spec_confirmation')

    def test_registered_people_counts_can_recover_a_missing_scope(self):
        self.m.catalog.ingest('doris-nju',{'tables':{
            name:{'columns':['cohort','patient_count']} for name in SUMMARY_TABLES}})
        library=FieldLibrary(self.m.store)
        for field in library.items():
            if field['status']=='confirmed':
                continue
            saved=library.save(field['id'],{'expected_version':field['version'],'label':field['name'],
                'definition':'测试登记人数的筛选范围和汇总字段','basis':'Synthetic test fixture'},'demo-platform')
            library.save(field['id'],{'expected_version':saved['version'],'confirmation_note':'Fixture reviewed'},
                         'demo-platform',confirm=True)
        cases=((DIABETES_COUNTS,[],[]), (DORIS_PEOPLE,['采用哪项登记范围？'],['filter.summary_scope']))
        for table,questions,keys in cases:
            with self.subTest(table=table):
                scopes=SUMMARY_TABLES[table][1]
                intent=self.intent(selection='count_people',entity=table,outputs=[],filters=[],order_by=[],
                    limit=1,questions=questions,question_keys=keys)
                flow=self.ask(intent,'请汇总已登记数据集的去重人员数量')
                self.assertEqual(flow['state'],'awaiting_chain_confirmation')
                binding=flow['field_bindings'][0]
                self.assertEqual([option['id'] for option in binding['options']],[key for key,_ in scopes])
                with patch('management.intake.semantic_query.interpret',side_effect=AssertionError('范围已经选择')):
                    ready=self.post('/api/portal/queries/'+flow['id']+'/clarify',{'expected_version':flow['version'],
                        'answers':[{'intent_id':binding['intent_id'],'option_id':scopes[-1][0]}]})['flow']
                self.assertEqual(ready['state'],'waiting_manual_spec_confirmation')
                self.assertEqual(ready['professional_preview']['filters'][0]['value'],scopes[-1][0])
                unmatched={**intent,'questions':[],'filters':[
                    {'field':table+'.cohort','op':'=','value':'模型未对应的范围'}]}
                recoverable=self.ask(unmatched,'请汇总已登记数据集的去重人员数量')
                self.assertIn('与登记口径未能对应',recoverable['user_guidance'])
                self.assertEqual(recoverable['state'],'awaiting_chain_confirmation')
                self.assertIsNone(recoverable['query_draft'])
                extra={**intent,'questions':[],'filters':[
                    {'field':table+'.cohort','op':'=','value':scopes[0][0]},
                    {'field':'observations.observed_at','op':'>=','value':'2025-01-01'}]}
                blocked=self.ask(extra,'统计 2025 年以来的去重人数')
                self.assertEqual(blocked['state'],'attention')
                self.assertEqual(blocked['field_bindings'],[])
        self.assertFalse(self.e.store.list('job'))

    def test_diabetes_count_variants_keep_the_scope_and_operation(self):
        catalog=[{'name':DIABETES_COUNTS,'source':'doris-nju',
                  'fields':[{'name':name,'status':'confirmed'} for name in ('cohort','patient_count')]}]
        for text in ('糖尿病的患者有多少人','一共有几个人患有糖尿病？','帮我查一下得糖尿病的病人总人数'):
            with self.subTest(text=text):
                _,options=diabetes_question(text,catalog)
                self.assertEqual([option['id'] for option in options],
                                 ['diabetes_all','diabetes_type2','diabetes_type1'])
        _,options=diabetes_question('Doris 中的２型糖尿病患者有多少位？',catalog)
        self.assertEqual([option['id'] for option in options],['diabetes_type2'])
        for text in ('去年糖尿病的患者有多少人','糖尿病的女性患者有多少人','模拟 HIS 的糖尿病有多少人',
                     '糖尿病患者人数和年龄'):
            with self.subTest(text=text):
                self.assertEqual(diabetes_question(text,catalog),(None,[]))

    def test_follow_up_keeps_distinct_questions_and_prior_choices(self):
        draft=self.intent(questions=['先确认组别。'],question_keys=['filter.group'],choices=[['A 组','B 组']])
        flow=self.ask(draft)
        follow=self.intent(questions=['先确认组别？','再选择日期范围。'],
            question_keys=['filter.group','filter.date'],choices=[['A 组','B 组'],['1 月 3 日之前']])
        url='/api/portal/queries/'+flow['id']
        with patch('management.intake.semantic_query.interpret',return_value=follow):
            revised=self.post(url+'/clarify',{'expected_version':flow['version'],
                'answers':[{'intent_id':'operation-0','option_id':'choice-0'}]})['flow']
        self.assertEqual(revised['clarification_questions'],['再选择日期范围。'])
        self.assertEqual(revised['field_bindings'][0]['options'][0]['label'],'1 月 3 日之前')
        with patch('management.intake.semantic_query.interpret',return_value=self.intent()) as model:
            ready=self.post(url+'/clarify',{'expected_version':revised['version'],
                'answers':[{'intent_id':'operation-0','option_id':'choice-0'}]})['flow']
        self.assertEqual(ready['state'],'waiting_manual_spec_confirmation')
        self.assertEqual([item['answer'] for item in model.call_args.kwargs['clarifications']],['A 组','1 月 3 日之前'])

    def test_finds_intermediate_table_instead_of_asking_for_a_join_key(self):
        db=sqlite3.connect(self.data/'his.db')
        db.executescript("""
        CREATE TABLE example_patient (patient_id TEXT PRIMARY KEY, birth_year INTEGER);
        CREATE TABLE example_visit (encounter_key TEXT PRIMARY KEY, person_key TEXT REFERENCES example_patient(patient_id));
        CREATE TABLE example_diagnosis (code TEXT, encounter_key TEXT REFERENCES example_visit(encounter_key));
        INSERT INTO example_patient VALUES ('known', 1980), ('other', 1990);
        INSERT INTO example_visit VALUES ('first','known'), ('second','other');
        INSERT INTO example_diagnosis VALUES ('E11.9','first'), ('I10','second');
        """)
        db.close()
        self.m.catalog.ingest('his',SQLiteSource(self.data/'his.db').snapshot())
        library=FieldLibrary(self.m.store)
        for field in library.items():
            if field['status']=='confirmed':
                continue
            item=self.m.store.require('object',field['id'])
            self.m.catalog.annotate(field['id'],{'expected_version':item['version'],'classification':'internal'},'demo-platform')
            draft=library.save(field['id'],{'expected_version':field['version'],'label':field['name'],
                'definition':'模拟中间表关联','basis':'测试源声明的外键'},'demo-platform')
            library.save(field['id'],{'expected_version':draft['version'],'confirmation_note':'已验证'},'demo-platform',confirm=True)
        intent=self.intent(entity='example_diagnosis',outputs=['example_patient.birth_year'],
            filters=[{'field':'example_diagnosis.code','op':'=','value':'E11.9'}],order_by=[],
            questions=['缺少已确认的患者关联，请提供关联键'])
        flow=self.ask(intent,'查询诊断编码 E11.9 的患者出生年份')
        self.assertEqual(flow['state'],'waiting_manual_spec_confirmation')
        self.assertFalse(flow['field_bindings'])
        links=flow['professional_preview']['join_path']
        self.assertEqual([(edge['from_table'],edge['to_table']) for edge in links],
            [('example_diagnosis','example_visit'),('example_visit','example_patient')])
        url='/api/portal/queries/'+flow['id']
        submitted=self.post(url+'/execute',{'confirmed':True,'expected_version':flow['version']})['flow']
        job=self.e.run(self.e.store.require('job',submitted['job_id']))
        self.assertEqual(job['state'],'success',job.get('error'))
        self.client.get(url,headers={'X-Demo-Role':'platform'})
        self.assertEqual(self.post(url+'/result',{})['result']['rows'],[[1980]])
        db=sqlite3.connect(self.data/'his.db')
        db.execute("INSERT INTO example_diagnosis VALUES ('E11.9','first')")
        db.commit()
        db.close()
        intent={**intent,'selection':'count_people','outputs':[],
                'summary':'统计匹配诊断的去重人数','limit':1}
        flow=self.ask(intent,'诊断编码 E11.9 有多少人？')
        self.assertEqual(flow['state'],'waiting_manual_spec_confirmation')
        self.assertEqual(len(flow['professional_preview']['join_path']),2)
        url='/api/portal/queries/'+flow['id']
        submitted=self.post(url+'/execute',{'confirmed':True,'expected_version':flow['version']})['flow']
        job=self.e.run(self.e.store.require('job',submitted['job_id']))
        self.assertEqual(job['state'],'success',job.get('error'))
        self.client.get(url,headers={'X-Demo-Role':'platform'})
        self.assertEqual(self.post(url+'/result',{})['result']['rows'],[[1]])

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
