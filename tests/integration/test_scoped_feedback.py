"""Service feedback is tied to an owned run and an actually invoked pinned node."""
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app
from platform_core.storage import DomainError


class ScopedFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name,start_runtime=False)
        self.runtime=self.app.state.runtime
        self.m=self.runtime.management.store
        self.e=self.runtime.execution.store
        self.client=TestClient(self.app)
        self.client.__enter__()
        self.ratings={key:4 for key in ('usability','explainability','timeliness','satisfaction')}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.tmp.cleanup()

    def save(self,store,kind,oid,data):
        previous=store.get(kind,oid)
        return store.save(kind,oid,data,previous['version'] if previous else 0,'fixture','Service feedback boundary test')

    def fixture(self,actor='demo-requester',status='failed'):
        self.save(self.m,'service','SVC-OWN',{'name':'目录覆盖核验','provider':'demo-provider'})
        self.save(self.m,'service','SVC-OTHER',{'name':'另一机构服务','provider':'other-provider'})
        demand=self.save(self.m,'demand','REQ-SCOPED',{'title':'研究目录范围核验','purpose':'立项核验',
            'created_by':actor,'state':'draft','service_ids':['SVC-OWN','SVC-OTHER']})
        spec=self.save(self.m,'spec','SPEC-SCOPED',{'demand_id':demand['id'],'demand_version':demand['version']})
        self.save(self.m,'demand',demand['id'],{**demand,'state':'confirmed','spec_id':spec['id'],'spec_version':spec['version']})
        plan=self.save(self.e,'plan','PLAN-SCOPED',{'name':'目录核验执行方案','created_by':actor,
            'spec_id':spec['id'],'spec_version':spec['version'],'service_versions':{'SVC-OWN':1,'SVC-OTHER':1},
            'order':['own','other'],'purpose':'立项核验','nodes':[{'id':'own','operation':'service','service_id':'SVC-OWN','retries':0},
                     {'id':'other','operation':'service','service_id':'SVC-OTHER','retries':0}]})
        self.save(self.e,'plan_state',plan['id'],{'plan_version':1,'status':'confirmed'})
        nodes={'own':{'status':'success' if status=='success' else 'blocked'},'other':{'status':'blocked'}}
        self.save(self.e,'job','JOB-SCOPED',{'actor':actor,'plan_id':plan['id'],'plan_version':1,'state':status,
            'nodes':nodes,'timeline':[{'stage':'node_started','node':'own'},
                                     {'stage':'service_invoked','node':'own','service_id':'SVC-OWN'}]})
        self.save(self.e,'run','JOB-SCOPED',{'actor':actor,'object_kind':'plan','object_id':plan['id'],
            'object_version':1,'plan_id':plan['id'],'plan_version':1,'status':status,'nodes':nodes})
        return plan

    def post(self,service_id='SVC-OWN',role='requester',comment='目录更新情况已核对',run_id='JOB-SCOPED'):
        body={'run_id':run_id,'ratings':self.ratings,'comment':comment}
        if service_id is not None:body['service_id']=service_id
        return self.client.post('/api/management/feedback',json=body,headers={'X-Demo-Role':role})

    def test_invoked_failed_service_feedback_is_visible_only_in_its_provider_projection(self):
        self.fixture()
        overall=self.post(None,comment='PRIVATE-WHOLE-TASK')
        self.assertEqual(overall.status_code,200,overall.text)
        scoped=self.post(comment='本院目录服务需要补充更新说明')
        self.assertEqual(scoped.status_code,200,scoped.text)
        self.assertEqual(scoped.json()['service_id'],'SVC-OWN')
        self.assertEqual(scoped.json()['service_version'],1)
        provider=self.client.get('/api/management/work-orders/REQ-SCOPED',headers={'X-Demo-Role':'provider'})
        self.assertEqual(provider.status_code,200,provider.text)
        self.assertEqual([item['id'] for item in provider.json()['feedback']],[scoped.json()['id']])
        self.assertNotIn('PRIVATE-WHOLE-TASK',provider.text)

    def test_unknown_and_unstarted_services_are_rejected_without_saving(self):
        self.fixture()
        for sid in ('SVC-UNKNOWN','SVC-OTHER','   '):
            with self.subTest(service=sid):
                response=self.post(sid)
                self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.m.list('feedback'),[])

    def test_scope_uses_fixed_run_version_even_after_plan_and_service_changes(self):
        plan=self.fixture(status='success')
        service=self.m.require('service','SVC-OWN')
        self.save(self.m,'service','SVC-OWN',{**service,'name':'目录覆盖核验新版'})
        self.save(self.m,'service','SVC-NEW',{'name':'后续新增服务','provider':'demo-provider'})
        self.save(self.e,'plan',plan['id'],{**plan,'service_versions':{'SVC-OWN':2,'SVC-NEW':1},
            'nodes':[{'id':'new','operation':'service','service_id':'SVC-NEW'}]})
        accepted=self.post('SVC-OWN')
        self.assertEqual(accepted.status_code,200,accepted.text)
        self.assertEqual(accepted.json()['service_version'],1)
        rejected=self.post('SVC-NEW')
        self.assertEqual(rejected.status_code,422,rejected.text)
        self.assertEqual(len(self.m.list('feedback')),1)

    def test_foreign_run_and_provider_or_auditor_submission_are_rejected(self):
        self.fixture(actor='another-requester')
        for role in ('requester','provider','auditor'):
            response=self.post(role=role)
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(self.m.list('feedback'),[])

    def test_unscoped_legacy_evaluation_keeps_original_contract(self):
        self.fixture()
        response=self.post(None)
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('service_id',response.json())
        self.assertNotIn('service_version',response.json())
        self.assertEqual(response.json()['ratings'],self.ratings)

    def test_nonterminal_runs_and_malformed_scope_cannot_be_rated(self):
        self.fixture(status='running')
        self.assertEqual(self.post().status_code,422)
        run=self.e.require('run','JOB-SCOPED')
        self.save(self.e,'run',run['id'],{**run,'status':'failed'})
        for sid in ([],{},1):
            self.assertEqual(self.post(sid).status_code,422)
        self.assertEqual(self.m.list('feedback'),[])

    def test_timeline_from_an_unrelated_job_cannot_authorize_service_feedback(self):
        self.fixture()
        job=self.e.require('job','JOB-SCOPED')
        self.save(self.e,'job',job['id'],{**job,'plan_version':999})
        response=self.post()
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.m.list('feedback'),[])

    def test_node_started_without_dispatch_does_not_allow_scoped_feedback(self):
        self.fixture()
        job=self.e.require('job','JOB-SCOPED')
        for timeline in ([{'stage':'node_started','node':'own'}],
                         [{'stage':'service_invoked','node':'own','service_id':'SVC-OTHER'}],
                         [{'stage':'service_invoked','node':'other','service_id':'SVC-OWN'}]):
            self.save(self.e,'job',job['id'],{**job,'timeline':timeline})
            response=self.post()
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.m.list('feedback'),[])
        self.assertEqual(self.post(None).status_code,200)

    def test_attempt_authorization_rejection_before_dispatch_cannot_be_rated_as_service_call(self):
        self.fixture()
        execution=self.runtime.execution
        job=self.save(self.e,'job','JOB-ATTEMPT-DENIED',{'actor':'demo-requester','plan_id':'PLAN-SCOPED',
            'plan_version':1,'state':'queued','grant_id':'GRANT-REVOKED','nodes':{},'timeline':[]})
        # Exercise the real executor between node entry and dispatch. Its second
        # guard rejects authorization before the service handler can be called.
        with patch.object(execution,'guard',side_effect=[{},DomainError('授权已撤销',403)]), \
             patch.object(self.runtime,'_dispatch') as dispatch:
            result=execution.run(job)
            dispatch.assert_not_called()
        self.assertEqual(result['state'],'failed')
        self.assertTrue(any(item['stage']=='node_started' for item in result['timeline']))
        self.assertFalse(any(item['stage']=='service_invoked' for item in result['timeline']))
        response=self.post(run_id=job['id'])
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.post(None,run_id=job['id']).status_code,200)


if __name__=='__main__':unittest.main()
