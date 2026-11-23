"""OpenAlex public scholarly metadata connector and bounded daily snapshot."""
import logging
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import httpx

from management.catalog.sources import SQLiteSource
from platform_core.storage import DomainError

SOURCE = 'openalex'
URL = 'https://api.openalex.org/works'
PATH = 'openalex.sqlite'
FILTER = 'primary_topic.field.id:27'
FIELD_DOCS = 'https://help.openalex.org/data/works/attributes/'
TABLES = {
    'openalex_works': {
        'work_id': ('TEXT PRIMARY KEY', 'OpenAlex 论文唯一标识'),
        'title': ('TEXT', '论文标题'),
        'doi': ('TEXT', '数字对象标识符 DOI'),
        'publication_date': ('TEXT', '发表日期'),
        'publication_year': ('INTEGER', '发表年份'),
        'work_type': ('TEXT', '文献类型'),
        'language': ('TEXT', '文献语言代码'),
        'cited_by_count': ('INTEGER', 'OpenAlex 当前引用次数'),
        'source_id': ('TEXT', '期刊或出版来源 OpenAlex ID'),
        'source_title': ('TEXT', '期刊或出版来源名称'),
        'landing_page_url': ('TEXT', '原文页面链接'),
        'oa_status': ('TEXT', '开放获取状态'),
        'is_oa': ('BOOLEAN', '是否开放获取'),
        'abstract': ('TEXT', 'OpenAlex 提供的摘要文本'),
        'upstream_updated_at': ('TEXT', 'OpenAlex 记录更新时间'),
        'synced_at': ('TEXT', '平台同步时间'),
    },
    'openalex_authors': {
        'author_id': ('TEXT PRIMARY KEY', '作者 OpenAlex ID'),
        'author_name': ('TEXT', '作者名称'),
        'orcid': ('TEXT', '作者 ORCID'),
    },
    'openalex_institutions': {
        'institution_id': ('TEXT PRIMARY KEY', '机构 OpenAlex ID'),
        'institution_name': ('TEXT', '机构名称'),
        'country_code': ('TEXT', '机构所在国家或地区代码'),
    },
    'openalex_sources': {
        'source_id': ('TEXT PRIMARY KEY', '期刊或出版来源 OpenAlex ID'),
        'source_title': ('TEXT', '期刊或出版来源名称'),
        'source_type': ('TEXT', '出版来源类型'),
    },
    'openalex_authorships': {
        'work_id': ('TEXT', '论文 OpenAlex ID'),
        'author_id': ('TEXT', '作者 OpenAlex ID'),
        'author_order': ('INTEGER', '论文署名顺序'),
        'institution_id': ('TEXT', '署名机构 OpenAlex ID'),
        'raw_affiliation': ('TEXT', '作者原始机构署名'),
    },
    'openalex_topics': {
        'work_id': ('TEXT', '论文 OpenAlex ID'),
        'topic_id': ('TEXT', '主题 OpenAlex ID'),
        'topic_name': ('TEXT', '学术主题名称'),
        'topic_score': ('REAL', '主题匹配分数'),
        'field_id': ('TEXT', '学科领域 OpenAlex ID'),
        'field_name': ('TEXT', '学科领域名称'),
        'subfield_id': ('TEXT', '学科子领域 OpenAlex ID'),
        'subfield_name': ('TEXT', '学科子领域名称'),
    },
    'openalex_references': {
        'work_id': ('TEXT', '引用方论文 OpenAlex ID'),
        'referenced_work_id': ('TEXT', '被引用论文 OpenAlex ID'),
    },
    'openalex_yearly': {
        'publication_year': ('INTEGER PRIMARY KEY', '发表年份'),
        'work_count': ('INTEGER', '当前同步范围内论文数'),
        'citation_count': ('INTEGER', '当前同步范围内被引次数之和'),
        'synced_at': ('TEXT', '平台同步时间'),
    },
}
FIELD_ALIASES = {
    ('openalex_works', 'work_id'): ['论文ID', '文献ID', 'OpenAlex论文标识'],
    ('openalex_works', 'title'): ['论文标题', '文献标题', '文章标题'],
    ('openalex_works', 'doi'): ['DOI', '数字对象标识'],
    ('openalex_works', 'publication_date'): ['发表时间', '出版日期'],
    ('openalex_works', 'publication_year'): ['年份', '发表年份', '出版年份'],
    ('openalex_works', 'work_type'): ['文献类型', '成果类型'],
    ('openalex_works', 'cited_by_count'): ['引用数', '被引次数', '被引频次'],
    ('openalex_works', 'source_title'): ['期刊', '出版物', '发表来源'],
    ('openalex_works', 'oa_status'): ['开放获取状态', '开放获取类型'],
    ('openalex_works', 'is_oa'): ['是否开放获取', '开放论文'],
    ('openalex_works', 'abstract'): ['摘要', '论文摘要', '文献摘要'],
    ('openalex_authors', 'author_name'): ['作者', '研究者', '作者姓名'],
    ('openalex_institutions', 'institution_name'): ['机构', '作者机构', '所属机构'],
    ('openalex_topics', 'topic_name'): ['研究主题', '学科主题', '主题'],
    ('openalex_topics', 'field_name'): ['学科领域', '领域'],
    ('openalex_topics', 'subfield_name'): ['学科子领域', '子领域'],
}

