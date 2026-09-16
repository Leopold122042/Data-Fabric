# Data Fabric
For presentation and exploration only

## Clean generated demo data

Stop all MedFabric server processes before cleanup. The default cutoff keeps
patient numbers up to and including P2000, not 2000 rows per table. Related visits,
orders, lab results, studies, vitals and EMR JSON for larger numbers are removed.
Device records and retained patients' history remain unchanged.

```powershell
python cleanup_data.py
python cleanup_data.py --apply
```

The first command previews counts without committing changes. The second saves a
complete database/EMR backup under `data_backups/`, applies cleanup, and compacts
SQLite files. Backups are excluded from Git. Do not delete the backup until the
result has been checked. For a nondefault server port, pass `--port PORT`.

If cleanup fails after changes begin, keep the server stopped and restore the
entire backup as `data/` (move the incomplete directory aside first, including
SQLite WAL/SHM sidecars); do not mix files from different snapshots.

Restarting the server resumes data generation after the largest remaining HIS
patient number. Cleanup is a snapshot operation, not a permanent size limit.
Previously tracked files removed by cleanup will appear as deletions in Git;
review these together with the database changes when publishing a data snapshot.
