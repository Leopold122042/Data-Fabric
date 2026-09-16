# -*- coding: utf-8 -*-
"""discovery.py — 主动元数据发现引擎（架构模型之“编织层”核心驱动）。

职责：
1) SourceAdapter：对五类异构源的统一纳管接口——快照(snapshot)、变更摘要(summarize)、只读访问；
2) DiscoveryEngine：周期性重扫各源，做结构/内容差异比对，产出元数据事件流：
     catalog_built / schema_change / data_update / quality_anomaly / semantic_link_discovered
3) 跨系统身份对齐追踪（patient_id 在 HIS/LIS/PACS/EMR/IoT 中的出现集合变化）；
4) EventBus：线程安全的事件总线，供 REST 与 SSE 推送。

主动元数据 = “持续感知 + 自动触发动作”：本引擎即“感知”一环，事件触发目录更新、图谱重建与服务告警。
"""
import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime

import medsim

# ---------------------------------------------------------------- 事件总线
class EventBus:
    def __init__(self, maxlen=800):
        self.maxlen = int(maxlen)
        self.lock = threading.Lock()
        self.events = []          # [{seq, ts, source_id, etype, severity, title, detail}]
        self.seq = 0

    def publish(self, evs):
        out = []
        with self.lock:
            for e in evs or []:
                self.seq += 1
                ev = dict(e)
                ev["seq"] = self.seq
                ev.setdefault("ts", datetime.now().strftime("%H:%M:%S"))
                self.events.append(ev)
                out.append(ev)
            if len(self.events) > self.maxlen:
                self.events = self.events[-self.maxlen:]
        return out

    def since(self, after_seq=0, limit=300):
        with self.lock:
            evs = [e for e in self.events if e["seq"] > int(after_seq or 0)]
            return evs[:int(limit or 300)]