RELATIONS = {
    'openalex_works': [{'from': 'source_id', 'table': 'openalex_sources', 'to': 'source_id'}],
    'openalex_authors': [],
    'openalex_institutions': [],
    'openalex_sources': [],
    'openalex_authorships': [
        {'from': 'work_id', 'table': 'openalex_works', 'to': 'work_id'},
        {'from': 'author_id', 'table': 'openalex_authors', 'to': 'author_id'},
        {'from': 'institution_id', 'table': 'openalex_institutions', 'to': 'institution_id'},
    ],
    'openalex_topics': [{'from': 'work_id', 'table': 'openalex_works', 'to': 'work_id'}],
    'openalex_references': [
        {'from': 'work_id', 'table': 'openalex_works', 'to': 'work_id'},
        {'from': 'referenced_work_id', 'table': 'openalex_works', 'to': 'work_id'},
    ],
    'openalex_yearly': [],
}

class OpenAlexSource(SQLiteSource):
    def snapshot(self):
        snapshot = super().snapshot()
        tables = {name: item for name, item in snapshot['tables'].items() if name in TABLES}
        for name, item in tables.items():
            item['foreign_keys'] = [reference for reference in RELATIONS[name]
                if reference['from'] in item['columns']
                and reference['to'] in tables.get(reference['table'], {}).get('columns', [])]
        return {'domain': '学术文献', 'tables': tables}


SEARCH = 'SVC-OPENALEX-SEARCH'
DETAIL = 'SVC-OPENALEX-DETAIL'
STATS = 'SVC-OPENALEX-STATS'
SERVICES = {
    SEARCH: ('OpenAlex 文献检索', '按标题或摘要、年份、作者、机构、主题和开放获取状态检索医学与健康科学文献。', [
        {'k': 'keyword', 'type': 'string', 'req': False, 'demo': '心血管'},
        {'k': 'publication_year', 'type': 'int', 'req': False, 'demo': 2026},
        {'k': 'author', 'type': 'string', 'req': False, 'demo': ''},
        {'k': 'institution', 'type': 'string', 'req': False, 'demo': ''},
        {'k': 'topic', 'type': 'string', 'req': False, 'demo': ''},
        {'k': 'open_access', 'type': 'bool', 'req': False, 'demo': True},
        {'k': 'limit', 'type': 'int', 'req': False, 'demo': 20},
    ], ('openalex_works', 'openalex_authorships', 'openalex_authors', 'openalex_institutions', 'openalex_topics')),
    DETAIL: ('OpenAlex 文献详情', '按 OpenAlex ID 或 DOI 查看本地同步文献及其作者、机构、主题和参考文献。', [
        {'k': 'identifier', 'type': 'string', 'req': True, 'demo': 'W123'},
    ], ('openalex_works', 'openalex_authorships', 'openalex_authors', 'openalex_institutions', 'openalex_topics', 'openalex_references')),
    STATS: ('OpenAlex 年度成果统计', '按发表年份汇总当前同步范围的文献数量与引用次数。', [
        {'k': 'publication_year', 'type': 'int', 'req': False, 'demo': 2026},
    ], ('openalex_yearly',)),
}


