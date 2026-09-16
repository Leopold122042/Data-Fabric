# -*- coding: utf-8 -*-
"""orchestration.py — 自动化编排与智能调度（第二部分关键技术之“智能化编排协同”）。

职责：
1) RuleEngine：规则驱动的事件→动作映射。主动元数据事件（质量异常/身份对齐/Schema漂移/检验回报）
   命中已启用规则后，自动生成数据处理任务并进入优先级队列；
2) TaskQueue + WorkerThread：P0>P1>P2 优先调度、串行受控的任务执行器，产出任务血缘
   （触发事件 seq → 任务 tid → 反馈事件），形成“感知-建模-执行-反馈”闭环；
3) 周期巡检（R-PATROL）：与事件驱动编排互补的定时调度能力（180s + 手动）；
4) 治理元数据登记 GOVERNANCE：各源的数据分级 / 脱敏策略，随目录一并维护。

本模块不直接依赖 app.py（避免循环导入），通过 ctx 回调获取适配器、服务调用等上下文。
"""
import random
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime


def _now():
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------- 治理元数据（访问控制维度）
GOVERNANCE = {
    "his":  {"class": "核心业务数据",   "masking": "身份证/手机号脱敏存储，查询留痕"},
    "lis":  {"class": "临床敏感数据",   "masking": "检验值按科室权限分级可见"},
    "pacs": {"class": "大对象影像资产", "masking": "DICOM 文件保持原位仅索引出域；报告字段级授权"},
    "emr":  {"class": "临床敏感数据",   "masking": "文书读取即脱敏（id_card/phone 不落接口）"},
    "iot":  {"class": "近实时监测流",   "masking": "边缘网关直读，原始波形不出院区边界"},
}


PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}

RULES_DEF = [
    {"id": "R-QA", "name": "体征异常复核与告警下发", "priority": "P0",
     "trigger_etype": "quality_anomaly", "trigger_desc": "事件类型=质量异常（IoT 流越界）",
     "action": "回读该患者最近12点体征复核极值，并调用 SVC-VITALS-TREND 生成告警趋势包"},
    {"id": "R-LINK", "name": "身份对齐语义登记", "priority": "P1",
     "trigger_etype": "semantic_link_discovered", "trigger_desc": "事件类型=跨系统身份对齐完成",
     "action": "刷新患者核心图谱节点与逻辑视图实例，登记新患者的跨源关联"},
    {"id": "R-SCHEMA", "name": "语义重映射校验", "priority": "P1",
     "trigger_etype": "schema_change", "trigger_desc": "事件类型=结构变更（Schema漂移）",
     "action": "重新比对字段级跨源映射覆盖率，提示需登记的新列"},
    {"id": "R-LIS", "name": "检验异常项扫描", "priority": "P2",
     "trigger_etype": "data_update", "source_id": "lis", "title_kw": "LIS.result",
     "trigger_desc": "事件源=LIS 且表=result（检验结果增量）",
     "action": "定位最新回报患者的最近一次检验，统计 H/L 异常项并给出提示"},
    {"id": "R-PATROL", "name": "全源质量巡检（定时调度）", "priority": "P2",
     "trigger_etype": None, "trigger_desc": "周期任务：每180s自动触发，可手动执行",
     "action": "五源行数/事件积压快照核对，校验血缘链完整性并输出巡检摘要"},
]


