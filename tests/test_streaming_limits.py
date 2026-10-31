import os
import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import medsim


class StreamingLimitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "emr").mkdir()
        self.data_patch = patch.object(medsim, "DATA_DIR", str(self.root))
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        schemas = {
            "his.db": """
                CREATE TABLE patient(patient_id TEXT PRIMARY KEY, name TEXT, gender TEXT,
                    birth_date TEXT, id_card TEXT, insurance_type TEXT);
                CREATE TABLE visit(visit_id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT,
                    vtype TEXT, dept TEXT, doctor TEXT, admission_time TEXT, diagnosis_code TEXT,
                    diagnosis_name TEXT, chief_complaint TEXT);
            """,
            "lis.db": """
                CREATE TABLE test_order(test_no TEXT PRIMARY KEY, patient_id TEXT, visit_id INTEGER,
                    sample_type TEXT, collect_time TEXT, report_time TEXT, status TEXT);
                CREATE TABLE result(result_id INTEGER PRIMARY KEY AUTOINCREMENT, test_no TEXT,
                    item_code TEXT, item_name TEXT, value_num REAL, unit TEXT, ref_range TEXT, flag TEXT);
            """,
            "pacs.db": """
                CREATE TABLE study(study_uid TEXT PRIMARY KEY, patient_id TEXT, modality TEXT,
                    body_part TEXT, exam_date TEXT, accession_no TEXT, files INTEGER, size_mb REAL,
                    report_summary TEXT);
            """,
            "iot.db": """
                CREATE TABLE vitals(vital_id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT,
                    ts TEXT, heart_rate REAL, spo2 REAL, temp REAL);
            """,
        }
        for name, schema in schemas.items():
            with closing(sqlite3.connect(self.root / name)) as con:
                con.executescript(schema)
                if name == "his.db":
                    con.executemany("INSERT INTO patient VALUES (?, '姓名', '男', '1960-', '', '职工医保')",
                                    [("P%04d" % i,) for i in range(1, 4)])
                elif name == "iot.db":
                    con.executemany("INSERT INTO vitals(patient_id,ts,heart_rate,spo2,temp) VALUES (?, ?, 80, 98, 36.5)",
                                    [("P%04d" % i, medsim.NOW.strftime("%Y-%m-%d %H:%M")) for i in range(1, 4)])
                con.commit()

    def rows(self, name, table):
        with closing(sqlite3.connect(self.root / name)) as con:
            return con.execute("SELECT * FROM " + table).fetchall()

    def simulator(self, **kwargs):
        return medsim.StreamingSim(random.Random(42), **kwargs)

    def test_defaults_and_environment_overrides(self):
        with patch.dict(os.environ, {}, clear=True):
            sim = self.simulator()
            self.assertEqual((sim.max_patients, sim.max_vitals), (2000, 100000))
        with patch.dict(os.environ, {"MEDFABRIC_MAX_PATIENTS": "10", "MEDFABRIC_MAX_VITALS": "40"}):
            sim = self.simulator()
            self.assertEqual((sim.max_patients, sim.max_vitals), (10, 40))

    def test_invalid_limits_fail_before_opening_data(self):
        for name in ("MEDFABRIC_MAX_PATIENTS", "MEDFABRIC_MAX_VITALS"):
            for value in ("0", "-1", "1.5", "many", ""):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}), \
                        patch.object(medsim, "_conn") as connect:
                    with self.assertRaisesRegex(ValueError, name + " must be a positive integer"):
                        self.simulator()
                    connect.assert_not_called()
        for value in (True, 2.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "MEDFABRIC_MAX_PATIENTS"):
                self.simulator(max_patients=value)

    def test_vitals_stop_at_exact_boundary(self):
        sim = self.simulator(max_patients=3, max_vitals=4)
        for _ in range(30):
            sim.tick()
        self.assertEqual(len(self.rows("iot.db", "vitals")), 4)
        self.assertEqual(len(self.rows("his.db", "patient")), 3)

    def test_arrival_requires_capacity_for_all_three_initial_vitals(self):
        sim = self.simulator(max_patients=4, max_vitals=5)
        next_pid = sim.next_pid
        self.assertFalse(sim._start_arrival(medsim.NOW))
        self.assertEqual(sim.next_pid, next_pid)
        self.assertEqual(sim.pending, [])
        self.assertEqual(len(self.rows("iot.db", "vitals")), 3)

    def test_pending_patient_reservation_and_existing_stages_finish(self):
        sim = self.simulator(max_patients=4, max_vitals=6)
        self.assertTrue(sim._start_arrival(medsim.NOW))
        self.assertFalse(sim._start_arrival(medsim.NOW))
        for _ in range(30):
            sim.tick()
        self.assertEqual(len(self.rows("his.db", "patient")), 4)
        self.assertEqual(len(self.rows("iot.db", "vitals")), 6)
        self.assertEqual(len(self.rows("lis.db", "test_order")), 1)
        self.assertTrue((self.root / "emr" / "P0004.json").is_file())
        self.assertEqual(sim.pending, [])

    def test_restart_does_not_reset_capacity(self):
        for _ in range(2):
            sim = self.simulator(max_patients=4, max_vitals=80)
            for _ in range(50):
                sim.tick()
        self.assertEqual(len(self.rows("his.db", "patient")), 4)
        self.assertEqual(len(self.rows("iot.db", "vitals")), 80)
        self.assertEqual(len(list((self.root / "emr").glob("*.json"))), 1)

    def test_existing_over_limit_records_are_preserved(self):
        patients = self.rows("his.db", "patient")
        vitals = self.rows("iot.db", "vitals")
        sim = self.simulator(max_patients=2, max_vitals=2)
        self.assertEqual(self.rows("his.db", "patient"), patients)
        self.assertEqual(self.rows("iot.db", "vitals"), vitals)
        for _ in range(20):
            sim.tick()
        self.assertEqual(self.rows("his.db", "patient"), patients)
        self.assertEqual(self.rows("iot.db", "vitals"), vitals)

    def test_connections_are_closed_after_normal_pipeline_and_tick_error(self):
        opened = []
        real_connect = medsim._conn

        def tracked_connect(name):
            con = real_connect(name)
            opened.append(con)
            return con

        with patch.object(medsim, "_conn", side_effect=tracked_connect):
            sim = self.simulator(max_patients=4, max_vitals=80)
            for _ in range(30):
                sim.tick()
            with patch.object(sim, "_tick_vitals", side_effect=RuntimeError("interrupted")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    sim.tick()
        for con in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                con.execute("SELECT 1")

    def test_failed_arrival_rolls_back_and_does_not_reserve_patient(self):
        with closing(sqlite3.connect(self.root / "iot.db")) as con:
            con.execute("CREATE TRIGGER reject_vitals BEFORE INSERT ON vitals BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            con.commit()
        sim = self.simulator(max_patients=4, max_vitals=10)
        next_pid = sim.next_pid
        with self.assertRaisesRegex(sqlite3.IntegrityError, "blocked"):
            sim._start_arrival(medsim.NOW)
        self.assertEqual(sim.next_pid, next_pid)
        self.assertEqual(sim.pending, [])
        self.assertEqual(len(self.rows("iot.db", "vitals")), 3)
        # A new write transaction succeeds: the failed arrival released its lock.
        with closing(sqlite3.connect(self.root / "iot.db", timeout=0.1)) as con:
            con.execute("BEGIN IMMEDIATE")
            con.rollback()

    def test_failed_patient_stage_rolls_back_and_can_resume(self):
        sim = self.simulator(max_patients=4, max_vitals=20)
        self.assertTrue(sim._start_arrival(medsim.NOW))
        arrival = sim.pending[0]
        with closing(sqlite3.connect(self.root / "his.db")) as con:
            con.execute("CREATE TRIGGER reject_visit BEFORE INSERT ON visit BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            con.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "blocked"):
            sim._advance(arrival, medsim.NOW)
        self.assertEqual(arrival["stage"], 0)
        self.assertEqual(len(self.rows("his.db", "patient")), 3)
        self.assertEqual(self.rows("his.db", "visit"), [])
        with closing(sqlite3.connect(self.root / "his.db", timeout=0.1)) as con:
            con.execute("DROP TRIGGER reject_visit")
            con.commit()
        sim._advance(arrival, medsim.NOW)
        self.assertEqual(arrival["stage"], 1)
        self.assertEqual(len(self.rows("his.db", "patient")), 4)
        self.assertEqual(len(self.rows("his.db", "visit")), 1)

    def test_repeated_emr_write_failure_preserves_document_and_rolls_back_studies(self):
        sim = self.simulator(max_patients=4, max_vitals=20)
        sim._start_arrival(medsim.NOW)
        arrival = sim.pending[0]
        sim._advance(arrival, medsim.NOW)
        sim._advance(arrival, medsim.NOW)
        target = self.root / "emr" / "P0004.json"
        original = '{"patient_id":"P0004","note":"previous complete document"}'
        target.write_text(original, encoding="utf-8")

        def interrupted_dump(value, fh, **kwargs):
            fh.write('{"incomplete":')
            raise OSError("disk write interrupted")

        with patch.object(sim.rng, "random", return_value=0), \
                patch.object(medsim.json, "dump", side_effect=interrupted_dump):
            for _ in range(5):
                with self.assertRaisesRegex(OSError, "disk write interrupted"):
                    sim._advance(arrival, medsim.NOW)
                self.assertEqual(arrival["stage"], 2)
                self.assertEqual(self.rows("pacs.db", "study"), [])
                self.assertEqual(target.read_text(encoding="utf-8"), original)
                self.assertEqual(list((self.root / "emr").iterdir()), [target])

        with patch.object(sim.rng, "random", return_value=0):
            sim._advance(arrival, medsim.NOW)
        self.assertEqual(arrival["stage"], 3)
        self.assertEqual(len(self.rows("pacs.db", "study")), 1)
        self.assertEqual(medsim.json.loads(target.read_text(encoding="utf-8"))["patient_id"], "P0004")
        for _ in range(30):
            sim.tick()
        self.assertEqual(len(self.rows("pacs.db", "study")), 1)
        self.assertEqual(sim.pending, [])

    def test_emr_replace_failure_preserves_existing_document_and_cleans_temporary(self):
        sim = self.simulator(max_patients=4, max_vitals=20)
        sim._start_arrival(medsim.NOW)
        arrival = sim.pending[0]
        sim._advance(arrival, medsim.NOW)
        sim._advance(arrival, medsim.NOW)
        target = self.root / "emr" / "P0004.json"
        target.write_text("previous document", encoding="utf-8")
        with patch.object(sim.rng, "random", return_value=0), \
                patch.object(medsim.os, "replace", side_effect=PermissionError("document is open")):
            with self.assertRaisesRegex(PermissionError, "document is open"):
                sim._advance(arrival, medsim.NOW)
        self.assertEqual(arrival["stage"], 2)
        self.assertEqual(self.rows("pacs.db", "study"), [])
        self.assertEqual(target.read_text(encoding="utf-8"), "previous document")
        self.assertEqual(list((self.root / "emr").iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
