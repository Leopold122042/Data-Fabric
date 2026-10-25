"""A-owned SQLite control plane: immutable versions, optimistic writes, durable outbox."""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    return prefix + '-' + uuid.uuid4().hex[:16]


class DomainError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class Store:
    def __init__(self, path, environment='demo'):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path, self.environment = str(path), environment
        self.lock = threading.RLock()
        self.local = threading.local()
        with self.connection() as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS versions (
              environment TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
              version INTEGER NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL,
              reason TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(environment,kind,id,version));
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, environment TEXT NOT NULL,
              event_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS version_time ON versions(environment,kind,created_at);
            ''')

    @contextmanager
    def connection(self):
        active = getattr(self.local, 'transaction', None)
        if active is not None:
            yield active
            return
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def atomic(self):
        with self.lock:
            if getattr(self.local, 'transaction', None) is not None:
                yield
                return
            with self.connection() as con:
                con.execute('BEGIN IMMEDIATE')
                self.local.transaction = con
                try:
                    yield
                finally:
                    self.local.transaction = None

    @staticmethod
    def decode(row):
        if row is None:
            return None
        return {**json.loads(row['payload']), 'id': row['id'], 'version': row['version'],
                'environment': row['environment'], 'updated_at': row['created_at'],
                'updated_by': row['actor'], 'change_reason': row['reason']}

    def get(self, kind, oid, version=None):
        sql = 'SELECT * FROM versions WHERE environment=? AND kind=? AND id=?'
        args = [self.environment, kind, oid]
        if version is not None:
            sql += ' AND version=?'; args.append(version)
        with self.connection() as con:
            return self.decode(con.execute(sql+' ORDER BY version DESC LIMIT 1', args).fetchone())

    def require(self, kind, oid, version=None):
        item = self.get(kind, oid, version)
        if item is None:
            raise DomainError('对象或版本不存在：'+str(oid), 404)
        return item

    def list(self, kind, at=None):
        sql = 'SELECT * FROM versions WHERE environment=? AND kind=?'
        args = [self.environment, kind]
        if at:
            sql += ' AND created_at<=?'; args.append(at)
        with self.connection() as con:
            rows = con.execute(sql+' ORDER BY id, version DESC', args).fetchall()
        result = {}
        for row in rows:
            if row['id'] not in result:
                result[row['id']] = self.decode(row)
        return list(result.values())

    def history(self, kind, oid):
        with self.connection() as con:
            return [self.decode(r) for r in con.execute(
                'SELECT * FROM versions WHERE environment=? AND kind=? AND id=? ORDER BY version DESC',
                (self.environment, kind, oid))]

    def save(self, kind, oid, payload, expected=0, actor='system', reason='更新'):
        # Ignore client-supplied system fields; all version identities are server generated.
        data = {k: v for k,v in payload.items() if k not in
                {'id','version','environment','updated_at','updated_by','change_reason','expected_version'}}
        with self.lock, self.connection() as con:
            if not con.in_transaction:
                con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT MAX(version) FROM versions WHERE environment=? AND kind=? AND id=?',
                              (self.environment,kind,oid)).fetchone()
            actual = row[0] or 0
            if expected != actual:
                raise DomainError(f'版本冲突：当前版本为{actual}，请刷新后重试', 409)
            version, stamp = actual+1, now()
            con.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)',
                        (self.environment,kind,oid,version,stamp,actor,reason,json.dumps(data,ensure_ascii=False)))
            event = {'kind': kind, 'object_id': oid, 'old_version': actual, 'version': version,
                     'environment': self.environment, 'reason': reason}
            con.execute('INSERT INTO events(environment,event_id,created_at,payload) VALUES (?,?,?,?)',
                        (self.environment,new_id('EV'),stamp,json.dumps(event,ensure_ascii=False)))
        return self.require(kind,oid,version)

    def events(self, after=0, limit=200):
        with self.connection() as con:
            return [{**json.loads(r['payload']), 'seq': r['seq'], 'event_id': r['event_id'],
                     'created_at': r['created_at']} for r in con.execute(
                'SELECT * FROM events WHERE environment=? AND seq>? ORDER BY seq LIMIT ?',
                (self.environment,after,min(max(limit,1),500)))]
