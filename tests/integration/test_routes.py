import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app
import medsim

class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,start_runtime=False)
        self.client=TestClient(self.app);self.client.__enter__()
    def tearDown(self):self.client.__exit__(None,None,None);self.tmp.cleanup()

    def test_routes_unique_and_legacy_shell_preserved(self):
        routes=[(r.path,tuple(sorted(r.methods))) for r in self.app.routes if hasattr(r,'methods')]
        self.assertEqual(len(routes),len(set(routes)))
        for path in ['/api/health','/api/services','/api/data-services','/api/security/overview','/api/management/status',
                     '/static/management/catalog.html','/static/management/intake.html','/']:
            self.assertEqual(self.client.get(path).status_code,200,path)

    def test_desktop_external_routes_and_saved_catalog_survive_split(self):
        headers={'X-Medfabric-External':'1'}
        with patch('external_discovery.definitions',return_value=[{'id':'saved-table'}]), patch('external_discovery.collections',return_value=[]):
            r=self.client.get('/api/external/discovery/catalog',headers=headers)
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['tables'][0]['id'],'saved-table')
            self.assertEqual(self.client.get('/api/external/discovery/catalog').status_code,403)
        self.assertIn('/static/platform/redirect.js',self.client.get('/').text)
        html=self.client.get('/static/platform/connections.html').text
        self.assertIn('id="tab-external"',html)
        self.assertIn('/static/external.js',html)
        self.assertIn('/static/shared/platform-shell.js',html)
        self.assertNotIn('A 管理工作台',html)
        self.assertNotIn('B 执行与交付',html)
        self.assertIn('/static/platform/connections.html',self.client.get('/static/platform/redirect.js').text)

    def test_no_llm_fake_response_and_auditor_cannot_write(self):
        self.assertEqual(self.client.post('/api/management/intake/understand').status_code,503)
        r=self.client.post('/api/management/concepts',headers={'X-Demo-Role':'auditor'},json={'name':'x'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.client.post('/api/management/concepts',headers={'Origin':'https://other.example'},json={}).status_code,403)

    def test_legacy_registration_cannot_bypass_publication(self):
        r=self.client.post('/api/data-services/register',json={'id':'SVC-NEW','name':'test','lifecycle':'published'})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['service']['lifecycle'],'draft')
        self.assertEqual(self.client.post('/api/data-services/register',json={'id':'SVC-NEW','name':'replace'}).status_code,409)

    def test_other_role_cannot_read_private_demand_history(self):
        d=self.client.post('/api/management/demands',json={'title':'private'},headers={'X-Demo-Role':'requester'}).json()
        r=self.client.get('/api/management/items/demand',headers={'X-Demo-Role':'provider'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.client.get('/api/management/items/demand/'+d['id']+'/history',headers={'X-Demo-Role':'provider'}).status_code,403)

    def test_store_restart_retains_draft_and_history(self):
        r=self.client.post('/api/management/concepts',json={'name':'age','meaning':'years','domain':'demo','data_type':'integer'}).json()
        app2=create_app(self.tmp.name,start_runtime=False)
        with TestClient(app2) as c:
            rows=c.get('/api/management/items/concept').json()['items'];self.assertEqual(rows[0]['id'],r['id'])

class StartupTest(unittest.TestCase):
    def test_all_legacy_pages_and_handler_validation_on_isolated_sources(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(medsim,'DATA_DIR',str(Path(tmp)/'data')):
            medsim.generate_base()
            app=create_app(Path(tmp)/'control',simulate=False)
            from platform_core.healthcare_scenarios import seed_healthcare
            from platform_core.scenario_replacement import replace_legacy_scenarios
            def before_monitor(operation):
                def checked(runtime):
                    self.assertFalse(any(t.is_alive() for t in runtime.threads),
                        'Catalog monitor must not recreate retired objects during replacement')
                    return operation(runtime)
                return checked
            with patch('platform_core.healthcare_scenarios.seed_healthcare', side_effect=before_monitor(seed_healthcare)), \
                 patch('platform_core.scenario_replacement.replace_legacy_scenarios', side_effect=before_monitor(replace_legacy_scenarios)), \
                 TestClient(app) as c:
                self.assertTrue(app.state.runtime.demo_scenarios['ready'])
                self.assertTrue(any(t.is_alive() for t in app.state.runtime.threads))
                for path in ['/api/overview','/api/catalog','/api/graph?patients=2','/api/patients?limit=1','/api/search?q=高血压','/api/cockpit','/api/orchestrator']:
                    r=c.get(path);self.assertEqual(r.status_code,200,(path,r.text[:300]))
                items=c.get('/api/management/items/object').json()['items'];self.assertTrue(items)
                r=c.post('/api/services/SVC-META-CATALOG/invoke',json={});self.assertEqual(r.status_code,200);self.assertIsNone(r.json().get('error'))
                # A's publication validation really executes an existing B handler on isolated data.
                source=next(x for x in items if x['kind']=='field')
                payload={'name':'目录查询验证','service_type':'query','mode':'regular','visibility':'internal',
                    'purpose':'research','output':'目录','audience':'platform','conditions':'demo','provider':'demo-provider',
                    'source_scope':'local','update_period':'scan','quality_commitment':'complete','restrictions':'demo only','metering':'calls',
                    'handler_id':'SVC-META-CATALOG','source_objects':[source['id']],
                    'input_schema':{'type':'object'},'output_schema':{'type':'object'},'sample_input':{}}
                svc=c.post('/api/management/services',json=payload).json()
                val=c.post('/api/management/services/'+svc['id']+'/validate',json={})
                self.assertEqual(val.status_code,200,val.text);self.assertTrue(val.json()['passed'],val.text)
                validation=val.json()['id']
                for target in ('review','trial','published'):
                    response=c.post('/api/management/services/'+svc['id']+'/transition',json={'target':target,
                        'expected_version':svc['version'],'reason':'verified','validation_id':validation})
                    self.assertEqual(response.status_code,200,response.text);svc=response.json()
                response=c.post('/api/services/'+svc['id']+'/invoke',json={})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIsNone(response.json().get('error'),response.text)
                self.assertTrue(c.get('/api/management/runs').json()['items'])
            self.assertTrue(app.state.runtime.stop_event.is_set())
            self.assertFalse(app.state.runtime.orch._thread.is_alive())
