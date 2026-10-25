import tempfile,threading,time,unittest,json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app
from platform_core.storage import DomainError
import external_discovery as d

class ExternalGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.registry=patch.object(d,'DIRECTORY',Path(self.tmp.name)/'registry');self.registry.start()
        self.app=create_app(Path(self.tmp.name)/'runtime',start_runtime=False);self.g=self.app.state.runtime.external_governance
        self.current=SimpleNamespace(cloud_credentials=('alice','secret-token'),virtual_lock=threading.RLock(),touched=time.monotonic())
        self.rec={'id':'table1','name':'sample','path':'private.csv','member':None,'sheet':'CSV 原始字段','columns':['region','value'],'types':['VARCHAR','VARCHAR'],'rows':1,'header_row':1,'mapping_mode':'raw'}
        d.save_registry('alice',{'table1':self.rec})
        self.headers={'X-Medfabric-External':'1','X-Demo-Role':'platform'}
    def tearDown(self):self.g.close();self.registry.stop();self.tmp.cleanup()
    def grant(self,**overrides):
        return self.g.grant(self.current,{'table_ids':['table1'],'subject':'demo-platform','purpose':'research','expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),**overrides},'demo-platform')
    def test_scope_identity_purpose_expiry_revoke_schema(self):
        g=self.grant();check=lambda **kw:self.g.check(self.current,g['id'],kw.get('ids',['table1']),kw.get('actor','demo-platform'),kw.get('purpose','research'),'test')
        check()
        for kw in ({'ids':['other']},{'actor':'demo-requester'},{'purpose':'care'}):
            with self.assertRaises(DomainError):check(**kw)
        self.rec['columns'].append('new');d.save_registry('alice',{'table1':self.rec})
        with self.assertRaises(DomainError):check()
        g=self.grant();self.g.revoke(g['id'],'demo-platform')
        with self.assertRaises(DomainError):check()
        g=self.grant();self.g.store.save('external_grant',g['id'],{**g,'expires_at':'2000-01-01T00:00:00+00:00'},g['version'])
        with self.assertRaises(DomainError):check()
    def test_baseline_content_schema_unavailable_and_no_secrets(self):
        def scan(payload):
            with patch('external_sources.fetch',return_value=payload):return self.g.scan(self.current,['table1'],'demo-platform')
        # Read-table cache is prefilled by governance; no second network call.
        first=scan(b'region,value\nA,1\n');self.assertEqual(first['changes'][0]['change_type'],'baseline')
        self.assertEqual(self.g.store.list('external_metadata')[0]['state'],'available')
        self.assertEqual(scan(b'region,value\nA,1\n')['changes'],[])
        grant=self.grant()
        self.assertEqual(scan(b'region,value\nA,2\n')['changes'][0]['change_type'],'content')
        self.assertEqual(scan(b'region,value,added\nA,2,x\n')['changes'][0]['change_type'],'schema')
        self.assertEqual(self.g.store.require('external_grant',grant['id'])['status'],'invalidated')
        with patch('external_sources.fetch',side_effect=RuntimeError('secret-token')):
            self.assertEqual(self.g.scan(self.current,['table1'],'demo-platform')['changes'][0]['change_type'],'unavailable')
        serialized=json.dumps(self.g.store.list('external_metadata')+self.g.store.list('external_change'))
        self.assertNotIn('secret-token',serialized);self.assertNotIn('private.csv',serialized)
    def test_api_denies_before_fetch_and_revocation_before_delivery(self):
        grant=self.grant();headers={**self.headers,'X-External-Grant':grant['id'],'X-External-Purpose':'research'}
        with TestClient(self.app) as c,patch('external_api.session',return_value=self.current),patch('execution.external.routes.session',return_value=self.current),patch('external_discovery.query',return_value={'columns':['value'],'rows':[[1]]}) as query:
            url='/api/external/discovery/query';body={'view_id':'table1'}
            self.assertEqual(c.post(url,headers=self.headers,json=body).status_code,403);query.assert_not_called()
            self.assertEqual(c.post(url,headers=headers,json=body).status_code,200)
            def revoke(*args):self.g.revoke(grant['id'],'demo-platform');return {'columns':['secret'],'rows':[['never-deliver']]}
            query.side_effect=revoke
            r=c.post(url,headers=headers,json=body);self.assertEqual(r.status_code,403);self.assertNotIn('never-deliver',r.text)
            self.assertEqual(c.post('/api/external/query',headers=headers,json={'sql':'SELECT 1'}).status_code,403)
    def test_monitor_ttl_and_owner_binding(self):
        self.g.watch(self.current,['table1'],60,'demo-platform');self.assertTrue(self.g.watch_status(self.current)['enabled'])
        self.current.touched=time.monotonic()-1900;self.g.start()
        for _ in range(30):
            if not self.g.watch_status(self.current)['enabled']:break
            time.sleep(.05)
        self.assertFalse(self.g.watch_status(self.current)['enabled'])
    def test_roles_cannot_issue_grants(self):
        with TestClient(self.app) as c,patch('execution.external.routes.session',return_value=self.current):
            for role in ('requester','provider','auditor'):
                self.assertEqual(c.post('/api/external/governance/grants',headers={**self.headers,'X-Demo-Role':role},json={}).status_code,403)

    def test_selection_errors_are_specific_and_duplicates_count_once(self):
        records={'t':{}}
        self.assertEqual(self.g.selected_tables(records,['t','t']),['t'])
        for ids,message in [([], '尚未选择'),([str(i) for i in range(101)],'单次最多100'),(['other'],'不在当前云盘')]:
            with self.assertRaisesRegex(DomainError,message):self.g.selected_tables(records,ids)

    def test_governance_picker_only_contains_connected_account(self):
        d.save_registry('bob',{'other':{**self.rec,'id':'other','name':'bob-only'}})
        with TestClient(self.app) as c,patch('execution.external.routes.session',return_value=self.current):
            data=c.get('/api/external/governance/status',headers=self.headers).json()
            self.assertEqual([r['id'] for r in data['tables']],['table1'])
            self.current.cloud_credentials=None
            data=c.get('/api/external/governance/status',headers=self.headers).json()
            self.assertFalse(data['connected']);self.assertEqual(data['tables'],[])
