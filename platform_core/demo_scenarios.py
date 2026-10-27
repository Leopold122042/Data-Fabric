"""Idempotent fictional walkthroughs, built through the actual local domain gates.

The source is separate from medical data. Successful jobs and signed deliveries
come from the query executor; rejection and recovery are exercised, not invented.
"""
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
import sqlite3

import medsim
from management.catalog.sources import SQLiteSource
from management.reconciliation.service import Reconciliation
from platform_core.storage import DomainError, now


SEED_ID = 'DEMO-SCENARIOS-V1'
REQUESTER_RETEST_ID = 'DEMO-SCENARIOS-V1-REQUESTER-RETEST'
SOURCE_ID = 'DEMO-SOURCE-TRAINING'
SOURCE_FILE = 'demo_scenarios_v1/fictional_records.sqlite'
APPLICATION_ID = 1296450617
PLATFORM = 'demo-platform'
REQUESTER = 'demo-requester'
PROVIDER = 'demo-provider'
MANAGEMENT_KINDS = ('source', 'object', 'scan', 'concept', 'mapping', 'mapping_rule',
                    'relation', 'identifier', 'service', 'validation', 'notice',
                    'demand', 'spec', 'pair', 'case', 'feedback', 'improvement', 'metric_definition')
EXECUTION_KINDS = ('view', 'plan', 'workflow', 'plan_state', 'job', 'grant', 'decision',
                   'run', 'delivery', 'trigger', 'contract', 'facility', 'command')


def _source(root):
    path = (root / SOURCE_FILE).resolve()
    if not path.is_relative_to(root):
        raise DomainError('模拟源路径超出数据目录', 403)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
            if con.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
                raise DomainError('模拟源文件名已被其他数据占用，未覆盖', 409)
        return path
    # Exclusive creation prevents overwriting an unrelated file in a concurrent run.
    with path.open('xb'):
        pass
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('PRAGMA application_id=' + str(APPLICATION_ID))
        con.execute('PRAGMA user_version=1')
        con.execute('CREATE TABLE training_observations (cohort TEXT NOT NULL, observed_at TEXT NOT NULL, region TEXT, volume INTEGER NOT NULL)')
        con.executemany('INSERT INTO training_observations VALUES(?,?,?,?)',
                        [(cohort, '2026-01-15', region, 1)
                         for cohort in ('A', 'B', 'C')
                         for region in ('东区（模拟）', '西区（模拟）') for _ in range(6)])
    return path


def _new_ids(store, kinds):
    return {kind: {r['id'] for r in store.list(kind)} for kind in kinds}


def _decorate(store, kind, record, name):
    """Add display provenance only to newly created, non-plan records."""
    return store.save(kind, record['id'], {**record, 'name': name,
        'demo': True, 'demo_seed': SEED_ID}, record['version'], PLATFORM, '模拟案例显示说明；原运行事实与凭证不变')


class _EvidenceScope:
    def __init__(self, evidence, plan_ids):
        self.evidence, self.plan_ids = evidence, set(plan_ids)

    def list(self):
        return [r for r in self.evidence.list() if r.get('object_id') in self.plan_ids]

    def get(self, oid):
        record = self.evidence.get(oid)
        if record.get('object_id') not in self.plan_ids:
            raise DomainError('模拟复核仅允许引用本批案例')
        return record


