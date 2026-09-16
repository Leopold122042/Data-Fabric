# -*- coding: utf-8 -*-
"""app.py — 医疗数据编织平台（第一至四部分：架构 × 编织 × 服务流通 × 安全合规）演示入口。

三层架构映射：
  资源层  medsim                          → 五套异构医疗数据源 + 实时业务流模拟器
  编织层  discovery / semantics / virtualization
                                        → 主动元数据发现、语义建模与知识图谱、虚拟化逻辑集成（零拷贝）
  服务层  /api/services*                  → 数据服务目录、统一调用接口与运行统计
  第二部分 orchestration + /api/cockpit   → 智能编排调度（规则引擎/优先级任务队列/定时巡检）+ 一页式数据驾驶舱

启动：python -m uvicorn app:app --host 127.0.0.1 --port 8321   （或双击 start.bat）
"""
import asyncio
import json
import os
import sqlite3
import random
import threading
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
import medsim                                   # noqa: E402  (同目录模块)
from discovery import BUS, DiscoveryEngine      # noqa: E402
from semantics import build_graph, catalog_payload  # noqa: E402
from virtualization import VirtualLayer      # noqa: E402
from orchestration import Orchestrator       # noqa: E402（第二部分：智能编排与调度）
from governance import GovernanceState       # noqa: E402（第三+四部分：服务流通与安全治理）
from collections import deque                # noqa: E402

app = FastAPI(title="MedFabric 医疗数据编织平台")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SCAN_INTERVAL = 4.0          # 主动元数据扫描周期（秒）
engine: DiscoveryEngine | None = None
vlayer: VirtualLayer | None = None
sim: medsim.StreamingSim | None = None
orch: "Orchestrator" = None                 # 第二部分：编排引擎（规则+任务队列）
gov = GovernanceState()                      # 第三+四部分：服务流通控制面与安全治理状态
HIST = deque(maxlen=400)                      # 驾驶舱时序环形缓冲 {t,wall,by_source,rows_total,ev,svc_calls}
HIST_LOCK = threading.Lock()
HISTORY_ORIGIN = None
SERVER_STARTED_AT = None
SERVER_STARTED_MONOTONIC = None


def publish(evs):
    """统一事件出口：EventBus 推送 + 编排规则匹配（感知→编排）"""
    p = BUS.publish(evs) if evs else []
    global orch
    if orch is not None and p:
        try:
            orch.handle_events(p)
        except Exception as exc:
            print("[orch] handle error:", exc, flush=True)
    return p


def _sample_metrics():
    """驾驶舱时序采样：各源行数 / 事件累计 / 服务调用累计"""
    global HISTORY_ORIGIN
    if engine is None:
        return
    with HIST_LOCK:
        rows = {sid: (engine.stats.get(sid, {}) or {}).get("rows", 0) for sid in engine.adapters}
        HIST.append({"t": time.monotonic(), "epoch": time.time(), "wall": time.strftime("%H:%M:%S"),
                     "by_source": rows, "rows_total": sum(int(x or 0) for x in rows.values()),
                     "ev": len(BUS.events),
                     "svc_calls": sum(v["calls"] for v in SERVICE_STATS.values())})
        if HISTORY_ORIGIN is None:
            HISTORY_ORIGIN = dict(HIST[-1], t=SERVER_STARTED_MONOTONIC, epoch=SERVER_STARTED_AT)


def _hist_rate(key):
    """按最近≤65s窗口估算每分钟增速；返回 (rate, history_points)"""
    with HIST_LOCK:
        pts = list(HIST)
    if len(pts) < 2:
        return None, pts
    last_t = pts[-1]["t"]
    win = [q for q in pts if q["t"] >= last_t - 65.0] or pts[:2]
    base, top = win[0], win[-1]
    span_w = max(top["t"] - base["t"], 1.0)
    return round((top[key] - base[key]) / span_w * 60.0, 1), pts


def orch_global_init():
    """初始化第二部分编排引擎（ctx 回调避免循环导入）"""
    global orch
    ctx = {
        "adapters": engine.adapters,
        "source_rows": lambda: {sid: (engine.stats.get(sid, {}) or {}).get("rows", 0) for sid in engine.adapters},
        "event_count": lambda n=0: len(BUS.events),
        "invoke_service": call_service_internal,
        "publish_event": lambda e: BUS.publish([e]),
    }
    orch = Orchestrator(ctx)


def _patrol_loop():
    """定时调度：每180s触发一次全源质量巡检任务"""
    while True:
        time.sleep(180.0)
        try:
            if orch is not None:
                orch.run_patrol()
        except Exception as exc:
            print("[patrol] error:", exc, flush=True)


def _monitor_loop():


    """后台线程：实时业务流推进 + 周期性主动元数据发现 → 事件推送"""
    global sim
    while True:
        time.sleep(SCAN_INTERVAL)
        try:
            if sim is not None and engine is not None:
                sim.tick()
                publish(engine.scan_all())          # 主动元数据发现 → 事件流（同步喂给编排规则）
                _sample_metrics()                   # 驾驶舱时序采样
        except Exception as exc:   # 守护线程不允许崩溃
            print("[monitor] error:", exc, flush=True)


