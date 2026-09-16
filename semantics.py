# -*- coding: utf-8 -*-
"""semantics.py — 统一语义建模与知识图谱（编织层“语义”能力）。

基于发现引擎产出的物理元数据，通过规则映射表 FIELD_MAP 做跨源统一语义描述：
  - ENTITY_TYPES : 实体类型定义（患者/就诊/检验/影像/体征序列/病历文书…）
  - FIELD_MAP    : source.table.column -> (entity, attribute) —— 多源异构字段的语义对齐
  - TERMINOLOGY : ICD-10 诊断术语表 + LIS 检验项目词表，实现业务语义标注（主动元数据驱动的自动打标）
  - build_graph(): 以患者为核心的知识图谱构建（实例数限幅以便可视化），边带置信度

体现“统一语义建模与逻辑关联机制”：不改动任何物理库，仅建立语义层与身份键的逻辑关联。
"""
import json
import os
import sqlite3

import medsim

ENTITY_TYPES = {
    "patient":       {"label": "患者",   "color": "#22d3ee", "desc": "患者主索引（跨系统身份主键 patient_id）"},
    "visit":         {"label": "就诊",   "color": "#2dd4bf", "desc": "门诊/住院/急诊就诊事件，含 ICD-10 诊断"},
    "lab_test":      {"label": "检验单", "color": "#fbbf24", "desc": "LIS 申请单 + 结果明细（标本、项目、参考区间）"},
    "imaging_study": {"label": "影像检查","color": "#a78bfa", "desc": "PACS DICOM 研究索引与报告摘要"},
    "vitals_series": {"label": "体征序列","color": "#fb7185", "desc": "IoT 监护设备生命体征时间序列（流式）"},
    "emr_doc":       {"label": "病历文书","color": "#94a3b8", "desc": "EMR 半结构化诊疗文书（JSON 文档）"},
}

# (source, table, column) -> (entity_type, attribute_label)
FIELD_MAP = [
    ("his", "patient", "patient_id",   "patient", "患者ID"),
    ("his", "patient", "name",         "patient", "姓名"),
    ("his", "patient", "gender",       "patient", "性别"),
    ("his", "patient", "birth_date",   "patient", "出生日期(脱敏)"),
    ("his", "patient", "id_card",      "patient", "证件号(脱敏)"),
    ("his", "patient", "insurance_type","patient", "参保类型"),
    ("his", "visit", "visit_id",       "visit",   "就诊ID"),
    ("his", "visit", "vtype",          "visit",   "门诊/住院"),
    ("his", "visit", "dept",           "visit",   "科室"),
    ("his", "visit", "doctor",         "visit",   "接诊医生"),
    ("his", "visit", "admission_time", "visit",   "就诊时间"),
    ("his", "visit", "diagnosis_code", "visit",   "ICD-10编码"),
    ("his", "visit", "diagnosis_name", "visit",   "诊断名称"),
    ("lis", "test_order", "test_no",      "lab_test", "检验单号"),
    ("lis", "test_order", "sample_type",  "lab_test", "标本类型"),
    ("lis", "test_order", "collect_time", "lab_test", "采集时间"),
    ("lis", "result",   "item_code",      "lab_test", "项目代码"),
    ("lis", "result",   "item_name",      "lab_test", "项目名称"),
    ("lis", "result",   "value_num",      "lab_test", "结果值"),
    ("lis", "result",   "unit",           "lab_test", "单位"),
    ("lis", "result",   "ref_range",      "lab_test", "参考区间"),
    ("pacs","study", "study_uid",     "imaging_study", "DICOM UID"),
    ("pacs","study", "modality",      "imaging_study", "模态(CT/MR/DR/US)"),
    ("pacs","study", "body_part",     "imaging_study", "检查部位"),
    ("pacs","study", "exam_date",     "imaging_study", "检查日期"),
    ("pacs","study", "files",         "imaging_study", "影像文件数"),
    ("iot", "vitals", "ts",           "vitals_series", "时间点"),
    ("iot", "vitals", "heart_rate",   "vitals_series", "心率(bpm)"),
    ("iot", "vitals", "spo2",         "vitals_series", "血氧(%)"),
    ("iot", "devices","device_type",  "vitals_series", "设备类型"),
    ("emr", "dossier", "present_illness_history", "emr_doc", "现病史"),
    ("emr", "dossier", "physical_exam",           "emr_doc", "体格检查"),
    ("emr", "dossier", "preliminary_diagnosis",   "emr_doc", "初步诊断"),
]

DIAG_TERM = {c: (n, d) for c, n, d in medsim.DIAGNOSES}


def tag_diagnoses(codes):
    """ICD-10 编码 -> 标准术语标注（语义对齐示例）"""
    out = []
    seen = set()
    for code in codes or []:
        if not code or code in seen:
            continue
        seen.add(code)
        hit = DIAG_TERM.get(str(code).strip())
        out.append({"code": str(code), "name": hit[0] if hit else "未收录术语", "dept_hint": hit[1] if hit else "-",
                    "aligned": bool(hit)})
    return out


