"""Trim demo data by patient number, after stopping the server."""

import argparse
from contextlib import ExitStack, closing
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import socket
import sqlite3


ROOT = Path(__file__).resolve().parent
TABLES = {
    "his.db": ("patient", "visit", "orders"),
    "lis.db": ("test_order", "result"),
    "pacs.db": ("study",),
    "iot.db": ("vitals", "devices"),
}


def patient_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"P[0-9]+", value):
        raise ValueError(f"Invalid patient ID: {value!r}; no cleanup performed.")
    return int(value[1:])


def clean(data_dir, limit=2000, apply=False, port=8321):
    data_dir = Path(data_dir).resolve()
    if limit < 1:
        raise ValueError("Patient limit must be positive.")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} is in use. Stop the server first.")
    for name in TABLES:
        if not (data_dir / name).is_file():
            raise ValueError(f"Missing database: {data_dir / name}")
    if not (data_dir / "emr").is_dir():
        raise ValueError("Missing emr directory.")

    # Reserve every database before planning or changing any records.
    with ExitStack() as stack:
        connections = {}
        before = {}
        for name, tables in TABLES.items():
            con = stack.enter_context(closing(sqlite3.connect(
                (data_dir / name).as_uri() + "?mode=rw", uri=True, timeout=2)))
            connections[name] = con
            con.execute("BEGIN IMMEDIATE")
            before[name] = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                            for t in tables}
            for table in tables:
                columns = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
                if "patient_id" in columns:
                    for (pid,) in con.execute(f'SELECT DISTINCT patient_id FROM "{table}"'):
                        patient_number(pid)

        documents = []
        for path in sorted((data_dir / "emr").glob("*.json")):
            number = patient_number(path.stem)
            with path.open(encoding="utf-8") as source:
                doc = json.load(source)
            if doc.get("patient_id") != path.stem:
                raise ValueError(f"Patient ID does not match filename: {path.name}")
            if number > limit:
                documents.append(path)

        his = connections["his.db"]
        lis = connections["lis.db"]
        # Remove child records before their parent records.
        his.execute("DELETE FROM orders WHERE visit_id IN (SELECT visit_id FROM visit "
                    "WHERE CAST(SUBSTR(patient_id,2) AS INTEGER)>?)", (limit,))
        lis.execute("DELETE FROM result WHERE test_no IN (SELECT test_no FROM test_order "
                    "WHERE CAST(SUBSTR(patient_id,2) AS INTEGER)>?)", (limit,))
        for name, tables in (("his.db", ("visit", "patient")),
                             ("lis.db", ("test_order",)),
                             ("pacs.db", ("study",)), ("iot.db", ("vitals",))):
            for table in tables:
                connections[name].execute(
                    f'DELETE FROM "{table}" WHERE CAST(SUBSTR(patient_id,2) AS INTEGER)>?',
                    (limit,))

        report = {}
        for name, tables in TABLES.items():
            report[name] = {t: before[name][t] - connections[name].execute(
                f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        report["emr_json"] = len(documents)
        print(json.dumps(report, indent=2))
        if not apply:
            for con in connections.values():
                con.rollback()
            print("PREVIEW ONLY: no data changed. Use --apply to back up and clean.")
            return report

        backup = data_dir.parent / "data_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup.mkdir(parents=True, exist_ok=False)
        # Separate readers see the committed pre-cleanup state, including WAL data.
        for name in TABLES:
            with closing(sqlite3.connect((data_dir / name).as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(backup / name)) as dest:
                    src.backup(dest)
        shutil.copytree(data_dir / "emr", backup / "emr")
        print(f"Backup: {backup}", flush=True)
        try:
            for con in connections.values():
                con.commit()
            for path in documents:
                path.unlink()
            for con in connections.values():
                con.execute("VACUUM")
        except Exception:
            print(f"Cleanup incomplete. Keep the server stopped and restore the FULL backup: {backup}")
            raise
        print(f"Done. Kept patient numbers <= {limit}; device records unchanged.")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and apply cleanup (default: preview)")
    parser.add_argument("--max-patient-id", type=int, default=2000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--port", type=int, default=8321, help="Server port to check before cleanup")
    args = parser.parse_args()
    try:
        clean(args.data_dir, args.max_patient_id, args.apply, args.port)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"Cleanup stopped: {exc}\n")


if __name__ == "__main__":
    main()
