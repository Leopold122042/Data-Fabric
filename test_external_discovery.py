import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import openpyxl
import external_discovery as d
from external_sources import SourceError
from test_external_zip import fixture

class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(d,'DIRECTORY',Path(self.tmp.name));self.patch.start()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def run_job(self,payload):
        job=d.start('root',True,True)
        listings={'root':{'items':[{'path':'root/a.csv','directory':False},{'path':'root/inner','directory':True},{'path':'root/bad.csv','directory':False}]},'root/inner':{'items':[{'path':'root/inner/book.zip','directory':False}]}}
        archive=fixture([('book.csv',b'id,value\n2,B\n')])
        def fetched(url,**kwargs):
            if url.endswith('bad.csv'):raise SourceError('bad file')
            return archive if url.endswith('.zip') else payload
        with patch.object(d,'list_directory',side_effect=lambda path,*args:listings[path]),patch.object(d,'fetch',side_effect=fetched):
            while not job['done']:d.step(job,'alice','secret')
        return job
    def test_recursive_archive_persistence_and_rescan(self):
        job=self.run_job(b'id,name\n1,A\n')
        self.assertEqual(job['registered'],2);self.assertEqual(len(job['errors']),1)
        records=d.definitions('alice');ids={r['id'] for r in records}
        self.assertEqual(len(ids),2);self.assertTrue(all(r['status']=='可查询' for r in records))
        self.assertNotIn('secret',next(Path(self.tmp.name).glob('*.json')).read_text())
        self.assertTrue(all('path' not in r for r in d.definitions()))
        self.run_job(b'id,name\n1,A\n');self.assertEqual(ids,{r['id'] for r in d.definitions('alice')})
        self.run_job(b'id,new_field\n1,A\n')
        self.assertTrue(all(r['status']=='可查询' for r in d.definitions('alice')))
        self.assertTrue(any('new_field' in r['columns'] for r in d.definitions('alice')))
        self.assertEqual(d.definitions('other'),[])
    def test_complex_header_preview_confirm_query_and_drift(self):
        book=openpyxl.Workbook();s=book.active;s.title='Sheet1';s.append(['报告',None]);s.append(['名称','得分']);s.append(['甲',8])
        buf=io.BytesIO();book.save(buf);book.close();payload=buf.getvalue()
        job=d.start('root',False,False);job['queue']=[{'path':'root/a.xlsx'}]
        with patch.object(d,'fetch',return_value=payload):
            d.step(job,'alice','secret');record=d.definitions('alice')[0]
            self.assertEqual(record['status'],'可查询')
            self.assertEqual(d.query(record['id'],'alice','secret')['rows'][0],('名称','得分'))
            preview=d.query(record['id'],'alice','secret',preview=True,header_row=2)
            self.assertEqual(preview['columns'],['名称','得分'])
            d.query(record['id'],'alice','secret',confirm=True,header_row=2)
            result=d.query(record['id'],'alice','secret')
            self.assertEqual(result['rows'],[('甲','8')])
            with self.assertRaises(SourceError):d.query(record['id'],'other','secret')
            with self.assertRaises(SourceError):d.query(record['id'],'alice','secret',sql='DELETE FROM x')
    def test_routes_require_connection_and_create_query(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import external_api as api
        app=FastAPI();app.include_router(api.router)
        h={'X-Medfabric-External':'1'}
        try:
            with TestClient(app) as c:
                r=c.post('/api/external/discovery/start',headers=h,json={'root':'root'})
                self.assertEqual(r.status_code,401)
                with patch('external_api.list_directory',return_value={'items':[]}),patch('external_virtual.catalog',return_value={'root':'root'}):
                    self.assertEqual(c.post('/api/external/box/connect',headers=h,json={'username':'alice','token':'secret'}).status_code,200)
                c.post('/api/external/discovery/start',headers=h,json={'root':'root'})
                with patch.object(d,'list_directory',return_value={'items':[{'path':'root/a.csv','directory':False}]}),patch.object(d,'fetch',return_value=b'id,name\n1,A\n'):
                    for _ in range(2):self.assertEqual(c.post('/api/external/discovery/step',headers=h,json={}).status_code,200)
                    tables=c.get('/api/external/discovery/catalog',headers=h).json()['tables']
                    r=c.post('/api/external/discovery/query',headers=h,json={'view_id':tables[0]['id']})
                    self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['rows'],[['1','A']])
        finally:
            for item in api._sessions.values():item.close()
            api._sessions.clear()

    def test_persistent_collection_union_projection_and_isolation(self):
        self.run_job(b'id,name\n1,A\n')
        groups=d.collections('alice');self.assertEqual(len(groups),1)
        group=groups[0];self.assertEqual(len(group['sources']),2)
        self.assertIn('数据·name',group['columns']);self.assertIn('数据·value',group['columns'])
        self.assertEqual(d.collections('other'),[])
        self.assertEqual(d.collections()[0]['sources'][0]['path'],'连接后可查看')
        archive=fixture([('book.csv',b'id,value\n2,B\n')])
        def fetched(url,**kwargs):return archive if url.endswith('.zip') else b'id,name\n1,A\n'
        ids=[r['id'] for r in group['sources']]
        with patch.object(d,'fetch',side_effect=fetched) as fetcher:
            result=d.query_collection(group['id'],'alice','secret',ids,
                'SELECT "数据·id", "数据·name", "数据·value" FROM "'+group['id']+'" ORDER BY "数据·id"')
            self.assertEqual(result['rows'],[('1','A',None),('2',None,'B')])
            self.assertEqual(fetcher.call_count,2)
            fetcher.reset_mock()
            result=d.query_collection(group['id'],'alice','secret',ids[:1])
            self.assertEqual(fetcher.call_count,1)
            with self.assertRaises(SourceError):d.query_collection(group['id'],'other','secret',ids)
            with self.assertRaises(SourceError):d.query_collection(group['id'],'alice','secret',['foreign'])
        saved=(Path(self.tmp.name)/'collections'/(d.owner('alice')+'.json')).read_text()
        self.assertNotIn('secret',saved);self.assertNotIn('数据·name',saved)
        old_id=group['id'];self.run_job(b'id,name\n1,A\n')
        self.assertEqual(d.collections('alice')[0]['id'],old_id)

    def test_automatic_rankings_fallback_and_schema_refresh(self):
        ranked=('t','直辖市',['APP','column_2','column_3'],['VARCHAR']*3,
                [['排名','直辖市','总分'],['1','北京市','72.8']])
        base={'id':'auto_test','path':'root/a.csv','name':'test','sheet':'直辖市'}
        record,mapped=d.automatic_mapping(ranked,base)
        self.assertEqual(record['mapping_mode'],'ranking')
        self.assertEqual(mapped[4][0][2:6],['北京市','APP',1,72.8])
        unknown=('t','直辖市',['未知指标','column_2','column_3'],['VARCHAR']*3,ranked[4])
        record,mapped=d.automatic_mapping(unknown,base)
        self.assertEqual(record['status'],'可查询');self.assertEqual(record['mapping_mode'],'raw')
        self.assertEqual(mapped,unknown)
        job=d.start('root',False,False);job['queue']=[{'path':'root/a.csv'}]
        with patch.object(d,'fetch',return_value=b'id,name\n1,A\n'):d.step(job,'alice','secret')
        item=d.definitions('alice')[0]
        # Previously blocked definitions are upgraded automatically on read.
        with d.LOCK:
            records=d.read_registry('alice');records[item['id']]['status']='待确认';d.save_registry('alice',records)
        with patch.object(d,'fetch',return_value=b'id,updated\n1,B\n'):
            result=d.query(item['id'],'alice','secret')
            self.assertEqual(result['columns'],['id','updated'])
            self.assertEqual(result['rows'],[('1','B')])
        self.assertEqual(d.definitions('alice')[0]['status'],'可查询')
        self.assertIn('collection_id',job)

    def test_remove_restore_persistence_and_query_exclusion(self):
        self.run_job(b'id,name\n1,A\n')
        group=d.collections('alice')[0];key=group['source_ids'][0]
        with self.assertRaises(SourceError):d.change_membership(group['id'],key,'other')
        with self.assertRaises(SourceError):d.change_membership(group['id'],'foreign','alice')
        with patch.object(d,'fetch') as fetcher:
            d.change_membership(group['id'],key,'alice')
            with self.assertRaises(SourceError):d.query_collection(group['id'],'alice','secret',[key])
            fetcher.assert_not_called()
        self.assertEqual(len(d.definitions('alice')),2)
        self.assertNotIn(key,d.collections('alice')[0]['source_ids'])
        self.assertEqual(d.collections('alice')[0]['excluded_sources'][0]['id'],key)
        self.run_job(b'id,name\n1,A\n')
        self.assertNotIn(key,d.collections('alice')[0]['source_ids'])
        d.change_membership(group['id'],key,'alice',True)
        self.assertIn(key,d.collections('alice')[0]['source_ids'])
        self.assertEqual(d.collections('alice')[0]['excluded_sources'],[])
        for source in group['source_ids']:d.change_membership(group['id'],source,'alice')
        self.assertEqual(d.collections('alice')[0]['sources'],[])
        d.change_membership(group['id'],key,'alice',True)
        self.assertEqual(d.collections('alice')[0]['source_ids'],[key])

    def test_keyword_search_all_columns_union_null_and_literal(self):
        payloads={'a.csv':'城市,分数,空列\n北京市,72,\n上海市,80,\n',
                  'b.csv':"机构,备注\n北京大学,100%_\n其他,北京办事处\n",'c.csv':'未命中专属字段\n杭州\n'}
        job=d.start('root',False,False);job['queue']=[{'path':'root/'+p} for p in payloads]
        from urllib.parse import unquote
        def fetched(url,**kwargs):return payloads[unquote(url).rsplit('/',1)[-1]].encode()
        with patch.object(d,'fetch',side_effect=fetched):
            while not job['done']:d.step(job,'alice','secret')
            group=d.collections('alice')[0]
            result=d.query_collection(group['id'],'alice','secret',group['source_ids'],keyword='北京')
            self.assertEqual(len(result['rows']),3)
            self.assertNotIn('数据·未命中专属字段',result['columns'])
            self.assertNotIn('数据·空列',result['columns'])
            self.assertEqual(result['matched_source_count'],2)
            cols=result['columns'];city=cols.index('数据·城市');org=cols.index('数据·机构')
            self.assertEqual(result['rows'][0][city],'北京市');self.assertIsNone(result['rows'][0][org])
            self.assertIsNone(result['rows'][1][city]);self.assertEqual(result['rows'][1][org],'北京大学')
            literal=d.query_collection(group['id'],'alice','secret',group['source_ids'],keyword='%_')
            self.assertEqual(len(literal['rows']),1)
            self.assertNotIn('数据·城市',literal['columns'])
            self.assertEqual(literal['matched_source_count'],1)
            injected=d.query_collection(group['id'],'alice','secret',group['source_ids'],keyword="' OR 1=1 --")
            self.assertEqual(injected['rows'],[])
            self.assertEqual(injected['columns'],[])
            self.assertEqual(len(d.query_collection(group['id'],'alice','secret',group['source_ids'],keyword='')['rows']),5)
            with self.assertRaises(SourceError):d.query_collection(group['id'],'alice','secret',group['source_ids'],sql='SELECT 1',keyword='北京')

    def test_search_prunes_blank_fields_before_display_limit_preserves_zero(self):
        payload=('城市,全空,时长,零值,空格\n'+''.join('北京,,{},0,  \n'.format('4' if i==201 else '') for i in range(202))).encode()
        job=d.start('root',False,False);job['queue']=[{'path':'root/a.csv'}]
        with patch.object(d,'fetch',return_value=payload):
            d.step(job,'alice','secret');g=d.collections('alice')[0]
            result=d.query_collection(g['id'],'alice','secret',g['source_ids'],keyword='北京')
            self.assertEqual(len(result['rows']),200);self.assertTrue(result['truncated'])
            self.assertNotIn('数据·全空',result['columns']);self.assertNotIn('数据·空格',result['columns'])
            self.assertIn('数据·时长',result['columns'])
            self.assertEqual(result['rows'][0][result['columns'].index('数据·零值')],'0')
            self.assertIn('全空',d.definitions('alice')[0]['columns'])

    def test_limits_and_no_rows_in_catalog(self):
        job=d.start('root',False,False);job['queue']=[{'path':'root/a.csv'}];job['processed']=100
        with patch.object(d,'fetch') as fetched:d.step(job,'alice','secret');fetched.assert_not_called()
        self.assertTrue(job['done']);self.assertTrue(job['skipped'])
        job=self.run_job(b'id,name\n1,PRIVATE_VALUE\n')
        self.assertNotIn('PRIVATE_VALUE',next(Path(self.tmp.name).glob('*.json')).read_text())

if __name__=='__main__':unittest.main()