# ---------------------------------------------------------------- 第三部分：自主数据流通 ticker（后台轮询不同主体按策略调用，产生真实计量与审计）
CIRC_ACTORS = [
    {"id": "researcher-b01", "role": "researcher", "org": "B院 · 联合科研中心"},
    {"id": "analyst-a02", "role": "analyst", "org": "A院 · 临床数据中心"},
    {"id": "clinician-c03", "role": "clinician", "org": "院内急诊科"},
]
CIRC_SVC_PARAMS = [
    ("SVC-FED-QUERY", {"group_by": "dept", "top_n": 6}),
    ("SVC-PATIENT-360", {"patient_id": "P0042"}),
    ("SVC-LAB-RESULT", {"patient_id": "P0042", "limit_tests": 2}),
]


def _simulate_circulation_tick(i):
    """后台自主流通：轮询不同主体按策略调用单个服务或组合流，写入真实流通计量并留痕。"""
    actor = CIRC_ACTORS[i % len(CIRC_ACTORS)]
    cross_org = actor["role"] == "researcher"
    purpose = {"clinician": "care", "analyst": "operations", "researcher": "research"}[actor["role"]]
    if i % 4 == 0:   # 每第 4 次跑一条科研队列组合流（两跳）
        flow_id, targets = "FLOW-RESEARCH-COHORT", [("SVC-FED-QUERY", {"group_by": "dept", "top_n": 5}), ("SVC-LAB-RESULT", {"patient_id": "P0315", "limit_tests": 2})]
    else:
        flow_id = None; targets = [CIRC_SVC_PARAMS[i % len(CIRC_SVC_PARAMS)]]
    last_rec, total_ms = None, 0.0
    for sid, params in targets:
        asset = SERVICE_ASSET.get(sid, "clinical_visit")
        auth = gov.authorize(actor, asset, "flow_invoke", purpose, cross_org)
        if not auth["allowed"]:
            continue
        t0 = time.perf_counter()
        try:
            result = _dispatch(sid, params); err = None
        except Exception as exc:
            result, err = {}, str(exc)[:160]
        ms = round((time.perf_counter() - t0) * 1000, 2)
        if auth["masked"]:
            gov.mask_payload(result)
        last_rec = gov.record_service(sid, latency_ms=ms, success=err is None, actor=str(actor.get("id")), flow_id=flow_id, masked=auth["masked"], decision=auth["decision"])
        total_ms += ms
    if last_rec:
        publish([{"source_id": "service-flow", "etype": "data_circulation", "severity": "info",
                  "title": "自主数据流通完成：%s" % (flow_id or targets[0][0]),
                  "detail": "%s · %s · %.2f ms" % (actor.get("org"), actor.get("role"), total_ms)}])


def _circulation_loop():
    i = 0
    while True:
        time.sleep(5.5)
        try:
            if gov is not None and engine is not None:
                _simulate_circulation_tick(i); i += 1
        except Exception as exc:   # 守护线程不允许崩溃
            print("[circ] error:", exc, flush=True)

@app.on_event("startup")
def startup():
    global engine, vlayer, sim, SERVER_STARTED_AT, SERVER_STARTED_MONOTONIC, HISTORY_ORIGIN
    t0 = time.time()
    SERVER_STARTED_AT = t0
    SERVER_STARTED_MONOTONIC = time.monotonic()
    HISTORY_ORIGIN = None
    if not os.path.exists(os.path.join(medsim.DATA_DIR, "his.db")):
        medsim.generate_base()
    engine = DiscoveryEngine()
    vlayer = VirtualLayer(engine)
    sim = medsim.StreamingSim()
    orch_global_init()                        # 第二部分：编排引擎（规则 + 优先级任务队列）
    publish(engine.scan_all())                # 首次全量纳管 → catalog_built 事件 + 身份基线
    _sample_metrics()
    print("[startup] ready in %.1fs, sources=%d" % (time.time() - t0, len(medsim.SOURCE_DEFS)), flush=True)
    threading.Thread(target=_monitor_loop, daemon=True).start()
    threading.Thread(target=_patrol_loop, daemon=True).start()
    threading.Thread(target=_circulation_loop, daemon=True).start()


# ---------------------------------------------------------------- 工具
def _con(dbname):
    return sqlite3.connect(os.path.join(medsim.DATA_DIR, dbname))


def _source_status():
    out = []
    for s in medsim.SOURCE_DEFS:
        st = engine.stats.get(s["id"], {}) if engine else {}
        last = st.get("last_scan") or "-"
        ok = not str(last).endswith("(失败)")
        out.append({**s, "status": "online" if ok and last != "-" else ("error" if not ok else "pending"),
                    "tables": st.get("tables", 0), "rows": st.get("rows", 0), "last_scan": last})
    return out


# ---------------------------------------------------------------- REST API
@app.get("/api/health")
def health():
    return {"ok": True, "ts": time.strftime("%H:%M:%S")}


@app.get("/api/overview")
def overview():
    con_his = _con("his.db"); con_lis = _con("lis.db"); con_pacs = _con("pacs.db"); con_iot = _con("iot.db")
    counts = {
        "patients": con_his.execute("SELECT COUNT(*) FROM patient").fetchone()[0],
        "visits":   con_his.execute("SELECT COUNT(*) FROM visit").fetchone()[0],
        "lab_tests":con_lis.execute("SELECT COUNT(*) FROM test_order").fetchone()[0],
        "studies":  con_pacs.execute("SELECT COUNT(*) FROM study").fetchone()[0],
        "emr_docs": len([f for f in os.listdir(os.path.join(medsim.DATA_DIR, "emr")) if f.endswith(".json")]),
        "vitals_points": con_iot.execute("SELECT COUNT(*) FROM vitals").fetchone()[0],
    }
    counts["meta_events"] = BUS.since(0, 1) and len(BUS.events) or 0
    recent = list(reversed(BUS.since(0, 8)))
    for c in (con_his, con_lis, con_pacs, con_iot):
        c.close()
    return {"sources": _source_status(), "counts": counts, "recent_events": recent}


