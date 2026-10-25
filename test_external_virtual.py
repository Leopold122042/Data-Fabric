import unittest
from unittest.mock import patch
from external_virtual import normalize, execute_virtual, COLUMNS
from external_sources import SourceError

DEFINITION={'id':'test','path':'folder/test.xlsx','sheets':['省级']}
CONFIG={'dataset':'test','root':'folder','sources':[DEFINITION], 'views':[{'id':'cesai_indicator_index','name':'test','sources':['test']}]}
TABLE=('box_sheet_1','省级',['APP','column_2','column_3','微信','column_5','column_6'],['VARCHAR']*6,
 [['排名','省份','指数','排名','省份','指数'],['1','甲省','20','1','乙省','30'],['2','乙省','10','2','甲省','15'],[None,'平均值','15',None,'平均值','22.5'],['新媒体',None,None,None,None,None],['排名','省份','指数',None,None,None],['1','甲省','25',None,None,None]])

class VirtualTest(unittest.TestCase):
    def test_normalize_blocks_keeps_names_and_metrics(self):
        rows=normalize(TABLE,DEFINITION,'test')
        self.assertEqual(len(rows),5)
        self.assertEqual([(r[2],r[3],r[5]) for r in rows],[('甲省','APP',20),('乙省','微信',30),('乙省','APP',10),('甲省','微信',15),('甲省','新媒体',25)])
        self.assertEqual(rows[0][6],'folder/test.xlsx')

    def test_query_refresh_and_no_persistent_data(self):
        with patch('external_virtual.catalog',return_value=CONFIG),patch('external_virtual.read_definition',return_value=[TABLE]) as read:
            for _ in range(2):
                r=execute_virtual('cesai_indicator_index','u','t','SELECT "指标", "数值" FROM cesai_indicator_index WHERE "对象名称"=\'甲省\' ORDER BY "数值"')
                self.assertEqual(r['rows'],[('微信',15.0),('APP',20.0),('新媒体',25.0)])
            self.assertEqual(read.call_count,2)
            with self.assertRaises(SourceError):execute_virtual('cesai_indicator_index','u','t','DELETE FROM cesai_indicator_index')
            with patch('external_virtual.read_definition',return_value=[]),self.assertRaises(SourceError):execute_virtual('cesai_indicator_index','u','t','SELECT 1')

    def test_catalog_requires_cloud_auth(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import external_api as api
        app=FastAPI();app.include_router(api.router)
        try:
            with TestClient(app) as client,patch('external_virtual.catalog',return_value=CONFIG):
                h={'X-Medfabric-External':'1'}
                with patch('external_api.list_directory',side_effect=SourceError('认证失败')):
                    r=client.post('/api/external/virtual/catalog',headers=h,json={'username':'u','token':'bad'})
                    self.assertEqual(r.status_code,400);self.assertNotIn('folder/test.xlsx',r.text)
                with patch('external_api.list_directory',return_value={'items':[]}):
                    r=client.post('/api/external/virtual/catalog',headers=h,json={'username':'u','token':'good'})
                    self.assertEqual(r.status_code,200)
        finally:
            for session in api._sessions.values():session.close()
            api._sessions.clear()

if __name__=='__main__':unittest.main()
