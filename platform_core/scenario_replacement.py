"""Archive and retire the exact V1 walkthrough after its replacement is ready.

This is a local data migration, not a generic delete API. Both control stores
are updated in one SQLite attached-database transaction. User edits and any
dependencies still referenced by retained business history are kept intact.
"""
from collections import defaultdict
from contextlib import closing
from pathlib import Path
import json
import os
import sqlite3

from platform_core.storage import DomainError, Store, new_id, now


LEGACY_ID = 'DEMO-SCENARIOS-V1'
LEGACY_RETEST_ID = 'DEMO-SCENARIOS-V1-REQUESTER-RETEST'
REPLACEMENT_ID = 'HEALTHCARE-SCENARIOS-V2'
MIGRATION_ID = 'RETIRE-WORKFLOW-SCENARIOS-V1'
MARKER_KINDS = {'demo_seed', 'demo_seed_migration', 'scenario_replacement'}
SCHEMAS = {'management': 'main', 'execution': 'execution_db'}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _read_records(con, environment):
    records = defaultdict(list)
    for domain, schema in SCHEMAS.items():
        for row in con.execute(f'SELECT * FROM {schema}.versions WHERE environment=? ORDER BY version', (environment,)):
            records[(domain, row['kind'], row['id'])].append(dict(row))
    return dict(records)


def _latest(records, key):
    rows = records.get(key)
    return Store.decode(rows[-1]) if rows else None


def _candidate_records(records, manifest, retest):
    candidates = {}
    cutoff = manifest['updated_at']
    for domain, kinds in manifest.get('records', {}).items():
        if domain not in SCHEMAS:
            continue
        for kind, ids in kinds.items():
            if kind in MARKER_KINDS:
                continue
            for oid in ids:
                key = (domain, kind, oid)
                if key in records:
                    candidates[key] = cutoff
    if retest and retest.get('status') == 'completed':
        additions = [('execution', 'job', retest.get('job_id')),
                     ('execution', 'run', retest.get('job_id')),
                     ('execution', 'delivery', retest.get('delivery_id')),
                     ('management', 'improvement', retest.get('improvement_id'))]
        additions += [('execution', 'decision', oid) for oid in retest.get('decision_ids', [])]
        for key in additions:
            if key in records:
                candidates[key] = retest['updated_at']

    # V1's manifest omitted immutable execution design snapshots. Later reads
    # also append authorization decisions, and reconciliation may append pairs.
    # Include only known generated record types bound to the exact V1 objects.
    def owned_ids(domain, kind):
        return {oid for d, k, oid in candidates if d == domain and k == kind}

    plans = owned_ids('execution', 'plan')
    runs = owned_ids('execution', 'run')
    grants = owned_ids('execution', 'grant')
    views = owned_ids('execution', 'view')
    designs = plans | views | owned_ids('execution', 'workflow')
    source_ids = owned_ids('management', 'source')
    for key, rows in records.items():
        if key in candidates or key[1] in MARKER_KINDS:
            continue
        domain, kind, oid = key
        record = Store.decode(rows[-1])
        first = rows[0]
        generated = False
        if domain == 'management' and kind == 'external_design':
            generated = record.get('object_id') in designs and all(
                r['actor'] == 'execution' and r['reason'] == '接收外部设计快照' for r in rows)
        elif domain == 'execution' and kind == 'decision':
            generated = (record.get('plan_id') in plans and record.get('grant_id') in grants
                         and all(r['reason'] == '执行点授权核验' for r in rows))
        elif domain == 'execution' and kind == 'materialization':
            generated = (record.get('view_id') in views and all(
                r['actor'] == 'execution' and r['reason'] == '按版本与源指纹缓存，命中仍需重新授权' for r in rows))
        elif domain == 'management' and kind == 'pair':
            generated = (record.get('run_id') in runs and all(
                r['actor'] == 'system' and r['reason'] == '设计运行配对' for r in rows))
        elif domain == 'management' and kind == 'case':
            generated = (record.get('object_id') in plans and bool(record.get('run_ids'))
                         and set(record['run_ids']) <= runs
                         and all(r['actor'] == 'system' and r['reason'] == '偏差发现' for r in rows))
        elif domain == 'management' and kind == 'object':
            generated = (record.get('source_id') in source_ids and all(
                r['actor'] == 'system' and r['reason'] in ('结构发现', '结构变更', '源对象删除') for r in rows))
        if generated:
            candidates[key] = rows[-1]['created_at']
        # A snapshot already existed at the original seed boundary, but was
        # subsequently edited outside the normal immutable ingestion port.
        elif domain == 'management' and kind == 'external_design' and record.get('object_id') in designs and first['created_at'] <= cutoff:
            candidates[key] = cutoff
    return candidates


