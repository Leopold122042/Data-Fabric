import io
import unittest
from unittest.mock import patch
from external_sources import parse_file, LogicalSession, SourceError, github_url

class FormatsTest(unittest.TestCase):
    def test_new_formats_both_sources_and_sql(self):
        samples = {'json':b'[{"id":1,"extra":{"a":2}},{"id":2,"name":"B"}]',
                   'jsonl':b'{"id":1}\n{"id":2}\n', 'ndjson':b'{"id":1}\n{"id":2}',
                   'tsv':b'id\tname\n1\tA\n2\tB\n', 'txt':'第一行\n第二行'.encode()}
        for source in ('github','box'):
            for ext,payload in samples.items():
                with self.subTest(source=source,ext=ext):
                    tables=parse_file(payload,'example.'+ext,source)
                    s=LogicalSession()
                    try:
                        s.replace(source,'example.'+ext,tables)
                        self.assertEqual(s.query('SELECT COUNT(*) FROM '+tables[0][0])['rows'],[(2,)])
                    finally:s.close()
        cols,rows=parse_file(samples['json'],'x.json','box')[0][2::2]
        self.assertEqual(cols,['id','extra','name'])
        self.assertEqual(rows[0],['1','{"a": 2}',None])

    def test_parquet_and_excel(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        import openpyxl
        buf=io.BytesIO();pq.write_table(pa.table({'id':[1,2],'name':['甲',None]}),buf)
        table=parse_file(buf.getvalue(),'x.parquet','github')[0]
        self.assertEqual(table[4],[['1','甲'],['2',None]])
        b=io.BytesIO();w=openpyxl.Workbook();w.active.append(['id']);w.active.append([1]);w.save(b);w.close()
        self.assertEqual(parse_file(b.getvalue(),'x.xlsx','github')[0][0],'github_sheet_1')

    def test_invalid_and_limits(self):
        for ext,data in [('json',b'[]'),('json',b'[1,2]'),('json',b'{"x":NaN}'),('jsonl',b'{}\nbad'),('tsv',b'a\tb\n1'),('parquet',b'bad'),('txt',b'\xff'),('exe',b'hi')]:
            with self.subTest(ext=ext),self.assertRaises(SourceError):parse_file(data,'x.'+ext,'box')
        with patch('external_sources.MAX_ROWS',1):
            with self.assertRaises(SourceError):parse_file(b'a\nb','x.txt','box')
        self.assertTrue(github_url('https://github.com/a/b/blob/main/x.json').endswith('/x.json'))

    def test_api_sources_and_failed_refresh(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import external_api as api
        app=FastAPI();app.include_router(api.router)
        with TestClient(app) as c:
            h={'X-Medfabric-External':'1'}
            try:
                for source in ('github','box'):
                    body={'url':'https://github.com/a/b/blob/main/x.json'} if source=='github' else {'path':'x.json','username':'u','token':'t'}
                    with patch('external_api.fetch',return_value=b'[{"id":1}]'):
                        r=c.post('/api/external/'+source+'/load',headers=h,json=body)
                    self.assertEqual(r.status_code,200,r.text)
                    with patch('external_api.fetch',return_value=b'bad'):
                        self.assertEqual(c.post('/api/external/'+source+'/load',headers=h,json=body).status_code,400)
                r=c.post('/api/external/query',headers=h,json={'sql':'SELECT g.id FROM github_json g JOIN box_json b ON g.id=b.id'})
                self.assertEqual(r.json()['rows'],[['1']])
            finally:
                for s in api._sessions.values():s.close()
                api._sessions.clear()

if __name__=='__main__':unittest.main()
