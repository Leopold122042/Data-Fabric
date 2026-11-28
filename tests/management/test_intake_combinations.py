import unittest
from unittest.mock import Mock
from management.intake.flow import IntakeFlow

class IntakeCombinationTest(unittest.TestCase):
    def setUp(self):
        self.flow=object.__new__(IntakeFlow)
        self.flow._source_connection_status=Mock(return_value='connected')
        self.flow._has_confirmed_patient_id_link=Mock(return_value=True)
        self.catalog=[]
        for table,names in {'patient':[('name','姓名'),('patient_id','患者ID')],
                'visit':[('patient_id','患者ID'),('chief_complaint','主诉'),('doctor','医生')],
                'dossier':[('patient_id','患者ID'),('chief_complaint','主诉')]}.items():
            self.catalog.append(dict(id=table,kind='table',name=table,table=table,source_id='his',status='active'))
            for name,label in names:
                self.catalog.append(dict(id=table+'.'+name,kind='field',name=name,table=table,
                    parent_id=table,source_id='his',status='active',field_label=label,
                    aliases=[label],meaning=label,definition_status='confirmed'))
    def test_unjoinable_alternative_does_not_block_complete_combination(self):
        result=self.flow._person_multi_field_lookup('唐军的主诉和医生',self.catalog)
        self.assertEqual(result['kind'],'field_value')
        binding=next(x for x in result['semantic_bindings'] if x['phrase']=='主诉')
        self.assertEqual([x['id'] for x in binding['candidate_fields']],['visit.chief_complaint'])
        self.assertIn('doctor',result['sql'])
    def test_two_feasible_sources_still_require_selection(self):
        self.catalog.append(dict(id='dossier.doctor',kind='field',name='doctor',table='dossier',
            parent_id='dossier',source_id='his',status='active',field_label='医生',aliases=['医生'],
            meaning='医生',definition_status='confirmed'))
        result=self.flow._person_multi_field_lookup('唐军的主诉和医生',self.catalog)
        binding=next(x for x in result['semantic_bindings'] if x['phrase']=='主诉')
        self.assertEqual(len(binding['candidate_fields']),2)

    def test_relation_survives_classification_but_not_structure_change(self):
        flow=object.__new__(IntakeFlow)
        old=dict(id='a',version=1,kind='field',name='patient_id',parent_id='patient',
                 source_id='his',table='patient',structure={'type':'text'},status='active')
        other={**old,'id':'b','parent_id':'vitals','source_id':'iot','table':'vitals'}
        current={**old,'version':2,'classification':'identifier'}
        flow.store=Mock()
        flow.store.list.return_value=[dict(type='same_patient_id',status='confirmed',source='a',target='b',field_versions={'a':1,'b':1})]
        flow.store.get.side_effect=lambda kind,key:current if key=='a' else other
        flow.store.history.return_value=[current,old]
        self.assertTrue(flow._has_confirmed_patient_id_link('a','b'))
        current['structure']={'type':'integer'}
        self.assertFalse(flow._has_confirmed_patient_id_link('a','b'))

    def test_single_field_uses_same_combination_planner(self):
        result=self.flow._person_field_lookup('唐军的医生',self.catalog)
        self.assertEqual(result['kind'],'field_value')
        self.assertIn('doctor',result['sql'])
        self.assertEqual(result['join_table']['id'],'patient')