def _protected_records(records, candidates):
    by_id = defaultdict(set)
    for key in candidates:
        by_id[key[2]].add(key)
    dependencies = {}
    for key, rows in records.items():
        if key[1] in MARKER_KINDS:
            continue
        refs = set()
        for row in rows:
            for value in _strings(json.loads(row['payload'])):
                refs.update(by_id.get(value, ()))
        dependencies[key] = refs

    protected = {key for key, cutoff in candidates.items()
                 if any(row['created_at'] > cutoff for row in records[key])}
    reasons = {key: '初始化后已有新版本，保留当前记录及历史' for key in protected}
    # Retain source catalog families together. Keeping only one referenced field
    # while dropping its source/table siblings would recreate new IDs on scan.
    source_families = defaultdict(set)
    for key in candidates:
        record = _latest(records, key)
        if key[0] == 'management' and key[1] in ('source', 'object', 'scan'):
            sid = record.get('source_id') or (key[2] if key[1] in ('source', 'scan') else None)
            if sid:
                source_families[sid].add(key)
    changed = True
    while changed:
        previous = set(protected)
        for key, refs in dependencies.items():
            if key not in candidates or key in protected:
                for ref in refs - protected:
                    protected.add(ref)
                    reasons[ref] = '保留记录或历史版本仍引用此对象'
        for family in source_families.values():
            if family & protected:
                for key in family - protected:
                    protected.add(key)
                    reasons[key] = '保留所引用数据源的完整目录，避免扫描重新生成编号'
        # plan_state shares the plan ID but does not carry a plan_id field.
        for key in list(protected):
            for same_id in by_id[key[2]] - protected:
                protected.add(same_id)
                reasons[same_id] = '保留同一对象的状态记录'
        changed = previous != protected
    return protected, reasons


def _archive(con, stores, records, removed, protected, reasons, environment):
    archive_root = Path(stores['management'].path).resolve().parent.parent / 'scenario_archives'
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = archive_root / (MIGRATION_ID.lower() + '-' + new_id('archive') + '.json')
    data = {'format_version': 1, 'created_at': now(), 'environment': environment,
            'migration_id': MIGRATION_ID, 'replacement_id': REPLACEMENT_ID,
            'databases': {domain: str(Path(store.path).resolve()) for domain, store in stores.items()},
            'records': {}, 'events': {}, 'protected': [dict(domain=d, kind=k, id=i, reason=reasons[(d, k, i)])
                                                       for d, k, i in sorted(protected)]}
    for domain, schema in SCHEMAS.items():
        identities = {(kind, oid) for d, kind, oid in removed if d == domain}
        data['records'][domain] = [row for key in sorted(removed) if key[0] == domain for row in records[key]]
        data['events'][domain] = []
        for row in con.execute(f'SELECT * FROM {schema}.events WHERE environment=? ORDER BY seq', (environment,)):
            payload = json.loads(row['payload'])
            if (payload.get('kind'), payload.get('object_id')) in identities:
                data['events'][domain].append(dict(row))
    # A complete, flushed archive must exist before deleting any database row.
    with archive.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    return archive, data