# ---------------------------------------------------------------- 源适配器
class _SqliteAdapterBase:
    kind = "rdbms"

    def __init__(self, source_id, dbname):
        self.source_id = source_id
        self.dbname = dbname

    def _con(self):
        con = sqlite3.connect(os.path.join(medsim.DATA_DIR, self.dbname))
        return con

    def snapshot(self):
        """返回 {tables: {table: {columns:[..], rows:int, hash:str}}}"""
        tables = {}
        con = self._con()
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in sorted(names):
            cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
            rows = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            h = hashlib.md5(("%s|%s" % (t, ",".join(cols))).encode("utf-8")).hexdigest()[:12]
            tables[t] = {"columns": cols, "rows": rows, "hash": h}
        con.close()
        return {"tables": tables}

    def summarize(self, table, before_rows):
        """针对行数增长生成人类可读摘要（供 data_update 事件）"""
        try:
            if self.source_id == "his" and table == "patient":
                r = list(self._con().execute("SELECT patient_id,name FROM patient ORDER BY rowid DESC LIMIT 1"))[0]
                return "%s %s 新建档" % (r[0], r[1])
            if self.source_id == "his" and table == "visit":
                con = self._con()
                r = list(con.execute("SELECT patient_id,dept,diagnosis_name FROM visit ORDER BY visit_id DESC LIMIT 1"))[0]
                return "新增就诊 %s · %s（%s）" % (r[0], r[1], r[2])
            if self.source_id == "lis" and table == "test_order":
                con = self._con()
                r = list(con.execute("SELECT test_no,patient_id FROM test_order ORDER BY rowid DESC LIMIT 1"))[0]
                return "%s %s 检验单已回报" % (r[0], r[1])
            if self.source_id == "pacs" and table == "study":
                con = self._con()
                r = list(con.execute("SELECT patient_id,modality,body_part FROM study ORDER BY rowid DESC LIMIT 1"))[0]
                return "新增 %s 影像检查：%s/%s" % (r[0], r[1], r[2])
            return "%s +行" % table
        except Exception:
            return "%s 数据更新" % table

    # ---- 只读访问（供虚拟化层零拷贝取数）----
    def patient(self, pid):
        con = self._con()
        r = list(con.execute("SELECT * FROM patient WHERE patient_id=?", (pid,))) if "his.db" == self.dbname else []
        con.close()
        return r[0] if r else None

    def visits_by_pid(self, pid):
        con = self._con()
        rows = list(con.execute("SELECT visit_id,vtype,dept,doctor,admission_time,diagnosis_code,diagnosis_name FROM visit WHERE patient_id=? ORDER BY admission_time DESC", (pid,))) if "his.db" == self.dbname else []
        out = [dict(zip(["visit_id","vtype","dept","doctor","time","code","diag"], r)) for r in rows]
        con.close()
        return out

    def orders_by_visit(self, visit_id):
        con = self._con()
        rows = list(con.execute("SELECT otype,item_name,status FROM orders WHERE visit_id=? ORDER BY order_id", (visit_id,))) if "his.db" == self.dbname else []
        con.close()
        return [dict(zip(["otype","item","status"], r)) for r in rows]

    def tests_by_pid(self, pid):
        con = sqlite3.connect(os.path.join(medsim.DATA_DIR, "lis.db"))
        rows = list(con.execute("SELECT test_no,sample_type,collect_time FROM test_order WHERE patient_id=? ORDER BY collect_time DESC", (pid,)))
        out = []
        for tno, sample, ct in rows:
            items = [dict(zip(["code","name","value","unit","ref","flag"], r)) for r in con.execute(
                "SELECT item_code,item_name,value_num,unit,ref_range,flag FROM result WHERE test_no=?", (tno,))]
            out.append({"test_no": tno, "sample_type": sample, "collect_time": ct, "items": items})
        con.close()
        return out

    def studies_by_pid(self, pid):
        con = sqlite3.connect(os.path.join(medsim.DATA_DIR, "pacs.db"))
        rows = list(con.execute("SELECT study_uid,modality,body_part,exam_date,files,size_mb,report_summary FROM study WHERE patient_id=? ORDER BY exam_date DESC", (pid,)))
        con.close()
        return [dict(zip(["uid","modality","body_part","date","files","size_mb","report"], r)) for r in rows]

    def vitals(self, pid, limit=30):
        con = sqlite3.connect(os.path.join(medsim.DATA_DIR, "iot.db"))
        rows = list(con.execute("SELECT ts,heart_rate,spo2,temp FROM vitals WHERE patient_id=? ORDER BY vital_id DESC LIMIT ?", (pid, int(limit))))
        con.close()
        rows.reverse()
        return [dict(zip(["ts","hr","spo2","temp"], r)) for r in rows]

    def search_diag(self, q):
        con = sqlite3.connect(os.path.join(medsim.DATA_DIR, "his.db"))
        like = "%" + q.strip() + "%"
        rows = list(con.execute(
            """SELECT p.patient_id,p.name,v.diagnosis_code,v.diagnosis_name,COUNT(*),MAX(v.admission_time)
               FROM visit v JOIN patient p ON p.patient_id=v.patient_id
               WHERE v.diagnosis_name LIKE ? OR v.diagnosis_code LIKE ? GROUP BY p.patient_id ORDER BY MAX(v.admission_time) DESC LIMIT 30""", (like, like)))
        con.close()
        return [dict(zip(["pid","name","code","diag","count","last"], r)) for r in rows]