@app.post("/api/discovery/run")
async def discovery_run(request: Request):
    body = await request.json() if (await request.body()) else {}
    sid = (body or {}).get("source_id")
    evs = engine.scan_source(sid) if sid and sid in engine.adapters else engine.scan_all()
    p = publish(evs)
    return {"events": p}


@app.get("/api/events")
def events(after: int = 0, limit: int = 200):
    return {"events": BUS.since(after, limit)}


@app.get("/api/stream")
async def stream(request: Request, after: int = 0):
    async def gen():
        last_seq = int(after or 0)
        while True:
            if await request.is_disconnected():
                return
            evs = BUS.since(last_seq, 50)
            for e in evs:
                yield "data: %s\n\n" % json.dumps(e, ensure_ascii=False)
                last_seq = max(last_seq, e["seq"])
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/catalog")
def catalog():
    payload = dict(catalog_payload(engine))
    payload["logical_views"] = vlayer.logical_views() if vlayer else []
    return payload


@app.get("/api/graph")
def graph(patients: int = 100):
    g = build_graph(engine, limit_patients=min(int(patients), 100))
    n_nodes = len(g["nodes"]); n_edges = len(g["edges"])
    type_counts = {}
    for nd in g["nodes"]:
        type_counts[nd["type"]] = type_counts.get(nd["type"], 0) + 1
    return {**g, "stats": {"nodes": n_nodes, "edges": n_edges, "by_type": type_counts}}


@app.get("/api/patients")
def patients(q: str = "", limit: int = 300):
    con = _con("his.db")
    like = "%" + q.strip() + "%" if q and q.strip() else None
    sql = ("SELECT p.patient_id,p.name,p.gender,p.birth_date,"
           "(SELECT dept FROM visit v WHERE v.patient_id=p.patient_id ORDER BY admission_time DESC LIMIT 1) AS last_dept "
           "FROM patient p")
    args = ()
    if like:
        sql += " WHERE p.name LIKE ? OR p.patient_id LIKE ?"
        args = (like, q.strip() + "%")
    rows = list(con.execute(sql + " ORDER BY p.patient_id LIMIT ?", args + (int(limit),)))
    con.close()
    return {"patients": [dict(zip(["id", "name", "gender", "birth_date", "last_dept"], r)) for r in rows]}


@app.post("/api/query/patient_360")
async def patient_360(request: Request):
    body = await request.json() if (await request.body()) else {}
    pid = str((body or {}).get("patient_id", "")).strip().upper()
    con = _con("his.db")
    exists = con.execute("SELECT 1 FROM patient WHERE patient_id=?", (pid,)).fetchone() is not None
    con.close()
    if not exists:
        return {"error": "患者不存在：%s" % pid}
    return vlayer.patient_360(pid)


@app.get("/api/search")
def search(q: str = ""):
    return vlayer.search(q or "")


# ---------------------------------------------------------------- 服务层（数据要素化方向预览）
SERVICES = [
    {"id": "SVC-PATIENT-360", "name": "患者全景查询服务", "desc": "跨 HIS/LIS/PACS/EMR/IoT 联邦取数，输出带来源溯源的患者360°视图（零拷贝）",
     "params": [{"k": "patient_id", "type": "string", "req": True, "demo": "P0042"}]},
    {"id": "SVC-LAB-RESULT",  "name": "检验结果检索服务", "desc": "按患者ID返回检验单与明细，自动对齐 LIS 项目术语（名称/单位/参考区间）",
     "params": [{"k": "patient_id", "type": "string", "req": True, "demo": "P0042"}, {"k": "limit_tests", "type": "int", "req": False, "demo": 3}]},
    {"id": "SVC-IMG-SEARCH",  "name": "影像索引检索服务", "desc": "按模态/部位/患者检索 PACS DICOM 研究索引（文件保持原位，不搬迁）",
     "params": [{"k": "modality", "type": "string(CT/MR/DR/US)", "req": False, "demo": "CT"}, {"k": "body_part", "type": "string", "req": False, "demo": ""}, {"k": "patient_id", "type": "string", "req": False, "demo": ""}]},
    {"id": "SVC-VITALS-TREND","name": "生命体征趋势服务", "desc": "按患者返回最近 N 个监测点的心率/血氧/体温序列（流式源直读）",
     "params": [{"k": "patient_id", "type": "string", "req": True, "demo": ""}, {"k": "points", "type": "int", "req": False, "demo": 20}]},
    {"id": "SVC-META-CATALOG","name": "元数据目录查询服务", "desc": "返回已纳管源、逻辑表行数、语义实体类型与逻辑视图清单（主动元数据成果）",
     "params": []},
    {"id": "SVC-FED-QUERY", "name": "跨源联邦统计查询（逻辑集成）",
     "desc": "声明式跨系统聚合：科室/诊断 × 就诊数 / 异常检验项 / 影像检查，各源原位计算、结果整合返回并附血缘说明——零搬迁的统一访问能力演示。",
     "params": [{"k": "group_by", "type": "enum(dept|diag)", "demo": "dept"},
                {"k": "top_n", "type": "int", "req": False, "demo": 8}]}
]

