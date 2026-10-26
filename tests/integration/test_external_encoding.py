"""Persisted cloud metadata uses UTF-8 independently of the host locale."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import external_discovery as discovery


class ExternalEncodingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'registry'
        self.directory_patch = patch.object(discovery, 'DIRECTORY', self.directory)
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)
        # Force omitted encodings to behave like a GBK Windows installation,
        # even when CI or the Python process itself runs in UTF-8 mode.
        original_read_text = Path.read_text

        def gbk_read_text(path, encoding=None, errors=None):
            return original_read_text(path, encoding=encoding or 'gbk', errors=errors)

        self.locale_patch = patch.object(Path, 'read_text', gbk_read_text)
        self.locale_patch.start()
        self.addCleanup(self.locale_patch.stop)
        self.username = '研究组甲'
        self.records = {
            'table-a': {
                'id': 'table-a', 'name': '诊疗统计🩺',
                'columns': ['地区', '研究人数'], 'types': ['VARCHAR', 'INTEGER'],
                'path': '私有目录/诊疗统计.csv', 'member': None,
                'sheet': '研究数据', 'status': '可查询',
            }
        }

    def test_registry_readers_preserve_unicode_and_account_isolation(self):
        discovery.save_registry(self.username, self.records)
        self.assertEqual(discovery.read_registry(self.username), self.records)
        self.assertEqual(discovery.definitions(self.username), list(self.records.values()))
        self.assertEqual(discovery.read_registry('其他研究组'), {})
        public = discovery.definitions()
        self.assertEqual(public[0]['name'], '诊疗统计🩺')
        self.assertEqual(public[0]['columns'], ['地区', '研究人数'])
        self.assertNotIn('path', public[0])
        self.assertNotIn('member', public[0])

    def test_collection_readers_and_membership_survive_non_utf8_locale(self):
        discovery.save_registry(self.username, self.records)
        groups = {
            'union-a': {
                'id': 'union-a', 'name': '研究资料🩺 · 统一虚拟表',
                'root': '私有目录', 'source_ids': ['table-a'],
                'excluded_source_ids': [], 'incomplete': False,
            }
        }
        discovery.save_collections(self.username, groups)
        self.assertEqual(discovery.collection_records(self.username), groups)
        owned = discovery.collections(self.username)[0]
        self.assertEqual(owned['name'], groups['union-a']['name'])
        self.assertIn('数据·研究人数', owned['columns'])
        self.assertEqual(owned['sources'][0]['path'], '私有目录/诊疗统计.csv')
        public = discovery.collections()[0]
        self.assertNotIn('root', public)
        self.assertEqual(public['sources'][0]['path'], '连接后可查看')
        self.assertEqual(discovery.collections('其他研究组'), [])
        discovery.change_membership('union-a', 'table-a', self.username)
        self.assertEqual(discovery.collections(self.username)[0]['source_ids'], [])
        discovery.change_membership('union-a', 'table-a', self.username, restore=True)
        self.assertEqual(discovery.collections(self.username)[0]['source_ids'], ['table-a'])
        self.assertEqual(discovery.read_registry(self.username), self.records)

    def test_platform_metadata_bridge_reads_the_same_unicode_registry(self):
        from execution.evidence import Evidence
        from management.facade import Management
        from management.catalog.external import publish, sync

        root = Path(self.temp.name)
        management = Management(root / 'management.db', Evidence(root / 'evidence.db'))
        discovery.save_registry(self.username, self.records)
        published = publish(management.catalog, self.username, ['table-a'], 'demo-platform')
        objects = management.catalog.objects(source_id=published['source_id'])
        table = next(obj for obj in objects if obj['kind'] == 'table')
        self.assertEqual(table['meaning'], '诊疗统计🩺')
        self.assertEqual({obj['name'] for obj in objects if obj['kind'] == 'field'}, {'地区', '研究人数'})
        # Periodic synchronization reads the same UTF-8 file independently of
        # the public discovery readers; exercise that path after a schema edit.
        self.records['table-a']['columns'].append('月份')
        self.records['table-a']['types'].append('VARCHAR')
        discovery.save_registry(self.username, self.records)
        sync(management.catalog)
        self.assertIn('月份', {obj['name'] for obj in management.catalog.objects(kind='field')})
        self.assertNotIn('私有目录', str(management.catalog.objects()))
