# -*- coding: utf-8 -*-
"""第三、四部分：数据服务流通与安全合规治理的演示状态层。

这个模块不复制底层医疗数据，只保存服务登记、流通计量、授权决策、审计
和风险处置等控制面信息；数据仍由 app.py 的虚拟化层按需读取。
"""
from collections import deque
from copy import deepcopy
from datetime import datetime
import re
import threading
import time


CLASS_ORDER = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}
CLASS_LABEL = {"L1": "公开", "L2": "内部", "L3": "敏感", "L4": "高度敏感"}

DATA_ASSETS = [
    {"id": "clinical_patient", "name": "患者主索引", "source": "HIS", "class": "L4", "rows_key": "patients",
     "fields": ["patient_id", "name", "gender", "birth_date", "id_card", "phone"], "masking": "身份字段掩码 + 患者ID伪名化", "scope": "院内"},
    {"id": "clinical_visit", "name": "就诊与诊断", "source": "HIS", "class": "L3", "rows_key": "visits",
     "fields": ["patient_id", "dept", "diagnosis_name", "diagnosis_code", "doctor"], "masking": "患者ID伪名化，医生字段按角色展示", "scope": "院内/科研"},
    {"id": "lab_result", "name": "检验结果", "source": "LIS", "class": "L3", "rows_key": "lab_tests",
     "fields": ["patient_id", "test_no", "item", "value", "unit", "flag"], "masking": "患者ID伪名化，结果值保留统计粒度", "scope": "院内/科研"},
    {"id": "imaging_index", "name": "影像检查索引", "source": "PACS", "class": "L3", "rows_key": "studies",
     "fields": ["patient_id", "study_uid", "modality", "body_part", "report"], "masking": "患者ID伪名化，DICOM UID保留审计映射", "scope": "院内/科研"},
    {"id": "vitals_stream", "name": "生命体征流", "source": "IoT", "class": "L4", "rows_key": "vitals_points",
     "fields": ["patient_id", "vital_time", "hr", "spo2", "temperature"], "masking": "身份字段掩码，明细仅限医疗角色", "scope": "院内"},
    {"id": "emr_document", "name": "病历文书", "source": "EMR", "class": "L4", "rows_key": "emr_docs",
     "fields": ["patient_id", "chief_complaint", "present_illness_history", "physical_exam", "treatment_plan"], "masking": "全文脱敏 + 最小必要字段", "scope": "院内"},
]

POLICIES = [
    {"id": "POL-CLINICIAN", "name": "临床诊疗策略", "role": "clinician", "max_class": "L4", "purposes": ["care", "quality"], "cross_org": False, "default_mask": False},
    {"id": "POL-ANALYST", "name": "院内分析策略", "role": "analyst", "max_class": "L3", "purposes": ["research", "quality", "operations"], "cross_org": False, "default_mask": True},
    {"id": "POL-RESEARCH", "name": "科研协作策略", "role": "researcher", "max_class": "L3", "purposes": ["research"], "cross_org": True, "default_mask": True},
    {"id": "POL-OPS", "name": "平台运维策略", "role": "operator", "max_class": "L2", "purposes": ["operations"], "cross_org": False, "default_mask": True},
]

SERVICE_FLOWS = [
    {"id": "FLOW-CARE-360", "name": "临床患者全景协同", "scene": "临床诊疗", "service_ids": ["SVC-PATIENT-360", "SVC-VITALS-TREND"], "mode": "院内实时", "status": "active", "description": "患者全景 + 生命体征趋势，服务于床旁诊疗"},
    {"id": "FLOW-RESEARCH-COHORT", "name": "科研队列分析供给", "scene": "科研分析", "service_ids": ["SVC-FED-QUERY", "SVC-LAB-RESULT"], "mode": "脱敏共享", "status": "active", "description": "跨源队列统计与检验结果按需组合，输出脱敏分析结果"},
    {"id": "FLOW-QUALITY-REVIEW", "name": "质量异常复核链", "scene": "质量治理", "service_ids": ["SVC-VITALS-TREND", "SVC-LAB-RESULT"], "mode": "规则触发", "status": "active", "description": "异常体征触发趋势与检验复核，结果回写治理事件"},
]