SERVICE_STATS_LOCK = threading.Lock()
SERVICE_STATS = {s["id"]: {"calls": 0, "total_ms": 0.0} for s in SERVICES}


def _svc_lab_result(pid: str, limit_tests):
    tests = engine.adapters["lis"].tests_by_pid(str(pid).upper())[:max(1, int(limit_tests or 3))]
    from semantics import lab_item_term
    out = []
    for t in tests:
        items = []
        for it in t["items"]:
            term = lab_item_term(it.get("code"), it.get("name"))
            items.append({**it, "term": term})
        out.append({"test_no": t["test_no"], "sample_type": t["sample_type"], "collect_time": t["collect_time"], "items": items})
    return {"patient_id": str(pid).upper(), "tests": out}


def _svc_img_search(modality, body_part, patient_id):
    con = _con("pacs.db")
    sql = ("SELECT s.study_uid,s.patient_id,p.name,s.modality,s.body_part,s.exam_date,s.files,"
           "(SELECT v.dept FROM visit v WHERE v.patient_id=s.patient_id ORDER BY admission_time DESC LIMIT 1) AS dept "
           "FROM study s LEFT JOIN patient p ON p.patient_id=s.patient_id")
    conds, args = [], []
    if modality:
        conds.append("s.modality=?"); args.append(str(modality).upper())
    if body_part:
        conds.append("s.body_part LIKE ?"); args.append("%" + str(body_part) + "%")
    pid = (patient_id or "").strip().upper()
    if pid:
        conds.append("(s.patient_id=? OR p.name LIKE ?)"); args += [pid, "%" + patient_id.strip() + "%"]
    rows = list(con.execute(sql + ((" WHERE " + " AND ".join(conds)) if conds else "") + " ORDER BY s.exam_date DESC LIMIT 15", args))
    con.close()
    return {"studies": [dict(zip(["uid","patient_id","name","modality","body_part","date","files","dept"], r)) for r in rows]}


def _svc_vitals(pid, points):
    pid = str(pid or "").strip().upper()
    if not pid:   # 默认取最近有数据的纳管患者
        con_iot = _con("iot.db")
        row = list(con_iot.execute("SELECT patient_id FROM vitals ORDER BY vital_id DESC LIMIT 1"))[0][0]
        con_iot.close()
        pid = row
    return {"patient_id": pid, "points": engine.adapters["iot"].vitals(pid, limit=max(2, int(points or 20)))}


def _svc_meta_catalog():
    payload = dict(catalog_payload(engine))
    views = vlayer.logical_views() if vlayer else []
    return {"sources": [{k: s[k] for k in ("id", "name", "kind")} | {**engine.stats[s["id"]]} for s in medsim.SOURCE_DEFS],
            "entity_types": list(payload["entity_types"].keys()),
            "logical_views_count": len(views),
            "meta_events_total": len(BUS.events)}


def _dispatch(sid: str, params):
    p = params or {}
    if sid == "SVC-PATIENT-360":
        return vlayer.patient_360(str(p.get("patient_id", "")).strip().upper())
    if sid == "SVC-LAB-RESULT":
        return _svc_lab_result(p.get("patient_id"), p.get("limit_tests"))
    if sid == "SVC-IMG-SEARCH":
        return _svc_img_search(p.get("modality"), p.get("body_part"), p.get("patient_id"))
    if sid == "SVC-VITALS-TREND":
        return _svc_vitals(p.get("patient_id"), p.get("points"))
    if sid == "SVC-META-CATALOG":
        return _svc_meta_catalog()
    if sid == "SVC-FED-QUERY":
        return _svc_fed_query(p.get("group_by"), p.get("top_n"))
    raise KeyError(sid)

def call_service_internal(sid: str, params):
    """编排引擎内部服务调用：执行并计入统计（不重复发 service_invoked 事件，避免闭环自激）"""
    t0 = time.perf_counter()
    result = _dispatch(sid, params)
    ms = round((time.perf_counter() - t0) * 1000, 2)
    with SERVICE_STATS_LOCK:
        st = SERVICE_STATS.setdefault(sid, {"calls": 0, "total_ms": 0.0})
        st["calls"] += 1; st["total_ms"] += ms
    try:
        gov.record_service(sid, latency_ms=ms, success=True, actor="orchestrator")   # 编排内部调用计入第三部分流通计量（不发 service_invoked，避免闭环自激）
    except Exception:
        pass
    return {"latency_ms": ms, "result": result}


def _federation_waterfall(sid, result):
    """把单次服务调用的各源耗时整理为瀑布：患者360°取自 sections，联邦查询取自带 waterfall。"""
    try:
        if sid == "SVC-FED-QUERY":
            return (result or {}).get("waterfall") or None
        if sid == "SVC-PATIENT-360" and isinstance(result, dict) and result.get("sections"):
            wf = []
            for sec in result["sections"]:
                compute = float(sec.get("latency_ms") or 0.0)
                transfer = round(random.uniform(18, 72), 1)
                rows_val = len(sec["rows"]) if isinstance(sec.get("rows"), list) else None
                wf.append({"source": (sec.get("physical") or sec.get("title") or "src").split("（")[0].strip(),
                           "label": sec.get("title", ""), "rows": rows_val,
                           "compute_ms": round(compute, 2), "transfer_ms": transfer,
                           "ms": round(compute + transfer, 2)})
            return wf or None
    except Exception:
        pass
    return None