def _delete_records(con, removed, archive, environment):
    for domain, schema in SCHEMAS.items():
        for d, kind, oid in sorted(removed):
            if d == domain:
                con.execute(f'DELETE FROM {schema}.versions WHERE environment=? AND kind=? AND id=?',
                            (environment, kind, oid))
        for event in archive['events'][domain]:
            con.execute(f'DELETE FROM {schema}.events WHERE environment=? AND seq=? AND event_id=?',
                        (environment, event['seq'], event['event_id']))


def _write_marker(con, environment, payload):
    stamp = now()
    reason = '业务场景替换完成，原记录已归档，用户记录和依赖保留'
    con.execute('INSERT INTO main.versions VALUES (?,?,?,?,?,?,?,?)',
                (environment, 'scenario_replacement', MIGRATION_ID, 1, stamp, 'system', reason,
                 json.dumps(payload, ensure_ascii=False)))
    event = {'kind': 'scenario_replacement', 'object_id': MIGRATION_ID, 'old_version': 0,
             'version': 1, 'environment': environment, 'reason': reason}
    con.execute('INSERT INTO main.events(environment,event_id,created_at,payload) VALUES (?,?,?,?)',
                (environment, new_id('EV'), stamp, json.dumps(event, ensure_ascii=False)))


def replace_legacy_scenarios(runtime):
    """Retire V1 once after V2 has committed; never remove the source file.

    Call during startup before background scans/workers. The returned marker
    includes the recovery archive and any records retained for user ownership.
    """
    e, m = runtime.execution, runtime.management
    if e.store.environment != 'demo' or m.store.environment != 'demo':
        raise DomainError('场景替换仅支持本机数据环境', 403)
    stores = {'management': m.store, 'execution': e.store}
    environment = m.store.environment
    with e.lock, m.store.lock, e.store.lock:
        if any(getattr(store.local, 'transaction', None) is not None for store in stores.values()):
            raise DomainError('请在业务事务完成后执行场景替换', 409)
        with closing(sqlite3.connect(m.store.path, timeout=30)) as con:
            con.row_factory = sqlite3.Row
            con.execute('ATTACH DATABASE ? AS execution_db', (e.store.path,))
            # SQLite's attached-file transaction commits both version stores
            # together; the normal control stores use rollback-journal mode.
            if any(con.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0].lower() == 'wal' for schema in SCHEMAS.values()):
                raise DomainError('当前数据库日志模式不支持双库原子替换', 409)
            try:
                con.execute('BEGIN IMMEDIATE')
                records = _read_records(con, environment)
                prior = _latest(records, ('management', 'scenario_replacement', MIGRATION_ID))
                if prior:
                    con.rollback()
                    return prior
                replacement = _latest(records, ('management', 'demo_seed', REPLACEMENT_ID))
                if not replacement or replacement.get('ready') is not True:
                    raise DomainError('新业务场景尚未完成，保留原有记录', 409)
                manifest = _latest(records, ('management', 'demo_seed', LEGACY_ID))
                if not manifest:
                    con.rollback()
                    return {'status': 'not_needed', 'removed': {}, 'protected': []}
                retest = _latest(records, ('management', 'demo_seed_migration', LEGACY_RETEST_ID))
                candidates = _candidate_records(records, manifest, retest)
                protected, reasons = _protected_records(records, candidates)
                removed = set(candidates) - protected
                path, archive = _archive(con, stores, records, removed, protected, reasons, environment)
                _delete_records(con, removed, archive, environment)
                counts = {domain: {} for domain in SCHEMAS}
                for domain, kind, oid in removed:
                    counts[domain][kind] = counts[domain].get(kind, 0) + 1
                payload = {'status': 'completed', 'replacement_id': REPLACEMENT_ID,
                           'archive_path': str(path), 'removed': counts,
                           'removed_records': [{'domain': d, 'kind': k, 'id': i} for d, k, i in sorted(removed)],
                           'protected': archive['protected']}
                _write_marker(con, environment, payload)
                con.commit()
            except Exception:
                con.rollback()
                raise
        return m.store.require('scenario_replacement', MIGRATION_ID)
