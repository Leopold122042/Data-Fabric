import io
import stat
import unittest
import zipfile
from unittest.mock import patch
from external_rar import inspect_zip, scan_zip
from external_sources import SourceError, github_url


def fixture(entries):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in entries:z.writestr(name,data)
    return buffer.getvalue()

class ZipTest(unittest.TestCase):
    def test_list_extract(self):
        data=fixture([('目录/',b''),('目录/数据#1.csv',b'id,name\n1,A\n'),('note.txt','中文'.encode()),('nested.zip',b'zip')])
        r=inspect_zip(data)
        self.assertEqual(len(r['items']),4)
        self.assertFalse(r['items'][-1]['supported'])
        self.assertEqual(inspect_zip(data,'note.txt')['content'],'中文'.encode())
        for name in ('absent.txt','nested.zip'):
            with self.assertRaises(SourceError):inspect_zip(data,name)
        self.assertTrue(github_url('https://github.com/a/b/blob/main/a.zip').endswith('.zip'))

    def test_invalid_and_limits(self):
        for name in ('../a.txt','/a.txt','C:/a.txt','dir\\a.txt'):
            with self.assertRaises(SourceError):inspect_zip(fixture([(name,b'a')]))
        info=zipfile.ZipInfo('link.txt');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
        with self.assertRaises(SourceError):inspect_zip(fixture([(info,b'target')]))
        with self.assertRaises(SourceError):inspect_zip(b'not zip')
        with patch('external_rar.MAX_TOTAL',1),self.assertRaises(ValueError):scan_zip(fixture([('a.txt',b'abc')]))
        with patch('external_rar.MAX_ENTRIES',1),self.assertRaises(ValueError):scan_zip(fixture([('a.txt',b'a'),('b.txt',b'b')]))

    def test_api_user_path_preview_and_failure(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import external_api as api
        app=FastAPI();app.include_router(api.router)
        headers={'X-Medfabric-External':'1'}
        payload=fixture([('数据#1.csv',b'id,name\n1,A\n'),('bad.csv',b'a,b\n1')])
        try:
            with TestClient(app) as client, patch('external_api.fetch',return_value=payload):
                for source in ('box','github'):
                    body={'path':'2021CESAI报告数据汇总/抖音+双微数据结果最终版0109.zip ','username':'u','token':'t'} if source=='box' else {'url':'https://github.com/a/b/blob/main/test.zip'}
                    endpoint='/api/external/'+source+'/load'
                    listed=client.post(endpoint,headers=headers,json=body)
                    self.assertEqual(listed.status_code,200,listed.text)
                    body.update(archive_member='数据#1.csv',archive_digest=listed.json()['archive']['digest'])
                    loaded=client.post(endpoint,headers=headers,json=body)
                    self.assertEqual(loaded.status_code,200,loaded.text)
                    body['archive_member']='bad.csv'
                    self.assertEqual(client.post(endpoint,headers=headers,json=body).status_code,400)
                    queried=client.post('/api/external/query',headers=headers,json={'sql':'SELECT * FROM '+source+'_csv'})
                    self.assertEqual(queried.json()['rows'],[['1','A']])
        finally:
            for session in api._sessions.values():session.close()
            api._sessions.clear()

if __name__=='__main__':unittest.main()
