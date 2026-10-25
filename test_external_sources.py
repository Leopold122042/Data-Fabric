import io
import unittest
from unittest.mock import patch
from external_sources import (LogicalSession, SourceError, parse_csv, parse_excel,
                              github_url, box_url, safe_column_names, list_directory)

CSV = b'passengerid,name,sex,age,survived,pclass,fare\n1,A,female,24,1,1,30\n2,B,male,,0,3,8\n'

class ExternalSourcesTest(unittest.TestCase):
    def setUp(self):
        self.session = LogicalSession()
        self.addCleanup(self.session.close)

    def test_csv_mapping_and_aggregate(self):
        self.session.replace('github','example',parse_csv(CSV,'passengers'))
        result = self.session.query('SELECT COUNT(*), AVG(CAST(survived AS INTEGER)) FROM passengers')
        self.assertEqual(result['rows'],[(2,0.5)])
        self.assertIsNone(self.session.query('SELECT age FROM passengers WHERE passenger_id=2')['rows'][0][0])

    def test_refresh_replaces_data_and_failure_preserves_old_table(self):
        self.session.replace('github','example',parse_csv(CSV,'passengers'))
        with self.assertRaises(SourceError):
            self.session.replace('github','bad',[('broken','bad',['x'],['INTEGER'],[('not a number',)])])
        self.assertEqual(self.session.query('SELECT COUNT(*) FROM passengers')['rows'],[(2,)])
        self.session.replace('github','next',parse_csv(b'x\n1\n','raw'))
        self.assertEqual([t['name'] for t in self.session.snapshot()['tables']],['github_csv'])

    def test_excel_headers_empty_sheet_text_types_and_cross_source_query(self):
        import openpyxl
        book=openpyxl.Workbook();sheet=book.active;sheet.title='中文表'
        sheet.append(['ID','ID',None]);sheet.append([1,2,'value'])
        book.create_sheet('empty');buf=io.BytesIO();book.save(buf);book.close()
        tables=parse_excel(buf.getvalue())
        self.assertEqual(tables[0][2],['ID','ID_2','column_3'])
        self.session.replace('box','example.xlsx',tables)
        self.session.replace('github','example',parse_csv(CSV,'passengers'))
        self.assertEqual(self.session.query('SELECT name FROM passengers JOIN box_sheet_1 ON passenger_id=TRY_CAST("ID" AS INTEGER)')['rows'],[('A',)])

    def test_reject_write_multi_statement_and_external_io(self):
        for sql in ['CREATE TABLE x(a INT)','SELECT 1; SELECT 2',"COPY (SELECT 1) TO '/tmp/medfabric-test'", "ATTACH ':memory:' AS other", "SELECT * FROM read_csv('/etc/passwd')", "INSTALL httpfs", "SET enable_external_access=true"]:
            with self.subTest(sql=sql), self.assertRaises(SourceError): self.session.query(sql)
        self.assertEqual(self.session.query('WITH x AS (SELECT 1 AS n) SELECT n FROM x')['rows'],[(1,)])

    def test_result_limit_and_duplicate_columns(self):
        r=self.session.query('SELECT i AS x,i AS x FROM range(250) t(i)')
        self.assertEqual(len(r['rows']),200);self.assertTrue(r['truncated'])
        self.assertEqual(r['columns'],['x','x']);self.assertEqual(r['rows'][1],(1,1))

    def test_invalid_data_and_paths(self):
        for data in [b'',b'name,name\nA,B\n',b'a,b\n1\n']:
            with self.assertRaises(SourceError):parse_csv(data,'raw')
        for url in ['http://raw.githubusercontent.com/a/b/c/d.csv','https://localhost/a.csv','https://github.com/a/b/blob/main/../d.csv']:
            with self.assertRaises(SourceError):github_url(url)
        self.assertEqual(github_url('https://github.com/a/b/blob/main/d.csv'),'https://raw.githubusercontent.com/a/b/main/d.csv')
        with self.assertRaises(SourceError):box_url('../secret')
        with self.assertRaises(SourceError):box_url('%2e%2e/secret')
        self.assertEqual(safe_column_names(['A','a',''],3),['A','a_2','column_3'])

    def test_webdav_listing(self):
        xml=b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/seafdav/</d:href></d:response><d:response><d:href>/seafdav/demo/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>'
        with patch('external_sources.fetch',return_value=xml):
            self.assertEqual(list_directory('','u','secret')['items'],[{'name':'demo','path':'demo','directory':True}])

class ExternalApiTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import external_api
        self.api=external_api
        app=FastAPI();app.include_router(external_api.router)
        self.client=TestClient(app)
        self.other=TestClient(app)
        self.headers={'X-Medfabric-External':'1'}
    def tearDown(self):
        for s in self.api._sessions.values():s.close()
        self.api._sessions.clear()
        self.client.close();self.other.close()
    def test_session_isolation_and_origin(self):
        with patch('external_api.fetch',return_value=CSV):
            r=self.client.post('/api/external/github/load',headers=self.headers,json={})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.other.get('/api/external/status',headers=self.headers).json()['tables'],[])
        self.assertEqual(self.client.get('/api/external/status',headers={**self.headers,'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/external/status').status_code,403)
        self.assertEqual(self.client.post('/api/external/query',headers=self.headers,json={'sql':'SELECT COUNT(*) FROM passengers'}).json()['rows'],[[2]])
        self.client.post('/api/external/clear',headers=self.headers,json={})
        self.assertEqual(self.client.get('/api/external/status',headers=self.headers).json()['tables'],[])
    def test_credentials_not_in_responses(self):
        with patch('external_api.list_directory',side_effect=RuntimeError('secret-token')):
            r=self.client.post('/api/external/box/list',headers=self.headers,json={'username':'u','token':'secret-token'})
        self.assertEqual(r.status_code,400)
        self.assertNotIn('secret-token',r.text)
        r=self.client.post('/api/external/box/list',headers=self.headers,
                           json={'username':'u','token':'secret-token'*201})
        self.assertEqual(r.status_code,422)
        self.assertNotIn('secret-token',r.text)

if __name__=='__main__':unittest.main()