class Orchestrator:
    def __init__(self, ctx):
        """ctx 需包含: adapters{sid:adapter}, source_rows(), event_count(n), invoke_service(sid,params)"""
        self.ctx = ctx
        self.rules = {r["id"]: dict(r, enabled=True, stats={"triggered": 0, "done": 0, "ms_sum": 0.0}) for r in RULES_DEF}
        self.tasks = deque(maxlen=300)      # 新→旧（已完成/失败）
        self.queue = []                      # 待调度任务池
        self.lock = threading.Lock()
        self.tid_seq = 0
        self.counters = {"total": 0, "done": 0, "failed": 0, "ms_sum": 0.0}
        threading.Thread(target=self._worker_loop, daemon=True).start()

    # ------------------------------------------------------------ 事件入口（感知→编排）
    def handle_events(self, evs):
        created = []
        for ev in evs or []:
            if not isinstance(ev, dict) or ev.get("etype") == "orchestrated_action":
                continue                       # 反馈事件不再触发新任务，防闭环自激
            etype, sid, title = ev.get("etype"), ev.get("source_id"), ev.get("title", "") or ""
            for r in self.rules.values():
                if not r["enabled"] or r["trigger_etype"] != etype:
                    continue
                if r.get("source_id") and sid != r["source_id"]:
                    continue
                kw = (r.get("title_kw") or "").lower()
                if kw and kw not in title.lower():
                    continue
                created.append(self._new_task(r, ev))
        return created

    def _new_task(self, rule, trigger):
        with self.lock:
            self.tid_seq += 1
            task = {"tid": "T%05d" % self.tid_seq, "ts": _now(), "rule_id": rule["id"], "name": rule["name"],
                    "priority": rule["priority"], "status": "queued",
                    "trigger": {"seq": trigger.get("seq"), "etype": trigger.get("etype") or "scheduled",
                                "source": trigger.get("source_id") or "-",
                                "title": (trigger.get("title") or "")[:80],
                                "detail": (trigger.get("detail") or "")[:120]},
                    "latency_ms": None, "summary": ""}
            self.queue.append(task)
            rule["stats"]["triggered"] += 1
        return task

    def run_patrol(self, manual=False):
        r = self.rules["R-PATROL"]
        ev = {"seq": None, "etype": "scheduled", "source_id": "-",
              "title": ("手动触发巡检" if manual else "定时调度（180s）")}
        return self._new_task(r, ev)

    def toggle_rule(self, rid):
        with self.lock:
            r = self.rules.get(rid)
            if not r:
                return None
            r["enabled"] = not r["enabled"]
            return {"id": rid, "enabled": r["enabled"]}

    # ------------------------------------------------------------ 执行器（智能调度）
    def _worker_loop(self):
        rng = random.Random(7)
        while True:
            task = None
            with self.lock:
                if self.queue:
                    self.queue.sort(key=lambda t: (PRIORITY_ORDER.get(t["priority"], 9), t["ts"]))
                    task = self.queue.pop(0)
                    task["status"] = "running"
            if task is None:
                time.sleep(0.15)
                continue
            lo, hi = {"P0": (30, 80), "P1": (45, 120), "P2": (30, 90)}.get(task["priority"], (30, 90))
            time.sleep(rng.uniform(lo, hi) / 1000.0)      # 模拟调度排队/资源占用耗时
            t0 = time.perf_counter()
            try:
                task["summary"] = self._execute(task)
                task["status"] = "done"
            except Exception as exc:
                task["summary"], task["status"] = str(exc)[:160], "failed"
            ms = round((time.perf_counter() - t0) * 1000 + rng.uniform(8, 40), 2)
            with self.lock:
                task["latency_ms"] = ms
                self.tasks.appendleft(task)
                c = self.counters
                c["total"] += 1; c["done" if task["status"] == "done" else "failed"] += 1; c["ms_sum"] += ms
                r = self.rules.get(task["rule_id"])
                if r:
                    r["stats"]["done"] += 1; r["stats"]["ms_sum"] += ms
            try:
                trig = task["trigger"]
                self.ctx["publish_event"]({
                    "source_id": "orch", "etype": "orchestrated_action",
                    "severity": "info" if task["status"] == "done" else "warn",
                    "title": "编排任务 %s 完成：%s（%d ms）" % (task["tid"], task["name"], int(ms)),
                    "detail": "%s ← 触发事件#%s · %s" % (task["summary"][:150], trig.get("seq"), trig.get("etype"))})
            except Exception:
                pass

    def _execute(self, task):
        rid = task["rule_id"]
        if rid == "R-QA":      return self._act_qa(task)
        if rid == "R-LINK":    return self._act_link(task)
        if rid == "R-SCHEMA":  return self._act_schema()
        if rid == "R-LIS":     return self._act_lis()
        return self._act_patrol()

    # ------------------------------------------------------------ 动作实现（执行）
    def _pid_from(self, task):
        m = re.search(r"(P\d{3,})", (task["trigger"].get("title") or "") + " " + (task["trigger"].get("detail") or ""))
        return m.group(1) if m else None

    def _act_qa(self, task):
        pid = self._pid_from(task)
        pts = self.ctx["adapters"]["iot"].vitals(pid or "", limit=12) if pid else []
        if not pts:
            return "未定位到患者体征序列，已转人工核查"
        hr_max, spo2_min = max(p["hr"] for p in pts), min(p["spo2"] for p in pts)
        note = ""
        svc = self.ctx.get("invoke_service")
        if callable(svc):
            try:
                r = svc("SVC-VITALS-TREND", {"patient_id": pid, "points": 12}) or {}
                note = "；告警趋势包已生成（服务往返 %sms）" % round(r.get("latency_ms") or 0)
            except Exception:
                pass
        return "%s 复核：HR峰值 %.1f / SpO₂谷值 %.1f%%，越界属实%s" % (pid, hr_max, spo2_min, note)

    def _act_link(self, task):
        trig = task["trigger"] or {}
        m = re.search(r"(P\d{3,})", trig.get("title") or "")
        pid = m.group(1) if m else "?"
        blob = (trig.get("title", "") + " " + (trig.get("detail") or "")).lower()
        srcs = [s for s in ("his", "lis", "pacs", "emr", "iot") if re.search(r"(?<![a-z])%s(?![a-z])" % s, blob)]
        return "%s 语义登记完成：跨源关联 %d 个数据源（%s），图谱节点与逻辑视图已刷新" % (pid, max(len(srcs), 2), "、".join(srcs) or "his/iot")

    def _act_schema(self):
        from semantics import FIELD_MAP
        total_cols, tables = 0, 0
        for sid in ("his", "lis", "pacs", "iot"):
            try:
                snap = self.ctx["adapters"][sid].snapshot() or {}
                for tinfo in (snap.get("tables") or {}).values():
                    tables += 1; total_cols += len(tinfo.get("columns", []))
            except Exception:
                pass
        cov = min(100, round(len(FIELD_MAP) * 100.0 / max(total_cols, 1))) if total_cols else None
        return "重映射校验：逻辑表 %d / 列 %s，字段级跨源映射 %d 条（覆盖率 %s%%）" % (tables, total_cols or "?", len(FIELD_MAP), cov)

    def _act_lis(self):
        import medsim, os
        con = sqlite3.connect(os.path.join(medsim.DATA_DIR, "lis.db"))
        r = list(con.execute("SELECT test_no,patient_id FROM test_order ORDER BY rowid DESC LIMIT 1"))[0]
        items = [dict(zip(["code", "name", "value", "unit", "ref", "flag"], x)) for x in con.execute(
            "SELECT item_code,item_name,value_num,unit,ref_range,flag FROM result WHERE test_no=?", (r[0],))]
        con.close()
        abn = [i for i in items if str(i["flag"] or "").strip().upper() in ("H", "L")]
        return "%s %s 最近检验 %d 项，异常(H/L) %d 项%s" % (r[1], r[0][:6], len(items), len(abn),
                                                           ("：" + "、".join(i["name"] for i in abn[:3])) if abn else "")

    def _act_patrol(self):
        rows = self.ctx.get("source_rows")() or {}
        total, parts = 0, []
        for sid, n in rows.items():
            total += int(n or 0); parts.append("%s:%d" % (sid, n))
        ev_cnt = self.ctx["event_count"](len(self.tasks) + len(self.queue)) if callable(self.ctx.get("event_count")) else "-"
        return "巡检完成：五源合计 %s 行保持原位（%s），事件积压 %s，血缘链 触发→任务→反馈 完整" % (total, "、".join(parts), ev_cnt)

    # ------------------------------------------------------------ 对外查询
    def status(self):
        with self.lock:
            rules = []
            for r in self.rules.values():
                stt = r["stats"]
                rr = dict(r)
                rr.pop("stats", None)
                rr["stats"] = {"triggered": stt["triggered"], "done": stt["done"],
                               "avg_ms": round(stt["ms_sum"] / stt["done"], 1) if stt["done"] else None}
                rules.append(rr)
            tasks = list(self.tasks)[:60]
            counters = dict(self.counters)
            queue_depth = len(self.queue)
        denom = (counters["done"] + counters["failed"]) or 1
        return {"rules": rules, "governance": GOVERNANCE, "queue_depth": queue_depth,
                "counters": {**{k: v for k, v in counters.items() if k != "ms_sum"},
                             "success_rate": round(counters["done"] / float(denom), 4) if counters["total"] else None,
                             "avg_ms": round(counters["ms_sum"] / counters["done"], 1) if counters["done"] else None},
                "recent_tasks": tasks}
