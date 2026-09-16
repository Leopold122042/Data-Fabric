import contextlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cleanup_data import clean


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

    def run_clean(self, apply=False):
        with patch("cleanup_data.socket.socket") as probe, contextlib.redirect_stdout(io.StringIO()):
            probe.return_value.__enter__.return_value.connect_ex.return_value = 1
            return clean(self.data, apply=apply)

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

    def test_running_server_is_rejected(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(RuntimeError):
                clean(self.data, apply=True, port=listener.getsockname()[1])
        self.assertEqual(self.count(self.data, "his.db", "patient"), 4)


if __name__ == "__main__":
    unittest.main()
