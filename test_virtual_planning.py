import unittest
from unittest.mock import patch
from virtual_planner import constraints,choose_sources
from virtual_cache import FileCache
from external_sources import SourceError

VIEW={'id':'v','sources':['a','b']}
CONFIG={'sources':[{'id':'a','sheets':['城市','部门'],'sheet_capabilities':{'城市':{'指标':['APP'],'口径':['直辖市']},'部门':{'指标':['APP'],'口径':['部委']}}},{'id':'b','sheets':['城市'],'sheet_capabilities':{'城市':{'指标':['微信'],'口径':['直辖市']}}}]}

class PlanTest(unittest.TestCase):
    def test_select_specific_file_and_sheet(self):
        selected,limits=choose_sources(CONFIG,VIEW,"SELECT * FROM v WHERE 指标='APP' AND 口径='直辖市'")
        self.assertEqual([(x['id'],x['sheets']) for x in selected],[('a',['城市'])])
        self.assertEqual(limits['指标'],{'APP'})
        self.assertEqual(choose_sources(CONFIG,VIEW,"SELECT COUNT(*) FROM v WHERE 指标='不存在'")[0],[])
    def test_conservative_fallback_and_in(self):
        for sql in ["SELECT * FROM v WHERE 指标='APP' OR 对象名称='北京'", "WITH t AS (SELECT * FROM v) SELECT * FROM t WHERE 指标='APP'", "SELECT * FROM v a JOIN v b ON a.指标=b.指标 WHERE a.指标='APP'", "SELECT 'APP' AS 指标 FROM v WHERE 指标='APP'", "SELECT * FROM v WHERE lower(指标)='app'", "SELECT * FROM v WHERE 指标 NOT IN ('微信')"]:
            self.assertEqual(len(choose_sources(CONFIG,VIEW,sql)[0]),2,sql)
        self.assertEqual(constraints("SELECT * FROM v WHERE 指标 IN ('APP','微信')",'v')['指标'],{'APP','微信'})
        for sql in ['DELETE FROM v','SELECT 1; SELECT 2']:
            with self.assertRaises(SourceError):constraints(sql,'v')
    def test_unknown_mapping_preserved(self):
        config={'sources':[{'id':'a','sheets':['城市']}]}
        self.assertEqual(len(choose_sources(config,{'id':'v','sources':['a']},"SELECT * FROM v WHERE 指标='APP'")[0]),1)

class CacheTest(unittest.TestCase):
    def test_reuse_change_force_credentials_ttl(self):
        store={}
        with patch('virtual_cache.remote_version',return_value=('etag','v1')) as head,patch('virtual_cache.fetch',return_value=b'file') as download:
            FileCache(store,'u','t').get('a.xlsx')
            FileCache(store,'u','t').get('a.xlsx')
            self.assertEqual(download.call_count,1);self.assertEqual(head.call_count,2)
            head.return_value=('etag','v2');FileCache(store,'u','t').get('a.xlsx')
            self.assertEqual(download.call_count,2)
            FileCache(store,'u','t',True).get('a.xlsx');self.assertEqual(download.call_count,3)
            FileCache(store,'u','changed').get('a.xlsx');self.assertEqual(download.call_count,4)
            for value in store.values():value['at']-=301
            FileCache(store,'u','changed').get('a.xlsx');self.assertEqual(download.call_count,5)
    def test_no_versions_failure_isolation(self):
        with patch('virtual_cache.remote_version',return_value=None) as head,patch('virtual_cache.fetch',return_value=b'x') as download:
            store={};FileCache(store,'u','t').get('x');FileCache(store,'u','t').get('x')
            self.assertEqual(download.call_count,1)
            FileCache({},'u','t').get('x');self.assertEqual(download.call_count,2)
            head.side_effect=SourceError('forbidden')
            with self.assertRaises(SourceError):FileCache(store,'u','t').get('x')
            self.assertEqual(download.call_count,2)
    def test_only_selected_sheet_parsed(self):
        import io,openpyxl
        from external_sources import parse_file
        book=openpyxl.Workbook();book.active.title='selected';book.active.append(['a']);book.active.append([1])
        other=book.create_sheet('excluded');other.cell(50002,1,'oversized')
        out=io.BytesIO();book.save(out);book.close()
        tables=parse_file(out.getvalue(),'test.xlsx','box',selected_sheets=['selected'])
        self.assertEqual([t[1] for t in tables],['selected'])

if __name__=='__main__':unittest.main()