class GovernanceState:
    def __init__(self):
        self.lock = threading.RLock()
        self.audit = deque(maxlen=800)
        self.risks = deque(maxlen=200)
        self.circulation = deque(maxlen=300)
        self.services = {}
        self.service_stats = {}
        self.seq = 0
        self._seed_services()
        self._seed_runtime_signals()

    def _seed_services(self):
        baseline = {
            "SVC-PATIENT-360": ("患者全景查询服务", "临床诊疗", "L4", "临床数据服务", "院内"),
            "SVC-LAB-RESULT": ("检验结果检索服务", "检验协同", "L3", "检验数据服务", "院内/科研"),
            "SVC-IMG-SEARCH": ("影像索引检索服务", "影像协同", "L3", "影像数据服务", "院内/科研"),
            "SVC-VITALS-TREND": ("生命体征趋势服务", "实时监护", "L4", "监护数据服务", "院内"),
            "SVC-META-CATALOG": ("元数据目录查询服务", "数据发现", "L2", "元数据服务", "院内/科研"),
            "SVC-FED-QUERY": ("跨源联邦统计查询", "科研分析", "L3", "分析数据服务", "院内/科研"),
        }
        for sid, (name, scene, cls, category, scope) in baseline.items():
            self.services[sid] = {"id": sid, "name": name, "scene": scene, "category": category, "class": cls,
                                  "scope": scope, "owner": "数据编织平台", "lifecycle": "published", "quality_score": 98,
                                  "reuse_score": 0, "registered_at": "2026-09-08 00:00:00", "last_called": None,
                                  "tags": ["可发现", "可调用", "零拷贝"], "description": "服务化封装的数据能力，可按需组合与授权调用"}
            self.service_stats[sid] = {"calls": 0, "success": 0, "total_ms": 0.0, "value_points": 0}

    def _seed_runtime_signals(self):
        self._audit("system", "平台初始化", "governance", "bootstrap", "allow", "第三、四部分治理基线已加载", risk=0)

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _audit(self, actor, action, target, operation, decision, reason, *, risk=0, masked=False, trace=None):
        self.seq += 1
        item = {"id": "AUD-%05d" % self.seq, "ts": self._now(), "actor": actor, "action": action,
                "target": target, "operation": operation, "decision": decision, "reason": reason,
                "risk": risk, "masked": bool(masked), "trace": trace or []}
        self.audit.appendleft(item)
        return item

    def get_asset(self, asset_id):
        return next((deepcopy(x) for x in DATA_ASSETS if x["id"] == asset_id), None)

    def policy_for(self, role):
        return next((deepcopy(x) for x in POLICIES if x["role"] == role), None)

    def authorize(self, actor=None, asset_id="clinical_visit", operation="query", purpose="research", cross_org=False):
        actor = actor or {}
        role = str(actor.get("role") or "analyst").lower()
        actor_name = str(actor.get("name") or actor.get("id") or role)
        org = str(actor.get("org") or "院内研究中心")
        asset = self.get_asset(asset_id)
        policy = self.policy_for(role)
        trace = ["身份识别：%s / %s" % (actor_name, role), "目标资产：%s / %s" % (asset_id, (asset or {}).get("class", "未知")), "用途声明：%s" % purpose]
        risk = 0
        if not asset:
            decision, reason, masked = "deny", "目标数据资产未登记", False
        elif not policy:
            decision, reason, masked = "deny", "角色未绑定访问策略", False
        elif CLASS_ORDER[asset["class"]] > CLASS_ORDER[policy["max_class"]]:
            decision, reason, masked = "deny", "数据分级高于角色可访问上限（%s ≤ %s）" % (policy["max_class"], asset["class"]), False
            risk = 3
        elif purpose not in policy["purposes"]:
            decision, reason, masked = "deny", "当前用途不在策略允许范围：%s" % ",".join(policy["purposes"]), False
            risk = 2
        elif cross_org and not policy["cross_org"]:
            decision, reason, masked = "deny", "跨组织调用未获得跨域授权", False
            risk = 3
        else:
            masked = bool(policy["default_mask"] or asset["class"] in ("L3", "L4") and role != "clinician")
            decision, reason = ("allow_masked" if masked else "allow"), ("策略通过，按最小必要原则%s" % ("返回脱敏结果" if masked else "返回授权字段"))
            trace.append("授权策略：%s" % policy["id"])
            trace.append("输出控制：%s" % (asset["masking"] if masked else "临床角色可见授权字段"))
        rec = self._audit(actor_name, "访问决策", asset_id, operation, decision, reason, risk=risk, masked=masked, trace=trace)
        if risk >= 2:
            self.risks.appendleft({"id": "RISK-%05d" % self.seq, "ts": rec["ts"], "level": "high" if risk == 3 else "medium",
                                  "type": "unauthorized_access", "target": asset_id, "actor": actor_name,
                                  "summary": reason, "status": "open", "audit_id": rec["id"]})
        return {"decision": decision, "allowed": decision != "deny", "masked": masked, "reason": reason,
                "asset": asset, "policy": policy, "trace": trace, "audit_id": rec["id"]}

    @staticmethod
    def mask_value(field, value, mode="standard"):
        if value is None:
            return value
        s = str(value)
        f = str(field or "").lower()
        if mode == "aggregate":
            return "已脱敏统计值"
        if "phone" in f or "mobile" in f:
            return s[:3] + "****" + s[-4:] if len(s) >= 7 else "****"
        if "id_card" in f or f in ("idcard", "身份证号"):
            return s[:3] + "************" + s[-2:] if len(s) >= 5 else "********"
        if f in ("name", "patient_name", "姓名"):
            return (s[:1] + "*" * max(2, len(s) - 1)) if s else "*"
        if f == "patient_id" or f.endswith("_patient_id"):
            return s[:1] + "****" + s[-1:] if len(s) > 2 else "P****"
        if f in ("chief_complaint", "present_illness_history", "physical_exam", "treatment_plan", "report"):
            return s[:12] + "……（内容已脱敏）" if len(s) > 12 else "（内容已脱敏）"
        return value

    def mask_payload(self, value, mode="standard"):
        if isinstance(value, list):
            return [self.mask_payload(x, mode) for x in value]
        if isinstance(value, dict):
            return {k: self.mask_value(k, self.mask_payload(v, mode), mode) if not isinstance(v, (dict, list)) else self.mask_payload(v, mode) for k, v in value.items()}
        return value

    def record_service(self, sid, *, latency_ms, success=True, actor="demo-analyst", flow_id=None, masked=False, decision="allow_masked"):
        with self.lock:
            svc = self.services.setdefault(sid, {"id": sid, "name": sid, "class": "L3", "lifecycle": "published", "quality_score": 95, "reuse_score": 0, "scope": "院内/科研", "tags": []})
            st = self.service_stats.setdefault(sid, {"calls": 0, "success": 0, "total_ms": 0.0, "value_points": 0})
            st["calls"] += 1; st["success"] += int(success); st["total_ms"] += float(latency_ms); st["value_points"] += max(1, int(100 - min(float(latency_ms), 99)))
            svc["last_called"] = self._now(); svc["reuse_score"] = min(100, st["calls"] * 5 + (20 if flow_id else 0))
            rec = {"id": "FLOW-%05d" % (self.seq + 1), "ts": self._now(), "service_id": sid, "flow_id": flow_id,
                   "actor": actor, "latency_ms": round(float(latency_ms), 2), "status": "success" if success else "failed",
                   "decision": decision, "masked": bool(masked), "value_points": max(1, int(100 - min(float(latency_ms), 99)))}
            self.circulation.appendleft(rec)
            return rec

    def snapshot(self, legacy_stats=None):
        with self.lock:
            items = []
            for sid, svc in self.services.items():
                x = deepcopy(svc); st = self.service_stats.get(sid, {})
                if legacy_stats and sid in legacy_stats:
                    st = {**st, "calls": max(st.get("calls", 0), legacy_stats[sid].get("calls", 0)), "avg_ms": legacy_stats[sid].get("avg_ms")}
                calls = int(st.get("calls", 0) or 0)
                x["stats"] = {"calls": calls, "success": st.get("success", 0), "success_rate": round(st.get("success", 0) / max(calls, 1), 3),
                               "avg_ms": round(st.get("total_ms", 0.0) / calls, 2) if calls else legacy_stats.get(sid, {}).get("avg_ms") if legacy_stats and sid in legacy_stats else None,
                               "value_points": st.get("value_points", 0)}
                items.append(x)
            total_calls = sum(x["stats"]["calls"] for x in items)
            total_value = sum(x["stats"]["value_points"] for x in items)
            return {"services": items, "flows": deepcopy(SERVICE_FLOWS), "recent_circulation": list(self.circulation)[:12],
                    "metrics": {"registered": len(items), "published": sum(x.get("lifecycle") == "published" for x in items), "calls": total_calls,
                                "value_points": total_value, "open_risks": sum(x.get("status") == "open" for x in self.risks),
                                "avg_latency_ms": round(sum(x["stats"]["avg_ms"] or 0 for x in items) / max(1, sum(x["stats"]["avg_ms"] is not None for x in items)), 2)},
                    "assets": deepcopy(DATA_ASSETS), "policies": deepcopy(POLICIES)}

    def security_snapshot(self):
        with self.lock:
            counts = {"total": len(self.audit), "allow": sum(x["decision"] in ("allow", "allow_masked") for x in self.audit),
                      "masked": sum(x.get("masked") for x in self.audit), "denied": sum(x["decision"] == "deny" for x in self.audit),
                      "open_risks": sum(x.get("status") == "open" for x in self.risks)}
            return {"assets": deepcopy(DATA_ASSETS), "policies": deepcopy(POLICIES), "audit": list(self.audit)[:20],
                    "risks": list(self.risks)[:30], "metrics": counts}

    def resolve_risk(self, rid, note="人工复核完成"):
        with self.lock:
            for risk in self.risks:
                if risk["id"] == rid:
                    risk["status"] = "resolved"; risk["resolved_at"] = self._now(); risk["resolution"] = note
                    self._audit("security-operator", "风险处置", risk["target"], "resolve", "allow", note, risk=0)
                    return deepcopy(risk)
        return None
