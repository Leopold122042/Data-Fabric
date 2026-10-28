"""Local healthcare collaboration cases, using the existing domain operations.

Fixtures contain no patient records. A published service and its actual handler
are prerequisites; unavailable services leave initialization pending for retry.
"""
from datetime import datetime, timedelta, timezone

from management.work_orders import WorkOrders
from platform_core.storage import DomainError


SEED_ID = 'HEALTHCARE-WORK-ORDERS-V1'
SERVICE_ID = 'SVC-RESEARCH-CATALOG'
DEMAND_ID = 'REQ-RESEARCH-CATALOG-COLLAB'
PENDING_ID = 'REQ-STROKE-CATALOG-SCOPE'
PLAN_ID = 'PLAN-RESEARCH-CATALOG-COLLAB'
CONTRACT_ID = 'AGR-RESEARCH-CATALOG-COLLAB'
PLATFORM, PROVIDER, REQUESTER = 'demo-platform', 'demo-provider', 'demo-requester'


def seed_work_orders(runtime):
    """Initialize once, preserving existing edits and every historical record."""
    e, m = runtime.execution, runtime.management
    if e.store.environment != 'demo' or m.store.environment != 'demo':
        raise DomainError('协作业务案例只能写入本地开发环境', 403)
    with e.lock, m.store.atomic(), e.store.atomic():
        existing = m.store.get('demo_seed', SEED_ID)
        if existing:
            return existing
        service = m.store.get('service', SERVICE_ID)
        if not service or service.get('lifecycle') != 'published':
            return {'ready': False, 'status': 'waiting_service', 'service_id': SERVICE_ID,
                    'reason': '科研目录覆盖核验服务尚未发布，协作案例待服务核验通过后初始化'}
        for store, kind, oid in ((m.store, 'demand', DEMAND_ID), (m.store, 'demand', PENDING_ID),
                                 (e.store, 'plan', PLAN_ID), (e.store, 'contract', CONTRACT_ID)):
            if store.get(kind, oid):
                raise DomainError('协作案例编号已有记录且无初始化依据，未覆盖：' + oid, 409)
        now = datetime.now(timezone.utc)
        deadline = (now + timedelta(days=90)).isoformat()
        context = {'demo': True, 'demo_seed': SEED_ID}
        orders = WorkOrders(runtime)
        messages = []

        def discuss(demand_id, role, content):
            result = orders.message(demand_id, {'body': content, 'service_id': SERVICE_ID},
                                    {'id': 'demo-' + role, 'role': role})
            messages.append(result['id'])

        title = '代谢风险课题用数前目录覆盖核验'
        demand = m.intake.save({
            'id': DEMAND_ID, 'title': title, 'entry': 'service',
            'description': '临床研究中心申请核对医院信息、检验、影像、电子病历与监测来源的目录覆盖，'
                           '为代谢风险课题立项提供结构依据；本次不筛选队列或获取病例明细。',
            'service_ids': [SERVICE_ID], 'object_scope': '当前已接入的五类业务来源目录摘要',
            'time_start': (now - timedelta(days=2)).isoformat(), 'time_end': deadline,
            'purpose': '代谢风险课题立项前数据来源覆盖确认', 'quality': '完整',
            'deadline': deadline, 'delivery': '对象级', 'audience': '临床研究中心、医院数据中心',
            'authorization_evidence': '签发绑定用途与方案版本的授权，交付前复核有效期及参与方确认',
            'execution_constraints': {'scope': {'kind': 'metadata'}, 'output_mode': 'object',
                'circulation_modes': ['controlled_compute'], 'quality_min_complete': 1,
                'max_rows': 60, 'min_group_size': 5}, **context}, REQUESTER)
        discuss(demand['id'], 'requester', '本次申请仅核对课题所需数据来源的目录覆盖，输出来源名称与表数量；'
                '队列筛选、字段明细及患者纳入条件将在后续研究用数申请中另行确认。')
        discuss(demand['id'], 'provider', '已核对目录覆盖核验服务的输出口径：返回当前接入来源的结构摘要，'
                '执行有效期内按确认范围调用；不交付病例或检验结果。')
        spec = m.intake.confirm(demand['id'], {'expected_version': demand['version'],
            'confirmations': {'content': True, 'attachments': True, 'gaps': True,
                              'execution_constraints': True},
            'note': '已明确仅核对当前目录摘要；时间窗为执行有效期，不代表历史病例覆盖范围'}, PLATFORM)
        plan = e.plan({'id': PLAN_ID, 'name': title + '执行方案', 'spec_id': spec['id'],
            'spec_version': spec['version'], 'circulation_mode': 'controlled_compute',
            'nodes': [{'id': 'catalog-coverage', 'operation': 'service', 'service_id': SERVICE_ID}]}, REQUESTER)
        e.confirm(plan['id'], plan['version'], REQUESTER)
        contract = e.store.save('contract', CONTRACT_ID, {
            'name': '代谢风险课题目录核验使用约定', 'created_by': REQUESTER,
            'plan_id': plan['id'], 'plan_version': plan['version'], 'purpose': plan['purpose'],
            'parties': [PLATFORM, PROVIDER, REQUESTER], 'accepted': [], 'expires_at': deadline,
            'scope': 'local', 'obligations': '仅交付来源名称与表数量用于课题立项核查；'
                '不含病例、检验结果或个人标识，不代表已获批使用后续研究数据。', **context},
            0, REQUESTER, '登记目录核验三方使用约定')
        for party in contract['parties']:
            contract = e.update('contract', contract, {'accepted': contract['accepted'] + [party]}, party)
        expired = e.security.grant({'subject': REQUESTER, 'purpose': plan['purpose'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (now - timedelta(days=1)).isoformat(), 'contract_id': contract['id']}, PLATFORM)
        expired = e.update('grant', expired, {'name': '目录核验前期授权（已到期）', **context}, PLATFORM)
        try:
            e.submit(plan['id'], plan['version'], expired['id'], REQUESTER)
        except DomainError as exc:
            if '已到期' not in str(exc):
                raise
        else:
            raise DomainError('已到期授权未在提交阶段被拒绝')
        discuss(demand['id'], 'platform', '前期授权已到期，提交检查已拒绝请求且未启动任务；'
                '按已确认用途、方案版本与三方约定重新办理限期授权。')
        grant = e.security.grant({'subject': REQUESTER, 'purpose': plan['purpose'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'expires_at': (now + timedelta(days=30)).isoformat(), 'contract_id': contract['id']}, PLATFORM)
        grant = e.update('grant', grant, {'name': title + '用途授权', **context}, PLATFORM)
        job = e.submit(plan['id'], plan['version'], grant['id'], REQUESTER)
        job = e.update('job', job, {'name': title, **context}, REQUESTER)
        job = e.run(job)
        if job['state'] != 'success' or not job.get('delivery_id'):
            raise DomainError('目录核验协作申请未完成实际交付：' + str(job.get('error')))
        delivery = e.store.require('delivery', job['delivery_id'])
        e.update('delivery', delivery, {'name': title + '交付凭证', **context}, REQUESTER)
        feedback = m.feedback.evaluate({'run_id': job['id'],
            'ratings': {'usability': 4, 'explainability': 5, 'timeliness': 4, 'satisfaction': 4},
            'comment': '来源名称和表数量已用于课题立项的数据可用性清单；目录摘要符合约定。'
                       '后续队列筛选将另提范围与字段申请，本次结果不用于判断患者纳入条件。'}, REQUESTER)
        discuss(demand['id'], 'requester', '目录摘要已收到并核对，来源名称与表数量符合本次立项清单需要。'
                '已提交使用评价；后续研究明细用数将单独申请。')
        pending = m.intake.save({'id': PENDING_ID, 'title': '多中心卒中研究数据源覆盖范围确认',
            'entry': 'service', 'service_ids': [SERVICE_ID],
            'description': '拟联合中心院区与东院区开展卒中研究，先确认目录覆盖核验的范围与交付口径；'
                           '尚待填写完整范围、执行有效期和质量要求。',
            'purpose': '卒中研究立项前数据来源可用性确认', 'audience': '临床研究中心、医院数据中心',
            **context}, REQUESTER)
        discuss(pending['id'], 'provider', '请补充拟核对的来源范围、执行有效期与质量要求。目录核验返回当前来源摘要，'
                '无法据此直接判断各院区的卒中患者覆盖；如需院区或诊断筛选，请在后续用数需求中明确。')
        discuss(pending['id'], 'requester', '已收到范围补充要求，正在与课题组核对来源清单。本申请暂不进入执行，'
                '补齐材料后再提交规格确认。')
        result = {'ready': True, 'service_id': SERVICE_ID, 'demand_id': demand['id'],
            'pending_demand_id': pending['id'], 'spec_id': spec['id'], 'plan_id': plan['id'],
            'plan_version': plan['version'], 'contract_id': contract['id'], 'expired_grant_id': expired['id'],
            'grant_id': grant['id'], 'job_id': job['id'], 'delivery_id': delivery['id'],
            'feedback_id': feedback['id'], 'message_ids': messages, **context}
        return m.store.save('demo_seed', SEED_ID, result, 0, PLATFORM, '完成科研目录服务协作申请案例')
