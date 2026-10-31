"""Initialize local medical sources without overwriting an existing dataset."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

import medsim


REQUIRED_TABLES = {
    "his.db": {"patient", "visit", "orders"},
    "lis.db": {"test_order", "result"},
    "pacs.db": {"study"},
    "iot.db": {"devices", "vitals"},
}


def _is_link(path):
    return path.is_symlink() or bool(
        getattr(path.lstat(), "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def validate_data(data_dir):
    """Check all sources read-only, including SQLite integrity and required tables."""
    root = Path(data_dir).absolute()
    if not root.is_dir() or _is_link(root):
        raise ValueError(f"Data directory is missing or is a link: {root}")
    for name, required in REQUIRED_TABLES.items():
        path = root / name
        if not path.is_file() or _is_link(path):
            raise ValueError(f"Missing database or unsupported link: {path}")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
            checks = con.execute("PRAGMA quick_check").fetchall()
            if checks != [("ok",)]:
                raise ValueError(f"Database integrity check failed: {name}")
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if required - tables:
                raise ValueError(f"Missing tables in {name}: {sorted(required - tables)}")
    emr = root / "emr"
    if not emr.is_dir() or _is_link(emr):
        raise ValueError(f"Missing EMR directory or unsupported link: {emr}")


def initialize(data_dir=None, seed=42):
    """Keep valid existing sources; publish new sources only after full validation."""
    root = Path(medsim.DATA_DIR if data_dir is None else data_dir).absolute()
    root.parent.mkdir(parents=True, exist_ok=True)
    lock = root.parent / f".{root.name}.initialize.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Initialization lock exists: {lock}. "
                           "Wait for the other initializer; if it stopped, inspect the lock before removing it.") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "data_dir": str(root)}, handle)
        if os.path.lexists(root):
            if not root.is_dir() or _is_link(root):
                raise ValueError(f"Refusing to initialize a file or linked directory: {root}")
            if any(root.iterdir()):
                try:
                    validate_data(root)
                except (ValueError, sqlite3.Error) as exc:
                    raise ValueError(f"Existing directory is incomplete or invalid; all files were preserved. "
                                     f"Restore a complete backup or choose a new empty directory. Details: {exc}") from exc
                return {"status": "existing", "data_dir": str(root)}
        # Staging and destination share a filesystem, allowing one rename to publish.
        with tempfile.TemporaryDirectory(prefix=f".{root.name}-init-", dir=root.parent) as temporary:
            staging = Path(temporary) / "sources"
            result = medsim.generate_base(seed=seed, data_dir=staging)
            validate_data(staging)
            if root.exists():
                root.rmdir()  # Only removes an empty directory; concurrent writes stop publication.
            staging.rename(root)
        return {"status": "initialized", "data_dir": str(root), **result}
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(medsim.DATA_DIR))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        result = initialize(args.data_dir, args.seed)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"Initialization stopped: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
