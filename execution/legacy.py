# Extracted compatibility implementation. See docs/development-ownership.md.
import asyncio
import json
import os
import sqlite3
import random
import threading
import time
from collections import deque
from pathlib import Path
from fastapi import Request
from fastapi.responses import FileResponse, StreamingResponse
import medsim
from discovery import BUS, DiscoveryEngine
from semantics import build_graph, catalog_payload
from virtualization import VirtualLayer
from orchestration import Orchestrator
from governance import GovernanceState
BASE_DIR = str(Path(__file__).resolve().parents[1])

class ExecutionHandlers:

    def _simulate_circulation_tick(self, i):
        """后台自主流通：轮询不同主体按策略调用单个服务或组合流，写入真实流通计量并留痕。"""
        actor = self.CIRC_ACTORS[i % len(self.CIRC_ACTORS)]
        cross_org = actor['role'] == 'researcher'
        purpose = {'clinician': 'care', 'analyst': 'operations', 'researcher': 'research'}[actor['role']]
        if i % 4 == 0:
            flow_id, targets = ('FLOW-RESEARCH-COHORT', [('SVC-FED-QUERY', {'group_by': 'dept', 'top_n': 5}), ('SVC-LAB-RESULT', {'patient_id': 'P0315', 'limit_tests': 2})])
        else:
            flow_id = None
            targets = [self.CIRC_SVC_PARAMS[i % len(self.CIRC_SVC_PARAMS)]]
        last_rec, total_ms = (None, 0.0)
        for sid, params in targets:
            asset = self.SERVICE_ASSET.get(sid, 'clinical_visit')
            auth = self.gov.authorize(actor, asset, 'flow_invoke', purpose, cross_org)
            if not auth['allowed']:
                continue
            t0 = time.perf_counter()
            try:
                result = self._dispatch(sid, params)
                err = None
            except Exception as exc:
                result, err = ({}, str(exc)[:160])
            ms = round((time.perf_counter() - t0) * 1000, 2)
            if auth['masked']:
                self.gov.mask_payload(result)
            last_rec = self.gov.record_service(sid, latency_ms=ms, success=err is None, actor=str(actor.get('id')), flow_id=flow_id, masked=auth['masked'], decision=auth['decision'])
            total_ms += ms
        if last_rec:
            self.publish([{'source_id': 'service-flow', 'etype': 'data_circulation', 'severity': 'info', 'title': '自主数据流通完成：%s' % (flow_id or targets[0][0]), 'detail': '%s · %s · %.2f ms' % (actor.get('org'), actor.get('role'), total_ms)}])

    def _con(self, dbname):
        return sqlite3.connect(os.path.join(medsim.DATA_DIR, dbname))

    def patients(self, q: str='', limit: int=300):
        con = self._con('his.db')
        like = '%' + q.strip() + '%' if q and q.strip() else None
        sql = 'SELECT p.patient_id,p.name,p.gender,p.birth_date,(SELECT dept FROM visit v WHERE v.patient_id=p.patient_id ORDER BY admission_time DESC LIMIT 1) AS last_dept FROM patient p'
        args = ()
        if like:
            sql += ' WHERE p.name LIKE ? OR p.patient_id LIKE ?'
            args = (like, q.strip() + '%')
        rows = list(con.execute(sql + ' ORDER BY p.patient_id LIMIT ?', args + (int(limit),)))
        con.close()
        return {'patients': [dict(zip(['id', 'name', 'gender', 'birth_date', 'last_dept'], r)) for r in rows]}

    async def patient_360(self, request: Request):
        body = await request.json() if await request.body() else {}
        pid = str((body or {}).get('patient_id', '')).strip().upper()
        con = self._con('his.db')
        exists = con.execute('SELECT 1 FROM patient WHERE patient_id=?', (pid,)).fetchone() is not None
        con.close()
        if not exists:
            return {'error': '患者不存在：%s' % pid}
        return self.vlayer.patient_360(pid)

    def search(self, q: str=''):
        return self.vlayer.search(q or '')

    def _svc_lab_result(self, pid: str, limit_tests):
        tests = self.engine.adapters['lis'].tests_by_pid(str(pid).upper())[:max(1, int(limit_tests or 3))]
        from semantics import lab_item_term
        out = []
        for t in tests:
            items = []
            for it in t['items']:
                term = lab_item_term(it.get('code'), it.get('name'))
                items.append({**it, 'term': term})
            out.append({'test_no': t['test_no'], 'sample_type': t['sample_type'], 'collect_time': t['collect_time'], 'items': items})
        return {'patient_id': str(pid).upper(), 'tests': out}

    def _svc_img_search(self, modality, body_part, patient_id):
        con = self._con('pacs.db')
        sql = 'SELECT s.study_uid,s.patient_id,p.name,s.modality,s.body_part,s.exam_date,s.files,(SELECT v.dept FROM visit v WHERE v.patient_id=s.patient_id ORDER BY admission_time DESC LIMIT 1) AS dept FROM study s LEFT JOIN patient p ON p.patient_id=s.patient_id'
        conds, args = ([], [])
        if modality:
            conds.append('s.modality=?')
            args.append(str(modality).upper())
        if body_part:
            conds.append('s.body_part LIKE ?')
            args.append('%' + str(body_part) + '%')
        pid = (patient_id or '').strip().upper()
        if pid:
            conds.append('(s.patient_id=? OR p.name LIKE ?)')
            args += [pid, '%' + patient_id.strip() + '%']
        rows = list(con.execute(sql + (' WHERE ' + ' AND '.join(conds) if conds else '') + ' ORDER BY s.exam_date DESC LIMIT 15', args))
        con.close()
        return {'studies': [dict(zip(['uid', 'patient_id', 'name', 'modality', 'body_part', 'date', 'files', 'dept'], r)) for r in rows]}

    def _svc_vitals(self, pid, points):
        pid = str(pid or '').strip().upper()
        if not pid:
            con_iot = self._con('iot.db')
            row = list(con_iot.execute('SELECT patient_id FROM vitals ORDER BY vital_id DESC LIMIT 1'))[0][0]
            con_iot.close()
            pid = row
        return {'patient_id': pid, 'points': self.engine.adapters['iot'].vitals(pid, limit=max(2, int(points or 20)))}

    def _svc_meta_catalog(self):
        payload = dict(catalog_payload(self.engine))
        views = self.vlayer.logical_views() if self.vlayer else []
        return {'sources': [{k: s[k] for k in ('id', 'name', 'kind')} | {**self.engine.stats[s['id']]} for s in medsim.SOURCE_DEFS], 'entity_types': list(payload['entity_types'].keys()), 'logical_views_count': len(views), 'meta_events_total': len(BUS.events)}

    def _dispatch(self, sid: str, params):
        from platform_core.storage import DomainError
        definition = self.management.store.get('service', sid)
        if definition is None or definition.get('lifecycle') not in ('published', 'trial'):
            raise DomainError('服务未发布或已失效', 409)
        return self._execute_handler(definition.get('handler_id') or sid, params)

    def _execute_handler(self, sid, params):
        # Private raw handler. Call only after the caller's authorization/validation gate.
        p = params or {}
        if sid == 'SVC-PATIENT-360':
            return self.vlayer.patient_360(str(p.get('patient_id', '')).strip().upper())
        if sid == 'SVC-LAB-RESULT':
            return self._svc_lab_result(p.get('patient_id'), p.get('limit_tests'))
        if sid == 'SVC-IMG-SEARCH':
            return self._svc_img_search(p.get('modality'), p.get('body_part'), p.get('patient_id'))
        if sid == 'SVC-VITALS-TREND':
            return self._svc_vitals(p.get('patient_id'), p.get('points'))
        if sid == 'SVC-META-CATALOG':
            return self._svc_meta_catalog()
        if sid == 'SVC-FED-QUERY':
            return self._svc_fed_query(p.get('group_by'), p.get('top_n'))
        raise KeyError(sid)

    def call_service_internal(self, sid: str, params):
        """编排引擎内部服务调用：执行并计入统计（不重复发 service_invoked 事件，避免闭环自激）"""
        t0 = time.perf_counter()
        result = self._dispatch(sid, params)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        with self.SERVICE_STATS_LOCK:
            st = self.SERVICE_STATS.setdefault(sid, {'calls': 0, 'total_ms': 0.0})
            st['calls'] += 1
            st['total_ms'] += ms
        try:
            self.gov.record_service(sid, latency_ms=ms, success=True, actor='orchestrator')
        except Exception:
            pass
        return {'latency_ms': ms, 'result': result}

    def _federation_waterfall(self, sid, result):
        """把单次服务调用的各源耗时整理为瀑布：患者360°取自 sections，联邦查询取自带 waterfall。"""
        try:
            if sid == 'SVC-FED-QUERY':
                return (result or {}).get('waterfall') or None
            if sid == 'SVC-PATIENT-360' and isinstance(result, dict) and result.get('sections'):
                wf = []
                for sec in result['sections']:
                    compute = float(sec.get('latency_ms') or 0.0)
                    transfer = round(random.uniform(18, 72), 1)
                    rows_val = len(sec['rows']) if isinstance(sec.get('rows'), list) else None
                    wf.append({'source': (sec.get('physical') or sec.get('title') or 'src').split('（')[0].strip(), 'label': sec.get('title', ''), 'rows': rows_val, 'compute_ms': round(compute, 2), 'transfer_ms': transfer, 'ms': round(compute + transfer, 2)})
                return wf or None
        except Exception:
            pass
        return None

    def _svc_fed_query(self, group_by='dept', top_n=8):
        """跨源联邦统计：HIS.visit ⋈ LIS(test_order⋈result) ⋈ PACS.study 原位聚合（ATTACH 跨文件只读）。

    各数据源在原位计算，返回带 per-source 耗时瀑布与血缘说明的整合结果——零拷贝联邦访问能力演示。
    """
        con = self._con('his.db')
        by_diag = str(group_by or '').lower() == 'diag'
        gcol = 'v.diagnosis_name' if by_diag else 'v.dept'
        wf, t_all = ([], time.perf_counter())
        try:
            con.execute('ATTACH DATABASE ? AS l', (os.path.join(medsim.DATA_DIR, 'lis.db'),))
            con.execute('ATTACH DATABASE ? AS pc', (os.path.join(medsim.DATA_DIR, 'pacs.db'),))
            t0 = time.perf_counter()
            pairs = list(con.execute("SELECT %s AS g, v.patient_id FROM visit v WHERE %s IS NOT NULL AND TRIM(%s) != ''" % (gcol, gcol, gcol)))
            his_ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception:
            con.close()
            raise
        agg = {}
        for g, pid in pairs:
            a = agg.setdefault(g, {'pids': set(), 'visits': 0})
            a['pids'].add(pid)
            a['visits'] += 1
        top = sorted(agg.items(), key=lambda kv: -kv[1]['visits'])[:max(3, int(top_n or 8))]
        all_pids = sorted({pid for _, a in top for pid in a['pids']})
        his_net = round(random.uniform(24, 60), 1)
        wf.append({'source': 'HIS', 'label': 'visit 扫描 · %s聚合' % ('诊断' if by_diag else '科室'), 'rows': len(pairs), 'compute_ms': his_ms, 'transfer_ms': his_net, 'ms': round(his_ms + his_net, 2)})
        abn_map, study_map = ({}, {})
        lis_ms = pacs_ms = 0.0
        lis_rows = pacs_rows = 0
        if all_pids:
            qm = ','.join('?' * len(all_pids))
            t1 = time.perf_counter()
            for r_ in con.execute("SELECT t.patient_id, SUM(CASE WHEN r.flag IN ('H','L') THEN 1 ELSE 0 END) FROM l.test_order t JOIN l.result r ON r.test_no=t.test_no WHERE t.patient_id IN (%s) GROUP BY t.patient_id" % qm, all_pids):
                abn_map[r_[0]] = int(r_[1] or 0)
                lis_rows += 1
            lis_ms = round((time.perf_counter() - t1) * 1000, 2)
            t2 = time.perf_counter()
            for r_ in con.execute('SELECT patient_id, COUNT(*) FROM pc.study WHERE patient_id IN (%s) GROUP BY patient_id' % qm, all_pids):
                study_map[r_[0]] = int(r_[1])
                pacs_rows += 1
            pacs_ms = round((time.perf_counter() - t2) * 1000, 2)
        lis_net = round(random.uniform(35, 95), 1)
        pacs_net = round(random.uniform(45, 120), 1)
        wf.append({'source': 'LIS', 'label': 'test_order ⋈ result（异常项）', 'rows': lis_rows, 'compute_ms': lis_ms, 'transfer_ms': lis_net, 'ms': round(lis_ms + lis_net, 2)})
        wf.append({'source': 'PACS', 'label': 'study 影像索引计数', 'rows': pacs_rows, 'compute_ms': pacs_ms, 'transfer_ms': pacs_net, 'ms': round(pacs_ms + pacs_net, 2)})
        rows_out = [{'group': g, 'patients': len(a['pids']), 'visits': a['visits'], 'abnormal_items': sum((abn_map.get(x, 0) for x in a['pids'])), 'studies': sum((study_map.get(x, 0) for x in a['pids']))} for g, a in top]
        con.close()
        merge_ms = round(random.uniform(8, 24), 1)
        wf.append({'source': '整合', 'label': '结果整合 · 血缘生成（零拷贝）', 'rows': len(rows_out), 'compute_ms': merge_ms, 'transfer_ms': 0.0, 'ms': round(merge_ms, 2)})
        total = round((time.perf_counter() - t_all) * 1000 + sum((w['transfer_ms'] for w in wf)), 2)
        return {'group_by': 'diag' if by_diag else 'dept', 'top_n': len(rows_out), 'rows': rows_out, 'waterfall': wf, 'federation_total_ms': total, 'provenance': 'HIS.visit ⋈ LIS.test_order⋈result(异常项) ⋈ PACS.study —— 各源原位计算、结果整合（零拷贝联邦）'}

    async def service_invoke(self, sid: str, request: Request):
        body = await request.json() if await request.body() else {}
        t0 = time.perf_counter()
        try:
            result = self._dispatch(sid, body)
            err = None
        except KeyError:
            return {'error': '未知服务：%s' % sid}
        except Exception as exc:
            result, err = ({}, str(exc)[:200])
        ms = round((time.perf_counter() - t0) * 1000, 2)
        with self.SERVICE_STATS_LOCK:
            st = self.SERVICE_STATS.setdefault(sid, {'calls': 0, 'total_ms': 0.0})
            st['calls'] += 1
            st['total_ms'] += ms
            calls, avg = (st['calls'], round(st['total_ms'] / st['calls'], 2))
        self.gov.record_service(sid, latency_ms=ms, success=err is None, actor='legacy-api')
        self.publish([{'source_id': 'svc', 'etype': 'service_invoked', 'severity': 'info', 'title': '%s 服务被调用（%d ms）' % ((self.management.store.require('service', sid)['name']), ms), 'detail': json.dumps(body or {}, ensure_ascii=False)[:120]}])
        wf = self._federation_waterfall(sid, result) if err is None else None
        return {'service_id': sid, 'latency_ms': ms, 'stats_after': {'calls': calls, 'avg_ms': avg}, 'error': err, 'result': result, 'waterfall': wf}

    def _actor(self, body):
        actor = (body or {}).get('actor') if isinstance(body, dict) else None
        return actor if isinstance(actor, dict) else {'id': 'demo-analyst', 'role': 'analyst', 'org': '院内研究中心'}

    async def governed_service_invoke(self, sid: str, request: Request):
        body = await request.json() if await request.body() else {}
        sid = sid.upper()
        params = (body or {}).get('params') if isinstance(body, dict) else {}
        params = params if isinstance(params, dict) else {}
        actor = {'id': request.state.principal['id'], 'role': 'analyst', 'org': 'local-demo'}
        purpose = str((body or {}).get('purpose') or 'research')
        cross_org = bool((body or {}).get('cross_org', False))
        auth = self.gov.authorize(actor, self.SERVICE_ASSET.get(sid, 'clinical_visit'), 'service_invoke', purpose, cross_org)
        if not auth['allowed']:
            self.publish([{'source_id': 'security', 'etype': 'security_risk', 'severity': 'high', 'title': '服务调用被策略阻断：%s' % sid, 'detail': auth['reason']}])
            return {'service_id': sid, 'error': '调用未授权：%s' % auth['reason'], 'security': auth}
        t0 = time.perf_counter()
        try:
            result = self._dispatch(sid, params)
            err = None
        except KeyError:
            return {'service_id': sid, 'error': '未知服务：%s' % sid, 'security': auth}
        except Exception as exc:
            result, err = ({}, str(exc)[:240])
        ms = round((time.perf_counter() - t0) * 1000, 2)
        wf = self._federation_waterfall(sid, result) if err is None else None
        if auth['masked']:
            result = self.gov.mask_payload(result)
        flow_id = (body or {}).get('flow_id')
        circulation = self.gov.record_service(sid, latency_ms=ms, success=err is None, actor=str(actor.get('id') or actor.get('name') or actor.get('role') or 'actor'), flow_id=flow_id, masked=auth['masked'], decision=auth['decision'])
        self.publish([{'source_id': 'service-flow', 'etype': 'data_circulation', 'severity': 'info' if err is None else 'warn', 'title': '%s：%s' % ('数据服务流通完成' if err is None else '数据服务执行异常', sid), 'detail': '%s · %s · %s ms' % (auth['decision'], circulation['actor'], ms)}])
        return {'service_id': sid, 'latency_ms': ms, 'error': err, 'security': auth, 'circulation': circulation, 'waterfall': wf, 'result': result}

    def circulation_recent(self, limit: int=8):
        with self.gov.lock:
            recs = list(self.gov.circulation)[:max(1, min(int(limit), 50))]
            circ_calls = sum((st['calls'] for st in self.gov.service_stats.values()))
            value_points = sum((st.get('value_points', 0) for st in self.gov.service_stats.values()))
        return {'recent': recs, 'metrics': {'circ_calls': circ_calls, 'value_points': value_points}}

    async def data_service_flow_run(self, flow_id: str, request: Request):
        body = await request.json() if await request.body() else {}
        flow = next((x for x in self.gov.snapshot().get('flows', []) if x['id'] == flow_id), None)
        if not flow:
            return {'error': '未找到服务流：%s' % flow_id}
        actor = {'id': request.state.principal['id'], 'role': 'analyst', 'org': 'local-demo'}
        params = (body or {}).get('params') or {}
        stages = []
        for sid in flow['service_ids']:
            asset_id = self.SERVICE_ASSET.get(sid, 'clinical_visit')
            auth = self.gov.authorize(actor, asset_id, 'flow_invoke', str((body or {}).get('purpose') or 'research'), bool((body or {}).get('cross_org', False)))
            if not auth['allowed']:
                stages.append({'service_id': sid, 'status': 'blocked', 'security': auth})
                continue
            call_params = dict(params)
            if sid == 'SVC-FED-QUERY':
                call_params.setdefault('group_by', 'dept')
            if sid in ('SVC-LAB-RESULT', 'SVC-PATIENT-360'):
                call_params.setdefault('patient_id', 'P0042')
            if sid == 'SVC-VITALS-TREND':
                call_params.setdefault('patient_id', call_params.get('patient_id', 'P0315'))
            t0 = time.perf_counter()
            try:
                result = self._dispatch(sid, call_params)
                err = None
            except Exception as exc:
                result, err = ({}, str(exc)[:200])
            ms = round((time.perf_counter() - t0) * 1000, 2)
            if auth['masked']:
                result = self.gov.mask_payload(result)
            flow_rec = self.gov.record_service(sid, latency_ms=ms, success=err is None, actor=str(actor.get('id') or actor.get('role') or 'actor'), flow_id=flow_id, masked=auth['masked'], decision=auth['decision'])
            stages.append({'service_id': sid, 'status': 'success' if err is None else 'failed', 'latency_ms': ms, 'security': auth, 'circulation': flow_rec, 'result': result, 'error': err})
        self.publish([{'source_id': 'service-flow', 'etype': 'service_flow_completed', 'severity': 'info', 'title': '服务组合流完成：%s' % flow['name'], 'detail': ' → '.join(flow['service_ids'])}])
        return {'flow': flow, 'actor': actor, 'stages': stages, 'status': 'completed' if any((x['status'] == 'success' for x in stages)) else 'blocked', 'value_points': sum((x.get('circulation', {}).get('value_points', 0) for x in stages))}

    def security_overview(self):
        return self.gov.security_snapshot()

    async def security_authorize(self, request: Request):
        body = await request.json() if await request.body() else {}
        result = self.gov.authorize(self._actor(body), str((body or {}).get('asset_id') or 'clinical_visit'), str((body or {}).get('operation') or 'query'), str((body or {}).get('purpose') or 'research'), bool((body or {}).get('cross_org', False)))
        return result

    async def security_mask_preview(self, request: Request):
        body = await request.json() if await request.body() else {}
        asset_id = str((body or {}).get('asset_id') or 'clinical_patient')
        raw = (body or {}).get('sample') or {'patient_id': 'P0042', 'name': '王伟', 'phone': '13812345678', 'id_card': '320102199001011234', 'diagnosis_name': '2型糖尿病', 'chief_complaint': '反复口渴、乏力三个月'}
        return {'asset_id': asset_id, 'mode': 'dynamic_masking', 'before': raw, 'after': self.gov.mask_payload(raw), 'policy': self.gov.get_asset(asset_id)}

    def security_audit(self, limit: int=50, decision: str=''):
        data = self.gov.security_snapshot()['audit']
        if decision:
            data = [x for x in data if x['decision'] == decision]
        return {'audit': data[:max(1, min(int(limit), 200))], 'total': len(data)}

    def security_risks(self, status: str=''):
        data = self.gov.security_snapshot()['risks']
        if status:
            data = [x for x in data if x.get('status') == status]
        return {'risks': data, 'open': sum((x.get('status') == 'open' for x in data))}

    async def security_risk_resolve(self, rid: str, request: Request):
        body = await request.json() if await request.body() else {}
        item = self.gov.resolve_risk(rid, str((body or {}).get('note') or '人工复核完成，已补充授权依据'))
        if not item:
            return {'error': '风险不存在：%s' % rid}
        self.publish([{'source_id': 'security', 'etype': 'security_risk_resolved', 'severity': 'info', 'title': '安全风险已处置：%s' % rid, 'detail': item.get('resolution', '')}])
        return item

    def orchestrator_status(self):
        return self.orch.status() if self.orch else {'error': 'orchestration engine not initialized'}

    async def rule_toggle(self, rid: str):
        r = self.orch.toggle_rule(rid) if self.orch else None
        return r or {'error': '未知规则：%s' % rid}

    def tasks(self, limit: int=60):
        st = self.orch.status() if self.orch else {}
        return {'tasks': (st.get('recent_tasks') or [])[:int(limit)], 'counters': st.get('counters'), 'queue_depth': st.get('queue_depth', 0)}

    async def patrol_now(self):
        t = self.orch.run_patrol(manual=True) if self.orch else None
        return t or {'error': 'orchestration engine not initialized'}
