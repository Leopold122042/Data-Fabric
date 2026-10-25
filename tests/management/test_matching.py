import tempfile,unittest
from pathlib import Path
from platform_core.storage import Store,DomainError
from management.catalog.service import Catalog
from management.semantic.service import Semantic
from management.semantic.matching import profile_columns

class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.db');self.catalog=Catalog(self.s);self.semantic=Semantic(self.s)
        self.catalog.ingest('cloud-demo',{'tables':{'机构':{'columns':['机构编号','名称','数值'],'column_details':[{'name':'机构编号','type':'text'},{'name':'名称','type':'text'},{'name':'数值','type':'real'}]}}})
        self.fields={x['name']:x for x in self.catalog.objects(kind='field')}
    def tearDown(self):self.tmp.cleanup()
    def concept(self,**kw):return self.semantic.concept({'name':'机构编号','meaning':'机构编号','domain':'组织管理','data_type':'text','context_keywords':['机构'],**kw},'test')
    def test_unknown_values_do_not_become_confident(self):
        c=self.concept();rows=self.semantic.candidates(c['id']);self.assertEqual(rows[0]['status'],'needs_confirmation');self.assertFalse(rows[0]['components']['values']['available'])
    def test_profile_context_suggestion_never_confirms(self):
        f=self.fields['机构编号'];p=profile_columns(['机构编号'],[['X1'],['X2']])['机构编号'];self.s.save('object',f['id'],{**f,'profile':p},f['version'])
        c=self.concept();row=self.semantic.candidates(c['id'])[0];self.assertEqual(row['status'],'suggested');self.assertEqual(self.s.list('mapping'),[]);self.assertNotIn('X1',str(p))
    def test_range_type_veto_and_invalid_bounds(self):
        f=self.fields['数值'];self.s.save('object',f['id'],{**f,'profile':{'min':200,'max':300,'count':3}},f['version'])
        c=self.concept(name='数值',data_type='real',range={'min':0,'max':100});rows=self.semantic.candidates(c['id']);self.assertTrue(all(r['status']=='rejected' for r in rows))
        with self.assertRaises(DomainError):self.concept(range={'min':10,'max':1})
    def test_conditional_allows_text_predicate_numeric_output(self):
        c=self.concept(name='指标值',data_type='real');f=self.fields
        m=self.semantic.mapping({'concept_id':c['id'],'fields':[f['名称']['id'],f['数值']['id']],'mapping_type':'conditional','expression':{'predicate':{'operator':'eq','args':[{'field':f['名称']['id']},{'literal':'客户数'}]},'then':{'field':f['数值']['id']},'else':{'literal':None}},'status':'draft'},'test')
        self.assertEqual(m['status'],'draft')
    def test_invalid_arithmetic_rejected(self):
        c=self.concept(data_type='real');f=self.fields['名称']['id']
        with self.assertRaises(DomainError):self.semantic.mapping({'concept_id':c['id'],'fields':[f],'mapping_type':'derived','expression':{'operator':'multiply','args':[{'field':f},{'literal':2}]}},'test')