def lab_item_term(item_code, item_name):
    for c, n, u, lo, hi in medsim.LAB_ITEMS:
        if c == item_code or n == item_name:
            return {"code": c, "name": n, "unit": u, "ref_range": "%s-%s" % (lo, hi)}
    return None


def build_graph(engine, limit_patients=40):
    """以患者为核心构建知识图谱（限幅实例，边带置信度）"""
    nodes, edges = [], []
    seen_nodes = set()

    def add_node(nid, label, ntype):
        if nid in seen_nodes:
            return False
        seen_nodes.add(nid)
        nodes.append({"id": nid, "label": label, "type": ntype})
        return True

    con_his = sqlite3.connect(os.path.join(medsim.DATA_DIR, "his.db"))
    recent_pids = list(con_his.execute(
        """SELECT p.patient_id, p.name FROM visit v JOIN patient p ON p.patient_id=v.patient_id
           GROUP BY v.patient_id ORDER BY MAX(v.admission_time) DESC LIMIT ?""", (int(limit_patients),)))

    con_lis = sqlite3.connect(os.path.join(medsim.DATA_DIR, "lis.db"))
    con_pacs = sqlite3.connect(os.path.join(medsim.DATA_DIR, "pacs.db"))
    iot_dir_ok = os.path.exists(os.path.join(medsim.DATA_DIR, "iot.db"))
    emr_dir = os.path.join(medsim.DATA_DIR, "emr")

    for pid, pname in recent_pids:
        add_node("patient:%s" % pid, "%s(%s)" % (pname, pid), "patient")
        visits = list(con_his.execute(
            "SELECT visit_id,vtype,dept FROM visit WHERE patient_id=? ORDER BY admission_time DESC LIMIT 2", (pid,)))
        for vid, vt, dept in visits:
            add_node("visit:%s" % pid + "_" + str(vid), "%s·%s(%d)" % (vt, dept, vid), "visit")
            edges.append({"s": "patient:%s" % pid, "t": "visit:%s_%s" % (pid, vid), "rel": "就诊于", "conf": 0.99})

        labs = list(con_lis.execute(
            "SELECT test_no FROM test_order WHERE patient_id=? ORDER BY collect_time DESC LIMIT 2", (pid,)))
        for tno, in labs:
            add_node("lab:%s" % tno, "检验单%s" % tno[-6:], "lab_test")
            edges.append({"s": "patient:%s" % pid, "t": "lab:%s" % tno, "rel": "检验关联", "conf": 0.97})

        studies = list(con_pacs.execute(
            "SELECT study_uid,modality FROM study WHERE patient_id=? ORDER BY exam_date DESC LIMIT 1", (pid,)))
        for uid, mod in studies:
            add_node("img:%s" % pid + "_" + str(uid[-8:]), "%s影像(%s)" % (mod, uid[:9] + "…"), "imaging_study")
            edges.append({"s": "patient:%s" % pid, "t": "img:%s_%s" % (pid, uid[-8:]), "rel": "影像检查", "conf": 0.96})

        has_vitals_flag = False
        if iot_dir_ok:
            try:
                con_iot = sqlite3.connect(os.path.join(medsim.DATA_DIR, "iot.db"))
                has_vitals_flag = con_iot.execute("SELECT COUNT(*) FROM vitals WHERE patient_id=?", (pid,)).fetchone()[0] > 0
                if has_vitals_flag:
                    add_node("vital:%s" % pid, "%s体征流" % pid, "vitals_series")
                    edges.append({"s": "patient:%s" % pid, "t": "vital:%s" % pid, "rel": "持续监护", "conf": 0.93})
                con_iot.close()
            except Exception:
                pass

        if os.path.exists(os.path.join(emr_dir, "%s.json" % pid)):
            add_node("emr:%s" % pid, "%s病历文书" % pid, "emr_doc")
            edges.append({"s": "patient:%s" % pid, "t": "emr:%s" % pid, "rel": "病案归档", "conf": 0.95})

    con_his.close(); con_lis.close(); con_pacs.close()
    return {"nodes": nodes, "edges": edges}


def catalog_payload(engine):
    """目录页数据：实体类型 + 字段语义映射（按实体分组）"""
    by_entity = {}
    for src, table, col, ent, attr in FIELD_MAP:
        by_entity.setdefault(ent, []).append({"source": src, "table": table, "column": col, "attr": attr})
    return {
        "entity_types": ENTITY_TYPES,
        "field_map": [{"entity": e, "label": ENTITY_TYPES[e]["label"], "color": ENTITY_TYPES[e]["color"],
                       "desc": ENTITY_TYPES[e]["desc"], "mappings": ms} for e, ms in by_entity.items()],
    }
