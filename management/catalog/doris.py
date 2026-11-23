"""Allowlisted, read-only Doris connections configured by the server operator."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
from threading import BoundedSemaphore, Event, Lock, Thread
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from platform_core.storage import DomainError

IDENTIFIER = re.compile(r'[a-zA-Z0-9_]+\Z')
SOURCE_ID = re.compile(r'[a-z][a-z0-9_]{0,39}\Z')
SYSTEM_DATABASES = {'information_schema', 'mysql', '__internal_schema', 'sys', 'performance_schema'}
OPERATORS = {'=', '!=', '<', '<=', '>', '>='}
CONNECT_TIMEOUT = 3
QUERY_TIMEOUT = 6
MAX_RESULT_BYTES = 8 * 1024 * 1024
SCAN_INTERVAL = 45
LOG = logging.getLogger(__name__)


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise DomainError('Doris 表或字段名称含有当前连接器不支持的字符', 409)
    return '`' + value + '`'


def _row_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        raise DomainError('Doris 二进制字段需由数据源视图转换后才能查询', 409)
    return value


class DorisSource:
    def __init__(self, entry):
        key = entry['id']
        if not isinstance(key, str) or not SOURCE_ID.fullmatch(key):
            raise ValueError('invalid Doris source ID')
        self.id = 'doris-' + key
        self.name = entry.get('name') or key
        self.host = entry['host']
        self.port = entry.get('port', 9030)
        self.user = entry['user']
        self.password = entry['password']
        self.transport = entry.get('transport', 'tls')
        self.vpn_interface = entry.get('vpn_interface')
        self.ca_file = entry.get('ca_file')
        self.time_zone = ZoneInfo(entry['time_zone']) if entry.get('time_zone') else None
        self.connections = BoundedSemaphore(3)
        if (any(not isinstance(x, str) or not x.strip() for x in (self.name, self.host, self.user, self.password))
                or type(self.port) is not int or not 1 <= self.port <= 65535
                or self.transport not in ('tls', 'vpn')):
            raise ValueError('invalid Doris source settings')
        if self.transport == 'vpn':
            address = ipaddress.ip_address(self.host)
            if (not any(address in ipaddress.ip_network(network) for network in
                        ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))
                    or not isinstance(self.vpn_interface, str)
                    or not re.fullmatch(r'[a-zA-Z0-9_.-]+|utun\*', self.vpn_interface)):
                raise ValueError('VPN transport requires a private address and tunnel interface')

        self.tables = {}
        tables = entry['tables']
        if not isinstance(tables, dict) or not tables:
            raise ValueError('explicit Doris table allowlist required')
        for database, names in tables.items():
            identifier(database)
            if database.casefold() in SYSTEM_DATABASES or not isinstance(names, list) or not names:
                raise ValueError('invalid Doris business database allowlist')
            for table in names:
                identifier(table)
                alias = f'doris_{key}__{database}__{table}'.lower()
                if alias in self.tables:
                    raise ValueError('duplicate Doris table alias')
                self.tables[alias] = (database, table)
        if len(self.tables) > 100:
            raise ValueError('Doris source exceeds table allowlist budget')

        counts = entry.get('count_services', [])
        if not isinstance(counts, list) or any(item not in [list(t) for t in self.tables.values()] for item in counts):
            raise ValueError('count service must reference an allowlisted database and table')
        self.count_services = {alias: f'DORIS-COUNT-{alias}' for alias, table in self.tables.items()
                               if list(table) in counts}
        binding = [self.id, self.host, self.port, self.user, self.transport,
                   str(self.ca_file), self.vpn_interface, entry.get('time_zone')]
        self.binding = hashlib.sha256(json.dumps(binding).encode()).hexdigest()

    def check_vpn_route(self):
        if sys.platform == 'darwin':
            command = ['/sbin/route', '-n', 'get', self.host]
            pattern = r'^\s*interface:\s*(\S+)'
        elif sys.platform.startswith('linux'):
            command = ['ip', 'route', 'get', self.host]
            pattern = r'\bdev\s+(\S+)'
        else:
            raise DomainError('此操作系统请使用启用 TLS 的 Doris 地址或受管网关', 503)
        try:
            output = subprocess.run(command, capture_output=True, text=True, check=True, timeout=2).stdout
            match = re.search(pattern, output, re.MULTILINE)
            interface = match.group(1) if match else ''
        except (OSError, subprocess.SubprocessError):
            interface = ''
        if interface == self.vpn_interface or (self.vpn_interface == 'utun*' and re.fullmatch(r'utun\d+', interface)):
            return
        raise DomainError(f'数据源「{self.name}」的请求未走指定 VPN 接口，请连接该数据源的 VPN', 503)

    @contextmanager
    def cursor(self, *, streaming=False):
        try:
            import pymysql
        except ImportError:
            raise DomainError('服务端缺少 Doris 数据库驱动', 503) from None
        if not self.connections.acquire(timeout=1):
            raise DomainError(f'数据源「{self.name}」当前查询较多，请稍后重试', 429)
        connection = None
        try:
            settings = {}
            driver = pymysql.connect
            if self.transport == 'tls':
                settings['ssl'] = ssl.create_default_context(cafile=self.ca_file)
                source_name = self.name

                class RequiredTLS(pymysql.connections.Connection):
                    def _request_authentication(self):
                        # Require TLS before the driver sends any auth data.
                        if not self.server_capabilities & pymysql.constants.CLIENT.SSL:
                            raise DomainError(f'数据源「{source_name}」未提供 TLS 安全连接', 503)
                        super()._request_authentication()

                driver = RequiredTLS
            else:
                self.check_vpn_route()
                settings['ssl_disabled'] = True
            connection = driver(
                host=self.host, port=self.port, user=self.user, password=self.password,
                autocommit=True, charset='utf8mb4', local_infile=False,
                connect_timeout=CONNECT_TIMEOUT, read_timeout=QUERY_TIMEOUT + 2,
                write_timeout=CONNECT_TIMEOUT,
                cursorclass=pymysql.cursors.SSCursor if streaming else pymysql.cursors.Cursor,
                **settings,
            )
            cursor = connection.cursor()
            yield cursor
        except (ssl.SSLError, FileNotFoundError):
            raise DomainError(f'数据源「{self.name}」的 TLS 校验失败，请核对证书配置', 503) from None
        except pymysql.MySQLError as exc:
            code = exc.args[0] if exc.args else None
            if code in (1044, 1045, 1142, 1143, 1227, 1370):
                raise DomainError(f'数据源「{self.name}」拒绝连接或读取，请核对只读账号及表授权', 403) from None
            if code in (1054, 1146):
                raise DomainError(f'数据源「{self.name}」的表或字段已变化，请刷新目录', 409) from None
            if code in (2002, 2003, 2006, 2013, 2055):
                raise DomainError(f'数据源「{self.name}」连接失败或查询超时，请检查平台主机的 VPN 路由及源服务', 503) from None
            raise DomainError(f'数据源「{self.name}」拒绝或无法完成本次查询', 502) from None
        finally:
            # Close the socket directly. Closing an unfinished streaming cursor
            # first can drain all pending rows and exceed the transfer budget.
            try:
                if connection is not None and connection.open:
                    connection.close()
            finally:
                self.connections.release()

    def snapshot(self):
        with self.cursor() as cursor:
            predicates = ' OR '.join('(TABLE_SCHEMA=%s AND TABLE_NAME=%s)' for _ in self.tables)
            values = [value for pair in self.tables.values() for value in pair]
            cursor.execute(
                'SELECT /*+ SET_VAR(query_timeout=6) */ TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,'
                'COLUMN_TYPE,IS_NULLABLE,COLUMN_COMMENT FROM internal.information_schema.columns '
                'WHERE (' + predicates + ') ORDER BY TABLE_SCHEMA,TABLE_NAME,ORDINAL_POSITION LIMIT 15001',
                values,
            )
            rows = cursor.fetchall()
        if len(rows) > 15000:
            raise DomainError('Doris 元数据超过扫描预算，请缩小允许范围', 409)
        by_table = {pair: alias for alias, pair in self.tables.items()}
        result = {}
        for database, table, name, dtype, nullable, comment in rows:
            alias = by_table.get((database, table))
            if alias is None:
                continue
            identifier(name)
            item = result.setdefault(alias, {'columns': [], 'column_details': [],
                'physical_name': database + '.' + table, 'remote_binding': self.binding})
            item['columns'].append(name)
            item['column_details'].append({'name': name, 'type': dtype, 'nullable': nullable == 'YES',
                              'comment': comment or '', 'remote_binding': self.binding})
        if not result:
            raise DomainError(f'数据源「{self.name}」未返回允许表的结构，请核对表名和只读账号授权', 403)
        return {'domain': self.name, 'tables': result}

    def table_sql(self, table):
        pair = self.tables.get(table)
        if pair is None:
            raise DomainError('Doris 表已移出数据源允许范围', 403)
        return 'internal.' + '.'.join(identifier(part) for part in pair)

    def read(self, table, fields, filters, candidate_sets, time_window, limit, ordering=()):
        column_names = {field['name'] for field in fields}
        where = []
        args = []
        for name, op, value in filters:
            if name not in column_names or op not in OPERATORS:
                raise DomainError('Doris 筛选条件未绑定已登记字段', 403)
            where.append(f'{identifier(name)} {op} %s')
            args.append(value)
        for name, values in candidate_sets:
            if not values:
                where.append('1=0')
            else:
                where.append(f'{identifier(name)} IN (' + ','.join(['%s'] * len(values)) + ')')
                args.extend(values)
        if time_window:
            field, start, end = time_window
            dtype = str(field['structure']['type']).lower()
            if not dtype.startswith('datetime') or self.time_zone is None:
                raise DomainError('Doris 时间窗需要源端 DATETIME 字段及登记的时区，可改用明确的字段筛选条件', 409)
            column = identifier(field['name'])
            where.extend([f'{column} >= %s', f'{column} < %s'])
            args.extend(datetime.fromisoformat(value).astimezone(self.time_zone).replace(tzinfo=None)
                        for value in (start, end))
        query = ('SELECT /*+ SET_VAR(query_timeout=6) */ '
                 + ','.join(identifier(field['name']) for field in fields) + ' FROM ' + self.table_sql(table)
                 + (' WHERE ' + ' AND '.join(where) if where else ''))
        if ordering:
            order_terms = []
            for name, direction, nulls_first in ordering:
                if name not in column_names or direction not in ('asc', 'desc'):
                    raise DomainError('Doris 排序未绑定已登记字段', 403)
                column = identifier(name)
                order_terms.extend([f'{column} IS NULL ' + ('DESC' if nulls_first else 'ASC'),
                                    f'{column} {direction.upper()}'])
            query += ' ORDER BY ' + ','.join(order_terms)
        query += ' LIMIT ' + str(limit)
        rows = []
        size = 0
        with self.cursor(streaming=True) as cursor:
            cursor.execute(query, tuple(args))
            for source_row in cursor:
                row = tuple(_row_value(value) for value in source_row)
                size += sum(len(value.encode('utf-8')) if isinstance(value, str) else 16 for value in row)
                if size > MAX_RESULT_BYTES:
                    raise DomainError('Doris 读取超过传输预算，请缩小查询范围', 409)
                rows.append(row)
        return rows

    def count(self, table):
        with self.cursor() as cursor:
            cursor.execute('SELECT /*+ SET_VAR(query_timeout=6) */ COUNT(*) FROM ' + self.table_sql(table))
            count = int(cursor.fetchone()[0])
        return count if count >= 5 else None


class DorisSources:
    """Each source has its own metadata worker; queries use short-lived connections."""

    def __init__(self, store):
        self.store = store
        self.stop = Event()
        self.threads = []
        self.locks = {}
        self.sources = {}
        self.config_error = None
        path = Path(os.environ.get('MEDFABRIC_DORIS_CONFIG',
                    str(Path.home() / '.config' / 'medfabric' / 'doris.json'))).expanduser()
        try:
            if not path.is_file():
                return
            if os.name == 'posix' and path.stat().st_mode & 0o077:
                raise ValueError('Doris configuration file must be private')
            raw = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(raw, list) or len(raw) > 16:
                raise ValueError('invalid Doris configuration')
            for entry in raw:
                source = DorisSource(entry)
                if source.id in self.sources:
                    raise ValueError('duplicate Doris source ID')
                self.sources[source.id] = source
                self.locks[source.id] = Lock()
        except (ValueError, TypeError, KeyError, OSError, ZoneInfoNotFoundError, DomainError):
            self.sources.clear()
            self.locks.clear()
            self.config_error = 'Doris 配置无效。请核对文件格式、允许表范围及文件权限。'
            LOG.error(self.config_error)

    def status(self, source_id):
        if source_id not in self.sources:
            return 'unavailable'
        scan = self.store.get('scan', source_id)
        if scan and scan.get('status') == 'success' and any(
                table.get('remote_binding') == self.sources[source_id].binding
                for table in scan.get('tables', {}).values()):
            return 'connected'
        return 'unavailable'

    def statuses(self):
        result = []
        for source in self.sources.values():
            scan = self.store.get('scan', source.id) or {}
            status = self.status(source.id)
            result.append({'id': source.id, 'name': source.name, 'status': status,
                           'last_error': scan.get('error') if status != 'connected' else None})
        return result

    def scan(self, catalog, source):
        with self.locks[source.id]:
            try:
                snapshot = source.snapshot()
            except DomainError as exc:
                catalog.record_scan(source.id, {'status': 'failed', 'error': str(exc)})
                return
            with self.store.atomic():
                registered = self.store.get('source', source.id)
                if registered is not None and registered.get('connector') != 'doris':
                    LOG.error('Doris data source ID conflicts with an existing connector')
                    return
                data = {'source_id': source.id, 'connector': 'doris', 'source_id_kind': 'remote',
                        'name': source.name}
                if not registered or any(registered.get(k) != v for k, v in data.items()):
                    self.store.save('source', source.id, data, registered['version'] if registered else 0,
                                    'doris-connector', '登记 Doris 只读数据源')
                catalog.ingest(source.id, snapshot)
                catalog.record_scan(source.id, {'status': 'success', 'tables': snapshot['tables']})

    def start(self, catalog):
        def worker(source):
            while not self.stop.is_set():
                try:
                    self.scan(catalog, source)
                except Exception as exc:
                    LOG.warning('Doris metadata scan failed (%s)', type(exc).__name__)
                if self.stop.wait(SCAN_INTERVAL):
                    break

        for source in self.sources.values():
            thread = Thread(target=worker, args=(source,), name=f'doris-metadata-{source.id}', daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=0.1)

    def for_table(self, source_id, table, fields):
        source = self.sources.get(source_id)
        if source is None:
            raise DomainError('Doris 数据源未配置或已移除，请联系平台管理员', 503)
        source.table_sql(table)
        if any(field.get('structure', {}).get('remote_binding') != source.binding for field in fields):
            raise DomainError('Doris 连接配置已变更，请刷新目录并重新确认查询', 409)
        return source

    def services(self):
        for source in self.sources.values():
            for alias, service_id in source.count_services.items():
                yield source, alias, service_id