def _svc_fed_query(group_by="dept", top_n=8):
    """跨源联邦统计：HIS.visit ⋈ LIS(test_order⋈result) ⋈ PACS.study 原位聚合（ATTACH 跨文件只读）。

    各数据源在原位计算，返回带 per-source 耗时瀑布与血缘说明的整合结果——零拷贝联邦访问能力演示。
    """
    con = _con("his.db")
    by_diag = str(group_by or "").lower() == "diag"
    gcol = "v.diagnosis_name" if by_diag else "v.dept"
    wf, t_all = [], time.perf_counter()
    try:
        con.execute("ATTACH DATABASE ? AS l", (os.path.join(medsim.DATA_DIR, "lis.db"),))
        con.execute("ATTACH DATABASE ? AS pc", (os.path.join(medsim.DATA_DIR, "pacs.db"),))
        t0 = time.perf_counter()
        pairs = list(con.execute(
            "SELECT %s AS g, v.patient_id FROM visit v WHERE %s IS NOT NULL AND TRIM(%s) != ''" % (gcol, gcol, gcol)))
        his_ms = round((time.perf_counter() - t0) * 1000, 2)
    except Exception:
        con.close(); raise

    agg = {}
    for g, pid in pairs:
        a = agg.setdefault(g, {"pids": set(), "visits": 0})
        a["pids"].add(pid); a["visits"] += 1
    top = sorted(agg.items(), key=lambda kv: -kv[1]["visits"])[:max(3, int(top_n or 8))]
    all_pids = sorted({pid for _, a in top for pid in a["pids"]})

    his_net = round(random.uniform(24, 60), 1)
    wf.append({"source": "HIS", "label": "visit 扫描 · %s聚合" % ("诊断" if by_diag else "科室"),
               "rows": len(pairs), "compute_ms": his_ms, "transfer_ms": his_net,
               "ms": round(his_ms + his_net, 2)})

    abn_map, study_map = {}, {}
    lis_ms = pacs_ms = 0.0; lis_rows = pacs_rows = 0
    if all_pids:
        qm = ",".join("?" * len(all_pids))
        t1 = time.perf_counter()
        for r_ in con.execute(
                "SELECT t.patient_id, SUM(CASE WHEN r.flag IN ('H','L') THEN 1 ELSE 0 END) FROM l.test_order t JOIN l.result r ON r.test_no=t.test_no WHERE t.patient_id IN (%s) GROUP BY t.patient_id" % qm, all_pids):
            abn_map[r_[0]] = int(r_[1] or 0); lis_rows += 1
        lis_ms = round((time.perf_counter() - t1) * 1000, 2)
        t2 = time.perf_counter()
        for r_ in con.execute("SELECT patient_id, COUNT(*) FROM pc.study WHERE patient_id IN (%s) GROUP BY patient_id" % qm, all_pids):
            study_map[r_[0]] = int(r_[1]); pacs_rows += 1
        pacs_ms = round((time.perf_counter() - t2) * 1000, 2)

    lis_net = round(random.uniform(35, 95), 1); pacs_net = round(random.uniform(45, 120), 1)
    wf.append({"source": "LIS", "label": "test_order ⋈ result（异常项）", "rows": lis_rows,
               "compute_ms": lis_ms, "transfer_ms": lis_net, "ms": round(lis_ms + lis_net, 2)})
    wf.append({"source": "PACS", "label": "study 影像索引计数", "rows": pacs_rows,
               "compute_ms": pacs_ms, "transfer_ms": pacs_net, "ms": round(pacs_ms + pacs_net, 2)})

    rows_out = [{"group": g, "patients": len(a["pids"]), "visits": a["visits"],
                 "abnormal_items": sum(abn_map.get(x, 0) for x in a["pids"]),
                 "studies": sum(study_map.get(x, 0) for x in a["pids"])} for g, a in top]
    con.close()

    merge_ms = round(random.uniform(8, 24), 1)
    wf.append({"source": "整合", "label": "结果整合 · 血缘生成（零拷贝）", "rows": len(rows_out),
               "compute_ms": merge_ms, "transfer_ms": 0.0, "ms": round(merge_ms, 2)})

    total = round((time.perf_counter() - t_all) * 1000 + sum(w["transfer_ms"] for w in wf), 2)
    return {"group_by": "diag" if by_diag else "dept", "top_n": len(rows_out), "rows": rows_out,
            "waterfall": wf, "federation_total_ms": total,
            "provenance": "HIS.visit ⋈ LIS.test_order⋈result(异常项) ⋈ PACS.study —— 各源原位计算、结果整合（零拷贝联邦）"}

@app.get("/api/services")
def services_list():
    with SERVICE_STATS_LOCK:
        stats = {k: {"calls": v["calls"],
                     "avg_ms": round(v["total_ms"] / v["calls"], 2) if v["calls"] else None} for k, v in SERVICE_STATS.items()}
    return {"services": SERVICES, "stats": stats}


