import datetime
import io
import unittest
from unittest.mock import patch
import xlwt
from external_sources import parse_file, SourceError, LogicalSession
from external_rar import inspect_zip, inspect_rar
from test_external_zip import fixture
from test_external_rar import rar_fixture


def workbook():
    b=xlwt.Workbook();s=b.add_sheet('打分表');b.add_sheet('空表')
    for col,value in enumerate(['名称','名称','', '日期','布尔']):s.write(0,col,value)
    for col,value in enumerate(['甲','乙',90]):s.write(1,col,value)
    s.write(1,3,datetime.datetime(2026,10,14),xlwt.easyxf(num_format_str='YYYY-MM-DD'))
    s.write(1,4,True)
    buf=io.BytesIO();b.save(buf);return buf.getvalue()

class XlsTest(unittest.TestCase):
    def test_xls_mapping_query(self):
        for source in ('box','github'):
            tables=parse_file(workbook(),'网站打分表2.xls',source)
            self.assertEqual(len(tables),1)
            self.assertEqual(tables[0][2],['名称','名称_2','column_3','日期','布尔'])
            self.assertEqual(tables[0][4][0],['甲','乙','90','2026-10-14 00:00:00','True'])
            s=LogicalSession()
            try:
                s.replace(source,'test.xls',tables)
                self.assertEqual(s.query('SELECT CAST(column_3 AS INTEGER) FROM '+source+'_sheet_1')['rows'],[(90,)])
            finally:s.close()

    def test_archives(self):
        name='2021网站/网站打分表2.xls';data=workbook()
        for make,inspect in ((fixture,inspect_zip),):
            packed=make([(name,data)])
            self.assertTrue(inspect(packed)['items'][0]['supported'])
            self.assertEqual(parse_file(inspect(packed,name)['content'],name,'box')[0][1],'打分表')
        packed=rar_fixture([('scores.xls',data)])
        self.assertTrue(inspect_rar(packed)['items'][0]['supported'])
        self.assertEqual(inspect_rar(packed,'scores.xls')['content'],data)

    def test_bad_and_limits(self):
        with self.assertRaises(SourceError):parse_file(b'<html>not excel</html>','x.xls','box')
        with patch('external_sources.MAX_CELLS',2),self.assertRaises(SourceError):parse_file(workbook(),'x.xls','box')

if __name__=='__main__':unittest.main()