class EmrAdapter:
    kind = "document"

    def __init__(self, source_id):
        self.source_id = source_id

    @property
    def dir(self):
        return os.path.join(medsim.DATA_DIR, "emr")

    def snapshot(self):
        files = [f for f in os.listdir(self.dir) if f.endswith(".json")] if os.path.isdir(self.dir) else []
        keys, samples = set(), {}
        sample_n = 0
        for fn in sorted(files)[-40:]:   # 抽样推断文档模式（大目录时不全量读）
            try:
                doc = json.load(open(os.path.join(self.dir, fn), encoding="utf-8"))
            except Exception:
                continue
            sample_n += 1
            for k in doc.keys():
                keys.add(k)
                if k not in samples and isinstance(doc[k], (str, int, float)):
                    samples[k] = str(doc[k])[:40]
        h = hashlib.md5(("emr|" + ",".join(sorted(keys))).encode("utf-8")).hexdigest()[:12]
        return {"tables": {"dossier(病历文书)": {
            "columns": sorted(keys), "rows": len(files), "hash": h, "_samples": samples}}}

    def summarize(self, table, before_rows):
        files = [f for f in os.listdir(self.dir) if f.endswith(".json")]
        return "%d 份病历文书（最新 %s）" % (len(files), sorted(files)[-1][:-5] + " 落库")

    def read_dossier(self, pid):
        path = os.path.join(self.dir, pid + ".json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        # 敏感字段脱敏展示：隐藏 id_card（若存在）等；此处保留临床必要信息
        for k in ("id_card", "phone"):
            if k in doc:
                doc.pop(k, None)
        return doc


def _build_adapters():
    ad = {}
    for src in medsim.SOURCE_DEFS:
        sid = src["id"]
        if sid == "his":
            ad[sid] = _SqliteAdapterBase(sid, "his.db")
        elif sid == "lis":
            a = _SqliteAdapterBase(sid, "lis.db"); ad[sid] = a
        elif sid == "pacs":
            a = _SqliteAdapterBase(sid, "pacs.db"); ad[sid] = a
        elif sid == "iot":
            a = _SqliteAdapterBase(sid, "iot.db"); ad[sid] = a
        else:
            ad[sid] = EmrAdapter(sid)
    return ad


# ---------------------------------------------------------------- 发现引擎
class DiscoveryEngine:
    def __init__(self):
        self.adapters = _build_adapters()
        self.last = {}                 # sid -> snapshot
        self.stats = {s["id"]: {"tables": 0, "rows": 0, "last_scan": None} for s in medsim.SOURCE_DEFS}
        self.identity_sets = {}        # pid -> set(source_id)
        self.linked = set()            # 已对齐（发过事件或基线）的pid
        self.bootstrapped = False
        self._anomaly_cooldown = {}    # (pid, ts分钟级key) -> True

    def _pids_in_source(self, sid):
        a = self.adapters[sid]
        con_db = {"his": "his.db", "lis": "lis.db", "pacs": "pacs.db", "iot": "iot.db"}.get(sid)
        if not con_db:
            return set()
        con = sqlite3.connect(os.path.join(medsim.DATA_DIR, con_db))
        col_tab = {"his": ("patient", "patient_id"), "lis": ("test_order", "patient_id"),
                   "pacs": ("study", "patient_id"), "iot": ("vitals", "patient_id")}[sid]
        rows = [r[0] for r in con.execute("SELECT DISTINCT %s FROM %s" % (col_tab[1], col_tab[0]))]
        con.close()
        return set(rows)

    def _identity_pass(self, events):
        """跨系统身份对齐：pid 首次出现在 >=2 个源时发出 semantic_link_discovered"""
        all_sets = {}
        for sid in ("his", "lis", "pacs", "iot"):
            sids_ = self._pids_in_source(sid)
            for pid in sids_:
                all_sets.setdefault(pid, set()).add(sid)
        if os.path.isdir(EmrAdapter("emr").dir):
            for fn in os.listdir(EmrAdapter("emr").dir):
                if fn.endswith(".json"):
                    all_sets.setdefault(fn[:-5], set()).add("emr")

        new_multi = [pid for pid, ss in all_sets.items() if len(ss) >= 2 and pid not in self.linked]
        for pid in sorted(new_multi):
            sources = sorted(all_sets[pid])
            name_row = None
            try:
                con = sqlite3.connect(os.path.join(medsim.DATA_DIR, "his.db"))
                r = list(con.execute("SELECT name FROM patient WHERE patient_id=?", (pid,)))
                if r:
                    name_row = r[0][0]
                con.close()
            except Exception:
                pass
            title = "%s（%s）完成跨系统身份对齐" % (name_row, pid) if name_row else "患者%s 完成跨系统身份对齐" % pid
            detail = "该患者元数据已在以下数据源间建立关联：%s（共%d个）" % ("、".join(sources), len(sources))
            events.append({"source_id": "-", "etype": "semantic_link_discovered", "severity": "important",
                           "title": title, "detail": detail})
        for pid in all_sets:
            self.linked.add(pid) if len(all_sets[pid]) >= 2 else None

    def scan_source(self, sid):
        a = self.adapters[sid]
        events = []
        snap = a.snapshot()
        prev = self.last.get(sid)
        tinfo = snap["tables"]
        total_rows = sum(v["rows"] for v in tinfo.values())

        if not self.bootstrapped and sid == "his":
            pass  # 基线阶段静默建立身份集合，避免首扫事件风暴（在 scan_all 中处理）

        if prev is None:
            desc = "、".join("%s(%d行)" % (t, v["rows"]) for t, v in sorted(tinfo.items()))
            events.append({"source_id": sid, "etype": "catalog_built", "severity": "info",
                           "title": "%s 首次纳管，元数据目录已建立" % next(s["name"] for s in medsim.SOURCE_DEFS if s["id"] == sid),
                           "detail": desc})
        else:
            prev_t = prev["tables"]
            # schema change（新增表/列）
            for t, v in tinfo.items():
                pt = prev_t.get(t)
                if pt is None:
                    events.append({"source_id": sid, "etype": "schema_change", "severity": "warn",
                                   "title": "%s 检测到新逻辑表 %s" % (sid.upper(), t),
                                   "detail": "列结构：%s；当前%d行。目录与语义映射已自动更新。" % ("、".join(v["columns"]), v["rows"])})
                elif pt.get("hash") != v.get("hash"):
                    added = [c for c in v["columns"] if c not in prev_t[t]["columns"]]
                    events.append({"source_id": sid, "etype": "schema_change", "severity": "warn",
                                   "title": "%s.%s 结构变更" % (sid.upper(), t),
                                   "detail": ("新增列：" + "、".join(added)) if added else "字段定义/统计特征变化，已触发目录刷新。"})
            # data update（行数增长）
            for t, v in tinfo.items():
                pt = prev_t.get(t)
                if pt and v["rows"] > pt["rows"]:
                    events.append({"source_id": sid, "etype": "data_update", "severity": "info",
                                   "title": "%s.%s 增量 %d 行" % (sid.upper(), t, v["rows"] - pt["rows"]),
                                   "detail": a.summarize(t, pt["rows"])})

        # IoT 质量异常检查（主动元数据 → 自动触发动作）
        if sid == "iot":
            con = sqlite3.connect(os.path.join(medsim.DATA_DIR, "iot.db"))
            rows = list(con.execute("SELECT patient_id,ts,heart_rate,spo2 FROM vitals ORDER BY vital_id DESC LIMIT 40"))
            con.close()
            for pid, ts_, hr, spo in reversed(rows):
                if hr >= 125 or (spo is not None and spo <= 93.5):
                    key = "%s:%d" % (pid, int(datetime.now().timestamp()) // 60)
                    if self._anomaly_cooldown.get(key):
                        continue
                    self._anomaly_cooldown[key] = True
                    if len(self._anomaly_cooldown) > 512:   # 简易清理
                        for k in list(self._anomaly_cooldown)[:200]:
                            del self._anomaly_cooldown[k]
                    events.append({"source_id": "iot", "etype": "quality_anomaly", "severity": "warn",
                                   "title": "%s 生命体征异常波动" % pid,
                                   "detail": "心率 %.0f bpm、SpO₂ %.1f%%（%s）——已触发告警并建议核查监护设备与临床状态。" % (hr or -1, spo if spo is not None else -1, ts_)})

        self.last[sid] = snap
        st = self.stats[sid]
        st["tables"] = len(tinfo)
        st["rows"] = total_rows
        st["last_scan"] = datetime.now().strftime("%H:%M:%S")
        return events

    def scan_all(self):
        events = []
        for src in medsim.SOURCE_DEFS:
            try:
                events.extend(self.scan_source(src["id"]))
            except Exception as exc:   # 单源故障不影响整体（体现容错纳管）
                self.stats[src["id"]]["last_scan"] = datetime.now().strftime("%H:%M:%S") + "(失败)"
                if not getattr(DiscoveryEngine, "_fail_logged", False):
                    DiscoveryEngine._fail_logged = True
                    events.append({"source_id": src["id"], "etype": "schema_change", "severity": "warn",
                                   "title": "%s 扫描异常" % src["name"], "detail": str(exc)[:120]})
        if not self.bootstrapped:
            # 基线：静默记录已对齐身份，不发事件风暴
            silent = []
            self._identity_pass(silent)   # 仅填充 linked，不发事件风暴
        else:
            self._identity_pass(events)
        self.bootstrapped = True
        return events


ENGINE = None          # app.py 中初始化单例
BUS = EventBus()