def _load_key_from_local_env():
    """Read only this connector's key from the ignored project .env file."""
    if os.environ.get('OPENALEX_API_KEY'):
        return
    env_path = Path(__file__).resolve().parents[2] / '.env'
    try:
        lines = env_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    for line in lines:
        name, sep, value = line.partition('=')
        if sep and name.strip().lstrip('\ufeff') == 'OPENALEX_API_KEY':
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if value:
                os.environ['OPENALEX_API_KEY'] = value
            return


def _abstract(index):
    if not index:
        return None
    return ' '.join(word for _, word in sorted(
        (position, word) for word, positions in index.items() for position in positions))


def _nested_name(value, key='display_name'):
    return value.get(key) if isinstance(value, dict) else None


def _mapping(work, synced_at):
    wid = work['id']
    location = work.get('primary_location') or {}
    source = location.get('source') or {}
    oa = work.get('open_access') or {}
    works = [(wid, work.get('display_name'), work.get('doi'), work.get('publication_date'),
              work.get('publication_year'), work.get('type'), work.get('language'), work.get('cited_by_count'),
              source.get('id'), source.get('display_name'), location.get('landing_page_url'),
              oa.get('oa_status'), int(bool(oa.get('is_oa'))), _abstract(work.get('abstract_inverted_index')),
              work.get('updated_date'), synced_at)]
    authors, institutions, sources, authorships, topics, references = {}, {}, {}, [], [], []
    if source.get('id'):
        sources[source['id']] = (source['id'], source.get('display_name'), source.get('type'))
    for order, authorship in enumerate(work.get('authorships') or [], 1):
        author = authorship.get('author') or {}
        author_id = author.get('id')
        if author_id:
            authors[author_id] = (author_id, author.get('display_name'), author.get('orcid'))
        affiliations = authorship.get('raw_affiliation_strings') or []
        raw_affiliation = '; '.join(str(x) for x in affiliations if x) or None
        orgs = authorship.get('institutions') or []
        if not orgs:
            if author_id:
                authorships.append((wid, author_id, order, None, raw_affiliation))
            continue
        for organization in orgs:
            institution_id = organization.get('id')
            if institution_id:
                institutions[institution_id] = (institution_id, organization.get('display_name'),
                                                organization.get('country_code'))
            if author_id:
                authorships.append((wid, author_id, order, institution_id, raw_affiliation))
    for topic in work.get('topics') or []:
        field = topic.get('field') or {}
        subfield = topic.get('subfield') or {}
        topics.append((wid, topic.get('id'), topic.get('display_name'), topic.get('score'),
                       field.get('id'), field.get('display_name'), subfield.get('id'), subfield.get('display_name')))
    references.extend((wid, ref) for ref in work.get('referenced_works') or [])
    return {
        'openalex_works': works,
        'openalex_authors': list(authors.values()),
        'openalex_institutions': list(institutions.values()),
        'openalex_sources': list(sources.values()),
        'openalex_authorships': authorships,
        'openalex_topics': topics,
        'openalex_references': references,
    }


