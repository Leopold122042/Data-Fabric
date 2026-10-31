import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import medsim
from initialize_data import initialize, validate_data


class DataInitializationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.data = self.parent / 'sources'

    def hashes(self):
        return {p.relative_to(self.data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.data.rglob('*')
                if p.is_file() and not p.name.endswith(('-wal', '-shm'))}

    def test_empty_directory_initializes_and_repeat_preserves_persistent_files(self):
        self.data.mkdir()
        self.assertEqual(initialize(self.data)['status'], 'initialized')
        validate_data(self.data)
        with sqlite3.connect(self.data / 'his.db') as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM patient').fetchone()[0], 180)
        # Explicit close also matters on Windows, where open files cannot be removed.
        con.close()
        before = self.hashes()
        self.assertEqual(initialize(self.data, seed=13)['status'], 'existing')
        self.assertEqual(self.hashes(), before)

    def test_nonempty_incomplete_directory_is_preserved(self):
        self.data.mkdir()
        (self.data / 'notes.txt').write_text('keep this', encoding='utf-8')
        before = self.hashes()
        with self.assertRaisesRegex(ValueError, 'preserved'):
            initialize(self.data)
        self.assertEqual(self.hashes(), before)
        with self.assertRaisesRegex(ValueError, 'not empty'):
            medsim.generate_base(data_dir=self.data)
        self.assertEqual(self.hashes(), before)

    def test_generation_failure_never_publishes_partial_data(self):
        original = medsim._conn
        def fail_second_database(name, data_dir=None):
            if name == 'lis.db':
                raise sqlite3.OperationalError('generation interrupted')
            return original(name, data_dir)
        with patch.object(medsim, '_conn', side_effect=fail_second_database):
            with self.assertRaises(sqlite3.OperationalError):
                initialize(self.data)
        self.assertFalse(self.data.exists())
        self.assertEqual(list(self.parent.iterdir()), [])
        self.assertEqual(initialize(self.data)['status'], 'initialized')

    def test_existing_lock_prevents_concurrent_initialization(self):
        lock = self.parent / '.sources.initialize.lock'
        lock.write_text('other initializer', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'lock exists'):
            initialize(self.data)
        self.assertEqual(lock.read_text(encoding='utf-8'), 'other initializer')
        self.assertFalse(self.data.exists())

    def test_corrupted_existing_database_is_not_replaced(self):
        initialize(self.data)
        (self.data / 'lis.db').write_bytes(b'not a database')
        before = self.hashes()
        with self.assertRaisesRegex(ValueError, 'preserved'):
            initialize(self.data)
        self.assertEqual(self.hashes(), before)

    def test_new_file_during_generation_prevents_publication(self):
        generator = medsim.generate_base
        def concurrent_writer(**kwargs):
            result = generator(**kwargs)
            self.data.mkdir()
            (self.data / 'new.txt').write_text('other process', encoding='utf-8')
            return result
        with patch.object(medsim, 'generate_base', side_effect=concurrent_writer):
            with self.assertRaises(OSError):
                initialize(self.data)
        self.assertEqual((self.data / 'new.txt').read_text(encoding='utf-8'), 'other process')
        self.assertEqual(list(self.data.iterdir()), [self.data / 'new.txt'])

    def test_cli_honors_environment_and_old_entrypoint_is_idempotent(self):
        project = Path(__file__).resolve().parents[2]
        env = dict(os.environ, MEDFABRIC_DATA_DIR=str(self.data), PYTHONDONTWRITEBYTECODE='1')
        first = subprocess.run([sys.executable, str(project / 'initialize_data.py')],
                               env=env, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.hashes()
        second = subprocess.run([sys.executable, str(project / 'medsim.py')],
                                env=env, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('existing', second.stdout)
        self.assertEqual(self.hashes(), before)


if __name__ == '__main__':
    unittest.main()
