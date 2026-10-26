import sqlite3
import tempfile
import unittest
from pathlib import Path

from management.catalog.service import Catalog
from management.catalog.sources import SQLiteSource, SourceRegistry
from management.semantic.matching import rank
from management.semantic.service import Semantic, family
from management.store import DomainError, Store


class MatchingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'management.db')
        self.catalog = Catalog(self.store)
        self.semantic = Semantic(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def concept(self, **changes):
        return self.semantic.concept({'name': 'age', 'meaning': 'Age in years',
            'domain': 'demography', 'data_type': 'integer', 'range': {'min': 0, 'max': 120}, **changes}, 'reviewer')

    def fields(self, source, names, profile=True, dtype='INTEGER'):
        self.catalog.ingest(source, {'domain': 'demography', 'tables': {'people': {
            'columns': names, 'column_details': [{'name': n, 'type': dtype} for n in names]}}})
        result = {}
        for f in self.catalog.objects(source_id=source, kind='field'):
            if profile:
                f = self.store.save('object', f['id'], {**f, 'meaning': 'Age in years',
                    'profile': {'count': 3, 'non_null': 3, 'min': 20, 'max': 40}}, f['version'])
            result[f['name']] = f
        return result

    def learn(self, concept, field):
        return self.semantic.mapping({'concept_id': concept['id'], 'fields': [field['id']],
            'mapping_type': 'direct', 'status': 'confirmed', 'confirmation_note': 'Compared source definition and values'}, 'reviewer')

    def candidate(self, concept, field):
        return next(r for r in self.semantic.candidates(concept['id']) if r['field_id'] == field['id'])

    def test_sqlite_profile_reaches_value_ranking_and_preserves_zero_values(self):
        path = self.root / 'people.db'
        db = sqlite3.connect(path)
        try:
            db.execute('CREATE TABLE people (age INTEGER, label TEXT)')
            db.executemany('INSERT INTO people VALUES (?, ?)', [(0, 'A'), (30, 'AB'), (None, None)])
            db.commit()
        finally:
            db.close()
        self.catalog.sources = SourceRegistry(self.store, self.root)
        self.catalog.sources.register({'source_id': 'local', 'connector': 'sqlite', 'path': 'people.db'}, 'reviewer')
        self.catalog.ingest('local', SQLiteSource(path).snapshot())
        field = next(f for f in self.catalog.objects(kind='field') if f['name'] == 'age')
        profiled = self.catalog.profile(field['id'], 'reviewer')
        row = self.candidate(self.concept(), profiled)
        self.assertTrue(row['components']['values']['available'])
        self.assertEqual(row['components']['values']['score'], 1)
        self.assertEqual(profiled['profile']['min'], 0)
        self.assertAlmostEqual(profiled['profile']['null_rate'], 1 / 3)
        text_field = next(f for f in self.catalog.objects(kind='field') if f['name'] == 'label')
        text_profile = self.catalog.profile(text_field['id'], 'reviewer')
        text_concept = self.concept(name='label', data_type='text', range={'min_length': 1, 'max_length': 3})
        self.assertTrue(self.candidate(text_concept, text_profile)['components']['values']['available'])

    def test_historical_profiles_work_but_empty_unknown_or_inconsistent_counts_do_not(self):
        concept = {'name': 'age', 'meaning': 'Age', 'domain': 'demo', 'data_type': 'integer', 'range': {'min': 0, 'max': 120}}
        field = {'name': 'age', 'structure': {'type': 'INTEGER'}}
        legacy = {'row_count': 2, 'non_null_count': 2, 'min': 0, 'max': 30}
        self.assertTrue(rank(concept, {**field, 'profile': legacy}, {}, [], family)[2]['values']['available'])
        invalid = [
            {**legacy, 'non_null_count': 0}, {**legacy, 'non_null_count': None},
            {**legacy, 'non_null': 0}, {**legacy, 'non_null': None},
            {**legacy, 'non_null_count': True}, {**legacy, 'non_null_count': 3},
            {**legacy, 'numeric_ratio': None}, {**legacy, 'row_count': 2.5},
            {'count': 2, 'min': 0, 'max': 30}, {'min': 0, 'max': 30}
        ]
        for profile in invalid:
            with self.subTest(profile=profile):
                self.assertFalse(rank(concept, {**field, 'profile': profile}, {}, [], family)[2]['values']['available'])

    def test_confirmed_rule_changes_same_type_candidate_order_with_explanation(self):
        concept = self.concept()
        source = self.fields('training', ['age_years'])['age_years']
        target = self.fields('new_source', ['age_yrs', 'age_years'])
        before = [r for r in self.semantic.candidates(concept['id']) if r['source'] == 'new_source']
        self.assertEqual(before[0]['name'], 'age_yrs')
        mapping = self.learn(concept, source)
        after = [r for r in self.semantic.candidates(concept['id']) if r['source'] == 'new_source']
        self.assertEqual(after[0]['field_id'], target['age_years']['id'])
        self.assertGreater(after[0]['score'], after[0]['base_score'])
        self.assertEqual(after[0]['experience']['rules'][0]['mapping_id'], mapping['id'])
        self.assertEqual(after[0]['experience']['rules'][0]['confirmed_by'], 'reviewer')
        self.assertEqual(len(self.store.list('mapping')), 1)
        self.assertNotEqual(after[0]['status'], 'confirmed')

    def test_rule_never_overrides_hard_type_or_range_veto(self):
        concept = self.concept()
        source = self.fields('training', ['age_years'])['age_years']
        self.learn(concept, source)
        wrong_type = self.fields('text_source', ['age_years'], dtype='TEXT')['age_years']
        wrong_range = self.fields('bad_range', ['age_years'])['age_years']
        wrong_range = self.store.save('object', wrong_range['id'], {**wrong_range,
            'profile': {'count': 2, 'non_null': 2, 'min': 200, 'max': 300}}, wrong_range['version'])
        for field in (wrong_type, wrong_range):
            with self.subTest(field=field['id']):
                row = self.candidate(concept, field)
                self.assertEqual(row['status'], 'rejected')
                self.assertEqual(row['score'], 0)
                self.assertEqual(row['experience']['rules'], [])
                with self.assertRaises(DomainError):
                    self.learn(concept, field)

    def test_rule_does_not_replace_missing_profile_or_human_confirmation(self):
        concept = self.concept(name='age_years')
        self.learn(concept, self.fields('training', ['age_years'])['age_years'])
        target = self.fields('new_source', ['age_years'], profile=False)['age_years']
        row = self.candidate(concept, target)
        self.assertGreater(row['experience']['bonus'], 0)
        self.assertFalse(row['components']['values']['available'])
        self.assertEqual(row['status'], 'needs_confirmation')
        self.assertEqual(len(self.store.list('mapping')), 1)

    def test_disabled_or_stale_rule_is_ignored(self):
        for mutation in ('disabled', 'field_version', 'concept_version', 'mapping_version'):
            with self.subTest(mutation=mutation):
                concept = self.concept(name='age_' + mutation)
                source = self.fields('training_' + mutation, ['age_years'])['age_years']
                target = self.fields('target_' + mutation, ['age_years'])['age_years']
                mapping = self.learn(concept, source)
                self.assertGreater(self.candidate(concept, target)['experience']['bonus'], 0)
                if mutation == 'disabled':
                    self.semantic.mapping({**mapping, 'status': 'disabled', 'expected_version': mapping['version']}, 'reviewer')
                elif mutation == 'field_version':
                    self.catalog.annotate(source['id'], {'meaning': 'Meaning changed', 'expected_version': source['version']}, 'reviewer')
                elif mutation == 'concept_version':
                    concept = self.semantic.concept({**concept, 'meaning': 'Definition changed', 'expected_version': concept['version']}, 'reviewer')
                else:
                    self.store.save('mapping', mapping['id'], mapping, mapping['version'], 'reviewer', 'Legacy rule has not been refreshed')
                self.assertEqual(self.candidate(concept, target)['experience']['bonus'], 0)

    def test_complex_mapping_input_is_not_reused_as_direct_output(self):
        concept = self.concept()
        source = self.fields('training', ['age_years'])['age_years']
        target = self.fields('target', ['age_years'])['age_years']
        self.semantic.mapping({'concept_id': concept['id'], 'fields': [source['id']], 'mapping_type': 'derived',
            'expression': {'operator': 'add', 'args': [{'field': source['id']}, {'literal': 1}]},
            'status': 'confirmed', 'confirmation_note': 'Derived output checked'}, 'reviewer')
        self.assertEqual(self.candidate(concept, target)['experience']['bonus'], 0)


if __name__ == '__main__':
    unittest.main()
