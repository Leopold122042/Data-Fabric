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


class PatientRelationReviewTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from management.store import Store
        from management.catalog.service import Catalog
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'control.db')
        self.catalog = Catalog(self.store)
        self.snapshot = {'tables': {table: {'columns': ['patient_id'],
            'column_details': [{'name': 'patient_id', 'type': 'TEXT'}]}
            for table in ('patient', 'visit')}}
        self.catalog.ingest('his', self.snapshot)
        fields = {o['table']: o for o in self.store.list('object') if o['kind'] == 'field'}
        self.body = {'source': fields['patient']['id'], 'target': fields['visit']['id'],
            'type': 'same_patient_id', 'confirmation_note': 'Verified synthetic patient keys'}
        self.relation = self.catalog.link(self.body, 'test-admin')

    def test_classification_change_and_scan_keep_confirmation(self):
        field = self.store.require('object', self.body['source'])
        self.catalog.annotate(field['id'], {'expected_version': field['version'],
            'classification': 'identifier'}, 'test-admin')
        self.catalog.ingest('his', self.snapshot)
        self.assertEqual(self.store.require('relation', self.relation['id'])['status'], 'confirmed')
        self.assertEqual(self.catalog.link(self.body, 'test-admin')['version'], self.relation['version'])

    def test_structure_change_requires_explicit_reconfirmation(self):
        self.snapshot['tables']['patient']['column_details'][0]['nullable'] = False
        self.catalog.ingest('his', self.snapshot)
        reviewed = self.store.require('relation', self.relation['id'])
        self.assertEqual(reviewed['status'], 'review_required')
        self.catalog.ingest('his', self.snapshot)
        self.assertEqual(self.store.require('relation', self.relation['id'])['version'], reviewed['version'])
        confirmed = self.catalog.link(self.body, 'test-admin')
        self.assertEqual(confirmed['id'], self.relation['id'])
        self.assertEqual(confirmed['status'], 'confirmed')
        self.assertGreater(confirmed['version'], reviewed['version'])
        helper = object.__new__(IntakeFlow); helper.store = self.store
        self.assertTrue(helper._has_confirmed_patient_id_link(self.body['source'], self.body['target']))
        self.assertEqual(len(self.store.list('relation')), 1)

    def test_deleted_and_reappearing_field_does_not_auto_confirm(self):
        self.catalog.ingest('his', {'tables': {'patient': self.snapshot['tables']['patient']}})
        self.assertEqual(self.store.require('relation', self.relation['id'])['status'], 'review_required')
        self.catalog.ingest('his', self.snapshot)
        self.assertEqual(self.store.require('relation', self.relation['id'])['status'], 'review_required')

    def test_incompatible_types_cannot_be_reconfirmed(self):
        self.snapshot['tables']['visit']['column_details'][0]['type'] = 'INTEGER'
        self.catalog.ingest('his', self.snapshot)
        from management.store import DomainError
        with self.assertRaises(DomainError):
            self.catalog.link(self.body, 'test-admin')

    def test_example_compiles_then_blocks_changed_join(self):
        from management.catalog.field_library import FieldLibrary
        from management.intake.semantic_query import compile_intent
        from management.store import DomainError
        for table, names in {'patient': ['name', 'gender'], 'visit': ['dept']}.items():
            self.snapshot['tables'][table]['columns'] += names
            self.snapshot['tables'][table]['column_details'] += [
                {'name': name, 'type': 'TEXT'} for name in names]
        self.catalog.ingest('his', self.snapshot)
        library = FieldLibrary(self.store)
        for obj in self.catalog.objects(kind='field', status='active'):
            self.catalog.annotate(obj['id'], {'expected_version': obj['version'],
                'classification': 'identifier' if obj['name'] in ('name', 'patient_id') else 'public'}, 'test-admin')
        library.sync()
        for field in library.items():
            saved = library.save(field['id'], {'expected_version': field['version'],
                'label': field['name'], 'definition': 'Synthetic fixture ' + field['name'],
                'basis': 'Synthetic test schema'}, 'test-admin')
            library.save(field['id'], {'expected_version': saved['version'],
                'confirmation_note': 'Reviewed fixture'}, 'test-admin', confirm=True)
        intent = {'summary': 'Return gender and department for a named patient',
            'entity': 'visit', 'outputs': ['patient.gender', 'visit.dept'],
            'filters': [{'field': 'patient.name', 'op': '=', 'value': 'Example Patient'}],
            'order_by': [], 'limit': 200, 'questions': []}
        shape = compile_intent(intent, self.store)
        self.assertEqual([f['name'] for f in shape['output_fields']], ['gender', 'dept'])
        self.assertEqual(len(shape['tables']), 2)
        self.snapshot['tables']['visit']['column_details'][0]['nullable'] = False
        self.catalog.ingest('his', self.snapshot)
        with self.assertRaises(DomainError):
            compile_intent(intent, self.store)