def _seed_base(runtime):
    """Create one fictional dataset, return a persistent walkthrough manifest.

    Call after runtime.startup and before execution workers. A runtime without a
    metadata engine still receives query scenarios and honest failed validation,
    but no fabricated published service. Repeated calls never refresh existing
    dates, recreate results, or overwrite user edits.
    """
    e, m = runtime.execution, runtime.management
    if e.store.environment != 'demo' or m.store.environment != 'demo':
        raise DomainError('模拟案例只能写入demo环境', 403)
    with e.lock, m.store.atomic(), e.store.atomic():
        existing = m.store.get('demo_seed', SEED_ID)
        if existing:
            return existing
        if m.store.get('source', SOURCE_ID):
            raise DomainError('模拟源标识已存在但没有初始化记录，未覆盖', 409)
        before_m, before_e = _new_ids(m.store, MANAGEMENT_KINDS), _new_ids(e.store, EXECUTION_KINDS)
        root = Path(medsim.DATA_DIR).resolve()
        path = _source(root)
        m.catalog.sources.register({'source_id': SOURCE_ID, 'name': '工作流演练数据（全部虚构）',
                                    'connector': 'sqlite', 'path': SOURCE_FILE}, PLATFORM)
        adapter = SQLiteSource(path)
        snapshot = adapter.snapshot()
        snapshot['domain'] = '流程演示（虚构）'
        m.catalog.ingest(SOURCE_ID, snapshot)
        m.store.save('scan', SOURCE_ID, {'name': '模拟源结构扫描', 'status': 'success',
                     'tables': snapshot['tables'], 'demo': True}, 0, PLATFORM, '实际扫描独立虚构SQLite源')
        fields = {}
        table = None
        meanings = {'cohort': '演示任务范围，A/B/C均为虚构批次', 'observed_at': '虚构记录日期，供时间条件下推',
                    'region': '无现实机构对应的演示分组', 'volume': '虚构记录权重，固定为1'}
        for obj in m.catalog.objects(source_id=SOURCE_ID):
            obj = m.catalog.annotate(obj['id'], {'expected_version': obj['version'],
                'meaning': meanings.get(obj['name'], '仅用于演示流程的虚构记录表'), 'classification': '公开',
                'owner': '演示供给方', 'domain': '流程演示（虚构）',
                'constraints': {'fictional': True, 'purpose': '演示与测试，不代表真实业务统计'}}, PLATFORM)
            if obj['kind'] == 'field':
                obj = m.catalog.profile(obj['id'], PLATFORM)
                fields[obj['name']] = obj['id']
            else:
                table = obj['id']
        m.catalog.identifier({'local_id': table, 'global_id': 'urn:medfabric:local-demo:training-observations'}, PLATFORM)
        concept = m.semantic.concept({'id': 'DEMO-CONCEPT-REGION', 'name': '演示区域',
            'meaning': '用于演示聚合与字段血缘的虚构分组', 'domain': '流程演示（虚构）', 'data_type': 'TEXT'}, PLATFORM)
        m.semantic.mapping({'id': 'DEMO-MAPPING-REGION', 'concept_id': concept['id'],
            'fields': [fields['region']], 'mapping_type': 'direct', 'status': 'confirmed',
            'confirmation_note': '模拟受理岗核对独立虚构源字段、类型及值域'}, PLATFORM)

        views = {}
        for owner, suffix in ((PLATFORM, 'PLATFORM'), (REQUESTER, 'RESEARCH-B'), (PROVIDER, 'PROVIDER')):
            views[owner] = e.queries.define({'id': 'DEMO-VIEW-' + suffix, 'name': '区域汇总视图（模拟）· ' + suffix,
                'fields': [fields[k] for k in ('cohort', 'observed_at', 'region')],
                'group_by': [fields['region']], 'output_mode': 'aggregate'}, owner)

        # No row data is required for the real metadata service validation.
        service_body = {'id': 'DEMO-SERVICE-CATALOG', 'name': '目录结构核验服务（模拟需求）',
            'service_type': 'verification', 'mode': 'regular', 'visibility': 'specified',
            'allowed_subjects': [PLATFORM, REQUESTER], 'purpose': '目录结构核验与演示',
            'output': '来源名称和表数量；不交付业务明细', 'audience': '平台受理岗、研究组B（模拟）',
            'conditions': '必须绑定确认规格及有效本地演示授权', 'provider': PROVIDER,
            'source_scope': '本机内置来源的目录结构摘要；虚构训练表仅为目录引用示例，不在该handler统计中', 'update_period': '按需读取',
            'quality_commitment': '结构字段齐全，实际发布验证通过后上架', 'restrictions': '仅本机演示，不构成跨机构授权',
            'metering': '按次记录运行', 'handler_id': 'SVC-META-CATALOG', 'source_objects': [table],
            'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}, 'sample_input': {},
            'demo': True, 'demo_seed': SEED_ID}
        service = m.services.save(service_body, PROVIDER)
        val = m.services.validate(service['id'], PROVIDER)
        _decorate(m.store, 'validation', val, '目录服务发布前六项验证（模拟）')
        service = m.services.transition(service['id'], {'target': 'review', 'expected_version': service['version'],
            'reason': '模拟供给方提交完整服务说明'}, PLATFORM)
        if val['passed']:
            for target in ('trial', 'published'):
                service = m.services.transition(service['id'], {'target': target, 'expected_version': service['version'],
                    'validation_id': val['id'], 'reason': '模拟发布流程：实际目录handler验证通过'}, PLATFORM)
        pending = m.services.save({**service_body, 'id': 'DEMO-SERVICE-PENDING',
            'name': '目录摘要数组适配（模拟·待修正）', 'output_schema': {'type': 'array'}}, PROVIDER)
        failed_validation = m.services.validate(pending['id'], PROVIDER)
        _decorate(m.store, 'validation', failed_validation, '输出模式不匹配验证（模拟·拒绝发布）')
        draft = m.services.save({**service_body, 'id': 'DEMO-SERVICE-DRAFT', 'name': '区域统计服务设计（模拟草稿）',
            'description': '供给方草稿示例；执行能力当前由已确认逻辑视图提供'}, PROVIDER)

        deadline = datetime.now(timezone.utc) + timedelta(days=3650)
        expiry = deadline.isoformat()
        scenarios, plan_ids = [], []

        def demand(key, title, owner, cohort='A', delivery='controlled_compute', metadata=False, confirm=True, service_refs=None):
            rules = {'scope': {'kind': 'metadata'} if metadata else {'kind': 'rows', 'filters': [
                {'field': fields['cohort'], 'op': '=', 'value': cohort}]},
                'output_mode': 'object' if metadata else 'aggregate', 'circulation_modes': [delivery],
                'quality_min_complete': 1, 'max_rows': 20, 'min_group_size': 5}
            if not metadata:
                rules['time_field'] = fields['observed_at']
            d = m.intake.save({'id': 'DEMO-DEMAND-' + key, 'title': title, 'entry': 'service' if metadata else 'goal',
                'object_scope': '当前来源目录结构' if metadata else '独立虚构数据批次 ' + cohort,
                'time_start': '2020-01-01T00:00:00+00:00' if metadata else '2026-01-01',
                'time_end': (deadline + timedelta(days=1)).isoformat() if metadata else '2026-01-31',
                'purpose': '模拟流程演练', 'quality': '完整', 'deadline': expiry,
                'delivery': '对象级' if metadata else '汇总',
                'service_ids': service_refs if service_refs is not None else ([service['id']] if metadata else []),
                'description': '全部为模拟业务请求；研究组B映射本机demo-requester，不代表真实身份',
                'authorization_evidence': '本机演示授权需在方案确认后单独签发',
                'execution_constraints': rules, 'demo': True, 'demo_seed': SEED_ID}, owner)
            if not confirm:
                return d, None
            spec = m.intake.confirm(d['id'], {'expected_version': d['version'],
                'confirmations': {'content': True, 'attachments': True, 'gaps': True, 'execution_constraints': True},
                'note': '模拟受理岗核对范围、时间字段、完整率和交付规则；不代表真实外部授权'}, PLATFORM)
            return d, spec

        def plan(key, title, owner, cohort='A', delivery='controlled_compute', metadata=False):
            d, spec = demand(key, title, owner, cohort, delivery, metadata)
            node = {'id': 'directory', 'operation': 'service', 'service_id': service['id']} if metadata else {
                'id': 'region-summary', 'operation': 'view', 'view_id': views[owner]['id']}
            p = e.plan({'id': 'DEMO-PLAN-' + key, 'name': title, 'spec_id': spec['id'], 'spec_version': spec['version'],
                'nodes': [node], 'circulation_mode': delivery}, owner)
            e.confirm(p['id'], p['version'], owner)
            plan_ids.append(p['id'])
            contract = e.store.save('contract', 'DEMO-CONTRACT-' + key, {'name': title + ' · 合作约定',
                'created_by': owner, 'plan_id': p['id'], 'plan_version': p['version'], 'purpose': p['purpose'],
                'parties': [PLATFORM, PROVIDER, REQUESTER], 'accepted': [], 'expires_at': expiry,
                'scope': 'local-demo', 'obligations': '仅演示；按确认范围汇总；不得再分发；撤销后停止读取',
                'demo': True, 'demo_seed': SEED_ID}, 0, owner, '新建模拟合作约定')
            for party in contract['parties']:
                contract = e.update('contract', contract, {'accepted': contract['accepted'] + [party]}, party)
            g = e.security.grant({'subject': owner, 'purpose': p['purpose'], 'plan_id': p['id'],
                'plan_version': p['version'], 'expires_at': expiry, 'contract_id': contract['id']}, PLATFORM)
            g = _decorate(e.store, 'grant', g, title + ' · 有效授权')
            return d, spec, p, g, contract

        def run(p, g, label, retry_of=None):
            job = e.submit(p['id'], p['version'], g['id'], p['created_by'], retry_of=retry_of)
            job = _decorate(e.store, 'job', job, label)
            job = e.run(job)
            _decorate(e.store, 'run', e.evidence.get(job['id']), label + ' · 运行证据')
            if job['delivery_id']:
                _decorate(e.store, 'delivery', e.store.require('delivery', job['delivery_id']), label + ' · 交付凭证')
            return job

        for key, title, owner, cohort, mode in (
            ('PLATFORM', '区域汇总受控查看（模拟·平台方）', PLATFORM, 'A', 'controlled_compute'),
            ('RESEARCH-B', '研究组B授权下载（模拟）', REQUESTER, 'B', 'download'),
            ('API', '研究组B结果API引用（模拟）', REQUESTER, 'A', 'api')):
            d, spec, p, g, contract = plan(key, title, owner, cohort, mode)
            job = run(p, g, title)
            if job['state'] != 'success':
                raise DomainError('模拟成功场景执行未通过：' + str(job['error']))
            scenarios.append({'id': key, 'title': title, 'status': 'success', 'subject': owner,
                'subject_label': '研究组B（模拟）' if owner == REQUESTER else '平台受理岗（模拟）',
                'demand_id': d['id'], 'spec_id': spec['id'], 'plan_id': p['id'], 'grant_id': g['id'],
                'contract_id': contract['id'], 'job_id': job['id'], 'delivery_id': job['delivery_id'],
                'path': '/static/execution/index.html?tab=job', 'demo': True})
            feedback = m.feedback.evaluate({'run_id': job['id'], 'ratings': {'usability': 5, 'explainability': 4,
                'timeliness': 5, 'satisfaction': 4}, 'comment': '模拟评价：范围与来源清晰，结果仅含虚构区域和记录数'}, owner)
            _decorate(m.store, 'feedback', feedback, title + ' · 使用评价')
            trigger = e.trigger({'plan_id': p['id'], 'plan_version': p['version'], 'grant_id': g['id'],
                'kind': 'interval', 'interval': 86400}, owner)
            e.update('trigger', trigger, {'enabled': False, 'name': title + ' · 手动启用调度', 'demo': True})
            if key == 'RESEARCH-B':
                expired = e.security.grant({'subject': owner, 'purpose': p['purpose'], 'plan_id': p['id'],
                    'plan_version': p['version'], 'expires_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}, PLATFORM)
                expired = _decorate(e.store, 'grant', expired, '研究组B到期拒绝（模拟）')
                for grant, actor, reason in ((expired, owner, '授权已到期'), (g, PROVIDER, '主体或用途')):
                    try:
                        e.submit(p['id'], p['version'], grant['id'], actor)
                    except DomainError as exc:
                        if reason not in str(exc):
                            raise
                    else:
                        raise DomainError('模拟越权/过期请求未被拒绝，停止初始化')
                scenarios.append({'id': 'DENIED', 'title': '研究组B到期与主体不匹配拒绝（模拟）',
                    'status': 'denied', 'subject': owner, 'subject_label': '研究组B（模拟）',
                    'plan_id': p['id'], 'expired_grant_id': expired['id'], 'backend_calls': 0,
                    'path': '/static/execution/index.html?tab=decision', 'demo': True})

        d, spec, recovery, grant, contract = plan('RECOVERY', '完整率异常—暂停—复测闭环（模拟）', PLATFORM, 'C')
        baseline = [run(recovery, grant, '质量基线第' + str(i + 1) + '次（模拟）') for i in range(3)]
        if any(j['state'] != 'success' for j in baseline):
            raise DomainError('模拟复测基线未实际成功')
        # Deliberately change only the seeder-owned cohort, then restore it even
        # if generation fails. No live medical source is involved.
        try:
            with closing(sqlite3.connect(path)) as con, con:
                con.execute("UPDATE training_observations SET region=NULL WHERE cohort='C' AND region='西区（模拟）'")
            failed = run(recovery, grant, '缺失分组触发交付质量拒绝（模拟）')
            if failed['state'] != 'failed' or failed['delivery_id'] or 'quality' not in (failed['error'] or ''):
                raise DomainError('模拟质量异常未被真实交付检查拒绝')
        finally:
            with closing(sqlite3.connect(path)) as con, con:
                con.execute("UPDATE training_observations SET region='西区（模拟）' WHERE cohort='C' AND region IS NULL")
        scoped = Reconciliation(m.store, _EvidenceScope(e.evidence, plan_ids), m.catalog)
        scoped.controller = e.control
        scoped.run()
        cid = 'CASE-' + recovery['id'] + '-' + str(recovery['version']) + '-execution'
        case = m.store.require('case', cid)
        case = _decorate(m.store, 'case', case, '完整率异常暂停复核（模拟）')
        case = scoped.action(case['id'], {'expected_version': case['version'], 'action': 'pause_review',
            'note': '模拟复核：实际质量检查已拦截结果，暂停后续执行并恢复虚构字段值'}, PLATFORM)
        e.confirm(recovery['id'], recovery['version'], PLATFORM)
        retest = run(recovery, grant, '恢复字段后的同范围复测（模拟）', failed['id'])
        if retest['state'] != 'success':
            raise DomainError('模拟修复后未实际通过复测')
        scoped.run()
        case = m.store.require('case', cid)
        # The measured latency can still be anomalous on a busy machine; keep an
        # honest pending review instead of manufacturing a successful closure.
        try:
            case = scoped.action(case['id'], {'expected_version': case['version'], 'action': 'close',
                'evidence_id': retest['id'], 'note': '模拟复测：相同方案和范围实际执行成功，完整率恢复且交付检查通过'}, PLATFORM)
        except DomainError:
            case = scoped.action(case['id'], {'expected_version': case['version'], 'action': 'observe',
                'note': '模拟数据已恢复并实际交付；当前实测时延仍需继续观察，不伪造关闭'}, PLATFORM)
        comparison = m.feedback.compare({'before': [failed['id']], 'after': [retest['id']],
            'change_note': '模拟修复：恢复独立训练源C批次缺失的区域值，确认后重试同一版本方案'}, PLATFORM)
        comparison = _decorate(m.store, 'improvement', comparison, '完整率修复前后复测（模拟）')
        scenarios.append({'id': 'RECOVERY', 'title': '完整率异常—暂停—修复—复测（模拟）', 'status': case['status'],
            'subject': PLATFORM, 'subject_label': '平台受理岗（模拟）', 'demand_id': d['id'], 'spec_id': spec['id'],
            'plan_id': recovery['id'], 'grant_id': grant['id'], 'baseline_job_ids': [j['id'] for j in baseline],
            'failed_job_id': failed['id'], 'retest_job_id': retest['id'], 'delivery_id': retest['delivery_id'],
            'case_id': case['id'], 'improvement_id': comparison['id'],
            'path': '/static/management/reconciliation.html', 'demo': True})

        if service['lifecycle'] == 'published':
            d, spec, p, g, contract = plan('CATALOG', '已发布目录服务调用（模拟需求）', PLATFORM, metadata=True)
            job = run(p, g, '目录服务实际调用（模拟需求）')
            if job['state'] != 'success':
                raise DomainError('已验证目录服务实际执行失败：' + str(job['error']))
            feedback = m.feedback.evaluate({'run_id': job['id'], 'ratings': {'usability': 5, 'explainability': 5,
                'timeliness': 4, 'satisfaction': 4}, 'comment': '模拟平台评价：目录摘要与服务声明一致，可追溯到供给方服务版本'}, PLATFORM)
            _decorate(m.store, 'feedback', feedback, '目录核验服务使用评价（模拟）')
            service_retest = run(p, g, '目录服务同配置回归复测（模拟）')
            if service_retest['state'] != 'success':
                raise DomainError('目录服务回归复测未实际通过')
            service_comparison = m.feedback.compare({'before': [job['id']], 'after': [service_retest['id']],
                'change_note': '模拟回归：保持同一目录配置再次调用，核验结构稳定；没有业务数据修订，不声称成功率提升'}, PLATFORM)
            service_comparison = _decorate(m.store, 'improvement', service_comparison, '目录服务稳定性复测（模拟）')
            provider_grant = e.security.grant({'subject': PROVIDER, 'purpose': p['purpose'], 'plan_id': p['id'],
                'plan_version': p['version'], 'expires_at': expiry, 'contract_id': contract['id']}, PLATFORM)
            provider_grant = _decorate(e.store, 'grant', provider_grant, '供给方临时核验授权（模拟·已撤销）')
            provider_grant = e.security.revoke(provider_grant['id'], PLATFORM)
            try:
                e.security.check(provider_grant['id'], p, PROVIDER, p['purpose'], 'preflight')
            except DomainError as exc:
                if '已撤销' not in str(exc):
                    raise
            else:
                raise DomainError('模拟已撤销授权未被拒绝')
            scenarios.append({'id': 'CATALOG', 'title': '供给方发布—需求确认—目录服务交付（模拟）',
                'status': 'success', 'subject': PLATFORM, 'subject_label': '平台受理岗（模拟）',
                'service_id': service['id'], 'validation_id': val['id'], 'demand_id': d['id'], 'spec_id': spec['id'],
                'plan_id': p['id'], 'grant_id': g['id'], 'job_id': job['id'], 'delivery_id': job['delivery_id'],
                'feedback_id': feedback['id'], 'retest_job_id': service_retest['id'], 'improvement_id': service_comparison['id'],
                'provider_revoked_grant_id': provider_grant['id'],
                'path': '/static/management/services.html', 'demo': True})
        draft_demand, _ = demand('DRAFT', '待受理的新批次统计需求（模拟草稿）', REQUESTER,
            confirm=False, service_refs=[draft['id']])
        draft = m.services.save({**draft, 'expected_version': draft['version'],
            'quality_commitment': '补充完整率规则，待实际验证'}, PROVIDER)
        m.store.save('notice', 'DEMO-NOTICE-SERVICE', {'name': '服务草稿说明修订（模拟）',
            'service_id': draft['id'], 'service_version': draft['version'], 'reason': '模拟供给方补充质量说明，尚未发布；关联需求待受理',
            'requires_confirmation': True, 'affected_demands': [draft_demand['id']], 'demo': True}, 0, PROVIDER, '模拟服务定义变更通知')
        for owner, suffix, label in ((PLATFORM, 'LOCAL', '平台本地演示执行器'), (PROVIDER, 'SOURCE', '供给方虚构训练源设施')):
            e.store.save('facility', 'DEMO-FACILITY-' + suffix, {'name': label + '（模拟）', 'created_by': owner,
                'capabilities': ['本地SQLite只读查询', '结构摘要', '受控结果交付'], 'last_seen': now(),
                'scope': 'local-registration-only', 'remote_invocation': False, 'demo': True}, 0, owner, '登记模拟设施，不声称远程调用')
        m.feedback.definition({'id': 'DEMO-METRIC-COMPLETENESS', 'name': '交付完整率（模拟演示口径）',
            'definition': '实际交付结果中非空单元格数 / 单元格总数；空结果不伪造样本量',
            'source': '交付前六项检查与版本化运行证据', 'denominator': '本次结果单元格总数',
            'window': '同一方案版本、相同虚构批次；失败与复测分别展示', 'demo': True}, PLATFORM)
        for decision in e.store.list('decision'):
            if decision['id'] not in before_e['decision']:
                _decorate(e.store, 'decision', decision, ('允许' if decision['allowed'] else '拒绝') + ' · ' + decision['stage'] + '（模拟）')
        for pair in m.store.list('pair'):
            if pair['id'] not in before_m['pair']:
                _decorate(m.store, 'pair', pair, '设计与运行版本配对（模拟）')
        records = {'management': {k: sorted(_new_ids(m.store, (k,))[k] - before_m[k]) for k in MANAGEMENT_KINDS},
                   'execution': {k: sorted(_new_ids(e.store, (k,))[k] - before_e[k]) for k in EXECUTION_KINDS}}
        counts = {domain: {k: len(v) for k, v in kinds.items()} for domain, kinds in records.items()}
        return m.store.save('demo_seed', SEED_ID, {'name': '需求—执行—交付—治理演示案例', 'demo': True,
            'seed_version': 1, 'created_at': now(), 'source_id': SOURCE_ID, 'source_file': SOURCE_FILE,
            'notice': '业务请求、参与方和训练数据均为模拟；计算、拒绝、复测与签名检查由本地真实代码执行。',
            'published_service_ready': service['lifecycle'] == 'published', 'scenarios': scenarios,
            'counts': counts, 'records': records, 'role_labels': {PLATFORM: '平台受理岗（模拟）',
                PROVIDER: '演示供给方', REQUESTER: '研究组B（模拟）', 'demo-auditor': '审计方（模拟）'}},
            0, PLATFORM, '完成一次模拟案例初始化；后续启动不覆盖、不续期、不重复生成')


def _migrate_requester_retest(runtime, manifest):
    """Append one real requester regression without editing the V1 dataset.

    This optional addition owns its transaction after the base seed commits.
    Modified, paused or revoked examples are skipped permanently; startup never
    repairs, renews or overwrites them merely to populate a demonstration page.
    """
    e, m = runtime.execution, runtime.management
    with e.lock:
        existing = m.store.get('demo_seed_migration', REQUESTER_RETEST_ID)
        if existing:
            return existing
        try:
            with m.store.atomic(), e.store.atomic():
                if not manifest.get('demo') or manifest.get('id') != SEED_ID:
                    raise DomainError('缺少可核验的V1模拟案例清单')
                scenario = next((s for s in manifest.get('scenarios', []) if s.get('id') == 'RESEARCH-B'), None)
                if not scenario or scenario.get('subject') != REQUESTER or scenario.get('plan_id') != 'DEMO-PLAN-RESEARCH-B':
                    raise DomainError('原需求方模拟案例不存在或归属已改变')
                plan = e.store.require('plan', scenario['plan_id'])
                grant = e.store.require('grant', scenario['grant_id'])
                demand = m.store.require('demand', scenario['demand_id'])
                baseline = e.evidence.get(scenario['job_id'])
                original_delivery = e.store.require('delivery', scenario['delivery_id'])
                spec = m.store.require('spec', scenario['spec_id'])
                state = e.store.require('plan_state', plan['id'])
                contract = e.store.require('contract', scenario['contract_id'])
                # V1 wrote these fixed versions. Any subsequent editing is a
                # reason to leave this existing walkthrough entirely alone.
                expected = ((plan, 1), (grant, 2), (demand, 2), (baseline, 2),
                            (original_delivery, 2), (spec, 1), (state, 2), (contract, 4))
                if any(record['version'] != version for record, version in expected):
                    raise DomainError('本次复测所需的模拟记录已被修改，保留用户当前状态')
                if (plan.get('created_by') != REQUESTER or demand.get('created_by') != REQUESTER
                        or grant.get('subject') != REQUESTER or grant.get('demo_seed') != SEED_ID
                        or demand.get('demo_seed') != SEED_ID or baseline.get('demo_seed') != SEED_ID
                        or original_delivery.get('demo_seed') != SEED_ID
                        or baseline.get('status') != 'success' or baseline.get('actor') != REQUESTER
                        or baseline.get('plan_id') != plan['id'] or plan.get('spec_id') != spec['id']
                        or spec.get('demand_id') != demand['id'] or state.get('status') != 'confirmed'
                        or original_delivery.get('state') == 'destroyed'):
                    raise DomainError('原需求方模拟案例的证据、归属或确认状态不再匹配')
                for view_id, version in plan.get('view_versions', {}).items():
                    if e.store.require('view', view_id)['version'] != version:
                        raise DomainError('原需求方模拟视图已有更新，不自动重新执行')
                before_decisions = {r['id'] for r in e.store.list('decision')}
                job = e.submit(plan['id'], plan['version'], grant['id'], REQUESTER)
                job = _decorate(e.store, 'job', job, '研究组B同配置回归复测（模拟）')
                job = e.run(job)
                if job['state'] != 'success' or not job.get('delivery_id'):
                    raise DomainError('需求方模拟复测未通过：' + str(job.get('error')))
                _decorate(e.store, 'run', e.evidence.get(job['id']), '研究组B同配置复测证据（模拟）')
                _decorate(e.store, 'delivery', e.store.require('delivery', job['delivery_id']), '研究组B复测交付凭证（模拟）')
                comparison = m.feedback.compare({'before': [baseline['id']], 'after': [job['id']],
                    'change_note': '模拟回归：需求方在原规格、方案版本和有效授权下再次运行，核验同配置结果稳定；没有业务数据修订，不声称成功率提升'}, PLATFORM)
                comparison = _decorate(m.store, 'improvement', comparison, '研究组B同范围稳定性复测（模拟）')
                decisions = []
                for decision in e.store.list('decision'):
                    if decision['id'] not in before_decisions:
                        _decorate(e.store, 'decision', decision, '允许 · 需求方复测 ' + decision['stage'] + '（模拟）')
                        decisions.append(decision['id'])
                return m.store.save('demo_seed_migration', REQUESTER_RETEST_ID, {
                    'name': '研究组B同范围复测补充（模拟）', 'demo': True, 'demo_seed': SEED_ID,
                    'status': 'completed', 'subject': REQUESTER, 'plan_id': plan['id'],
                    'baseline_job_id': baseline['id'], 'job_id': job['id'], 'delivery_id': job['delivery_id'],
                    'improvement_id': comparison['id'], 'decision_ids': decisions,
                    'counts': {'management': {'improvement': 1}, 'execution': {
                        'job': 1, 'run': 1, 'delivery': 1, 'decision': len(decisions)}},
                    'path': '/static/management/feedback.html'}, 0, PLATFORM,
                    '一次性追加真实复测；不改原清单、方案、授权及结果')
        except Exception as exc:
            # Both stores have rolled back before the optional skip marker is
            # saved. Preserve all existing data and avoid repeated startup work.
            return m.store.save('demo_seed_migration', REQUESTER_RETEST_ID, {
                'name': '研究组B同范围复测补充（模拟）', 'demo': True, 'demo_seed': SEED_ID,
                'status': 'skipped', 'reason': str(exc), 'counts': {}}, 0, PLATFORM,
                '保留当前用户状态，跳过一次性模拟复测补充')


def seed_demo(runtime):
    """Seed once, then apply independent optional demo additions exactly once."""
    manifest = _seed_base(runtime)
    _migrate_requester_retest(runtime, manifest)
    return manifest