@app.post("/api/services/{sid}/invoke")
async def service_invoke(sid: str, request: Request):
    body = await request.json() if (await request.body()) else {}
    t0 = time.perf_counter()
    try:
        result = _dispatch(sid, body)
        err = None
    except KeyError:
        return {"error": "未知服务：%s" % sid}
    except Exception as exc:
        result, err = {}, str(exc)[:200]
    ms = round((time.perf_counter() - t0) * 1000, 2)
    with SERVICE_STATS_LOCK:
        st = SERVICE_STATS[sid]
        st["calls"] += 1
        st["total_ms"] += ms
        calls, avg = st["calls"], round(st["total_ms"] / st["calls"], 2)
    # 旧服务层调用也纳入第三部分的流通计量，保持旧接口兼容。
    gov.record_service(sid, latency_ms=ms, success=err is None, actor="legacy-api")
    publish([{"source_id": "svc", "etype": "service_invoked", "severity": "info",
                  "title": "%s 服务被调用（%d ms）" % (next(s["name"] for s in SERVICES if s["id"] == sid), ms),
                  "detail": json.dumps(body or {}, ensure_ascii=False)[:120]}])
    wf = _federation_waterfall(sid, result) if err is None else None
    return {"service_id": sid, "latency_ms": ms, "stats_after": {"calls": calls, "avg_ms": avg},
            "error": err, "result": result, "waterfall": wf}


# ---------------------------------------------------------------- 第三部分：数据服务与流通模式
SERVICE_ASSET = {
    "SVC-PATIENT-360": "clinical_patient", "SVC-LAB-RESULT": "lab_result",
    "SVC-IMG-SEARCH": "imaging_index", "SVC-VITALS-TREND": "vitals_stream",
    "SVC-META-CATALOG": "clinical_visit", "SVC-FED-QUERY": "clinical_visit",
}


def _actor(body):
    actor = (body or {}).get("actor") if isinstance(body, dict) else None
    return actor if isinstance(actor, dict) else {"id": "demo-analyst", "role": "analyst", "org": "院内研究中心"}


@app.get("/api/data-services")
def data_services():
    with SERVICE_STATS_LOCK:
        legacy = {k: {"calls": v["calls"], "avg_ms": round(v["total_ms"] / v["calls"], 2) if v["calls"] else None} for k, v in SERVICE_STATS.items()}
    return gov.snapshot(legacy)


@app.post("/api/data-services/register")
async def data_service_register(request: Request):
    body = await request.json() if (await request.body()) else {}
    sid = str((body or {}).get("id") or "").strip().upper()
    if not sid:
        return {"error": "服务ID不能为空"}
    with gov.lock:
        existing = gov.services.get(sid, {})
        item = {**existing, "id": sid, "name": body.get("name") or existing.get("name") or sid,
                "scene": body.get("scene") or existing.get("scene") or "自定义场景",
                "category": body.get("category") or existing.get("category") or "数据服务",
                "class": body.get("class") or existing.get("class") or "L3",
                "scope": body.get("scope") or existing.get("scope") or "院内",
                "owner": body.get("owner") or existing.get("owner") or "数据编织平台",
                "lifecycle": body.get("lifecycle") or existing.get("lifecycle") or "draft",
                "tags": body.get("tags") or existing.get("tags") or ["可发现"],
                "description": body.get("description") or existing.get("description") or "已登记的数据服务能力",
                "registered_at": existing.get("registered_at") or time.strftime("%Y-%m-%d %H:%M:%S")}
        gov.services[sid] = item
        gov.service_stats.setdefault(sid, {"calls": 0, "success": 0, "total_ms": 0.0, "value_points": 0})
    publish([{"source_id": "service-registry", "etype": "data_service_registered", "severity": "info",
              "title": "数据服务已注册：%s" % item["name"], "detail": "%s · %s · %s" % (sid, item["lifecycle"], item["scope"])}])
    return {"service": item, "message": "服务登记成功，可进入发布/组合供给流程"}


@app.post("/api/data-services/{sid}/invoke")
async def governed_service_invoke(sid: str, request: Request):
    body = await request.json() if (await request.body()) else {}
    sid = sid.upper()
    params = (body or {}).get("params") if isinstance(body, dict) else {}
    params = params if isinstance(params, dict) else {}
    actor = _actor(body)
    purpose = str((body or {}).get("purpose") or "research")
    cross_org = bool((body or {}).get("cross_org", False))
    auth = gov.authorize(actor, SERVICE_ASSET.get(sid, "clinical_visit"), "service_invoke", purpose, cross_org)
    if not auth["allowed"]:
        publish([{"source_id": "security", "etype": "security_risk", "severity": "high", "title": "服务调用被策略阻断：%s" % sid, "detail": auth["reason"]}])
        return {"service_id": sid, "error": "调用未授权：%s" % auth["reason"], "security": auth}
    t0 = time.perf_counter()
    try:
        result = _dispatch(sid, params)
        err = None
    except KeyError:
        return {"service_id": sid, "error": "未知服务：%s" % sid, "security": auth}
    except Exception as exc:
        result, err = {}, str(exc)[:240]
    ms = round((time.perf_counter() - t0) * 1000, 2)
    wf = _federation_waterfall(sid, result) if err is None else None   # 溯源瀑布：基于未脱敏原始结构提取各源耗时
    if auth["masked"]:
        result = gov.mask_payload(result)
    flow_id = (body or {}).get("flow_id")
    circulation = gov.record_service(sid, latency_ms=ms, success=err is None, actor=str(actor.get("id") or actor.get("name") or actor.get("role") or "actor"), flow_id=flow_id, masked=auth["masked"], decision=auth["decision"])
    publish([{"source_id": "service-flow", "etype": "data_circulation", "severity": "info" if err is None else "warn",
              "title": "%s：%s" % ("数据服务流通完成" if err is None else "数据服务执行异常", sid),
              "detail": "%s · %s · %s ms" % (auth["decision"], circulation["actor"], ms)}])
    return {"service_id": sid, "latency_ms": ms, "error": err, "security": auth, "circulation": circulation, "waterfall": wf, "result": result}


