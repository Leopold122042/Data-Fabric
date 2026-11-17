"""Query the six synthetic medical sources through their real storage endpoints.

SQLite files are fetched from GitHub or NJU WebDAV on every service call.
Their rows are copied from transient in-memory databases into a request-local
SQLite connection and discarded when the query ends. No source rows or cloud
credentials are persisted by this module.
"""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time

from external_sources import SourceError, box_url, fetch

VERSION = "0.1.14"
GITHUB_BRANCH = "feature/intake-query-reliability"
GITHUB_ROOT = (
    "https://raw.githubusercontent.com/Leopold122042/Data-Fabric/"
    f"{GITHUB_BRANCH}/medical-demo-sources/v{VERSION}"
)
BOX_ROOT = f"私人资料库/MedFabric/medical-demo-sources/v{VERSION}"
MAX_RESULT_ROWS = 200
QUERY_TIMEOUT_SECONDS = 5

SOURCES = {
    "his": {
        "name": "HIS 医院信息系统",
        "provider": "nju_box",
        "location": f"{BOX_ROOT}/his.db",
        "tables": {
            "patient": ["patient_id", "name", "gender", "birth_date", "id_card", "insurance_type"],
            "visit": ["visit_id", "patient_id", "vtype", "dept", "doctor", "admission_time", "diagnosis_code", "diagnosis_name", "chief_complaint"],
            "orders": ["order_id", "visit_id", "otype", "item_name", "status"],
        },
    },
    "lis": {
        "name": "LIS 检验信息系统",
        "provider": "github",
        "location": f"{GITHUB_ROOT}/lis.db",
        "tables": {
            "test_order": ["test_no", "patient_id", "visit_id", "sample_type", "collect_time", "report_time", "status"],
            "result": ["result_id", "test_no", "item_code", "item_name", "value_num", "unit", "ref_range", "flag"],
        },
    },
    "pacs": {
        "name": "PACS 影像归档系统",
        "provider": "github",
        "location": f"{GITHUB_ROOT}/pacs.db",
        "tables": {
            "study": ["study_uid", "patient_id", "modality", "body_part", "exam_date", "accession_no", "files", "size_mb", "report_summary"],
        },
    },
    "emr": {
        "name": "EMR 电子病历系统",
        "provider": "nju_box",
        "location": f"{BOX_ROOT}/emr.jsonl",
        "tables": {
            "dossiers": ["patient_id", "dept", "chief_complaint", "present_illness_history", "physical_exam", "preliminary_diagnosis", "auxiliary_examination", "treatment_plan"],
        },
    },
    "iot": {
        "name": "IoT 院内监测设备网",
        "provider": "github",
        "location": f"{GITHUB_ROOT}/iot.db",
        "tables": {
            "devices": ["device_id", "device_type", "ward"],
            "vitals": ["vital_id", "patient_id", "ts", "heart_rate", "spo2", "temp"],
        },
    },
    "operations": {
        "name": "临床业务协同数据集",
        "provider": "github",
        "location": f"{GITHUB_ROOT}/clinical_operations.sqlite",
        "tables": {
            "chronic_followup": ["随访日期", "院区", "科室", "服务项目", "年龄段", "随访状态"],
            "imaging_turnaround": ["检查日期", "院区", "检查类型", "签发时段"],
            "laboratory_reports": ["报告日期", "院区", "科室", "检验类别", "报告状态"],
            "research_cohort": ["入组日期", "院区", "科室", "课题代码", "年龄段", "入组状态"],
        },
    },
}


def catalog():
    return [
        {
            "id": source_id,
            "name": source["name"],
            "provider": source["provider"],
            "tables": list(source["tables"]),
            "location": source["location"],
            "read_mode": "查询时从来源重新读取",
        }
        for source_id, source in SOURCES.items()
    ]


