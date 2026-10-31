import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cleanup_data import BACKUP_MANIFEST, _is_link, _rotate_backups, _write_manifest, clean, main


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        (self.data / "emr").mkdir(parents=True)
        schemas = {
            "his.db": "CREATE TABLE patient(patient_id TEXT); CREATE TABLE visit(visit_id INTEGER, patient_id TEXT); CREATE TABLE orders(order_id INTEGER, visit_id INTEGER);",
            "lis.db": "CREATE TABLE test_order(test_no TEXT, patient_id TEXT, visit_id INTEGER); CREATE TABLE result(result_id INTEGER, test_no TEXT);",
            "pacs.db": "CREATE TABLE study(study_uid TEXT, patient_id TEXT);",
            "iot.db": "CREATE TABLE vitals(vital_id INTEGER, patient_id TEXT); CREATE TABLE devices(device_id TEXT); INSERT INTO devices VALUES ('D1');",
        }
        for name, schema in schemas.items():
            with contextlib.closing(sqlite3.connect(self.data / name)) as con:
                con.executescript(schema)
                for i, pid in enumerate(("P0001", "P2000", "P2001", "P10000"), 1):
                    if name == "his.db":
                        con.execute("INSERT INTO patient VALUES (?)", (pid,))
                        con.execute("INSERT INTO visit VALUES (?,?)", (i, pid))
                        con.execute("INSERT INTO orders VALUES (?,?)", (i, i))
                    elif name == "lis.db":
                        con.execute("INSERT INTO test_order VALUES (?,?,?)", (str(i), pid, i))
                        con.execute("INSERT INTO result VALUES (?,?)", (i, str(i)))
                    elif name == "pacs.db":
                        con.execute("INSERT INTO study VALUES (?,?)", (str(i), pid))
                    else:
                        con.execute("INSERT INTO vitals VALUES (?,?)", (i, pid))
                con.commit()
        for pid in ("P0001", "P2000", "P2001", "P10000"):
            (self.data / "emr" / f"{pid}.json").write_text(json.dumps({"patient_id": pid}))

    def run_clean(self, apply=False, **kwargs):
        with patch("cleanup_data.socket.socket") as probe, contextlib.redirect_stdout(io.StringIO()):
            probe.return_value.__enter__.return_value.connect_ex.return_value = 1
            return clean(self.data, apply=apply, **kwargs)

    def count(self, root, db, table):
        with contextlib.closing(sqlite3.connect(root / db)) as con:
            return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def test_preview_apply_backup_and_repeat(self):
        report = self.run_clean()
        self.assertEqual(report["emr_json"], 2)
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)
        self.assertEqual(len(list((self.data / "emr").glob("*.json"))), 4)
        self.assertFalse((self.data.parent / "data_backups").exists())
        self.run_clean(True)
        for db, tables in {"his.db": ("patient", "visit", "orders"),
                           "lis.db": ("test_order", "result"),
                           "pacs.db": ("study",), "iot.db": ("vitals",)}.items():
            for table in tables:
                self.assertEqual(self.count(self.data, db, table), 2)
        self.assertEqual(self.count(self.data, "iot.db", "devices"), 1)
        self.assertEqual({p.stem for p in (self.data / "emr").glob("*.json")}, {"P0001", "P2000"})
        backup = next((self.data.parent / "data_backups").iterdir())
        self.assertEqual(self.count(backup, "his.db", "patient"), 4)
        self.assertEqual(len(list((backup / "emr").glob("*.json"))), 4)
        self.assertEqual(self.run_clean(True)["emr_json"], 0)
        self.assertEqual(len(list(backup.parent.iterdir())), 1)
        manifest = json.loads((backup / BACKUP_MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["cleanup_status"], "complete")

    def test_invalid_json_leaves_data_unchanged(self):
        (self.data / "emr" / "P2001.json").write_text('{"patient_id":"P0001"}')
        with self.assertRaises(ValueError):
            self.run_clean(True)
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)

    def test_backup_failure_rolls_back(self):
        with patch("cleanup_data.shutil.copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_clean(True)
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)
        self.assertEqual(len(list((self.data / "emr").glob("*.json"))), 4)
        backup = next((self.data.parent / "data_backups").iterdir())
        manifest = json.loads((backup / BACKUP_MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["cleanup_status"], "pending")

    def make_backup(self, number, status="complete", source=None):
        backup = self.data.parent / "data_backups" / f"20260101_000000_{number:06d}"
        shutil.copytree(self.data, backup)
        if status is not None:
            _write_manifest(backup, source or self.data.resolve(), status)
        return backup

    def rotate(self, latest, keep=3, max_bytes=512 * 1024 * 1024):
        with contextlib.redirect_stdout(io.StringIO()):
            return _rotate_backups(self.data.resolve(), latest, keep, max_bytes)

    def test_backup_count_limit_protects_foreign_pending_and_unmarked(self):
        unmarked = self.make_backup(1, status=None)
        pending = self.make_backup(2, status="pending")
        foreign = self.make_backup(3, source=self.data.parent / "other-data")
        managed = [self.make_backup(i) for i in range(4, 9)]
        removed = self.rotate(managed[-1])
        self.assertEqual(removed, [item.name for item in managed[:2]])
        self.assertTrue(all(item.exists() for item in [unmarked, pending, foreign] + managed[2:]))
        self.assertTrue(all(not item.exists() for item in managed[:2]))

    def test_backup_size_limit_keeps_latest_even_when_oversized(self):
        first = self.make_backup(1)
        latest = self.make_backup(2)
        (latest / "payload.bin").write_bytes(b"x" * 1024)
        self.assertEqual(self.rotate(latest, max_bytes=1), [first.name])
        self.assertTrue(latest.is_dir())
        self.assertEqual(self.count(latest, "his.db", "patient"), 4)

    def test_cleanup_applies_retention_only_after_successful_backup(self):
        old = [self.make_backup(i) for i in range(1, 5)]
        self.run_clean(True, backup_keep=2)
        self.assertTrue(old[-1].exists())
        self.assertTrue(all(not item.exists() for item in old[:-1]))
        self.assertEqual(len(list(old[0].parent.iterdir())), 2)

    def test_failed_cleanup_keeps_all_recovery_points(self):
        old = [self.make_backup(i) for i in range(1, 5)]
        with patch("cleanup_data.Path.unlink", side_effect=OSError("cannot delete EMR")):
            with self.assertRaises(OSError):
                self.run_clean(True, backup_keep=1)
        self.assertTrue(all(item.exists() for item in old))
        backups = list(old[0].parent.iterdir())
        self.assertEqual(len(backups), 5)
        pending = next(item for item in backups if item not in old)
        self.assertEqual(json.loads((pending / BACKUP_MANIFEST).read_text())["cleanup_status"], "pending")
        self.assertEqual(self.count(pending, "his.db", "patient"), 4)

    def test_backup_path_must_remain_under_root(self):
        self.make_backup(1)
        outside = self.data.parent / "20260101_000000_000002"
        shutil.copytree(self.data, outside)
        with self.assertRaises(ValueError):
            self.rotate(outside)
        self.assertTrue(outside.exists())

    def test_linked_snapshot_is_never_deleted(self):
        linked = self.make_backup(1)
        latest = self.make_backup(2)
        outside = self.data.parent / "must-keep.txt"
        outside.write_text("keep")
        try:
            (linked / "external-link").symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        self.assertEqual(self.rotate(latest, keep=1), [])
        self.assertTrue(linked.exists())
        self.assertEqual(outside.read_text(), "keep")

    def test_backup_root_link_is_rejected_before_data_changes(self):
        outside = self.data.parent / "outside"
        outside.mkdir()
        try:
            (self.data.parent / "data_backups").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaises(ValueError):
            self.run_clean(True)
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)
        self.assertEqual(list(outside.iterdir()), [])

    def test_detected_reparse_entry_protects_entire_snapshot(self):
        linked = self.make_backup(1)
        latest = self.make_backup(2)
        entry = linked / "emr"
        with patch("cleanup_data._is_link", side_effect=lambda path: path == entry or _is_link(path)):
            self.assertEqual(self.rotate(latest, keep=1), [])
        self.assertTrue(linked.is_dir())
        self.assertEqual(self.count(linked, "his.db", "patient"), 4)

    def test_detected_reparse_root_stops_cleanup(self):
        root = self.data.parent / "data_backups"
        root.mkdir()
        with patch("cleanup_data._is_link", side_effect=lambda path: path == root or _is_link(path)):
            with self.assertRaises(ValueError):
                self.run_clean(True)
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)
        self.assertEqual(list(root.iterdir()), [])

    def test_invalid_retention_limits_are_rejected(self):
        for kwargs in ({"backup_keep": 0}, {"backup_max_mib": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_clean(True, **kwargs)
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)
        self.assertFalse((self.data.parent / "data_backups").exists())

    def test_cli_uses_configured_data_directory(self):
        with patch.dict(os.environ, {"MEDFABRIC_DATA_DIR": str(self.data)}), \
                patch("sys.argv", ["cleanup_data.py"]), patch("cleanup_data.clean") as operation:
            main()
        self.assertEqual(operation.call_args.args[0], self.data)

    def test_running_server_is_rejected(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(RuntimeError):
                clean(self.data, apply=True, port=listener.getsockname()[1])
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)


if __name__ == "__main__":
    unittest.main()
