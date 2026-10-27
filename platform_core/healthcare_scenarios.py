"""Synthetic healthcare business fixtures executed through the real local gates.

Business labels describe clinical data-service work. Provenance stays internal;
no real patient, clinician or hospital identity is included in the source.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import medsim
from management.catalog.sources import SQLiteSource
from management.reconciliation.service import Reconciliation
from platform_core.storage import DomainError, now

SEED_ID = 'HEALTHCARE-SCENARIOS-V2'
SOURCE_ID = 'SRC-CLINICAL-OPERATIONS'
SOURCE_FILE = 'healthcare_scenarios_v2/clinical_operations.sqlite'
APPLICATION_ID = 1296450618
PLATFORM, REQUESTER, PROVIDER = 'demo-platform', 'demo-requester', 'demo-provider'
MANAGEMENT_KINDS = ('source', 'object', 'scan', 'concept', 'mapping', 'mapping_rule',
                    'relation', 'identifier', 'service', 'validation', 'notice',
                    'demand', 'spec', 'pair', 'case', 'feedback', 'improvement', 'metric_definition')
EXECUTION_KINDS = ('view', 'plan', 'workflow', 'plan_state', 'job', 'grant', 'decision',
                   'run', 'delivery', 'trigger', 'contract', 'facility', 'command',
                   'materialization', 'node_result')
CAMPUSES = ('中心院区', '东院区', '西院区')
TABLES = {
    'chronic_followup': ('慢病随访登记', ('随访日期', '院区', '科室', '服务项目', '年龄段', '随访状态')),
    'research_cohort': ('临床研究队列筛选', ('入组日期', '院区', '科室', '课题代码', '年龄段', '入组状态')),
    'imaging_turnaround': ('影像检查签发记录', ('检查日期', '院区', '检查类型', '签发时段')),
    'laboratory_reports': ('检验报告质量登记', ('报告日期', '院区', '科室', '检验类别', '报告状态')),
}


def _source(root):
    path = (root / SOURCE_FILE).resolve()
    if not path.is_relative_to(root):
        raise DomainError('业务源路径超出数据目录', 403)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
            if con.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
                raise DomainError('业务源文件名已被其他数据占用，未覆盖', 409)
        return path
    with path.open('xb'):
        pass
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('PRAGMA application_id=' + str(APPLICATION_ID))
        con.execute('PRAGMA user_version=2')
        for table, (_, columns) in TABLES.items():
            definitions = ','.join('"' + c + '" TEXT' + ('' if c == '报告状态' else ' NOT NULL') for c in columns)
            con.execute('CREATE TABLE ' + table + ' (' + definitions + ')')
        followups, cohort, imaging, laboratory = [], [], [], []
        for campus_index, campus in enumerate(CAMPUSES):
            for day in range(1, 29):
                for n in range((10, 8, 6)[campus_index]):
                    programme = '高血压随访' if n % 2 == 0 else '糖尿病随访'
                    followups.append((f'2026-09-{day:02}', campus, '全科医学科', programme,
                        ('40—49岁', '50—59岁', '60—69岁')[(day + n) % 3],
                        '待回访' if (day + n) % 5 == 0 else '已完成'))
            # Extra records lie outside the accepted period and must not leak
            # into a confirmed September result.
            followups.extend([('2026-08-31', campus, '全科医学科', '高血压随访', '60—69岁', '已完成')] * 7)
            for band_index, band in enumerate(('40—49岁', '50—59岁', '60—69岁')):
                for n in range(18 + campus_index * 5 + band_index * 7):
                    cohort.append((f'2026-09-{n % 28 + 1:02}', campus, '内分泌科',
                        'RSP-2026-MET-09', band, '符合纳入'))
                cohort.extend([('2026-09-15', campus, '内分泌科', 'RSP-2026-MET-09', band, '待核对')] * 6)
                cohort.extend([('2026-09-16', campus, '心内科', 'RSP-2026-CVD-09', band, '符合纳入')] * 8)
            for modality_index, modality in enumerate(('CT', 'MR', '超声')):
                for stage, base_count in (('当日签发', 48), ('次日签发', 18), ('超过次日', 6)):
                    for n in range(base_count + campus_index * 4 + modality_index * 3):
                        imaging.append((f'2026-09-{n % 28 + 1:02}', campus, modality, stage))
            for test_index, category in enumerate(('血常规', '生化', '凝血')):
                for stage, base_count in (('已核验', 30), ('复核中', 6)):
                    for n in range(base_count + campus_index * 5 + test_index * 3):
                        laboratory.append((f'2026-09-{n % 28 + 1:02}', campus, '检验科', category, stage))
        for table, rows in zip(TABLES, (followups, cohort, imaging, laboratory)):
            con.executemany('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in TABLES[table][1]) + ')', rows)
    return path


def _ids(store, kinds):
    return {kind: {r['id'] for r in store.list(kind)} for kind in kinds}


def _name(store, kind, record, name):
    return store.save(kind, record['id'], {**record, 'name': name, 'demo': True, 'demo_seed': SEED_ID},
                      record['version'], PLATFORM, '补充业务名称与关联说明')


class _EvidenceScope:
    def __init__(self, evidence, plan_ids):
        self.evidence, self.plan_ids = evidence, set(plan_ids)

    def list(self):
        return [r for r in self.evidence.list() if r.get('object_id') in self.plan_ids]

    def get(self, oid):
        record = self.evidence.get(oid)
        if record.get('object_id') not in self.plan_ids:
            raise DomainError('本次复核仅允许引用当前业务批次')
        return record


def seed_healthcare(runtime):
    """Create V2 once; never renew dates or overwrite existing edits on restart."""
    e, m = runtime.execution, runtime.management
    if e.store.environment != 'demo' or m.store.environment != 'demo':
        raise DomainError('业务案例只能写入本地开发环境', 403)
    with e.lock, m.store.atomic(), e.store.atomic():
        existing = m.store.get('demo_seed', SEED_ID)
        if existing:
            return existing
        if m.store.get('source', SOURCE_ID):
            raise DomainError('业务源标识已存在但没有初始化记录，未覆盖', 409)
        before_m, before_e = _ids(m.store, MANAGEMENT_KINDS), _ids(e.store, EXECUTION_KINDS)
        path = _source(Path(medsim.DATA_DIR).resolve())
        m.catalog.sources.register({'source_id': SOURCE_ID, 'name': '医疗集团业务协同数据集',
            'connector': 'sqlite', 'path': SOURCE_FILE}, PLATFORM)
        snapshot = SQLiteSource(path).snapshot()
        snapshot['domain'] = '医疗质量与科研数据服务'
        m.catalog.ingest(SOURCE_ID, snapshot)
        m.store.save('scan', 'SCAN-CLINICAL-202609', {'name': '医疗业务数据结构与字段盘点',
            'status': 'success', 'source_id': SOURCE_ID, 'tables': snapshot['tables'],
            'demo': True, 'demo_seed': SEED_ID}, 0, PLATFORM, '扫描四类业务表并登记字段结构')
        fields, tables = {}, {}
        descriptions = {'院区': '业务发生院区，用于按组织汇总', '科室': '业务责任科室',
            '年龄段': '按十岁区间划分的年龄组，不包含精确出生日期', '服务项目': '慢病随访服务项目',
            '随访状态': '随访完成或待回访状态', '课题代码': '临床研究项目编号',
            '入组状态': '队列筛选后的入组核对状态', '检查类型': '影像检查模态',
            '签发时段': '检查完成至报告签发的日期区间', '检验类别': '检验专业类别',
            '报告状态': '报告核验环节的状态，用于识别缺失与待复核记录'}
        for obj in m.catalog.objects(source_id=SOURCE_ID):
            obj = m.catalog.annotate(obj['id'], {'expected_version': obj['version'],
                'meaning': descriptions.get(obj['name'], '业务发生日期' if obj['name'].endswith('日期') else TABLES[obj['table']][0]),
                'classification': '内部', 'owner': '医院数据中心', 'domain': '医疗质量与科研数据服务',
                'constraints': {'purpose': '院区运营分析、质量核验及经批准的科研统计', 'output': '按确认范围聚合输出；不提供个人标识'}}, PLATFORM)
            if obj['kind'] == 'field':
                obj = m.catalog.profile(obj['id'], PLATFORM)
                fields.setdefault(obj['table'], {})[obj['name']] = obj['id']
            else:
                tables[obj['table']] = obj['id']
                m.catalog.identifier({'local_id': obj['id'], 'global_id': 'urn:medfabric:clinical:' + obj['table']}, PLATFORM)
        for table, suffix, field, name in (
                ('chronic_followup', 'FOLLOWUP-CAMPUS', '院区', '随访服务院区'),
                ('research_cohort', 'RESEARCH-AGE', '年龄段', '科研队列年龄段'),
                ('imaging_turnaround', 'IMAGING-MODALITY', '检查类型', '影像检查模态'),
                ('laboratory_reports', 'LAB-STATUS', '报告状态', '检验报告核验状态')):
            concept = m.semantic.concept({'id': 'CONCEPT-' + suffix, 'name': name,
                'meaning': descriptions[field], 'domain': '医疗质量与科研数据服务', 'data_type': 'TEXT'}, PLATFORM)
            m.semantic.mapping({'id': 'MAP-' + suffix, 'concept_id': concept['id'],
                'fields': [fields[table][field]], 'mapping_type': 'direct', 'status': 'confirmed',
                'confirmation_note': '数据服务中心已核对业务定义、字段类型及值域'}, PLATFORM)

        configurations = {
            'FOLLOWUP': {'title': '2026年9月高血压随访完成情况分析', 'owner': PLATFORM,
                'table': 'chronic_followup', 'date': '随访日期', 'group': ['院区', '随访状态'],
                'scope': [('服务项目', '高血压随访')], 'mode': 'controlled_compute',
                'purpose': '慢病管理服务月度质控', 'audience': '医务部、全科医学科',
                'description': '核对三个院区9月高血压随访完成情况，按院区和随访状态汇总服务次数，支持安排待回访工作。',
                'feedback': '已完成与待回访分组清楚，统计范围与全科医学科月度管理口径一致。'},
            'COHORT': {'title': '代谢风险随访课题入组队列分层统计', 'owner': REQUESTER,
                'table': 'research_cohort', 'date': '入组日期', 'group': ['院区', '年龄段'],
                'scope': [('课题代码', 'RSP-2026-MET-09'), ('入组状态', '符合纳入')], 'mode': 'download',
                'purpose': '代谢风险随访课题可行性分析', 'audience': '临床研究中心、内分泌科',
                'description': '仅统计RSP-2026-MET-09课题已完成入组核对的9月记录，按院区和年龄段提供例数，不包含待核对记录和其他课题。',
                'feedback': '结果排除了待核对记录，可直接用于课题可行性表中的院区与年龄分层。'},
            'IMAGING': {'title': '影像报告签发时效分布查询', 'owner': REQUESTER,
                'table': 'imaging_turnaround', 'date': '检查日期', 'group': ['院区', '检查类型', '签发时段'],
                'scope': [], 'mode': 'api', 'purpose': '影像服务流程与工作负荷分析', 'audience': '临床研究中心、影像科',
                'description': '按9月检查日期汇总三个院区CT、MR与超声报告的签发时段，以授权API供流程分析使用，不输出影像文件或检查者身份。',
                'feedback': '接口按院区、检查类型和签发时段返回计数，能够复用到工作负荷分析。'},
            'LAB-QUALITY': {'title': '9月检验报告核验状态完整率复核', 'owner': PLATFORM,
                'table': 'laboratory_reports', 'date': '报告日期', 'group': ['院区', '检验类别', '报告状态'],
                'scope': [], 'mode': 'controlled_compute', 'purpose': '检验报告归档质量核查', 'audience': '医务部、检验科',
                'description': '核对血常规、生化及凝血报告状态，状态缺失时阻止交付；修复后使用相同范围和版本复测。'},
        }
        views = {}
        for key, cfg in configurations.items():
            views[key] = e.queries.define({'id': 'VIEW-' + key + '-202609', 'name': cfg['title'] + '视图',
                'fields': list(fields[cfg['table']].values()),
                'group_by': [fields[cfg['table']][name] for name in cfg['group']], 'output_mode': 'aggregate'}, cfg['owner'])
        e.queries.define({'id': 'VIEW-PROVIDER-LAB-202609', 'name': '检验科报告核验状态台账视图',
            'fields': list(fields['laboratory_reports'].values()),
            'group_by': [fields['laboratory_reports'][name] for name in ('院区', '报告状态')], 'output_mode': 'aggregate'}, PROVIDER)

        builtin_tables = [obj['id'] for source in medsim.SOURCE_DEFS
                          for obj in m.catalog.objects(source_id=source['id']) if obj['kind'] == 'table']
        service_scope = '已接入的医院信息、检验、影像、电子病历与监测来源目录摘要'
        service_body = {'id': 'SVC-RESEARCH-CATALOG', 'name': '科研数据目录覆盖核验',
            'service_type': 'verification', 'mode': 'regular', 'visibility': 'specified',
            'allowed_subjects': [PLATFORM, REQUESTER], 'purpose': '科研用数前的数据目录覆盖核验',
            'output': '来源名称与表数量', 'audience': '数据服务中心、临床研究中心',
            'conditions': '绑定已确认规格、有效授权和数据使用约定', 'provider': PROVIDER,
            'source_scope': service_scope, 'update_period': '按需读取',
            'quality_commitment': '结构字段齐全，实际验证通过后发布', 'restrictions': '仅提供目录摘要，不返回业务明细',
            'metering': '按核验请求记录运行次数', 'handler_id': 'SVC-META-CATALOG',
            'source_objects': builtin_tables, 'input_schema': {'type': 'object'},
            'output_schema': {'type': 'object'}, 'sample_input': {}, 'demo': True, 'demo_seed': SEED_ID}
        service = m.services.save(service_body, PROVIDER)
        validation = m.services.validate(service['id'], PROVIDER)
        _name(m.store, 'validation', validation, '科研目录覆盖核验发布检查')
        service = m.services.transition(service['id'], {'target': 'review', 'expected_version': service['version'],
            'reason': '供给方已提交完整服务定义与目录引用'}, PLATFORM)
        if validation['passed']:
            for target in ('trial', 'published'):
                service = m.services.transition(service['id'], {'target': target, 'expected_version': service['version'],
                    'validation_id': validation['id'], 'reason': '输入输出及目录引用核验通过，按流程发布'}, PLATFORM)
        pending = m.services.save({**service_body, 'id': 'SVC-CATALOG-ARRAY',
            'name': '科研目录批量接口格式适配', 'output_schema': {'type': 'array'},
            'description': '批量调用接口声明与当前输出格式不一致，修订后重新申请验证'}, PROVIDER)
        _name(m.store, 'validation', m.services.validate(pending['id'], PROVIDER), '批量接口输出格式一致性检查')
        draft = m.services.save({**service_body, 'id': 'SVC-FOLLOWUP-COVERAGE',
            'name': '出院随访数据可用性评估', 'purpose': '出院后30天随访研究的数据可用性确认',
            'description': '登记候选来源与随访字段要求，待补齐出院日期和回访关联规则后申请发布'}, PROVIDER)

        project_deadline = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        scenarios, plan_ids = [], []

        def demand(key, cfg, confirm=True, metadata=False, service_ids=None):
            rules = {'scope': {'kind': 'metadata'} if metadata else {'kind': 'rows', 'filters': [
                {'field': fields[cfg['table']][field], 'op': '=', 'value': value} for field, value in cfg['scope']]},
                'output_mode': 'object' if metadata else 'aggregate', 'circulation_modes': [cfg['mode']],
                'quality_min_complete': 1, 'max_rows': 60, 'min_group_size': 5}
            if not metadata:
                rules['time_field'] = fields[cfg['table']][cfg['date']]
                if not cfg['scope']:
                    rules['scope']['all_rows'] = True
            d = m.intake.save({'id': 'REQ-' + key + '-202609', 'title': cfg['title'],
                'entry': 'service' if metadata else 'goal', 'object_scope': '内置来源目录结构' if metadata else cfg['description'],
                'time_start': '2020-01-01T00:00:00+00:00' if metadata else '2026-09-01',
                'time_end': project_deadline if metadata else '2026-09-30', 'purpose': cfg['purpose'],
                'quality': '完整', 'deadline': project_deadline, 'delivery': '对象级' if metadata else '汇总',
                'service_ids': service_ids if service_ids is not None else ([service['id']] if metadata else []),
                'description': cfg['description'], 'audience': cfg['audience'],
                'authorization_evidence': '方案确认后签发用途限定授权，交付前核验有效期及使用约定',
                'execution_constraints': rules, 'demo': True, 'demo_seed': SEED_ID}, cfg['owner'])
            if not confirm:
                return d, None
            spec = m.intake.confirm(d['id'], {'expected_version': d['version'],
                'confirmations': {'content': True, 'attachments': True, 'gaps': True, 'execution_constraints': True},
                'note': '已核对业务范围、日期口径、完整率阈值、最小分组和交付方式'}, PLATFORM)
            return d, spec

        def plan(key, cfg, metadata=False):
            d, spec = demand(key, cfg, metadata=metadata)
            node = {'id': 'catalog-coverage', 'operation': 'service', 'service_id': service['id']} if metadata else {
                'id': key.lower() + '-summary', 'operation': 'view', 'view_id': views[key]['id']}
            p = e.plan({'id': 'PLAN-' + key + '-202609', 'name': cfg['title'] + '执行方案',
                'spec_id': spec['id'], 'spec_version': spec['version'], 'nodes': [node], 'circulation_mode': cfg['mode']}, cfg['owner'])
            e.confirm(p['id'], p['version'], cfg['owner'])
            plan_ids.append(p['id'])
            contract = e.store.save('contract', 'AGR-' + key + '-202609', {
                'name': cfg['title'] + '数据使用约定', 'created_by': cfg['owner'], 'plan_id': p['id'],
                'plan_version': p['version'], 'purpose': p['purpose'],
                'parties': [PLATFORM, PROVIDER] + ([REQUESTER] if cfg['owner'] == REQUESTER else []),
                'accepted': [], 'expires_at': project_deadline, 'scope': 'local',
                'obligations': '仅用于' + cfg['purpose'] + '；限确认范围内' + ('目录结构摘要' if metadata else '聚合统计') +
                    '；不得还原个人身份或向项目外转交；到期或撤销后停止读取',
                'demo': True, 'demo_seed': SEED_ID}, 0, cfg['owner'], '登记项目数据使用约定')
            for party in contract['parties']:
                contract = e.update('contract', contract, {'accepted': contract['accepted'] + [party]}, party)
            grant = e.security.grant({'subject': cfg['owner'], 'purpose': p['purpose'], 'plan_id': p['id'],
                'plan_version': p['version'], 'expires_at': expiry, 'contract_id': contract['id']}, PLATFORM)
            grant = _name(e.store, 'grant', grant, cfg['title'] + '用途授权')
            return d, spec, p, grant, contract

        def run(p, grant, label, retry_of=None):
            job = _name(e.store, 'job', e.submit(p['id'], p['version'], grant['id'], p['created_by'], retry_of=retry_of), label)
            job = e.run(job)
            _name(e.store, 'run', e.evidence.get(job['id']), label + '运行证据')
            if job['delivery_id']:
                _name(e.store, 'delivery', e.store.require('delivery', job['delivery_id']), label + '交付凭证')
            return job

        def record_scenario(key, cfg, d, spec, p, grant, contract, job):
            item = {'id': key, 'title': cfg['title'], 'status': 'success', 'subject': cfg['owner'],
                'demand_id': d['id'], 'spec_id': spec['id'], 'plan_id': p['id'], 'grant_id': grant['id'],
                'contract_id': contract['id'], 'job_id': job['id'], 'delivery_id': job['delivery_id'],
                'path': '/static/execution/index.html?tab=delivery', 'demo': True}
            scenarios.append(item)
            return item

        for key in ('FOLLOWUP', 'COHORT', 'IMAGING'):
            cfg = configurations[key]
            d, spec, p, grant, contract = plan(key, cfg)
            job = run(p, grant, cfg['title'])
            if job['state'] != 'success':
                raise DomainError('业务统计未通过执行检查：' + str(job['error']))
            item = record_scenario(key, cfg, d, spec, p, grant, contract, job)
            feedback = m.feedback.evaluate({'run_id': job['id'], 'ratings': {'usability': 5, 'explainability': 5,
                'timeliness': 4, 'satisfaction': 4}, 'comment': cfg['feedback']}, cfg['owner'])
            _name(m.store, 'feedback', feedback, cfg['title'] + '使用评价')
            trigger = e.trigger({'plan_id': p['id'], 'plan_version': p['version'], 'grant_id': grant['id'],
                'kind': 'interval', 'interval': 86400}, cfg['owner'])
            e.update('trigger', trigger, {'enabled': False, 'name': cfg['title'] + '日间更新调度',
                'description': '当前月度结果已交付，后续批次待确认时间范围后启用', 'demo': True, 'demo_seed': SEED_ID})
            if key == 'COHORT':
                retest = run(p, grant, '代谢风险课题队列同口径复核')
                if retest['state'] != 'success':
                    raise DomainError('科研队列复核未通过')
                comparison = m.feedback.compare({'before': [job['id']], 'after': [retest['id']],
                    'change_note': '保持课题、入组条件、时间窗及方案版本不变，二次执行核对分层计数与交付稳定性'}, PLATFORM)
                comparison = _name(m.store, 'improvement', comparison, '代谢风险课题队列同口径复测')
                item.update(retest_job_id=retest['id'], improvement_id=comparison['id'])
                expired = e.security.grant({'subject': REQUESTER, 'purpose': p['purpose'], 'plan_id': p['id'],
                    'plan_version': p['version'], 'expires_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}, PLATFORM)
                expired = _name(e.store, 'grant', expired, '代谢风险课题前期授权（已到期）')
                for test_grant, actor, reason in ((expired, REQUESTER, '授权已到期'), (grant, PROVIDER, '主体或用途')):
                    try:
                        e.submit(p['id'], p['version'], test_grant['id'], actor)
                    except DomainError as exc:
                        if reason not in str(exc):
                            raise
                    else:
                        raise DomainError('过期或主体不匹配的请求未被拒绝')
                scenarios.append({'id': 'ACCESS-REVIEW', 'title': '科研用数授权到期与申请主体核验',
                    'status': 'denied', 'subject': REQUESTER, 'plan_id': p['id'], 'expired_grant_id': expired['id'],
                    'backend_calls': 0, 'path': '/static/execution/index.html?tab=decision'})

        cfg = configurations['LAB-QUALITY']
        d, spec, recovery, grant, contract = plan('LAB-QUALITY', cfg)
        baseline = [run(recovery, grant, '检验报告核验状态基线第' + str(n + 1) + '批') for n in range(3)]
        if any(j['state'] != 'success' for j in baseline):
            raise DomainError('检验报告质量基线未通过')
        try:
            with closing(sqlite3.connect(path)) as con, con:
                con.execute('UPDATE laboratory_reports SET "报告状态"=NULL WHERE "院区"=? AND "检验类别"=? AND "报告状态"=?',
                            ('西院区', '生化', '已核验'))
            failed = run(recovery, grant, '西院区生化报告状态缺失检查')
            if failed['state'] != 'failed' or failed['delivery_id'] or 'quality' not in (failed['error'] or ''):
                raise DomainError('报告状态缺失未触发交付质量阻断')
        finally:
            with closing(sqlite3.connect(path)) as con, con:
                con.execute('UPDATE laboratory_reports SET "报告状态"=? WHERE "院区"=? AND "检验类别"=? AND "报告状态" IS NULL',
                            ('已核验', '西院区', '生化'))
        scoped = Reconciliation(m.store, _EvidenceScope(e.evidence, plan_ids), m.catalog)
        scoped.controller = e.control
        scoped.run()
        case_id = 'CASE-' + recovery['id'] + '-' + str(recovery['version']) + '-execution'
        case = _name(m.store, 'case', m.store.require('case', case_id), '西院区生化报告状态缺失复核工单')
        scoped.action(case['id'], {'expected_version': case['version'], 'action': 'pause_review',
            'note': '交付检查已拦截状态缺失结果，暂停后续执行；检验科核对状态映射并补齐缺失值'}, PLATFORM)
        e.confirm(recovery['id'], recovery['version'], PLATFORM)
        retest = run(recovery, grant, '西院区生化报告状态修复后复测', failed['id'])
        if retest['state'] != 'success':
            raise DomainError('报告状态修复后复测未通过')
        scoped.run()
        case = m.store.require('case', case_id)
        try:
            case = scoped.action(case['id'], {'expected_version': case['version'], 'action': 'close',
                'evidence_id': retest['id'], 'note': '同一方案版本及日期范围复测通过，报告状态完整且交付检查通过'}, PLATFORM)
        except DomainError:
            case = scoped.action(case['id'], {'expected_version': case['version'], 'action': 'observe',
                'note': '报告状态已补齐并通过交付检查；当前运行时延保留观察，待后续批次确认稳定性'}, PLATFORM)
        comparison = m.feedback.compare({'before': [failed['id']], 'after': [retest['id']],
            'change_note': '补齐西院区生化报告核验状态，保留原范围和方案版本重试，比较质量阻断与修复后的交付结果'}, PLATFORM)
        comparison = _name(m.store, 'improvement', comparison, '生化报告状态完整率修复对比')
        scenarios.append({'id': 'LAB-QUALITY', 'title': cfg['title'], 'status': case['status'], 'subject': PLATFORM,
            'demand_id': d['id'], 'spec_id': spec['id'], 'plan_id': recovery['id'], 'grant_id': grant['id'],
            'baseline_job_ids': [j['id'] for j in baseline], 'failed_job_id': failed['id'], 'retest_job_id': retest['id'],
            'delivery_id': retest['delivery_id'], 'case_id': case['id'], 'improvement_id': comparison['id'],
            'path': '/static/management/reconciliation.html'})

        if service['lifecycle'] == 'published':
            cfg = {'title': '临床研究用数前目录覆盖核验', 'owner': PLATFORM, 'mode': 'controlled_compute',
                'purpose': '科研项目数据可用性核查', 'audience': '临床研究中心、医院数据中心',
                'description': '核对医院信息、检验、影像、电子病历与监测来源的目录覆盖情况，为用数受理提供结构依据。'}
            d, spec, p, grant, contract = plan('CATALOG', cfg, metadata=True)
            job = run(p, grant, '科研用数目录覆盖核验')
            retest = run(p, grant, '科研目录核验接口同配置回归')
            if job['state'] != 'success' or retest['state'] != 'success':
                raise DomainError('目录核验服务实际调用未通过')
            item = record_scenario('CATALOG', cfg, d, spec, p, grant, contract, job)
            feedback = _name(m.store, 'feedback', m.feedback.evaluate({'run_id': job['id'],
                'ratings': {'usability': 4, 'explainability': 5, 'timeliness': 4, 'satisfaction': 4},
                'comment': '来源与表数量口径一致，可作为科研需求受理的目录覆盖依据；业务明细仍按各自授权取得。'}, PLATFORM), '科研目录覆盖核验使用评价')
            comparison = _name(m.store, 'improvement', m.feedback.compare({'before': [job['id']], 'after': [retest['id']],
                'change_note': '保持目录配置和接口版本不变重复调用，核对来源结构稳定性及返回模式一致性'}, PLATFORM), '科研目录核验接口稳定性复测')
            provider_grant = e.security.grant({'subject': PROVIDER, 'purpose': p['purpose'], 'plan_id': p['id'],
                'plan_version': p['version'], 'expires_at': expiry, 'contract_id': contract['id']}, PLATFORM)
            provider_grant = _name(e.store, 'grant', provider_grant, '目录接口维护窗口授权')
            e.security.revoke(provider_grant['id'], PLATFORM)
            try:
                e.security.check(provider_grant['id'], p, PROVIDER, p['purpose'], 'preflight')
            except DomainError as exc:
                if '已撤销' not in str(exc):
                    raise
            else:
                raise DomainError('维护窗口关闭后授权仍可用')
            item.update(service_id=service['id'], validation_id=validation['id'], feedback_id=feedback['id'],
                        retest_job_id=retest['id'], improvement_id=comparison['id'], provider_revoked_grant_id=provider_grant['id'])
        draft_cfg = {**configurations['FOLLOWUP'], 'title': '出院后30天随访研究数据可用性申请',
            'owner': REQUESTER, 'purpose': '出院后随访研究立项准备', 'audience': '临床研究中心、全科医学科',
            'description': '申请补充出院日期与随访关联口径，先核对候选来源和字段覆盖；当前未进入执行与交付。'}
        draft_demand, _ = demand('DISCHARGE-FOLLOWUP', draft_cfg, confirm=False, service_ids=[draft['id']])
        draft = m.services.save({**draft, 'expected_version': draft['version'],
            'quality_commitment': '补充出院日期与回访关联字段完整率要求，待重新验证'}, PROVIDER)
        m.store.save('notice', 'NOTICE-FOLLOWUP-COVERAGE', {'name': '出院随访字段口径补充通知',
            'service_id': draft['id'], 'service_version': draft['version'], 'requires_confirmation': True,
            'reason': '供给方补充出院日期与回访关联规则，关联需求需重新核对字段覆盖后提交规格确认',
            'affected_demands': [draft_demand['id']], 'demo': True, 'demo_seed': SEED_ID}, 0, PROVIDER, '通知关联需求核对服务定义修订')
        for owner, suffix, name, capabilities in (
                (PLATFORM, 'SERVICE-CENTER', '数据服务中心受控计算节点', ['本地聚合计算', '交付检查', '凭证签名']),
                (PROVIDER, 'HOSPITAL-CENTER', '医院数据中心目录接入节点', ['SQLite目录读取', '字段结构核验', '数据质量剖析'])):
            e.store.save('facility', 'FAC-' + suffix, {'name': name, 'created_by': owner,
                'capabilities': capabilities, 'last_seen': now(), 'scope': 'local-registration-only',
                'remote_invocation': False, 'demo': True, 'demo_seed': SEED_ID}, 0, owner, '登记本地设施能力与心跳')
        for suffix, name, definition, denominator, window in (
                ('COMPLETENESS', '交付字段完整率', '实际结果非空单元格数除以结果单元格总数', '本次结果单元格总数', '同一方案版本与统计日期范围'),
                ('FOLLOWUP', '随访完成占比', '已完成随访服务次数除以已完成与待回访服务次数之和；按服务次数统计，不解释为去重人数', '确认范围内随访服务次数', '按自然月及院区汇总'),
                ('IMAGING', '影像当日签发占比', '当日签发检查数除以三个签发时段检查数之和；按检查记录统计', '确认范围内影像检查记录数', '按检查日期所属自然月汇总')):
            m.feedback.definition({'id': 'METRIC-' + suffix, 'name': name, 'definition': definition,
                'source': '版本化运行证据及交付聚合计数', 'denominator': denominator,
                'window': window, 'demo': True, 'demo_seed': SEED_ID}, PLATFORM)
        stages = {'preflight': '提交前', 'node': '节点执行', 'delivery': '交付前', 'read': '结果读取'}
        for decision in e.store.list('decision'):
            if decision['id'] not in before_e['decision']:
                _name(e.store, 'decision', decision, ('允许' if decision['allowed'] else '拒绝') + ' · ' + stages.get(decision['stage'], decision['stage']) + '授权核验')
        for pair in m.store.list('pair'):
            if pair['id'] not in before_m['pair']:
                _name(m.store, 'pair', pair, '业务定义与运行版本核对')
        records = {'management': {k: sorted(_ids(m.store, (k,))[k] - before_m[k]) for k in MANAGEMENT_KINDS},
                   'execution': {k: sorted(_ids(e.store, (k,))[k] - before_e[k]) for k in EXECUTION_KINDS}}
        return m.store.save('demo_seed', SEED_ID, {'name': '医疗业务数据服务案例', 'demo': True, 'seed_version': 2, 'ready': True,
            'created_at': now(), 'source_id': SOURCE_ID, 'source_file': SOURCE_FILE, 'scenarios': scenarios,
            'published_service_ready': service['lifecycle'] == 'published', 'records': records,
            'counts': {domain: {k: len(ids) for k, ids in kinds.items()} for domain, kinds in records.items()},
            'source_rows': {table: info['rows'] for table, info in snapshot['tables'].items()},
            'role_labels': {PLATFORM: '数据服务中心', PROVIDER: '医院数据中心', REQUESTER: '临床研究中心', 'demo-auditor': '数据合规岗'}},
            0, PLATFORM, '完成医疗业务案例初始化')