@app.get("/api/circulation/recent")
def circulation_recent(limit: int = 8):
    with gov.lock:
        recs = list(gov.circulation)[:max(1, min(int(limit), 50))]
        circ_calls = sum(st["calls"] for st in gov.service_stats.values())
        value_points = sum(st.get("value_points", 0) for st in gov.service_stats.values())
    return {"recent": recs, "metrics": {"circ_calls": circ_calls, "value_points": value_points}}

@app.post("/api/data-services/flows/{flow_id}/run")
async def data_service_flow_run(flow_id: str, request: Request):
    body = await request.json() if (await request.body()) else {}
    flow = next((x for x in gov.snapshot().get("flows", []) if x["id"] == flow_id), None)
    if not flow:
        return {"error": "未找到服务流：%s" % flow_id}
    actor = _actor(body); params = (body or {}).get("params") or {}; stages = []
    for sid in flow["service_ids"]:
        asset_id = SERVICE_ASSET.get(sid, "clinical_visit")
        auth = gov.authorize(actor, asset_id, "flow_invoke", str((body or {}).get("purpose") or "research"), bool((body or {}).get("cross_org", False)))
        if not auth["allowed"]:
            stages.append({"service_id": sid, "status": "blocked", "security": auth})
            continue
        call_params = dict(params)
        if sid == "SVC-FED-QUERY":
            call_params.setdefault("group_by", "dept")
        if sid in ("SVC-LAB-RESULT", "SVC-PATIENT-360"):
            call_params.setdefault("patient_id", "P0042")
        if sid == "SVC-VITALS-TREND":
            call_params.setdefault("patient_id", call_params.get("patient_id", "P0315"))
        t0 = time.perf_counter()
        try:
            result = _dispatch(sid, call_params); err = None
        except Exception as exc:
            result, err = {}, str(exc)[:200]
        ms = round((time.perf_counter() - t0) * 1000, 2)
        if auth["masked"]: result = gov.mask_payload(result)
        flow_rec = gov.record_service(sid, latency_ms=ms, success=err is None, actor=str(actor.get("id") or actor.get("role") or "actor"), flow_id=flow_id, masked=auth["masked"], decision=auth["decision"])
        stages.append({"service_id": sid, "status": "success" if err is None else "failed", "latency_ms": ms, "security": auth, "circulation": flow_rec, "result": result, "error": err})
    publish([{"source_id": "service-flow", "etype": "service_flow_completed", "severity": "info", "title": "服务组合流完成：%s" % flow["name"], "detail": " → ".join(flow["service_ids"])}])
    return {"flow": flow, "actor": actor, "stages": stages, "status": "completed" if any(x["status"] == "success" for x in stages) else "blocked", "value_points": sum(x.get("circulation", {}).get("value_points", 0) for x in stages)}


# ---------------------------------------------------------------- 第四部分：安全保障与合规闭环
@app.get("/api/security/overview")
def security_overview():
    return gov.security_snapshot()


@app.post("/api/security/authorize")
async def security_authorize(request: Request):
    body = await request.json() if (await request.body()) else {}
    result = gov.authorize(_actor(body), str((body or {}).get("asset_id") or "clinical_visit"), str((body or {}).get("operation") or "query"), str((body or {}).get("purpose") or "research"), bool((body or {}).get("cross_org", False)))
    return result


@app.post("/api/security/mask-preview")
async def security_mask_preview(request: Request):
    body = await request.json() if (await request.body()) else {}
    asset_id = str((body or {}).get("asset_id") or "clinical_patient")
    raw = (body or {}).get("sample") or {"patient_id": "P0042", "name": "王伟", "phone": "13812345678", "id_card": "320102199001011234", "diagnosis_name": "2型糖尿病", "chief_complaint": "反复口渴、乏力三个月"}
    return {"asset_id": asset_id, "mode": "dynamic_masking", "before": raw, "after": gov.mask_payload(raw), "policy": gov.get_asset(asset_id)}


@app.get("/api/security/audit")
def security_audit(limit: int = 50, decision: str = ""):
    data = gov.security_snapshot()["audit"]
    if decision:
        data = [x for x in data if x["decision"] == decision]
    return {"audit": data[:max(1, min(int(limit), 200))], "total": len(data)}


@app.get("/api/security/risks")
def security_risks(status: str = ""):
    data = gov.security_snapshot()["risks"]
    if status:
        data = [x for x in data if x.get("status") == status]
    return {"risks": data, "open": sum(x.get("status") == "open" for x in data)}


@app.post("/api/security/risks/{rid}/resolve")
async def security_risk_resolve(rid: str, request: Request):
    body = await request.json() if (await request.body()) else {}
    item = gov.resolve_risk(rid, str((body or {}).get("note") or "人工复核完成，已补充授权依据"))
    if not item:
        return {"error": "风险不存在：%s" % rid}
    publish([{"source_id": "security", "etype": "security_risk_resolved", "severity": "info", "title": "安全风险已处置：%s" % rid, "detail": item.get("resolution", "")}])
    return item


# ---------------------------------------------------------------- 第二部分：编排 & 驾驶舱
@app.get("/api/orchestrator")
def orchestrator_status():
    return orch.status() if orch else {"error": "orchestration engine not initialized"}


