# -*- coding: utf-8 -*-
"""virtualization.py — 数据虚拟化与逻辑集成（编织层“零拷贝联邦访问”）。

核心思想：不搬迁、不复制物理数据；在发现引擎纳管的元数据之上构建逻辑视图，
由统一查询接口按需路由到各源执行取数（Python 侧联邦），并附来源溯源与耗时。
"""
import time


class VirtualLayer:
    def __init__(self, engine):
        self.engine = engine   # DiscoveryEngine：复用其 adapters

    @property
    def A(self):
        return self.engine.adapters

    # ---------------- 逻辑视图目录（由语义映射推导，非硬编码数据）----------------
    def logical_views(self):
        from semantics import FIELD_MAP, ENTITY_TYPES
        by_entity = {}
        for src, table, col, ent, attr in FIELD_MAP:
            by_entity.setdefault(ent, {"entity": ent, "label": ENTITY_TYPES[ent]["label"],
                                       "sources": set(), "columns": 0})
            by_entity[ent]["sources"].add(src)
            by_entity[ent]["columns"] += 1
        views = []
        for ent, v in by_entity.items():
            phys = sorted({"%s.%s" % (src, table) for src, table, col, e_, _a in FIELD_MAP if e_ == ent})
            views.append({**v, "sources": sorted(v["sources"]), "physical_tables": phys})
        return views

    # ---------------- 患者360°：跨系统联邦查询（零拷贝）----------------
    def patient_360(self, pid):
        A = self.A
        sections = []
        t_all = time.perf_counter()

        def section(title, source_id, physical, rows, extra=None):
            sec = {"title": title, "source_name": next(s["name"] for s in _src_defs() if s["id"] == source_id),
                   "physical": physical, "mode": "虚拟视图·零拷贝", "latency_ms": 0.0}
            if extra is not None:
                sec.update(extra)
            sections.append(sec)

        def timed(fetch):
            t0 = time.perf_counter()
            rows = fetch()
            return rows, round((time.perf_counter() - t0) * 1000, 2)

        his_a = A["his"]
        pat_rows, ms = timed(lambda: list(_con("his.db").execute(
            "SELECT patient_id,name,gender,birth_date,id_card,insurance_type FROM patient WHERE patient_id=?", (pid,))))
        section("基本信息", "his", "HIS.patient（Oracle 模拟）", None)
        sections[-1].update({"latency_ms": ms,
                             "rows": [dict(zip(["patient_id","name","gender","birth_date","id_card_masked","insurance_type"], r)) for r in pat_rows]})

        v_rows, ms = timed(lambda: his_a.visits_by_pid(pid))
        visits_out = []
        for v in v_rows[:6]:
            o_rows, _o_ms = timed(lambda vid=v["visit_id"]: his_a.orders_by_visit(vid))
            visits_out.append({**v, "orders": o_rows[:8]})
        section("就诊与医嘱", "his", "HIS.visit ⋈ HIS.orders（逻辑关联，未物理集中）", None)
        sections[-1].update({"latency_ms": ms, "rows": visits_out})

        l_rows, ms = timed(lambda: A["lis"].tests_by_pid(pid))
        section("检验结果", "lis", "LIS.test_order ⋈ LIS.result（联邦取数）", None)
        sections[-1].update({"latency_ms": ms, "rows": l_rows[:5],
                             "abnormal_items": sum(1 for t in l_rows for it in t["items"] if it.get("flag"))})

        s_rows, ms = timed(lambda: A["pacs"].studies_by_pid(pid))
        section("影像检查", "pacs", "PACS.study 索引（DICOM 文件保持原位）", None)
        sections[-1].update({"latency_ms": ms, "rows": s_rows[:5]})

        vit_rows, ms = timed(lambda: A["iot"].vitals(pid, limit=24))
        stats = {}
        if vit_rows:
            hrs = [r["hr"] for r in vit_rows if r.get("hr") is not None]
            spo = [r["spo2"] for r in vit_rows if r.get("spo2") is not None]
            stats = {"avg_hr": round(sum(hrs) / len(hrs), 1) if hrs else None,
                     "max_hr": max(hrs) if hrs else None,
                     "min_spo2": min(spo) if spo else None}
        section("生命体征趋势（近实时）", "iot", "IoT.vitals 流式时序（边缘网关直读）", None)
        sections[-1].update({"latency_ms": ms, "rows": vit_rows, "stats": stats})

        e_doc, ms = timed(lambda: A["emr"].read_dossier(pid))
        section("病历文书", "emr", "EMR.dossiers/%s.json（文档库按需读取）" % pid, None)
        sections[-1].update({"latency_ms": ms, "rows": [e_doc] if e_doc else [],
                             "note": "" if e_doc else "该患者暂无电子病历文书登记"})

        codes = []
        for v in (v_rows or [])[:6]:
            codes.append(v.get("code"))
        from semantics import tag_diagnoses, lab_item_term
        return {
            "patient_id": pid,
            "sections": sections,
            "semantic_tags": tag_diagnoses(codes),
            "total_latency_ms": round((time.perf_counter() - t_all) * 1000, 2),
            "zero_copy": True,
        }

    # ---------------- 跨源检索（诊断术语 / 检验项目）----------------
    def search(self, q):
        A = self.A
        import medsim as _m
        qq = str(q).strip()
        diag_hits = A["his"].search_diag(qq) if len(qq) >= 1 else []
        lab_terms = [{"code": c, "name": n, "unit": u} for c, n, u, lo, hi in _m.LAB_ITEMS
                     if qq and (qq.upper() == c or qq in n)]
        return {"diagnosis": diag_hits[:15], "lab_terms": lab_terms[:8]}

def _src_defs():
    import medsim
    return medsim.SOURCE_DEFS


def _con(dbname):
    import sqlite3, os, medsim
    con = sqlite3.connect(os.path.join(medsim.DATA_DIR, dbname))
    return con