def _open_source_connection(source_id, payload, connection):
    source = SOURCES[source_id]
    schema = source_id
    source_connection = None
    try:
        if source_id == "emr":
            connection.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
            definitions = ",".join('"' + c.replace('"', '""') + '" TEXT' for c in source["tables"]["dossiers"])
            connection.execute(f"CREATE TABLE {schema}.dossiers ({definitions})")
            text = payload.decode("utf-8-sig")
            records = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict) or set(value) != set(source["tables"]["dossiers"]):
                    raise SourceError("EMR 文件字段与已登记结构不一致；已停止本次查询。")
                records.append(tuple(
                    json.dumps(value[column], ensure_ascii=False, separators=(",", ":"))
                    if isinstance(value[column], (dict, list)) else value[column]
                    for column in source["tables"]["dossiers"]
                ))
                if len(records) > 50000:
                    raise SourceError("EMR 数据超过 50000 条上限。")
            placeholders = ",".join("?" for _ in source["tables"]["dossiers"])
            connection.executemany(f"INSERT INTO {schema}.dossiers VALUES ({placeholders})", records)
            return len(records)

        # Python's sqlite3.deserialize() only works reliably for the main
        # database. Load the downloaded bytes into a separate in-memory
        # connection, validate them, then copy their rows into the request's
        # attached in-memory schema. Nothing is written to a local file.
        source_connection = sqlite3.connect(":memory:")
        source_connection.row_factory = sqlite3.Row
        source_connection.deserialize(payload)
        tables = {
            row[0]
            for row in source_connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != set(source["tables"]):
            raise SourceError(f"{source['name']} 的表结构不符；已停止本次查询。")
        definitions = {}
        for table, expected_columns in source["tables"].items():
            actual_info = list(source_connection.execute(f'PRAGMA table_info("{table}")'))
            actual_columns = [row["name"] for row in actual_info]
            if actual_columns != expected_columns:
                raise SourceError(f"{source['name']} 的字段结构不符；已停止本次查询。")
            definitions[table] = actual_info

        connection.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        loaded_rows = 0
        for table, column_info in definitions.items():
            columns = [row["name"] for row in column_info]
            declarations = ",".join(
                '"' + row["name"].replace('"', '""') + '"' for row in column_info
            )
            quoted_table = '"' + table.replace('"', '""') + '"'
            connection.execute(f"CREATE TABLE {schema}.{quoted_table} ({declarations})")
            quoted_columns = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
            placeholders = ",".join("?" for _ in columns)
            cursor = source_connection.execute(f"SELECT {quoted_columns} FROM {quoted_table}")
            while True:
                chunk = cursor.fetchmany(1000)
                if not chunk:
                    break
                connection.executemany(
                    f"INSERT INTO {schema}.{quoted_table} VALUES ({placeholders})",
                    [tuple(row) for row in chunk],
                )
                loaded_rows += len(chunk)
        source_connection.close()
        source_connection = None
        return loaded_rows
    except SourceError:
        if source_connection is not None:
            source_connection.close()
        raise
    except (sqlite3.Error, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        if source_connection is not None:
            source_connection.close()
        raise SourceError(f"{source['name']} 文件无效或无法读取；已停止本次查询。") from None


def query(sql, source_ids, cloud_credentials=None):
    """Run one read-only query against explicitly selected remote source files."""
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 10000:
        raise SourceError("请输入一条不超过 10000 个字符的只读查询。")
    if sql.lstrip().split(None, 1)[0].rstrip(";").lower() not in ("select", "with"):
        raise SourceError("医疗源服务只接受 SELECT 或 WITH 查询。")
    if (not isinstance(source_ids, list) or not source_ids or len(source_ids) > len(SOURCES)
            or any(not isinstance(source_id,str) for source_id in source_ids)):
        raise SourceError("请选择至少一个、最多六个医疗来源。")
    selected = list(dict.fromkeys(source_ids))
    if len(selected) != len(source_ids) or any(source_id not in SOURCES for source_id in selected):
        raise SourceError("医疗来源编号无效或重复，请刷新来源列表。")
    if any(SOURCES[source_id]["provider"] == "nju_box" for source_id in selected):
        if not cloud_credentials or not cloud_credentials[0].strip() or not cloud_credentials[1]:
            raise SourceError("请先在数据接入页连接南大云盘，再查询 HIS 或 EMR。")

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    evidence = []
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        for source_id in selected:
            source = SOURCES[source_id]
            if source["provider"] == "nju_box":
                location = box_url(source["location"])
                username, token = cloud_credentials
                payload = fetch(location, username=username, token=token)
            else:
                # This URL is a fixed, versioned raw file in the project repo;
                # the generic connector intentionally accepts tabular formats
                # only, while this service validates SQLite schemas itself.
                location = source["location"]
                payload = fetch(location)
            loaded_rows = _open_source_connection(source_id, payload, connection)
            source_evidence = {
                "source_id": source_id,
                "source_name": source["name"],
                "provider": source["provider"],
                "location": source["location"],
                "bytes_read": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "rows_loaded": loaded_rows,
            }
            if source_id == "emr":
                source_evidence["documents_loaded"] = loaded_rows
            evidence.append(source_evidence)

        connection.execute("PRAGMA query_only=ON")
        connection.enable_load_extension(False)
        allowed_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            getattr(sqlite3, "SQLITE_RECURSIVE", -1),
        }

        def authorize(action, _arg1, arg2, _database, _trigger):
            if action not in allowed_actions:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() == "load_extension":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        started = time.monotonic()
        connection.set_progress_handler(
            lambda: int(time.monotonic() - started > QUERY_TIMEOUT_SECONDS), 1000
        )
        try:
            cursor = connection.execute(sql)
            columns = [item[0] for item in (cursor.description or [])]
            rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        except sqlite3.Error:
            raise SourceError("查询未通过只读 SQL 校验，或引用了未选择来源中的字段。") from None
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        truncated = len(rows) > MAX_RESULT_ROWS
        rows = rows[:MAX_RESULT_ROWS]
        values = []
        for row in rows:
            values.append([
                value.decode("utf-8", "replace") if isinstance(value, bytes) else value
                for value in row
            ])
        return {
            "service_id": "SVC-MEDICAL-REMOTE-QUERY",
            "columns": columns,
            "rows": values,
            "truncated": truncated,
            "elapsed_ms": elapsed_ms,
            "sources": evidence,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "query_mode": "每次服务调用均从所选 GitHub / 南大云盘源文件重新读取；仅在本次请求内存中联邦执行。",
        }
    except SourceError:
        raise
    finally:
        connection.close()