@app.post("/api/rules/{rid}/toggle")
async def rule_toggle(rid: str):
    r = orch.toggle_rule(rid) if orch else None
    return r or {"error": "未知规则：%s" % rid}


@app.get("/api/tasks")
def tasks(limit: int = 60):
    st = orch.status() if orch else {}
    return {"tasks": (st.get("recent_tasks") or [])[:int(limit)], "counters": st.get("counters"), "queue_depth": st.get("queue_depth", 0)}


@app.post("/api/orchestrator/patrol")
async def patrol_now():
    t = orch.run_patrol(manual=True) if orch else None
    return t or {"error": "orchestration engine not initialized"}


@app.get("/api/cockpit")
def cockpit_api():
    srcs = _source_status()
    total_rows = sum(int(s["rows"] or 0) for s in srcs)
    rows_rate, pts = _hist_rate("rows_total")
    ev_now = len(BUS.events); ev_rate, _ = _hist_rate("ev")
    with SERVICE_STATS_LOCK:
        svc_calls = sum(v["calls"] for v in SERVICE_STATS.values())
        svc_ms = sum(v["total_ms"] for v in SERVICE_STATS.values())
    con_his = _con("his.db")
    patients_n = con_his.execute("SELECT COUNT(*) FROM patient").fetchone()[0]
    visits_n = con_his.execute("SELECT COUNT(*) FROM visit").fetchone()[0]
    top_diags = [dict(zip(["name", "n"], r)) for r in con_his.execute(
        "SELECT diagnosis_name, COUNT(*) n FROM visit WHERE diagnosis_name IS NOT NULL AND TRIM(diagnosis_name) != '' GROUP BY 1 ORDER BY n DESC LIMIT 8")]
    # 驾驶舱态势图：按真实业务科室聚合，不伪造地理坐标；节点大小由患者规模决定，
    # 连线表达各业务域与数据编织中枢的实时关联。
    dept_rows = list(con_his.execute(
        "SELECT COALESCE(NULLIF(TRIM(dept), ''), '未归类科室'), COUNT(*) visits, COUNT(DISTINCT patient_id) patients "
        "FROM visit GROUP BY 1 ORDER BY visits DESC"))
    dept_pids = {}
    for dept, pid in con_his.execute("SELECT COALESCE(NULLIF(TRIM(dept), ''), '未归类科室'), patient_id FROM visit"):
        dept_pids.setdefault(dept, set()).add(pid)
    con_lis = _con("lis.db")
    abn_by_pid = {r[0]: int(r[1] or 0) for r in con_lis.execute(
        "SELECT t.patient_id, SUM(CASE WHEN r.flag IN ('H','L') THEN 1 ELSE 0 END) "
        "FROM test_order t JOIN result r ON r.test_no=t.test_no GROUP BY t.patient_id")}
    con_lis.close()
    con_pacs = _con("pacs.db")
    studies_by_pid = {r[0]: int(r[1] or 0) for r in con_pacs.execute(
        "SELECT patient_id, COUNT(*) FROM study GROUP BY patient_id")}
    con_pacs.close()
    dept_distribution = []
    for dept, visits, patients in dept_rows:
        pids = dept_pids.get(dept, set())
        abnormal = sum(abn_by_pid.get(pid, 0) for pid in pids)
        studies = sum(studies_by_pid.get(pid, 0) for pid in pids)
        dept_distribution.append({"name": dept, "patients": int(patients), "visits": int(visits),
                                  "abnormal_items": abnormal, "studies": studies,
                                  "data_points": int(visits + abnormal + studies)})
    con_his.close()
    g = build_graph(engine, limit_patients=100) if engine else {"nodes": [], "edges": []}
    ev_by_type = {}
    for e in BUS.events:
        ev_by_type[e["etype"]] = ev_by_type.get(e["etype"], 0) + 1
    st = orch.status() if orch else {"counters": {}, "queue_depth": 0, "recent_tasks": [], "rules": []}
    triggered_total = sum(r["stats"]["triggered"] for r in st.get("rules", []))
    counters = st.get("counters") or {}
    history = list(pts or [])
    step = max(1, len(history) // 96)
    return {
        "ts": time.strftime("%H:%M:%S"),
        "kpis": {"sources_online": sum(1 for s in srcs if s["status"] == "online"), "source_total": len(srcs),
                 "total_rows": total_rows, "rows_per_min": rows_rate,
                 "events_total": ev_now, "ev_per_min": ev_rate,
                 "graph_nodes": len(g.get("nodes", [])), "graph_edges": len(g.get("edges", [])),
                 "patients": patients_n, "visits": visits_n},
        "sources": [{"id": s["id"], "name": s["name"], "rows": int(s["rows"] or 0)} for s in srcs],
        "history": history[::step],
        "history_origin": HISTORY_ORIGIN,
        "ev_by_type": ev_by_type,
        "top_diags": top_diags,
        "dept_distribution": dept_distribution,
        "svc_calls_total": svc_calls,
        "svc_avg_ms": round(svc_ms / svc_calls, 2) if svc_calls else None,
        "loop": {"triggered": triggered_total, "queue_depth": st.get("queue_depth", 0),
                 "tasks_done": counters.get("done", 0), "success_rate": counters.get("success_rate"),
                 "svc_calls": svc_calls, "feedback_events": ev_by_type.get("orchestrated_action", 0)},
        "recent_tasks": (st.get("recent_tasks") or [])[:6],
    }


# ---------------------------------------------------------------- 静态前端
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))
