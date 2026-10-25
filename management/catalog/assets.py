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
