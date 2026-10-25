import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from platform_core.bootstrap import create_app
from management.catalog.external import publish,sync
from management.store import DomainError
import external_discovery as discovery

class ExternalBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.object(discovery,'DIRECTORY',Path(self.tmp.name)/'saved');self.patch.start()
        self.app=create_app(Path(self.tmp.name)/'control',start_runtime=False)
        self.catalog=self.app.state.runtime.management.catalog
        self.records={'cloud_table':{'id':'cloud_table','name':'统计表','columns':['region','value'],'types':['VARCHAR','DOUBLE'],'path':'private/secret.xlsx','token':'secret-token','rows':456}}
        discovery.save_registry('alice',self.records)
    def tearDown(self):self.patch.stop();self.tmp.cleanup()

    def test_opt_in_structure_versions_and_mapping_invalidation(self):
        self.assertEqual(sync(self.catalog),[])
        self.assertEqual(self.catalog.objects(),[])
        publish(self.catalog,'alice',['cloud_table'],'demo-platform')
        objects=self.catalog.objects();self.assertEqual(len(objects),3)
        exported=json.dumps(objects)
        for secret in ['secret.xlsx','secret-token']:self.assertNotIn(secret,exported)
        self.assertTrue(all(o['domain']=='外部数据' and 'rows' not in o for o in objects))
        self.assertEqual(sync(self.catalog),[])
        field=next(x for x in objects if x['name']=='value')
        m=self.app.state.runtime.management
        concept=m.semantic.concept({'name':'value','meaning':'value','domain':'external','data_type':'real'},'test')
        mapping=m.semantic.mapping({'concept_id':concept['id'],'fields':[field['id']],'mapping_type':'direct','status':'confirmed','confirmation_note':'checked'},'test')
        self.records['cloud_table']['types'][1]='VARCHAR'
        discovery.save_registry('alice',self.records);sync(self.catalog)
        self.assertEqual(m.store.require('mapping',mapping['id'])['status'],'review_required')
        self.assertEqual(m.store.require('object',field['id'])['version'],2)
        discovery.save_registry('alice',{});sync(self.catalog)
        self.assertTrue(all(x['status']=='deleted' for x in self.catalog.objects()))

    def test_publish_requires_current_owner_and_explicit_share(self):
        with self.assertRaises(DomainError):publish(self.catalog,'bob',['cloud_table'],'actor')
        headers={'X-Medfabric-External':'1'}
        current=SimpleNamespace(virtual_lock=threading.RLock(),cloud_credentials=('alice','secret-token'))
        with TestClient(self.app) as c,patch('external_api.session',return_value=current):
            endpoint='/api/external/platform/publish'
            body={'table_ids':['cloud_table'],'share_metadata':True}
            self.assertEqual(c.post(endpoint,headers={**headers,'X-Demo-Role':'auditor'},json=body).status_code,403)
            self.assertEqual(c.post(endpoint,headers=headers,json={'table_ids':['cloud_table']}).status_code,400)
            response=c.post(endpoint,headers=headers,json=body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn('secret-token',response.text)
            current.cloud_credentials=None
            self.assertEqual(c.post(endpoint,headers=headers,json=body).status_code,401)

    def test_inventory_and_navigation_available_across_platform(self):
        with TestClient(self.app) as c:
            for path in ['/static/workspace.html','/static/management/catalog.html','/static/management/semantic.html','/static/management/services.html','/static/platform/resources.html','/static/platform/connections.html']:
                html=c.get(path).text
                self.assertIn('platform-shell.js',html)
                self.assertNotIn('legacy-init.js',html)
            shell=c.get('/static/shared/platform-shell.js').text
            self.assertIn('外部数据接入',shell)
            self.assertIn('/static/platform/connections.html',shell)
            self.assertIn('/static/platform/resources.html',shell)
            self.assertIn('已接入',c.get('/api/management/status').json()['capabilities']['external_sources'])
            with patch('external_virtual.catalog',side_effect=AssertionError('legacy catalog must not be needed')):
                status=c.get('/api/external/platform/status',headers={'X-Medfabric-External':'1'})
                self.assertEqual(status.json(),{'connected':False})
