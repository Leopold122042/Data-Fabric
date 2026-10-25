import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from external_sources import SourceError
import external_api as api

CONFIG={'root':'private/root','sources':[{'id':'s','path':'private/secret.xlsx','member':'secret.xls','sheets':['Sheet1']}], 'views':[], 'notes':[]}

class ConnectionTest(unittest.TestCase):
    def setUp(self):
        app=FastAPI();app.include_router(api.router)
        self.client=TestClient(app);self.other=TestClient(app)
        self.headers={'X-Medfabric-External':'1'}
        self.catalog=patch('external_virtual.catalog',return_value=CONFIG);self.catalog.start()
    def tearDown(self):
        self.catalog.stop()
        for s in api._sessions.values():s.close()
        api._sessions.clear();self.client.close();self.other.close()
    def get_catalog(self,client=None):return (client or self.client).get('/api/external/virtual/catalog',headers=self.headers)
    def post(self,path,data):return self.client.post('/api/external/'+path,headers=self.headers,json=data)
    def test_directory_without_cloud_and_redaction(self):
        with patch('external_api.list_directory',side_effect=AssertionError('must not call cloud')):
            r=self.get_catalog()
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()['connected']);self.assertNotIn('secret',r.text);self.assertNotIn('private/root',r.text)
        r=self.post('virtual/query',{'view_id':'v','sql':'SELECT 1'})
        self.assertEqual(r.status_code,401)
    def test_connect_reuse_isolation_disconnect(self):
        with patch('external_api.list_directory',return_value={'items':[]}):
            r=self.post('box/connect',{'username':'user','token':'secret-token'})
        self.assertEqual(r.status_code,200);self.assertNotIn('secret-token',r.text)
        self.assertTrue(self.get_catalog().json()['connected'])
        self.assertFalse(self.get_catalog(self.other).json()['connected'])
        with patch('external_virtual.execute_virtual',return_value={'rows':[]}) as query:
            r=self.post('virtual/query',{'view_id':'v','sql':'SELECT 1'})
            self.assertEqual(r.status_code,200)
            self.assertEqual(query.call_args.args[:2],('v','user'))
            self.assertEqual(query.call_args.args[2],'secret-token')
        self.assertNotIn('secret-token',self.get_catalog().text)
        with patch('external_api.list_directory',return_value={'items':[]}) as browse:
            self.assertEqual(self.post('box/list',{'path':'folder'}).status_code,200)
            browse.assert_called_once_with('folder','user','secret-token')
        self.assertEqual(self.post('box/disconnect',{}).status_code,200)
        self.assertFalse(self.get_catalog().json()['connected'])
        self.assertEqual(self.post('virtual/query',{'view_id':'v','sql':'SELECT 1'}).status_code,401)
    def test_failed_switch_and_clear(self):
        with patch('external_api.list_directory',return_value={'items':[]}):self.post('box/connect',{'username':'user','token':'secret'})
        with patch('external_api.list_directory',side_effect=SourceError('认证失败')):
            self.assertEqual(self.post('box/connect',{'username':'other','token':'bad'}).status_code,400)
        self.assertFalse(self.get_catalog().json()['connected'])
        with patch('external_api.list_directory',return_value={'items':[]}):self.post('box/connect',{'username':'user','token':'secret'})
        self.post('clear',{})
        self.assertFalse(self.get_catalog().json()['connected'])

if __name__=='__main__':unittest.main()
