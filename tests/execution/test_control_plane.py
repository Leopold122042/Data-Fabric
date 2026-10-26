import json,sqlite3,tempfile,time,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app
from platform_core.storage import DomainError
from management.catalog.sources import SQLiteSource
import medsim


def expiry(seconds=3600):return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,start_runtime=False)
        self.e=self.app.state.runtime.execution;self.m=self.e.management
    def tearDown(self):self.e.close();self.tmp.cleanup()
    def medical(self):
        p=self.e.plan({'scenario':'medical'},'demo-platform');self.e.confirm(p['id'],p['version'],'demo-platform');return p
    def grant(self,p,**extra):
        return self.e.security.grant({'subject':'demo-platform','purpose':p['purpose'],'plan_id':p['id'],'plan_version':p['version'],'expires_at':expiry(),**extra},'demo-platform')
    def execute(self,p,g):return self.e.run(self.e.submit(p['id'],p['version'],g['id'],'demo-platform'))
    def spec(self):
        today=datetime.now(timezone.utc)
        d=self.m.intake.save({'title':'需求','object_scope':'当前本地元数据','time_start':(today-timedelta(days=1)).isoformat(),'time_end':(today+timedelta(days=2)).isoformat(),'purpose':'research','quality':'完整','deadline':(today+timedelta(days=2)).isoformat(),'delivery':'对象级',
            'execution_constraints':{'scope':{'kind':'metadata'},'output_mode':'object','circulation_modes':['controlled_compute'],'quality_min_complete':1}},'demo-platform')
        return self.m.intake.confirm(d['id'],{'expected_version':d['version'],'confirmations':{'content':True,'attachments':True,'gaps':True,'execution_constraints':True},'note':'已核对'},'demo-platform')
    def service_plan(self,**extras):
        spec=self.spec()
        return self.e.plan({'spec_id':spec['id'],'spec_version':spec['version'],'nodes':[
            {'id':'one','operation':'service','service_id':'SVC-META-CATALOG','retries':1},
            {'id':'two','operation':'service','service_id':'SVC-META-CATALOG','inputs':{'previous':'one'}}],**extras},'demo-platform')
    def test_medical_only_permitted_group_and_receipt(self):
        p=self.medical();g=self.grant(p);job=self.execute(p,g)
        self.assertEqual(job['state'],'success',job)
        d=self.e.delivery.read(job['delivery_id'],'demo-platform',p)
        self.assertEqual(d['result']['rows'],[['40—49岁','类别甲',5]])
        self.assertEqual(len(d['receipt']['checks']),6);self.assertTrue(all(d['receipt']['checks'].values()))
        self.assertTrue(self.e.delivery.verify(d['receipt'],d['signature'])['valid'])
        changed={**d['receipt'],'plan_version':999};self.assertFalse(self.e.delivery.verify(changed,d['signature'])['valid'])
        evidence=self.m.evidence.get(job['id']);self.assertTrue(evidence['delivered']);self.assertEqual(evidence['object_version'],p['version'])
        self.assertTrue(self.m.reconciliation.assess(evidence)[0])
    def test_expiry_and_detail_denied_before_handler(self):
        p=self.medical();g=self.grant(p,expires_at=expiry(-1))
        with patch('execution.control_plane.aggregate') as call:
            with self.assertRaisesRegex(DomainError,'到期'):self.execute(p,g)
            call.assert_not_called()
        with self.assertRaisesRegex(DomainError,'仅允许汇总'):self.e.plan({'scenario':'medical','output_mode':'object'},'demo-platform')
        self.assertFalse(self.e.store.list('decision')[-1]['allowed'])
    def test_revoke_after_node_before_delivery_blocks(self):
        p=self.medical();g=self.grant(p)
        def node(*args):
            self.e.security.revoke(g['id'],'demo-platform')
            return {'columns':['count'],'rows':[[5]]}
        with patch('execution.control_plane.aggregate',side_effect=node):job=self.execute(p,g)
        self.assertEqual(job['state'],'failed');self.assertIsNone(job['delivery_id']);self.assertEqual(self.e.store.list('delivery'),[])
    def test_retrieval_rechecks_revocation_and_destroy_removes_history(self):
        p=self.medical();g=self.grant(p);job=self.execute(p,g);oid=job['delivery_id']
        self.e.security.revoke(g['id'],'demo-platform')
        with self.assertRaises(DomainError):self.e.delivery.read(oid,'demo-platform',p)
        self.e.delivery.destroy(oid,'demo-platform')
        self.assertTrue(all(x.get('result') is None for x in self.e.store.history('delivery',oid)))
    def test_six_independent_delivery_gates(self):
        from execution.medical import aggregate
        p=self.medical();g=self.grant(p);result=aggregate()
        cases=[('format',{'columns':['count'],'rows':[['x',5]]},p,g,True),
               ('quality',result,p,g,False),('authorization',result,p,{**g,'status':'revoked'},True),
               ('sensitive_information',{'columns':['patient_id','count'],'rows':[['secret',5]]},p,g,True),
               ('granularity',{**result,'rows':[['40—49岁','类别甲',3]]},p,g,True),
               ('subsequent_purpose',result,{**p,'purpose':'other'},g,True)]
        for key,r,plan,grant,quality in cases:
            with self.subTest(key=key):self.assertFalse(self.e.delivery.checks(r,plan,grant,quality)[key])
    def test_paused_plan_and_idempotent_control(self):
        p=self.medical();g=self.grant(p);command={'command_id':'CMD-test','action':'pause','object_id':p['id']}
        a=self.e.control(command);b=self.e.control(command);self.assertEqual(a,b)
        with self.assertRaises(DomainError):self.execute(p,g)
    def test_new_plan_version_invalidates_old_authorization(self):
        p=self.medical();g=self.grant(p)
        self.e.plan({'id':p['id'],'expected_version':p['version'],'scenario':'medical'},'demo-platform')
        with self.assertRaises(DomainError):self.execute(p,g)
    def test_demand_change_blocks_prior_spec(self):
        p=self.service_plan();self.e.confirm(p['id'],p['version'],'demo-platform');self.e.current(p)
        spec=self.m.store.require('spec',p['spec_id']);d=self.m.store.require('demand',spec['demand_id'])
        self.m.intake.save({**d,'purpose':'changed','expected_version':d['version']},'demo-platform')
        with self.assertRaisesRegex(DomainError,'规格'):self.e.current(p)
        self.e.consume_events();self.assertEqual(self.e.store.require('plan_state',p['id'])['status'],'invalidated')
    def test_dag_retry_inputs_and_blocked_downstream(self):
        p=self.service_plan();self.e.confirm(p['id'],p['version'],'demo-platform');g=self.grant(p)
        result={'sources':[{'id':'test','name':'演示目录','tables':2}]}
        with patch.object(self.e.runtime,'_dispatch',side_effect=[RuntimeError('temporary'),result,result]) as handler:
            job=self.execute(p,g)
        self.assertEqual(job['state'],'success',job);self.assertEqual(handler.call_count,3)
        self.assertEqual(handler.call_args.args[1]['previous'],{'columns':['source','source_name','table_count'],'rows':[['test','演示目录',2]]})
        with patch.object(self.e.runtime,'_dispatch',side_effect=RuntimeError('permanent')) as handler:
            failed=self.execute(p,g)
        self.assertEqual(handler.call_count,2);self.assertEqual(failed['nodes']['two']['status'],'blocked')
    def test_cycle_rejected(self):
        with self.assertRaisesRegex(DomainError,'依赖环'):
            self.service_plan(nodes=[{'id':'a','operation':'service','service_id':'SVC-META-CATALOG','depends_on':['b']},{'id':'b','operation':'service','service_id':'SVC-META-CATALOG','depends_on':['a']}])
    def test_local_fix_queues_real_retry(self):
        p=self.medical();g=self.grant(p)
        with patch('execution.control_plane.aggregate',side_effect=DomainError('failure')):failed=self.execute(p,g)
        command=self.e.control({'command_id':'fix','action':'local_fix','object_id':p['id']})
        self.assertEqual(command['status'],'queued');self.assertEqual(self.e.store.require('job',command['job_id'])['retry_of'],failed['id'])
    def test_worker_processes_persistent_queue(self):
        p=self.medical();g=self.grant(p);job=self.e.submit(p['id'],p['version'],g['id'],'demo-platform');self.e.start()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            current=self.e.store.require('job',job['id'])
            if current['state'] in ('success','failed'):break
            time.sleep(.05)
        self.assertEqual(current['state'],'success',current)
    def test_role_boundary_and_legacy_identity_spoof(self):
        with TestClient(self.app) as c:
            p=c.post('/api/execution/plans',json={'scenario':'medical'}).json()
            self.assertEqual(c.post('/api/execution/grants',json={},headers={'X-Demo-Role':'requester'}).status_code,403)
            self.assertEqual(c.post('/api/services/SVC-PATIENT-360/invoke',json={'actor':{'role':'clinician'}},headers={'X-Demo-Role':'requester'}).status_code,403)
            self.assertEqual(c.get('/api/patients',headers={'X-Demo-Role':'requester'}).status_code,403)
            self.assertEqual(c.get('/api/execution/items/plan',headers={'X-Demo-Role':'requester'}).json()['items'],[])
            self.assertEqual(c.post('/api/execution/plans',json=[],headers={'X-Demo-Role':'platform'}).status_code,422)
    def test_contract_is_checked_at_each_stage(self):
        p=self.medical();contract=self.e.store.save('contract','contract',{'plan_id':p['id'],'plan_version':p['version'],'parties':['a','b'],'accepted':['a'],'expires_at':expiry()},0)
        g=self.grant(p,contract_id=contract['id'])
        with self.assertRaisesRegex(DomainError,'全部确认'):self.execute(p,g)
    def test_trigger_uses_current_authorization(self):
        p=self.medical();g=self.grant(p);t=self.e.trigger({'plan_id':p['id'],'plan_version':p['version'],'grant_id':g['id'],'kind':'interval','interval':5},'demo-platform')
        self.e.update('trigger',t,{'next_at':0});self.e.security.revoke(g['id'],'demo-platform');self.e.tick_triggers()
        self.assertFalse(self.e.store.require('trigger',t['id'])['enabled']);self.assertEqual(self.e.store.list('job'),[])

    def test_federated_query_pushdown_cache_and_schema_invalidation(self):
        root=Path(self.tmp.name)/'data';root.mkdir()
        with patch.object(medsim,'DATA_DIR',str(root)):
            for sid in ['his','lis']:
                con=sqlite3.connect(root/(sid+'.db'));con.execute('CREATE TABLE example(code INTEGER,region TEXT)');con.executemany('INSERT INTO example VALUES(?,?)',[(i,'A' if i<6 else 'B') for i in range(8)]);con.commit();con.close()
                self.m.catalog.ingest(sid,SQLiteSource(root/(sid+'.db')).snapshot())
            fields=self.m.catalog.objects(kind='field');pick=lambda sid,name:next(f['id'] for f in fields if f['source_id']==sid and f['name']==name)
            view=self.e.queries.define({'fields':[pick('his','code'),pick('his','region'),pick('lis','code')],'joins':[[pick('his','code'),pick('lis','code')]],'group_by':[pick('his','region')],'filters':[{'field':pick('his','code'),'op':'>=','value':0}]},'demo-platform')
            result,trace=self.e.queries.execute(view,'aggregate');self.assertEqual(result['rows'],[['A',6]])
            self.assertEqual(trace['sources'][0]['pushed_filters'],1)
            cached,trace=self.e.queries.execute(view,'aggregate');self.assertTrue(trace['cache_hit']);self.assertEqual(cached,result)
            obj=self.m.store.require('object',pick('his','region'));self.m.catalog.annotate(obj['id'],{'meaning':'changed','expected_version':obj['version']},'a')
            with self.assertRaises(DomainError):self.e.queries.execute(view,'aggregate')

    def test_revocation_observed_under_three_seconds(self):
        import threading
        p=self.medical();g=self.grant(p);entered=threading.Event();release=threading.Event()
        def slow_node(*args):
            entered.set();release.wait(4);return {'columns':['count'],'rows':[[5]]}
        with patch('execution.control_plane.aggregate',side_effect=slow_node):
            job=self.e.submit(p['id'],p['version'],g['id'],'demo-platform');self.e.start()
            self.assertTrue(entered.wait(2));started=time.monotonic();self.e.security.revoke(g['id'],'demo-platform')
            while time.monotonic()-started<2.8:
                current=self.e.store.require('job',job['id'])
                if current.get('blocked_observed_at'):break
                time.sleep(.03)
            self.assertTrue(current.get('blocked_observed_at'),current);self.assertLess(time.monotonic()-started,3)
            release.set()
        self.assertEqual(self.e.store.list('delivery'),[])
    def test_receipt_key_persists_and_expiry_removes_local_copy(self):
        p=self.medical();g=self.grant(p);job=self.execute(p,g);d=self.e.store.require('delivery',job['delivery_id'])
        from execution.delivery import Delivery
        restarted=Delivery(self.e.store,self.e.security);self.assertTrue(restarted.verify(d['receipt'],d['signature'])['valid'])
        self.e.update('delivery',d,{'receipt':{**d['receipt'],'expires_at':expiry(-1)}});self.e.cleanup()
        self.assertIsNone(self.e.store.require('delivery',d['id'])['result'])
    def test_other_requester_cannot_use_platform_spec(self):
        spec=self.spec()
        with self.assertRaisesRegex(DomainError,'他人'):
            self.e.plan({'spec_id':spec['id'],'nodes':[{'id':'x','operation':'service','service_id':'SVC-META-CATALOG'}]},'demo-requester')
    def test_missing_version_and_cross_site_are_rejected(self):
        p=self.medical();g=self.grant(p)
        with self.assertRaisesRegex(DomainError,'指定方案版本'):self.e.submit(p['id'],None,g['id'],'demo-platform')
        with TestClient(self.app) as c:
            self.assertEqual(c.post('/api/execution/plans',headers={'Origin':'https://foreign.invalid'},json={'scenario':'medical'}).status_code,403)
    def test_delivery_cannot_bypass_authorization_with_cached_result(self):
        p=self.medical();g=self.grant(p);job=self.execute(p,g);d=self.e.store.require('delivery',job['delivery_id'])
        self.e.security.revoke(g['id'],'demo-platform')
        with self.assertRaisesRegex(DomainError,'撤销'):
            self.e.delivery.create(d['result'],p,g['id'],'demo-platform','new-run')
    def test_confirmed_plan_composition_uses_real_success_evidence(self):
        p=self.medical();g=self.grant(p)
        with self.assertRaisesRegex(DomainError,'三次'):
            self.m.services.compose({'name':'组合','plan_id':p['id'],'plan_version':p['version']},'demo-platform')
        for _ in range(3):self.execute(p,g)
        service=self.m.services.compose({'name':'组合','plan_id':p['id'],'plan_version':p['version']},'demo-platform')
        self.assertEqual(len(service['composition']['evidence_ids']),3)
    def test_trigger_ignores_pre_registration_metadata_events(self):
        p=self.medical();g=self.grant(p)
        t=self.e.trigger({'kind':'metadata_change','plan_id':p['id'],'plan_version':p['version'],'grant_id':g['id']},'demo-platform')
        self.e.tick_triggers();self.assertEqual(self.e.store.list('job'),[])
        self.m.catalog.ingest('test',{'tables':{'t':{'columns':['x']}}});self.e.tick_triggers()
        self.assertEqual(len(self.e.store.list('job')),1)

    def test_delivery_modes_and_download_recheck(self):
        with TestClient(self.app) as c:
            for mode in ('controlled_compute','api','download'):
                p=self.e.plan({'scenario':'medical','circulation_mode':mode},'demo-platform');self.e.confirm(p['id'],p['version'],'demo-platform');g=self.grant(p);job=self.execute(p,g)
                oid=job['delivery_id'];r=c.get('/api/execution/deliveries/'+oid)
                self.assertEqual(r.status_code,200)
                if mode=='api':
                    self.assertNotIn('result',r.json());self.assertIn('reference',r.json())
                    self.assertEqual(c.get(r.json()['reference']).json()['rows'],[['40—49岁','类别甲',5]])
                download=c.get('/api/execution/deliveries/'+oid+'/download')
                self.assertEqual(download.status_code,200 if mode=='download' else 403)
                self.e.security.revoke(g['id'],'demo-platform')
                self.assertEqual(c.get('/api/execution/deliveries/'+oid).status_code,403)
    def test_external_queries_leave_unpaired_evidence_without_sql_or_rows(self):
        from types import SimpleNamespace
        fake=SimpleNamespace(query=lambda sql:{'columns':['x'],'rows':[['private-value']]})
        with TestClient(self.app) as c,patch('external_api.session',return_value=fake):
            r=c.post('/api/external/query',headers={'X-Medfabric-External':'1'},json={'sql':"SELECT 'private-value'"})
            self.assertEqual(r.status_code,403)
            self.assertNotIn('private-value',r.text)
        evidence=self.e.evidence.list()[0]
        self.assertEqual(evidence['object_version'],'unversioned-external')
        self.assertNotIn('private-value',json.dumps(evidence));self.assertIn('unpaired',self.m.reconciliation.assess(evidence)[1])

    def test_structured_mapping_compiler_and_concept_change(self):
        root=Path(self.tmp.name)/'data';root.mkdir()
        with patch.object(medsim,'DATA_DIR',str(root)):
            db=sqlite3.connect(root/'his.db');db.execute('CREATE TABLE metric(value INTEGER)');db.executemany('INSERT INTO metric VALUES(?)',[(2,)]*6);db.commit();db.close()
            self.m.catalog.ingest('his',SQLiteSource(root/'his.db').snapshot())
            field=self.m.catalog.objects(kind='field')[0]
            concept=self.m.semantic.concept({'name':'doubled','meaning':'twice value','domain':'test','data_type':'real'},'demo-platform')
            self.m.semantic.mapping({'concept_id':concept['id'],'fields':[field['id']],'mapping_type':'derived','expression':{'operator':'multiply','args':[{'field':field['id']},{'literal':2}]},'status':'confirmed','confirmation_note':'checked'},'demo-platform')
            view=self.e.queries.define({'concept_ids':[concept['id']],'group_by':[concept['id']]},'demo-platform')
            result,_=self.e.queries.execute(view,'aggregate');self.assertEqual(result['rows'],[[4,6]])
            self.m.semantic.concept({**concept,'meaning':'changed','expected_version':concept['version']},'demo-platform')
            with self.assertRaises(DomainError):self.e.queries.execute(view,'aggregate')
    def test_expression_rejects_raw_code_and_unknown_identifiers(self):
        from execution.query.expressions import compile_expression
        for expr in ["__import__('os')",{'operator':'eval','args':[]},{'field':'secret'}]:
            with self.assertRaises(DomainError):compile_expression(expr,{},[])

    def test_medical_detail_rejection_has_own_audit_and_zero_calls(self):
        p=self.medical()
        with TestClient(self.app) as c,patch('execution.control_plane.aggregate') as handler:
            response=c.post('/api/execution/plans/'+p['id']+'/detail-export',json={})
            self.assertEqual(response.status_code,403);self.assertEqual(response.json()['error'],'仅允许汇总查询');handler.assert_not_called()
        audit=self.e.store.list('decision')[-1];self.assertFalse(audit['delivered']);self.assertEqual(audit['backend_calls'],0)
