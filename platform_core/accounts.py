"""Persistent local-demo accounts and revocable browser sessions."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from platform_core.storage import DomainError, now

PASSWORD_ITERATIONS = 310_000
SESSION_HOURS = 12
BOOTSTRAP_ACCOUNTS = (
    ('demo_l1', '演示账户 · 1级', 1),
    ('demo_l2', '演示账户 · 2级', 2),
    ('demo_l3', '演示账户 · 3级', 3),
    ('demo_l4', '平台管理员 · 4级', 4),
)


def _password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS)


class AccountStore:
    """SQLite-backed accounts; only a hash of each session token is persisted."""
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(Path(self.path).parent, 0o700)
        except OSError:
            pass
        self.lock = threading.RLock()
        with self.connection() as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS accounts (
              username TEXT PRIMARY KEY, display_name TEXT NOT NULL,
              level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 4),
              salt BLOB NOT NULL, password_hash BLOB NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, last_login TEXT);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES accounts(username),
              created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS sessions_user ON sessions(username);
            CREATE TABLE IF NOT EXISTS login_failures (
              key TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_started TEXT NOT NULL,
              blocked_until TEXT);
            CREATE TABLE IF NOT EXISTS registration_limits (
              key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_started TEXT NOT NULL,
              blocked_until TEXT);
            CREATE TABLE IF NOT EXISTS account_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
              actor TEXT, actor_level INTEGER, action TEXT NOT NULL,
              target TEXT, details TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS account_audit_time ON account_audit(id DESC);
            ''')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._bootstrap_if_empty()
        self._apply_initial_admin_login_policy()

    @contextmanager
    def connection(self):
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

    def _bootstrap_if_empty(self):
        path = Path(self.path).parent / 'initial-accounts.json'
        issued = []
        with self.lock:
            try:
                with self.connection() as con:
                    if con.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():
                        return
                    if path.exists():
                        raise RuntimeError('Account database is empty but its initial credential file already exists; manual recovery is required.')
                    for username, display_name, level in BOOTSTRAP_ACCOUNTS:
                        password = secrets.token_urlsafe(18)
                        salt = secrets.token_bytes(16)
                        con.execute('INSERT INTO accounts(username,display_name,level,salt,password_hash,enabled,must_change_password,created_at) VALUES(?,?,?,?,?,1,1,?)',
                                    (username, display_name, level, salt, _password_hash(password, salt), now()))
                        issued.append({'username': username, 'password': password, 'level': level})
                    # Write the one-time credentials before the database commit so a
                    # permission or disk error cannot leave unreachable accounts.
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                        json.dump({'created_at': now(), 'accounts': issued}, handle, ensure_ascii=False, indent=2)
                        handle.write('\n')
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            except Exception:
                if issued:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                raise

    def _apply_initial_admin_login_policy(self):
        """Allow the original level-4 bootstrap credential to sign in directly.

        Verify it against the one-time credential file before clearing the flag,
        so a later administrator password reset still requires a password change.
        """
        migration = 'level4_initial_login_direct_v1'
        credential_path = Path(self.path).parent / 'initial-accounts.json'
        with self.lock, self.connection() as con:
            con.execute('CREATE TABLE IF NOT EXISTS account_migrations '
                        '(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
            if con.execute('SELECT 1 FROM account_migrations WHERE name=?',
                           (migration,)).fetchone():
                return
            row = con.execute('SELECT * FROM accounts WHERE username=?',
                              ('demo_l4',)).fetchone()
            if row and row['must_change_password'] and credential_path.exists():
                try:
                    issued = json.loads(credential_path.read_text(encoding='utf-8'))
                    credential = next((item.get('password') for item in issued.get('accounts', [])
                                       if item.get('username') == 'demo_l4'), None)
                except (OSError, ValueError, TypeError):
                    credential = None
                if isinstance(credential, str) and hmac.compare_digest(
                        _password_hash(credential, row['salt']), row['password_hash']):
                    con.execute('UPDATE accounts SET must_change_password=0 WHERE username=?',
                                ('demo_l4',))
                    self._audit(con, 'account.initial_login_policy_updated', 'system', 4,
                                'demo_l4', {'must_change_password': False})
            con.execute('INSERT INTO account_migrations(name,applied_at) VALUES(?,?)',
                        (migration, now()))

    @staticmethod
    def _audit(con, action, actor=None, actor_level=None, target=None, details=None):
        con.execute('INSERT INTO account_audit(occurred_at,actor,actor_level,action,target,details) VALUES(?,?,?,?,?,?)',
                    (now(), actor, actor_level, action, target,
                     json.dumps(details or {}, ensure_ascii=False, sort_keys=True)))

    def audit(self, action, actor=None, actor_level=None, target=None, details=None):
        with self.lock, self.connection() as con:
            self._audit(con, action, actor, actor_level, target, details)

    def audit_events(self, limit=200):
        limit = max(1, min(500, int(limit)))
        with self.lock, self.connection() as con:
            rows = con.execute('SELECT id,occurred_at,actor,actor_level,action,target,details '
                               'FROM account_audit ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [{**dict(row), 'details': json.loads(row['details'])} for row in rows]

    @staticmethod
    def validate_username(username):
        value = str(username or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}', value):
            raise DomainError('账号须为3至40位字母、数字、点、下划线或连字符')
        return value

    @staticmethod
    def validate_password(password):
        if not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise DomainError('密码须为10至128个字符')

    def authenticate(self, username, password, remote_key='local'):
        username = self.validate_username(username)
        self.check_login_rate(username, remote_key)
        valid = False
        principal = None
        token = None
        with self.lock, self.connection() as con:
            row = con.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
            valid = bool(row and row['enabled'] and isinstance(password, str)
                         and hmac.compare_digest(_password_hash(password, row['salt']), row['password_hash']))
            if valid:
                con.execute('DELETE FROM login_failures WHERE key IN (?,?)', (username, remote_key))
                con.execute('UPDATE accounts SET last_login=? WHERE username=?', (now(), username))
                token = secrets.token_urlsafe(32)
                expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat()
                con.execute('INSERT INTO sessions(token_hash,username,created_at,expires_at) VALUES(?,?,?,?)',
                            (hashlib.sha256(token.encode()).hexdigest(), username, now(), expires))
                principal = self._principal(row)
                self._audit(con, 'login.succeeded', username, int(row['level']), username,
                            {'remote': remote_key})
        if not valid:
            self.record_login_failure(username, remote_key)
            self.audit('login.failed', username, None, username, {'remote': remote_key})
            raise DomainError('账号或密码不正确', 401)
        return token, principal

    def verify_password(self, username, password):
        if not isinstance(password, str):
            return False
        with self.lock, self.connection() as con:
            row = con.execute('SELECT salt,password_hash,enabled FROM accounts WHERE username=?',
                              (username,)).fetchone()
        return bool(row and row['enabled'] and hmac.compare_digest(
            _password_hash(password, row['salt']), row['password_hash']))

    def check_login_rate(self, username, remote_key):
        instant = datetime.now(timezone.utc)
        with self.lock, self.connection() as con:
            rows = con.execute('SELECT key,blocked_until,window_started FROM login_failures WHERE key IN (?,?)',
                               (username, remote_key)).fetchall()
            for row in rows:
                blocked = row['blocked_until']
                if blocked and datetime.fromisoformat(blocked) > instant:
                    raise DomainError('登录尝试过多，请稍后再试', 429)

    def record_login_failure(self, username, remote_key):
        instant = datetime.now(timezone.utc)
        with self.lock, self.connection() as con:
            for key in {username, remote_key}:
                row = con.execute('SELECT * FROM login_failures WHERE key=?', (key,)).fetchone()
                started = datetime.fromisoformat(row['window_started']) if row else instant
                failures = row['failures'] if row and instant - started < timedelta(minutes=15) else 0
                failures += 1
                blocked = (instant + timedelta(minutes=15)).isoformat() if failures >= 8 else None
                con.execute('INSERT INTO login_failures(key,failures,window_started,blocked_until) VALUES(?,?,?,?) '
                            'ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,window_started=excluded.window_started,blocked_until=excluded.blocked_until',
                            (key, failures, started.isoformat() if failures > 1 else instant.isoformat(), blocked))

    @staticmethod
    def _principal(row):
        level = int(row['level'])
        return {'id': row['username'], 'username': row['username'], 'display_name': row['display_name'],
                'role': 'platform' if level == 4 else 'requester', 'level': level,
                'enabled': bool(row['enabled']), 'must_change_password': bool(row['must_change_password']),
                'demo': True}

    def principal(self, token):
        if not token or len(token) > 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock, self.connection() as con:
            row = con.execute('SELECT a.* FROM sessions s JOIN accounts a ON a.username=s.username '
                              'WHERE s.token_hash=? AND s.expires_at>? AND a.enabled=1',
                              (digest, datetime.now(timezone.utc).isoformat())).fetchone()
            if not row:
                con.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))
                return None
            return self._principal(row)

    def account(self, username, include_disabled=False):
        """Current enabled account snapshot for queued execution revalidation."""
        with self.lock, self.connection() as con:
            query='SELECT * FROM accounts WHERE username=?'
            if not include_disabled:query+=' AND enabled=1'
            row = con.execute(query,(username,)).fetchone()
        return self._principal(row) if row else None

    def revoke_session(self, token, actor=None, actor_level=None):
        if not token:
            return
        with self.lock, self.connection() as con:
            row = con.execute('SELECT username FROM sessions WHERE token_hash=?',
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            con.execute('DELETE FROM sessions WHERE token_hash=?',
                        (hashlib.sha256(token.encode()).hexdigest(),))
            if row:
                self._audit(con, 'logout', actor or row['username'], actor_level,
                            row['username'], {})

    def change_password(self, username, password, actor=None, actor_level=None):
        self.validate_password(password)
        salt = secrets.token_bytes(16)
        with self.lock, self.connection() as con:
            if not con.execute('SELECT 1 FROM accounts WHERE username=? AND enabled=1', (username,)).fetchone():
                raise DomainError('账户不存在或已停用', 404)
            con.execute('UPDATE accounts SET salt=?,password_hash=?,must_change_password=0 WHERE username=?',
                        (salt, _password_hash(password, salt), username))
            con.execute('DELETE FROM sessions WHERE username=?', (username,))
            self._audit(con, 'password.changed', actor or username, actor_level, username, {})
            remaining = con.execute('SELECT COUNT(*) FROM accounts WHERE must_change_password=1 AND username LIKE "demo_l%"').fetchone()[0]
            if remaining == 0:
                try:
                    (Path(self.path).parent / 'initial-accounts.json').unlink()
                except FileNotFoundError:
                    pass

    def create_account(self, username, password, display_name, actor=None, actor_level=None):
        username = self.validate_username(username)
        self.validate_password(password)
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name.strip()) > 80:
            raise DomainError('请填写不超过80字的显示名称')
        salt = secrets.token_bytes(16)
        with self.lock, self.connection() as con:
            if con.execute('SELECT 1 FROM accounts WHERE username=?', (username,)).fetchone():
                raise DomainError('账号名已存在', 409)
            con.execute('INSERT INTO accounts(username,display_name,level,salt,password_hash,enabled,must_change_password,created_at) VALUES(?,?,1,?,?,1,1,?)',
                        (username, display_name.strip(), salt, _password_hash(password, salt), now()))
            self._audit(con, 'account.created', actor, actor_level, username,
                        {'level': 1, 'display_name': display_name.strip()})
        return {'username': username, 'display_name': display_name.strip(), 'level': 1,
                'enabled': True, 'must_change_password': True}

    def _consume_registration_slot(self, remote_key):
        key = hashlib.sha256(str(remote_key or 'local').encode('utf-8')).hexdigest()
        instant = datetime.now(timezone.utc)
        with self.lock, self.connection() as con:
            row = con.execute('SELECT * FROM registration_limits WHERE key=?', (key,)).fetchone()
            if row and row['blocked_until'] and datetime.fromisoformat(row['blocked_until']) > instant:
                raise DomainError('注册请求过多，请一小时后再试', 429)
            started = datetime.fromisoformat(row['window_started']) if row else instant
            if not row or instant - started >= timedelta(hours=1):
                attempts = 1
                started = instant
            else:
                attempts = int(row['attempts']) + 1
            blocked_until = (instant + timedelta(hours=1)).isoformat() if attempts >= 10 else None
            con.execute('INSERT INTO registration_limits(key,attempts,window_started,blocked_until) VALUES(?,?,?,?) '
                        'ON CONFLICT(key) DO UPDATE SET attempts=excluded.attempts, '
                        'window_started=excluded.window_started,blocked_until=excluded.blocked_until',
                        (key, attempts, started.isoformat(), blocked_until))

    def register_account(self, username, password, display_name, remote_key='local'):
        """Public self-registration; the only assignable level is 1."""
        username = self.validate_username(username)
        self.validate_password(password)
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name.strip()) > 80:
            raise DomainError('请填写不超过80字的显示名称')
        self._consume_registration_slot(remote_key)
        display_name = display_name.strip()
        salt = secrets.token_bytes(16)
        password_hash = _password_hash(password, salt)
        with self.lock, self.connection() as con:
            if con.execute('SELECT 1 FROM accounts WHERE username=?', (username,)).fetchone():
                raise DomainError('账号名已被使用，请换一个账号名', 409)
            con.execute('INSERT INTO accounts(username,display_name,level,salt,password_hash,enabled,must_change_password,created_at) '
                        'VALUES(?,?,1,?,?,1,0,?)',
                        (username, display_name, salt, password_hash, now()))
            self._audit(con, 'account.self_registered', username, 1, username,
                        {'level': 1, 'display_name': display_name})
        return {'username': username, 'display_name': display_name, 'level': 1,
                'enabled': True, 'must_change_password': False}

    def list_accounts(self):
        with self.lock, self.connection() as con:
            rows = con.execute('SELECT username,display_name,level,enabled,must_change_password,created_at,last_login FROM accounts ORDER BY level DESC,username').fetchall()
        return [dict(row) | {'enabled': bool(row['enabled']), 'must_change_password': bool(row['must_change_password'])} for row in rows]

    def update_account(self, username, level=None, enabled=None, actor=None, actor_level=None):
        username = self.validate_username(username)
        if level is not None and (type(level) is not int or level not in (1, 2, 3, 4)):
            raise DomainError('权限等级只能是1、2、3或4')
        if enabled is not None and type(enabled) is not bool:
            raise DomainError('账户状态必须是布尔值')
        with self.lock, self.connection() as con:
            row = con.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
            if not row:
                raise DomainError('账户不存在', 404)
            next_level = row['level'] if level is None else level
            next_enabled = bool(row['enabled']) if enabled is None else enabled
            if next_level != 4 or not next_enabled:
                active_admins = con.execute('SELECT COUNT(*) FROM accounts WHERE level=4 AND enabled=1 AND username<>?',
                                            (username,)).fetchone()[0]
                if active_admins == 0:
                    raise DomainError('至少保留一个启用的4级管理员', 409)
            con.execute('UPDATE accounts SET level=?,enabled=? WHERE username=?',
                        (next_level, int(next_enabled), username))
            changes = {}
            if next_level != row['level']:
                changes.update(old_level=int(row['level']), new_level=next_level)
            if next_enabled != bool(row['enabled']):
                changes.update(old_enabled=bool(row['enabled']), new_enabled=next_enabled)
            if changes:
                self._audit(con, 'account.updated', actor, actor_level, username, changes)
            if not next_enabled:
                con.execute('DELETE FROM sessions WHERE username=?', (username,))
            result = con.execute('SELECT username,display_name,level,enabled,must_change_password,created_at,last_login FROM accounts WHERE username=?',
                                 (username,)).fetchone()
        value = dict(result)
        value['enabled'] = bool(value['enabled'])
        value['must_change_password'] = bool(value['must_change_password'])
        return value

    def reset_password(self, username, password, actor=None, actor_level=None):
        self.validate_password(password)
        username = self.validate_username(username)
        salt = secrets.token_bytes(16)
        with self.lock, self.connection() as con:
            if not con.execute('SELECT 1 FROM accounts WHERE username=?', (username,)).fetchone():
                raise DomainError('账户不存在', 404)
            con.execute('UPDATE accounts SET salt=?,password_hash=?,must_change_password=1 WHERE username=?',
                        (salt, _password_hash(password, salt), username))
            con.execute('DELETE FROM sessions WHERE username=?', (username,))
            self._audit(con, 'password.reset', actor, actor_level, username,
                        {'must_change_password': True})
        return {'username': username, 'must_change_password': True}
