SERVICE_FLOWS = [
    {"id": "FLOW-CARE-360", "name": "临床患者全景协同", "scene": "临床诊疗", "service_ids": ["SVC-PATIENT-360", "SVC-VITALS-TREND"], "mode": "院内实时", "status": "active", "description": "患者全景 + 生命体征趋势，服务于床旁诊疗"},
    {"id": "FLOW-RESEARCH-COHORT", "name": "科研队列分析供给", "scene": "科研分析", "service_ids": ["SVC-FED-QUERY", "SVC-LAB-RESULT"], "mode": "脱敏共享", "status": "active", "description": "跨源队列统计与检验结果按需组合，输出脱敏分析结果"},
    {"id": "FLOW-QUALITY-REVIEW", "name": "质量异常复核链", "scene": "质量治理", "service_ids": ["SVC-VITALS-TREND", "SVC-LAB-RESULT"], "mode": "规则触发", "status": "active", "description": "异常体征触发趋势与检验复核，结果回写治理事件"},
]