class OpenAlex:
    def __init__(self, catalog):
        _load_key_from_local_env()
        self.catalog = catalog
        self.path = catalog.sources.root / PATH
        self.stop = threading.Event()
        self.thread = None
        self.filters = os.environ.get('MEDFABRIC_OPENALEX_FILTER', FILTER).strip() or FILTER
        self.key = os.environ.get('OPENALEX_API_KEY', '').strip()
        self.interval = max(3600, min(int(os.environ.get('MEDFABRIC_OPENALEX_SYNC_SECONDS', '86400')), 604800))
        self.max_records = max(100, min(int(os.environ.get('MEDFABRIC_OPENALEX_MAX_RECORDS', '1000')), 10000))
        self.log = logging.getLogger(__name__)
        self.status_lock = threading.Lock()
        self._status = {'state': 'pending', 'last_error': None}

    def _request_page(self, client, cursor):
        params = {'filter': self.filters, 'sort': 'publication_date:desc', 'per_page': 100,
                  'cursor': cursor}
        headers = {'Authorization': 'Bearer ' + self.key} if self.key else {}
        response = client.get(URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get('results'), list):
            raise ValueError('OpenAlex returned an invalid results envelope')
        return payload

    def refresh(self):
        started = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.status_lock:
            self._status = {'state': 'syncing', 'started_at': started, 'last_error': None}
        try:
            synced = started
            works = []
            cursor = '*'
            request_cap = self.max_records if self.key else min(self.max_records, 100)
            with httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as client:
                while cursor and len(works) < request_cap:
                    payload = self._request_page(client, cursor)
                    works.extend(payload['results'][:request_cap - len(works)])
                    cursor = (payload.get('meta') or {}).get('next_cursor')
                    if not payload['results']:
                        break
            rows = {table: [] for table in TABLES}
            for work in works:
                for table, records in _mapping(work, synced).items():
                    rows[table].extend(records)
            with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                for table, fields in TABLES.items():
                    db.execute('DROP TABLE IF EXISTS "' + table + '"')
                    columns = ', '.join('"' + name + '" ' + dtype for name, (dtype, _) in fields.items())
                    db.execute('CREATE TABLE "' + table + '" (' + columns + ')')
                    if table == 'openalex_yearly':
                        continue
                    placeholders = ', '.join('?' for _ in fields)
                    names = ', '.join('"' + name + '"' for name in fields)
                    db.executemany('INSERT OR REPLACE INTO "' + table + '" (' + names + ') VALUES (' + placeholders + ')', rows[table])
                db.execute('INSERT INTO openalex_yearly '
                           'SELECT publication_year, COUNT(*), COALESCE(SUM(cited_by_count), 0), ? '
                           'FROM openalex_works WHERE publication_year IS NOT NULL GROUP BY publication_year', (synced,))
                db.execute('CREATE TABLE IF NOT EXISTS _openalex_sync (synced_at TEXT, source_filter TEXT)')
                sync_columns = {row[1] for row in db.execute('PRAGMA table_info(_openalex_sync)')}
                if 'record_count' not in sync_columns:
                    db.execute('ALTER TABLE _openalex_sync ADD COLUMN record_count INTEGER')
                db.execute('DELETE FROM _openalex_sync')
                db.execute('INSERT INTO _openalex_sync (synced_at, source_filter, record_count) VALUES (?, ?, ?)',
                           (synced, self.filters, len(works)))
            self.register()
            result = {'state': 'ready', 'last_success_at': synced, 'record_count': len(works),
                      'last_error': None}
            with self.status_lock:
                self._status = result
            self.log.info('OpenAlex snapshot refreshed: %d works', len(works))
            return len(works)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            message = ('OpenAlex API key was rejected (HTTP %d)' % code if code in (401, 403)
                       else 'OpenAlex API request failed (HTTP %d)' % code)
            self._failed(message)
            raise
        except (httpx.RequestError, ValueError, sqlite3.Error) as exc:
            message = 'OpenAlex sync failed (%s)' % type(exc).__name__
            self._failed(message)
            raise
        except Exception as exc:
            message = 'OpenAlex sync failed (%s)' % type(exc).__name__
            self._failed(message)
            raise

    def _failed(self, message):
        self.log.warning('%s', message)
        with self.status_lock:
            self._status = {'state': 'failed', 'last_error': message,
                            'failed_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}

    def _define_fields(self, objects):
        from management.catalog.field_library import FieldLibrary
        from management.store import now
        store = self.catalog.store
        library = FieldLibrary(store)
        definitions = {row['field_id']: row for row in library.items()}
        for obj in objects:
            if obj.get('classification') != 'public':
                obj = self.catalog.annotate(obj['id'], {'classification': 'public',
                    'classification_basis': 'OpenAlex 公共学术元数据',
                    'expected_version': obj['version']}, 'openalex')
            if obj['kind'] != 'field':
                continue
            field_label, field_definition = TABLES[obj['table']][obj['name']][1], TABLES[obj['table']][obj['name']][1]
            definition = definitions.get(obj['id']) or store.get('field_definition', obj['id'])
            if not definition or definition.get('status') not in ('draft', 'confirmed'):
                continue
            if definition.get('status') == 'confirmed':
                continue
            aliases = list(dict.fromkeys([field_label, *FIELD_ALIASES.get((obj['table'], obj['name']), []), obj['name']]))
            data = {**definition, 'label': field_label, 'definition': field_definition, 'aliases': aliases,
                    'basis': FIELD_DOCS, 'status': 'confirmed', 'confirmed_by': 'openalex-connector',
                    'confirmed_at': now(),
                    'confirmation_note': 'OpenAlex 官方公开字段映射；适用于本地公开学术元数据快照。'}
            store.save('field_definition', obj['id'], data, definition['version'],
                       'openalex-connector', '登记并确认 OpenAlex 公开字段定义')

    def register(self):
        snapshot = OpenAlexSource(self.path).snapshot()
        store = self.catalog.store
        if not store.get('source', SOURCE):
            store.save('source', SOURCE, {'source_id': SOURCE, 'connector': 'sqlite',
                'path': PATH, 'name': 'OpenAlex · 医学与健康科学',
                'description': '公开学术元数据；每日同步最近记录',
                'hidden_tables': ['_openalex_sync']}, 0, 'openalex-connector',
                '登记 OpenAlex 学术数据源')
        self.catalog.ingest(SOURCE, snapshot)
        objects = self.catalog.objects(source_id=SOURCE, status='active')
        self._define_fields(objects)
        for sid, (name, description, params, tables) in SERVICES.items():
            existing = store.get('service', sid)
            source_objects = [obj['id'] for obj in objects
                              if obj['kind'] == 'table' and obj['table'] in tables]
            if existing:
                continue
            store.save('service', sid, {'name': name, 'desc': description, 'description': description,
                'params': params, 'source_objects': source_objects, 'handler_id': sid,
                'service_type': 'metric' if sid == STATS else 'query', 'lifecycle': 'published',
                'visibility': 'public', 'provider': 'openalex', 'update_period': '每日自动同步',
                'tags': ['OpenAlex', '医学与健康科学', '公开元数据']}, 0,
                'openalex-connector', '登记 OpenAlex 学术数据服务')

    def start(self):
        if self.thread is not None:
            return

        def loop():
            while not self.stop.is_set():
                try:
                    self.refresh()
                except Exception:
                    pass
                if self.stop.wait(self.interval):
                    break

        self.thread = threading.Thread(target=loop, name='openalex-sync', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def status(self):
        with self.status_lock:
            memory = dict(self._status)
        status = {'source': 'OpenAlex', 'key_configured': bool(self.key),
                  'state': memory.get('state', 'pending'), 'last_error': memory.get('last_error'),
                  'sync_interval_seconds': self.interval, 'max_records': self.max_records,
                  'scope': '医学与健康科学（OpenAlex Medicine field 27）',
                  'logical_tables': list(TABLES)}
        if self.path.is_file():
            try:
                with closing(SQLiteSource(self.path).connection()) as db:
                    row = db.execute('SELECT synced_at, source_filter, record_count FROM _openalex_sync LIMIT 1').fetchone()
                    if row:
                        status.update(last_success_at=row[0], source_filter=row[1], record_count=row[2])
                        if memory.get('state') in ('pending', 'failed') and not memory.get('last_error'):
                            status['state'] = 'ready'
                        if memory.get('state') == 'pending':
                            status['state'] = 'ready'
            except sqlite3.Error:
                pass
        return status

    def invoke(self, sid, params):
        params = validate_params(sid, params)
        if not self.path.is_file():
            raise DomainError('OpenAlex 正在首次同步，请稍后重试', 503)
        with closing(SQLiteSource(self.path).connection()) as db:
            db.row_factory = sqlite3.Row
            status_row = db.execute('SELECT * FROM _openalex_sync LIMIT 1').fetchone()
            if status_row is None:
                raise DomainError('OpenAlex 尚无同步成功的数据', 503)
            result = {'source': 'OpenAlex', 'synced_at': status_row['synced_at'],
                      'scope': '医学与健康科学；结果仅覆盖当前同步快照'}
            if sid == SEARCH:
                clauses, values = [], []
                keyword = params.get('keyword')
                if keyword:
                    clauses.append("(instr(lower(COALESCE(w.title, '')), lower(?)) > 0 OR instr(lower(COALESCE(w.abstract, '')), lower(?)) > 0)")
                    values.extend((keyword, keyword))
                if params.get('publication_year') is not None:
                    clauses.append('w.publication_year = ?')
                    values.append(params['publication_year'])
                if params.get('author'):
                    clauses.append("EXISTS (SELECT 1 FROM openalex_authorships a JOIN openalex_authors p ON p.author_id=a.author_id WHERE a.work_id=w.work_id AND instr(lower(COALESCE(p.author_name,'')),lower(?))>0)")
                    values.append(params['author'])
                if params.get('institution'):
                    clauses.append("EXISTS (SELECT 1 FROM openalex_authorships a JOIN openalex_institutions i ON i.institution_id=a.institution_id WHERE a.work_id=w.work_id AND instr(lower(COALESCE(i.institution_name,'')),lower(?))>0)")
                    values.append(params['institution'])
                if params.get('topic'):
                    clauses.append("EXISTS (SELECT 1 FROM openalex_topics t WHERE t.work_id=w.work_id AND instr(lower(COALESCE(t.topic_name,'')),lower(?))>0)")
                    values.append(params['topic'])
                if params.get('open_access') is not None:
                    clauses.append('w.is_oa = ?')
                    values.append(int(params['open_access']))
                where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
                query = ('SELECT w.work_id, w.title, w.doi, w.publication_date, w.publication_year, '
                         'w.work_type, w.cited_by_count, w.source_title, w.landing_page_url, w.oa_status, w.is_oa '
                         'FROM openalex_works w' + where + ' ORDER BY w.publication_date DESC, w.cited_by_count DESC LIMIT ?')
                rows = [dict(row) for row in db.execute(query, (*values, params['limit']))]
                return {**result, 'count': len(rows), 'items': rows}
            if sid == STATS:
                year = params.get('publication_year')
                rows = db.execute('SELECT * FROM openalex_yearly WHERE (? IS NULL OR publication_year = ?) '
                                  'ORDER BY publication_year DESC', (year, year))
                return {**result, 'items': [dict(row) for row in rows]}
            identifier = params['identifier'].strip().lower()
            identifier = identifier.removeprefix('https://openalex.org/').removeprefix('https://doi.org/')
            paper = db.execute("SELECT * FROM openalex_works WHERE lower(work_id) IN (?, ?) OR "
                               "lower(replace(doi,'https://doi.org/','')) = ? ORDER BY work_id LIMIT 1",
                               (identifier, 'https://openalex.org/' + identifier, identifier)).fetchone()
            if paper is None:
                raise DomainError('当前 OpenAlex 同步范围内未找到该论文', 404)
            work_id = paper['work_id']
            result['paper'] = dict(paper)
            result['authors'] = [dict(row) for row in db.execute(
                'SELECT p.author_id, p.author_name, p.orcid, a.author_order, i.institution_id, i.institution_name '
                'FROM openalex_authorships a LEFT JOIN openalex_authors p ON p.author_id=a.author_id '
                'LEFT JOIN openalex_institutions i ON i.institution_id=a.institution_id '
                'WHERE a.work_id=? ORDER BY a.author_order', (work_id,))]
            result['topics'] = [dict(row) for row in db.execute(
                'SELECT * FROM openalex_topics WHERE work_id=? ORDER BY topic_score DESC', (work_id,))]
            result['references'] = [dict(row) for row in db.execute(
                'SELECT * FROM openalex_references WHERE work_id=? ORDER BY referenced_work_id', (work_id,))]
            return result

    @staticmethod
    def supports(sid):
        return sid in SERVICES


def validate_params(sid, value):
    if sid not in SERVICES or not isinstance(value, dict):
        raise DomainError('OpenAlex 服务或参数无效')
    if set(value) - {p['k'] for p in SERVICES[sid][2]}:
        raise DomainError('OpenAlex 服务包含未登记参数')
    params = dict(value)
    for key in ('keyword', 'author', 'institution', 'topic', 'identifier'):
        if key in params:
            if not isinstance(params[key], str) or len(params[key]) > 300:
                raise DomainError('检索文本和论文标识须为不超过 300 字的文本')
            params[key] = params[key].strip()
    if sid == DETAIL and not params.get('identifier'):
        raise DomainError('请提供 OpenAlex ID 或 DOI')
    if 'publication_year' in params and (type(params['publication_year']) is not int or
                                         not 1000 <= params['publication_year'] <= 9999):
        raise DomainError('发表年份须为四位整数')
    if 'open_access' in params and type(params['open_access']) is not bool:
        raise DomainError('开放获取筛选须为 true 或 false')
    if sid == SEARCH:
        limit = params.setdefault('limit', 20)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError('检索返回条数须为 1 至 100')
    return params
