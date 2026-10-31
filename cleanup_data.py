"""Trim demo data by patient number, after stopping the server."""

import argparse
from contextlib import ExitStack, closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat


ROOT = Path(__file__).resolve().parent
TABLES = {
    "his.db": ("patient", "visit", "orders"),
    "lis.db": ("test_order", "result"),
    "pacs.db": ("study",),
    "iot.db": ("vitals", "devices"),
}
BACKUP_MANIFEST = ".medfabric-backup.json"
BACKUP_OWNER = "medfabric.cleanup_data"
BACKUP_NAME = re.compile(r"[0-9]{8}_[0-9]{6}_[0-9]{6}")


def _is_link(path):
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _backup_root(data_dir, create=False):
    root = data_dir.parent / "data_backups"
    if root.exists() or root.is_symlink():
        if _is_link(root) or not root.is_dir() or root.resolve() != root:
            raise ValueError(f"Backup root must be a regular directory: {root}")
    elif create:
        root.mkdir()
    return root


def _snapshot_size(root, backup):
    # Check the absolute deletion boundary before looking inside a snapshot.
    if (not root.is_absolute() or root.resolve() != root or _is_link(root)
            or backup.parent != root or not BACKUP_NAME.fullmatch(backup.name)
            or backup.resolve() != backup or _is_link(backup) or not backup.is_dir()):
        raise ValueError(f"Unsafe backup path: {backup}")
    size = 0
    for folder, directories, files in os.walk(backup, followlinks=False):
        for name in directories + files:
            path = Path(folder) / name
            if _is_link(path):
                raise ValueError(f"Backup contains a link: {path}")
            if name in files:
                size += path.stat().st_size
    return size


def _write_manifest(backup, data_dir, status):
    manifest = {
        "owner": BACKUP_OWNER, "version": 1,
        "source_dir": str(data_dir), "cleanup_status": status,
    }
    temporary = backup / (BACKUP_MANIFEST + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(backup / BACKUP_MANIFEST)


def _rotate_backups(data_dir, latest, keep, max_bytes):
    root = _backup_root(data_dir)
    _snapshot_size(root, latest)
    snapshots = []
    for backup in root.iterdir():
        if not BACKUP_NAME.fullmatch(backup.name):
            continue
        try:
            size = _snapshot_size(root, backup)
            manifest = json.loads((backup / BACKUP_MANIFEST).read_text(encoding="utf-8"))
            if (not isinstance(manifest, dict) or manifest.get("owner") != BACKUP_OWNER
                    or manifest.get("version") != 1
                    or manifest.get("cleanup_status") != "complete"
                    or Path(manifest.get("source_dir", "")) != data_dir
                    or not all((backup / name).is_file() for name in TABLES)
                    or not (backup / "emr").is_dir()):
                continue
            snapshots.append((backup, size))
        except (OSError, ValueError, TypeError):
            # Old, incomplete, foreign, or linked directories are never rotated.
            continue
    snapshots.sort(key=lambda item: item[0].name)
    total = sum(size for _, size in snapshots)
    remaining = len(snapshots)
    removed = []
    for backup, size in snapshots:
        if remaining <= keep and total <= max_bytes:
            break
        if backup == latest:
            continue
        # Recheck the complete tree immediately before any recursive deletion.
        _snapshot_size(_backup_root(data_dir), backup)
        shutil.rmtree(backup)
        removed.append(backup.name)
        remaining -= 1
        total -= size
    if removed:
        print(f"Retired managed backups: {', '.join(removed)}")
    if total > max_bytes:
        print("Latest backup exceeds the size limit; kept it as the recovery point.")
    return removed


def patient_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"P[0-9]+", value):
        raise ValueError(f"Invalid patient ID: {value!r}; no cleanup performed.")
    return int(value[1:])


def clean(data_dir, limit=2000, apply=False, port=8321, backup_keep=3, backup_max_mib=512):
    data_dir = Path(data_dir).resolve()
    if limit < 1:
        raise ValueError("Patient limit must be positive.")
    if backup_keep < 1 or backup_max_mib < 1:
        raise ValueError("Backup count and size limits must be positive.")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} is in use. Stop the server first.")
    for name in TABLES:
        if not (data_dir / name).is_file():
            raise ValueError(f"Missing database: {data_dir / name}")
        if _is_link(data_dir / name):
            raise ValueError(f"Database must not be a link: {data_dir / name}")
    if not (data_dir / "emr").is_dir():
        raise ValueError("Missing emr directory.")
    if _is_link(data_dir / "emr"):
        raise ValueError("EMR directory must not be a link.")

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
            if _is_link(path):
                raise ValueError(f"EMR document must not be a link: {path}")
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

        if not documents and not any(value for counts in report.values()
                                     if isinstance(counts, dict) for value in counts.values()):
            for con in connections.values():
                con.rollback()
            print("No matching records to remove; no backup needed.")
            return report

        backup_root = _backup_root(data_dir, create=True)
        backup = backup_root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup.mkdir(exist_ok=False)
        _write_manifest(backup, data_dir, "pending")
        # Separate readers see the committed pre-cleanup state, including WAL data.
        for name in TABLES:
            with closing(sqlite3.connect((data_dir / name).as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(backup / name)) as dest:
                    src.backup(dest)
        shutil.copytree(data_dir / "emr", backup / "emr", symlinks=True)
        _snapshot_size(backup_root, backup)
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
        _write_manifest(backup, data_dir, "complete")
        try:
            _rotate_backups(data_dir, backup, backup_keep, backup_max_mib * 1024 * 1024)
        except (OSError, ValueError) as exc:
            print(f"Data cleanup succeeded; backup rotation stopped: {exc}")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and apply cleanup (default: preview)")
    parser.add_argument("--max-patient-id", type=int, default=2000)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("MEDFABRIC_DATA_DIR") or ROOT / "data"))
    parser.add_argument("--port", type=int, default=8321, help="Server port to check before cleanup")
    parser.add_argument("--backup-keep", type=int, default=3,
                        help="Maximum successful managed backups to retain (default: 3)")
    parser.add_argument("--backup-max-mib", type=int, default=512,
                        help="Managed backup size budget in MiB; always keep latest (default: 512)")
    args = parser.parse_args()
    try:
        clean(args.data_dir, args.max_patient_id, args.apply, args.port,
              args.backup_keep, args.backup_max_mib)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"Cleanup stopped: {exc}\n")


if __name__ == "__main__":
    main()
