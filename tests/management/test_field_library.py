import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from management.store import Store, DomainError
from management.catalog.service import Catalog
from management.catalog.field_library import FieldLibrary
from management.intake.flow import IntakeFlow

class FieldLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'store.db')
        self.catalog=Catalog(self.store)
        self.library=FieldLibrary(self.store)
        self.ingest()
        self.field=self.library.items()[0]
    def ingest(self,kind='TEXT',columns=None):
        self.catalog.ingest('his',{'tables':{'patient':{'columns':columns or ['gender'], 'column_details':[{'name':x,'type':kind} for x in (columns or ['gender'])]}}})
    def confirm(self):
        r=self.library.items()[0]
        return self.library.save(r['id'],{'expected_version':r['version'],'confirmation_note':'已核对来源定义'},'reviewer',True)
    def test_drafts_do_not_match_and_confirmed_aliases_persist(self):
        self.assertEqual(self.library.match('查询性别')['items'],[])
        self.confirm()
        self.assertEqual(len(FieldLibrary(Store(self.store.path)).match('查询性别')['items']),1)
    def test_edit_revokes_confirmation_and_stale_version_fails(self):
        row=self.confirm()
        self.library.save(row['id'],{'expected_version':row['version'],'definition':'新定义'},'editor')
        self.assertEqual(self.library.confirmed(),{})
        with self.assertRaises(DomainError):
            self.library.save(row['id'],{'expected_version':row['version'],'confirmation_note':'旧版本'},'reviewer',True)
    def test_scan_addition_change_and_deletion(self):
        self.confirm();self.ingest('INTEGER',['gender','new_field'])
        self.assertEqual(self.library.confirmed(),{})
        rows={r['name']:r for r in self.library.items()}
        self.assertEqual(rows['gender']['status'],'review_required')
        self.assertEqual(rows['new_field']['status'],'draft')
        self.ingest('INTEGER',['new_field'])
        self.assertEqual(next(r for r in self.library.items() if r['name']=='gender')['status'],'retired')
    def test_metadata_consumes_only_confirmed_definition(self):
        runtime=SimpleNamespace(management=SimpleNamespace(store=self.store))
        flow=IntakeFlow(runtime)
        self.assertNotIn('aliases',next(r for r in flow._catalog_metadata() if r['kind']=='field'))
        row=self.confirm()
        metadata=next(r for r in flow._catalog_metadata() if r['kind']=='field')
        self.assertIn('性别',metadata['aliases'])
        self.assertEqual(metadata['definition_version'],row['version'])
    def test_blank_confirmation_and_definition_are_rejected(self):
        with self.assertRaises(DomainError):self.library.save(self.field['id'],{'expected_version':1},'reviewer',True)
        row=self.library.save(self.field['id'],{'expected_version':1,'definition':''},'editor')
        with self.assertRaises(DomainError):self.library.save(row['id'],{'expected_version':row['version'],'confirmation_note':'核对'},'reviewer',True)
    def test_sync_idempotent_and_unscanned_change_cannot_match(self):
        row=self.confirm();self.assertEqual(self.library.sync(),[])
        field=self.store.require('object',row['id'])
        self.store.save('object',field['id'],{**field,'structure':{'name':'gender','type':'INTEGER'}},field['version'])
        self.assertEqual(self.library.confirmed(),{})
        with self.assertRaises(DomainError):self.library.save(row['id'],{'expected_version':row['version'],'confirmation_note':'核对'},'reviewer',True)
