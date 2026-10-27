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

class PlatformHandlers:

    def publish(self, evs):
        """统一事件出口：EventBus 推送 + 编排规则匹配（感知→编排）"""
        p = BUS.publish(evs) if evs else []
        if self.orch is not None and p:
            try:
                self.orch.handle_events(p)
            except Exception as exc:
                print('[orch] handle error:', exc, flush=True)
        return p

    def _sample_metrics(self):
        """驾驶舱时序采样：各源行数 / 事件累计 / 服务调用累计"""
        if self.engine is None:
            return
        with self.HIST_LOCK:
            rows = {sid: (self.engine.stats.get(sid, {}) or {}).get('rows', 0) for sid in self.engine.adapters}
            self.HIST.append({'t': time.monotonic(), 'epoch': time.time(), 'wall': time.strftime('%H:%M:%S'), 'by_source': rows, 'rows_total': sum((int(x or 0) for x in rows.values())), 'ev': len(BUS.events), 'svc_calls': sum((v['calls'] for v in self.SERVICE_STATS.values()))})
            if self.HISTORY_ORIGIN is None:
                self.HISTORY_ORIGIN = dict(self.HIST[-1], t=self.SERVER_STARTED_MONOTONIC, epoch=self.SERVER_STARTED_AT)

    def _hist_rate(self, key):
        """按最近≤65s窗口估算每分钟增速；返回 (rate, history_points)"""
        with self.HIST_LOCK:
            pts = list(self.HIST)
        if len(pts) < 2:
            return (None, pts)
        last_t = pts[-1]['t']
        win = [q for q in pts if q['t'] >= last_t - 65.0] or pts[:2]
        base, top = (win[0], win[-1])
        span_w = max(top['t'] - base['t'], 1.0)
        return (round((top[key] - base[key]) / span_w * 60.0, 1), pts)

    def orch_global_init(self):
        """初始化第二部分编排引擎（ctx 回调避免循环导入）"""
        ctx = {'adapters': self.engine.adapters, 'source_rows': lambda: {sid: (self.engine.stats.get(sid, {}) or {}).get('rows', 0) for sid in self.engine.adapters}, 'event_count': lambda n=0: len(BUS.events), 'invoke_service': self.call_service_internal, 'publish_event': lambda e: BUS.publish([e])}
        self.orch = Orchestrator(ctx)

    def _patrol_loop(self):
        """定时调度：每180s触发一次全源质量巡检任务"""
        while not self.stop_event.wait(180.0):
            try:
                if self.orch is not None:
                    self.orch.run_patrol()
            except Exception as exc:
                print('[patrol] error:', exc, flush=True)

    def _monitor_loop(self):
        while not self.stop_event.wait(self.SCAN_INTERVAL):
            try:
                if self.engine is not None:
                    if self.sim is not None:
                        self.sim.tick()
                    self.publish(self.engine.scan_all())
                    self.management.catalog.scan_engine(self.engine)
                    self._sample_metrics()
            except Exception as exc:
                print('[monitor] error:', exc, flush=True)

    def _circulation_loop(self):
        i = 0
        while not self.stop_event.wait(5.5):
            try:
                if self.gov is not None and self.engine is not None:
                    self._simulate_circulation_tick(i)
                    i += 1
            except Exception as exc:
                print('[circ] error:', exc, flush=True)

    def startup(self, start_background=True):
        t0 = time.time()
        self.SERVER_STARTED_AT = t0
        self.SERVER_STARTED_MONOTONIC = time.monotonic()
        self.HISTORY_ORIGIN = None
        if not os.path.exists(os.path.join(medsim.DATA_DIR, 'his.db')):
            raise RuntimeError('Data directory is empty. Generate fixtures explicitly before starting.')
        self.engine = DiscoveryEngine()
        self.vlayer = VirtualLayer(self.engine)
        self.sim = medsim.StreamingSim() if self.simulate else None
        self.orch_global_init()
        self.publish(self.engine.scan_all())
        self.management.catalog.scan_engine(self.engine)
        self.management.semantic.import_legacy()
        self._sample_metrics()
        print('[startup] ready in %.1fs, sources=%d' % (time.time() - t0, len(medsim.SOURCE_DEFS)), flush=True)
        if start_background:
            self.start_background()

    def start_background(self):
        if self.stop_event.is_set() or any(thread.is_alive() for thread in self.threads):
            return
        thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.threads.append(thread)
        thread.start()
        if self.simulate:
            thread = threading.Thread(target=self._patrol_loop, daemon=True)
            self.threads.append(thread)
            thread.start()
        if self.simulate:
            thread = threading.Thread(target=self._circulation_loop, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _source_status(self):
        out = []
        for s in medsim.SOURCE_DEFS:
            st = self.engine.stats.get(s['id'], {}) if self.engine else {}
            last = st.get('last_scan') or '-'
            ok = not str(last).endswith('(失败)')
            out.append({**s, 'status': 'online' if ok and last != '-' else 'error' if not ok else 'pending', 'tables': st.get('tables', 0), 'rows': st.get('rows', 0), 'last_scan': last})
        return out

    def health(self):
        return {'ok': True, 'ts': time.strftime('%H:%M:%S')}

    def overview(self):
        con_his = self._con('his.db')
        con_lis = self._con('lis.db')
        con_pacs = self._con('pacs.db')
        con_iot = self._con('iot.db')
        counts = {'patients': con_his.execute('SELECT COUNT(*) FROM patient').fetchone()[0], 'visits': con_his.execute('SELECT COUNT(*) FROM visit').fetchone()[0], 'lab_tests': con_lis.execute('SELECT COUNT(*) FROM test_order').fetchone()[0], 'studies': con_pacs.execute('SELECT COUNT(*) FROM study').fetchone()[0], 'emr_docs': len([f for f in os.listdir(os.path.join(medsim.DATA_DIR, 'emr')) if f.endswith('.json')]), 'vitals_points': con_iot.execute('SELECT COUNT(*) FROM vitals').fetchone()[0]}
        counts['meta_events'] = BUS.since(0, 1) and len(BUS.events) or 0
        recent = list(reversed(BUS.since(0, 8)))
        for c in (con_his, con_lis, con_pacs, con_iot):
            c.close()
        return {'sources': self._source_status(), 'counts': counts, 'recent_events': recent}

    def events(self, after: int=0, limit: int=200):
        return {'events': BUS.since(after, limit)}

    async def stream(self, request: Request, after: int=0):

        async def gen():
            last_seq = int(after or 0)
            while True:
                if await request.is_disconnected():
                    return
                evs = BUS.since(last_seq, 50)
                for e in evs:
                    yield ('data: %s\n\n' % json.dumps(e, ensure_ascii=False))
                    last_seq = max(last_seq, e['seq'])
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    def cockpit_api(self):
        srcs = self._source_status()
        total_rows = sum((int(s['rows'] or 0) for s in srcs))
        rows_rate, pts = self._hist_rate('rows_total')
        ev_now = len(BUS.events)
        ev_rate, _ = self._hist_rate('ev')
        with self.SERVICE_STATS_LOCK:
            svc_calls = sum((v['calls'] for v in self.SERVICE_STATS.values()))
            svc_ms = sum((v['total_ms'] for v in self.SERVICE_STATS.values()))
        con_his = self._con('his.db')
        patients_n = con_his.execute('SELECT COUNT(*) FROM patient').fetchone()[0]
        visits_n = con_his.execute('SELECT COUNT(*) FROM visit').fetchone()[0]
        top_diags = [dict(zip(['name', 'n'], r)) for r in con_his.execute("SELECT diagnosis_name, COUNT(*) n FROM visit WHERE diagnosis_name IS NOT NULL AND TRIM(diagnosis_name) != '' GROUP BY 1 ORDER BY n DESC LIMIT 8")]
        dept_rows = list(con_his.execute("SELECT COALESCE(NULLIF(TRIM(dept), ''), '未归类科室'), COUNT(*) visits, COUNT(DISTINCT patient_id) patients FROM visit GROUP BY 1 ORDER BY visits DESC"))
        dept_pids = {}
        for dept, pid in con_his.execute("SELECT COALESCE(NULLIF(TRIM(dept), ''), '未归类科室'), patient_id FROM visit"):
            dept_pids.setdefault(dept, set()).add(pid)
        con_lis = self._con('lis.db')
        abn_by_pid = {r[0]: int(r[1] or 0) for r in con_lis.execute("SELECT t.patient_id, SUM(CASE WHEN r.flag IN ('H','L') THEN 1 ELSE 0 END) FROM test_order t JOIN result r ON r.test_no=t.test_no GROUP BY t.patient_id")}
        con_lis.close()
        con_pacs = self._con('pacs.db')
        studies_by_pid = {r[0]: int(r[1] or 0) for r in con_pacs.execute('SELECT patient_id, COUNT(*) FROM study GROUP BY patient_id')}
        con_pacs.close()
        dept_distribution = []
        for dept, visits, patients in dept_rows:
            pids = dept_pids.get(dept, set())
            abnormal = sum((abn_by_pid.get(pid, 0) for pid in pids))
            studies = sum((studies_by_pid.get(pid, 0) for pid in pids))
            dept_distribution.append({'name': dept, 'patients': int(patients), 'visits': int(visits), 'abnormal_items': abnormal, 'studies': studies, 'data_points': int(visits + abnormal + studies)})
        con_his.close()
        g = build_graph(self.engine, limit_patients=100) if self.engine else {'nodes': [], 'edges': []}
        ev_by_type = {}
        for e in BUS.events:
            ev_by_type[e['etype']] = ev_by_type.get(e['etype'], 0) + 1
        st = self.orch.status() if self.orch else {'counters': {}, 'queue_depth': 0, 'recent_tasks': [], 'rules': []}
        triggered_total = sum((r['stats']['triggered'] for r in st.get('rules', [])))
        counters = st.get('counters') or {}
        history = list(pts or [])
        step = max(1, len(history) // 96)
        return {'ts': time.strftime('%H:%M:%S'), 'kpis': {'sources_online': sum((1 for s in srcs if s['status'] == 'online')), 'source_total': len(srcs), 'total_rows': total_rows, 'rows_per_min': rows_rate, 'events_total': ev_now, 'ev_per_min': ev_rate, 'graph_nodes': len(g.get('nodes', [])), 'graph_edges': len(g.get('edges', [])), 'patients': patients_n, 'visits': visits_n}, 'sources': [{'id': s['id'], 'name': s['name'], 'rows': int(s['rows'] or 0)} for s in srcs], 'history': history[::step], 'history_origin': self.HISTORY_ORIGIN, 'ev_by_type': ev_by_type, 'top_diags': top_diags, 'dept_distribution': dept_distribution, 'svc_calls_total': svc_calls, 'svc_avg_ms': round(svc_ms / svc_calls, 2) if svc_calls else None, 'loop': {'triggered': triggered_total, 'queue_depth': st.get('queue_depth', 0), 'tasks_done': counters.get('done', 0), 'success_rate': counters.get('success_rate'), 'svc_calls': svc_calls, 'feedback_events': ev_by_type.get('orchestrated_action', 0)}, 'recent_tasks': (st.get('recent_tasks') or [])[:6]}

    def index(self):
        return FileResponse(os.path.join(BASE_DIR, 'static', 'index.html'))
