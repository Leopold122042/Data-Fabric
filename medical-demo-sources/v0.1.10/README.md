# Medical demo source files · v0.1.10

These are generated synthetic demonstration datasets. They do not contain real patient or hospital records.

| Source | File | Tables |
|---|---|---|
| LIS | `lis.db` | `test_order`, `result` |
| PACS | `pacs.db` | `study` |
| IoT | `iot.db` | `devices`, `vitals` |
| Clinical operations | `clinical_operations.sqlite` | `chronic_followup`, `imaging_turnaround`, `laboratory_reports`, `research_cohort` |

HIS and EMR are stored in the user's NJU cloud directory and are deliberately excluded from this public repository. The platform reads each selected source file over its provider endpoint for every service invocation; it does not keep a persistent query snapshot.
