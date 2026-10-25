"""Self-contained RAR4 stored fixtures; no user files or credentials."""
import struct
import unittest
import zlib
from unittest.mock import patch
from external_rar import inspect_rar
from external_sources import SourceError, github_url


def rar_fixture(entries):
    def header(kind, flags, tail):
        body=struct.pack('<BHH',kind,flags,7+len(tail))+tail
        return struct.pack('<H',zlib.crc32(body)&0xffff)+body
    data=b'Rar!\x1a\x07\x00'+header(0x73,0,b'\0'*6)
    for name,content in entries:
        name=name.encode()
        tail=struct.pack('<IIBIIBBHI',len(content),len(content),3,zlib.crc32(content),0,20,0x30,len(name),0o100644)+name
        data+=header(0x74,0x8000,tail)+content
    return data+header(0x7b,0,b'')

class RarTest(unittest.TestCase):
    def test_real_rar_list_and_extract(self):
        blob=rar_fixture([('folder/data.csv',b'id,name\n1,A\n'),('note.txt','中文'.encode()),('image.png',b'fake')])
        result=inspect_rar(blob)
        self.assertEqual(len(result['items']),3)
        self.assertTrue(result['items'][0]['supported'])
        self.assertFalse(result['items'][2]['supported'])
        self.assertEqual(inspect_rar(blob,'note.txt')['content'],'中文'.encode())
        for name in ('absent.txt','image.png'):
            with self.assertRaises(SourceError):inspect_rar(blob,name)
        self.assertTrue(github_url('https://github.com/a/b/blob/main/test.rar').endswith('.rar'))

    def test_invalid_paths_duplicates_and_timeout(self):
        for entries in [[('../bad.txt',b'a')],[('/bad.txt',b'a')],[('a.txt',b'a'),('a.txt',b'b')]]:
            with self.assertRaises(SourceError):inspect_rar(rar_fixture(entries))
        with self.assertRaises(SourceError):inspect_rar(b'not rar')
        import subprocess
        with patch('external_rar.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',30)):
            with self.assertRaises(SourceError):inspect_rar(b'anything')

    def test_api_list_extract_and_changed_archive(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import external_api as api
        app=FastAPI();app.include_router(api.router)
        blob=rar_fixture([('data.csv',b'id,name\n1,A\n')])
        h={'X-Medfabric-External':'1'}
        try:
            with TestClient(app) as client,patch('external_api.fetch',return_value=blob):
                for source in ('github','box'):
                    body={'url':'https://github.com/a/b/blob/main/test.rar'} if source=='github' else {'path':'test.rar','username':'u','token':'secret'}
                    url='/api/external/'+source+'/load'
                    r=client.post(url,headers=h,json=body)
                    self.assertEqual(r.status_code,200,r.text)
                    body.update(archive_member='data.csv',archive_digest=r.json()['archive']['digest'])
                    r=client.post(url,headers=h,json=body)
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertEqual(r.json()['tables'][-1]['rows'],1)
                    self.assertIn(' :: data.csv',r.json()['tables'][-1]['location'])
                    body['archive_digest']='bad'
                    self.assertEqual(client.post(url,headers=h,json=body).status_code,400)
                r=client.post('/api/external/query',headers=h,json={'sql':'SELECT * FROM box_csv'})
                self.assertEqual(r.json()['rows'],[['1','A']])
        finally:
            for s in api._sessions.values():s.close()
            api._sessions.clear()

if __name__=='__main__':unittest.main()
