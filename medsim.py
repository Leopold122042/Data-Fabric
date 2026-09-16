# -*- coding: utf-8 -*-
"""medsim.py — 模拟医疗数据源生成器（架构模型之“数据资源层”）。

在本地生成五套异构、物理分散的医院侧数据源，用于演示数据编织平台：
  HIS   医院信息系统        SQLite 关系库（患者 / 就诊 / 医嘱）
  LIS   检验信息系统        SQLite 关系库（检验申请单 / 结果明细）
  PACS  影像归档与通信系统  SQLite 索引库 + DICOM 文件元数据
  EMR   电子病历            JSON 文档库（半结构化病历文本，按患者一文件）
  IoT   院内监测设备        SQLite 时序库（生命体征流，StreamingSim 持续写入）

基础数据集使用固定随机种子生成（确定性、可复现）；StreamingSim 模拟实时业务流：
新入院患者会依次出现在 IoT → HIS → LIS → PACS/EMR 中，供主动元数据引擎“边跑边发现”。
"""
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
NOW = datetime(2026, 9, 7, 14, 30)          # 演示基准时间（与课题申报书同期）

SOURCE_DEFS = [
    {"id": "his",   "name": "HIS 医院信息系统",       "kind": "rdbms",     "engine": "Oracle 19c（SQLite 模拟）",            "location": "院内数据中心 /his_prod",          "desc": "患者主索引、门诊/住院就诊、医嘱与诊断"},
    {"id": "lis",   "name": "LIS 检验信息系统",       "kind": "rdbms",     "engine": "MySQL 8.0（SQLite 模拟）",             "location": "院内数据中心 /lis_prod",          "desc": "标本采集、检验申请单与结果明细"},
    {"id": "pacs",  "name": "PACS 影像归档系统",      "kind": "rdbms+file","engine": "PostgreSQL + DICOM 文件库（SQLite 模拟）","location": "影像服务器 /pacs_store/studies",   "desc": "CT/MR/DR/US 检查索引与影像文件元数据"},
    {"id": "emr",   "name": "EMR 电子病历系统",       "kind": "document",  "engine": "MongoDB（JSON 文档模拟）",              "location": "病案库 /emr/dossiers/*.json",     "desc": "半结构化病程记录与诊疗文书"},
    {"id": "iot",   "name": "IoT 院内监测设备网",     "kind": "stream",    "engine": "Kafka + InfluxDB（SQLite 模拟）",       "location": "护理单元边缘网关 /vitals_stream", "desc": "监护仪/血糖仪实时生命体征流"},
]

SURNAMES = list("王李张刘陈杨赵黄周吴徐孙马朱胡郭何林罗郑梁谢宋唐许韩冯邓曹彭")
GIVEN1   = ["伟","芳","娜","敏","静","丽","强","磊","军","洋","勇","艳","杰","娟","涛","明","超","霞","平","刚"]

DIAGNOSES = [  # (ICD-10, 名称, 科室)
    ("I10",     "原发性高血压",         "心内科"),
    ("E11.9",   "2型糖尿病",            "内分泌科"),
    ("I25.1",   "动脉粥样硬化性心脏病", "心内科"),
    ("J18.9",   "肺炎，未特指",          "呼吸内科"),
    ("J06.9",   "急性上呼吸道感染",      "急诊科"),
    ("I63.9",   "脑梗死",              "神经内科"),
    ("G43.9",   "偏头痛，未特指",        "神经内科"),
    ("K29.1",   "慢性浅表性胃炎",       "消化内科"),
    ("M51.2",   "腰椎间盘突出症伴神经根病","骨科"),
    ("E78.0",   "高胆固醇血症",         "内分泌科"),
    ("N39.0",   "尿路感染，未特指",      "急诊科"),
    ("R42",     "头晕及眩晕综合征",      "神经内科"),
]

COMPLAINTS = [
    "反复胸闷、心悸5天", "咳嗽咳痰1周、发热2天", "阵发性头痛3月余，加重伴视物模糊1天",
    "餐后上腹痛半年，反酸烧心2周", "腰腿痛放射至左下肢3个月", "发热咽痛2天，伴乏力",
    "活动后气促1月，夜间不能平卧2天", "双足麻木进行性加重2周",
]

LAB_ITEMS = [  # (code, 名称, unit, lo, hi)
    ("ALT",   "谷丙转氨酶",     "U/L",      7.0,   40.0),
    ("AST",   "谷草转氨酶",     "U/L",      13.0,  35.0),
    ("WBC",   "白细胞计数",     "10^9/L",   3.5,   9.5),
    ("RBC",   "红细胞计数",     "10^12/L",  3.8,   5.8),
    ("HGB",   "血红蛋白",       "g/L",      120.0, 160.0),
    ("PLT",   "血小板计数",     "10^9/L",   90.0,  300.0),
    ("GLU",   "空腹血糖",       "mmol/L",   3.9,   6.1),
    ("HBA1C", "糖化血红蛋白",   "%",        4.0,   6.5),
    ("TC",    "总胆固醇",       "mmol/L",   3.0,   5.7),
    ("TG",    "甘油三酯",       "mmol/L",   0.4,   1.7),
    ("CREA",  "血肌酐",         "umol/L",   57.0,  97.0),
    ("K",     "血清钾",         "mmol/L",   3.5,   5.3),
]

DEPT_DOCTORS = {
    "心内科": ["王建国","李慧敏"], "内分泌科": ["陈静","赵磊"], "呼吸内科": ["刘洋","孙丽华"],
    "神经内科":["周涛","吴桂芳"], "消化内科": ["郑海峰","钱晓燕"], "骨科":     ["冯志刚","蒋立群"],
    "急诊科":   ["韩冰","曹俊"],  "普外科":   ["许文斌","杜若飞"],
}

MODALITY_BY_DEPT = {
    "心内科": [("DR", "胸部"), ("CT", "胸部")], "呼吸内科": [("CT", "胸部"), ("DR", "胸部")],
    "神经内科":[("MR", "头颅"), ("CT", "头颅")], "消化内科": [("US", "腹部")],
    "骨科":     [("DR", "腰椎"), ("MR", "腰椎")], "内分泌科": [("US", "甲状腺")],
    "急诊科":   [("DR", "胸部"), ("US", "腹部")],  "普外科":    [("US", "腹部")],
}

IMAGING_REPORTS = {
    "CT": ["两肺纹理清晰，未见明显异常密度影。纵隔居中，心影大小形态正常。", "左下肺见斑片状高密度影，考虑炎性病变可能，建议随访。"],
    "DR": ["心肺膈肌未见明显异常。", "双肺野透亮度增高，余未见特殊。"],
    "MR": ["双侧大脑半球对称，脑室系统居中；MRA示左侧颈内动脉起始段轻度狭窄。", "T2WI/FLAIR 见散在点状高信号影，考虑慢性缺血灶可能。"],
    "US": ["肝脏形态大小正常，实质回声均匀；胆囊壁光滑；双肾未见异常。", "甲状腺右叶可见低回声结节（TI-RADS 3类）。"],
}

ORDER_ITEMS = {
    "西药":     ["阿司匹林肠溶片","氨氯地平片","二甲双胍缓释片","阿托伐他汀钙片","奥美拉唑胶囊","头孢呋辛酯片"],
    "中成药":   ["复方丹参滴丸","六味地黄丸","蓝芩口服液"],
    "护理":     ["心电监护","吸氧治疗","静脉采血","留置导尿"],
    "材料":     ["一次性注射器","输液器","敷料包"],
}

WARD_BY_DEPT = {"心内科":"心血管病区一区","内分泌科":"内分泌病区","呼吸内科":"呼吸与危重症病区",
                "神经内科":"神经内外科病区","消化内科":"消化病区","骨科":"骨关节病区"}


def _conn(dbname):
    path = os.path.join(DATA_DIR, dbname)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ---------------------------------------------------------------- 基础数据生成
def generate_base(seed=42):
    if os.path.isdir(DATA_DIR):
        import shutil
        shutil.rmtree(DATA_DIR)
    os.makedirs(os.path.join(DATA_DIR, "emr"), exist_ok=True)
    rng = random.Random(seed)

    his = _conn("his.db")
    his.executescript("""
      CREATE TABLE patient(patient_id TEXT PRIMARY KEY, name TEXT, gender TEXT, birth_date TEXT, id_card TEXT, insurance_type TEXT);
      CREATE TABLE visit(visit_id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, vtype TEXT, dept TEXT, doctor TEXT, admission_time TEXT, diagnosis_code TEXT, diagnosis_name TEXT, chief_complaint TEXT);
      CREATE TABLE orders(order_id INTEGER PRIMARY KEY AUTOINCREMENT, visit_id INTEGER, otype TEXT, item_name TEXT, status TEXT);
    """)

    lis = _conn("lis.db")
    lis.executescript("""
      CREATE TABLE test_order(test_no TEXT PRIMARY KEY, patient_id TEXT, visit_id INTEGER, sample_type TEXT, collect_time TEXT, report_time TEXT, status TEXT);
      CREATE TABLE result(result_id INTEGER PRIMARY KEY AUTOINCREMENT, test_no TEXT, item_code TEXT, item_name TEXT, value_num REAL, unit TEXT, ref_range TEXT, flag TEXT);
    """)

    pacs = _conn("pacs.db")
    pacs.executescript("""
      CREATE TABLE study(study_uid TEXT PRIMARY KEY, patient_id TEXT, modality TEXT, body_part TEXT, exam_date TEXT, accession_no TEXT, files INTEGER, size_mb REAL, report_summary TEXT);
    """)

    iot = _conn("iot.db")
    iot.executescript("""
      CREATE TABLE devices(device_id TEXT PRIMARY KEY, device_type TEXT, ward TEXT);
      CREATE TABLE vitals(vital_id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, heart_rate REAL, spo2 REAL, temp REAL);
      CREATE INDEX idx_vitals_pid_ts ON vitals(patient_id, ts);
    """)

    n_patients = 180
    patients = []
    visit_rows_all = []   # (pid, vtype, dept, doctor, adm_dt, code, dname, complaint)
    for i in range(1, n_patients + 1):
        pid = "P%04d" % i
        gender = "男" if rng.random() < 0.55 else "女"
        birth_y = rng.randint(1938, 2016)
        name = rng.choice(SURNAMES) + rng.choice(GIVEN1)
        region = rng.choice(["320102","320104","320505","320703"])
        id_card = "%s%d%02d%02d********%c" % (region, birth_y, rng.randint(1, 12), rng.randint(1, 28), rng.choice("0123456789X"))
        insurance = rng.choices(["职工医保","居民医保","自费"], weights=[0.55, 0.35, 0.10])[0]
        patients.append((pid, name, gender, "%d-" % birth_y, id_card, insurance))

        idx = rng.choices(range(len(DIAGNOSES)), weights=[24,20,14,9,8,7,5,6,5,3,4,5])[0]
        code, dname, dept = DIAGNOSES[idx]
        n_visits = rng.randint(1, 5) if code in ("I10","E11.9") else rng.randint(1, 3)
        for _ in range(n_visits):
            vtype = "住院" if (code in ("I25.1","J18.9","I63.9","M51.2") and rng.random() < 0.7) else "门诊"
            adm = NOW - timedelta(days=rng.randint(0, 720), hours=rng.randint(0, 12), minutes=rng.choice([0,15,30,45]))
            visit_rows_all.append((pid, vtype, dept, DEPT_DOCTORS.get(dept, ["王建国"])[rng.randrange(len(DEPT_DOCTORS[dept]))], adm, code, dname, COMPLAINTS[rng.randrange(len(COMPLAINTS))]))

    his.executemany("INSERT INTO patient VALUES (?,?,?,?,?,?)", patients)
    his.executemany(
        "INSERT INTO visit(patient_id,vtype,dept,doctor,admission_time,diagnosis_code,diagnosis_name,chief_complaint) VALUES (?,?,?,?,?,?,?,?)",
        [(p, vt, d, doc, a.strftime("%Y-%m-%d %H:%M"), c, n_, cc) for p, vt, d, doc, a, c, n_, cc in visit_rows_all])
    his.commit()

    # 医嘱 + LIS + PACS（按就诊抽样生成，控制规模）
    visit_sample = list(his.execute("SELECT visit_id, patient_id, dept FROM visit ORDER BY random() LIMIT 420"))
    for vid, pid, dept in visit_sample:
        orders = []
        has_lab = has_img = False
        for _ in range(rng.randint(2, 7)):
            otype = rng.choices(["西药","中成药","护理","材料"], weights=[0.5, 0.18, 0.24, 0.08])[0]
            orders.append((vid, otype, ORDER_ITEMS[otype][rng.randrange(len(ORDER_ITEMS[otype]))], "已执行"))
        if rng.random() < 0.75:
            has_lab = True
            orders[-1] = (vid, "检验", "常规检验组合（血）", "已执行")
        if dept in MODALITY_BY_DEPT and rng.random() < 0.4:
            has_img = True
            orders.append((vid, "影像", "%s影像学检查" % dept, "已执行"))
        his.executemany("INSERT INTO orders(visit_id,otype,item_name,status) VALUES (?,?,?,?)", orders)

        if has_lab or has_img:
            vrow = list(his.execute("SELECT admission_time FROM visit WHERE visit_id=?", (vid,)))[0]
            adm_t = datetime.strptime(vrow[0], "%Y-%m-%d %H:%M")
        if has_lab:
            tno = "T%d" % rng.randint(10**7, 9*10**7)
            collect = adm_t + timedelta(minutes=rng.choice([20, 35, 60]))
            report = collect + timedelta(minutes=rng.randrange(40, 240))
            lis.execute("INSERT INTO test_order VALUES (?,?,?,?,?,?,?)",
                        (tno, pid, vid, rng.choices(["全血","血清","尿液"], weights=[0.75,0.15,0.1])[0],
                         collect.strftime("%Y-%m-%d %H:%M"), report.strftime("%Y-%m-%d %H:%M"), "已审核"))
            items = LAB_ITEMS if rng.random() < 0.4 else [LAB_ITEMS[i] for i in rng.sample(range(len(LAB_ITEMS)), 6)]
            for code_, nm, unit, lo, hi in items:
                val = round(rng.uniform(lo * 0.85, hi * 1.12), 1)
                flag = "H" if val > hi else ("L" if val < lo else "")
                lis.execute("INSERT INTO result(test_no,item_code,item_name,value_num,unit,ref_range,flag) VALUES (?,?,?,?,?,?,?)",
                            (tno, code_, nm, val, unit, "%s-%s" % (lo, hi), flag))

        if has_img:
            modality, body = MODALITY_BY_DEPT[dept][rng.randrange(len(MODALITY_BY_DEPT[dept]))]
            exam_t = adm_t + timedelta(hours=rng.randint(1, 8))
            pacs.execute("INSERT INTO study VALUES (?,?,?,?,?,?,?,?,?)",
                         ("1.2.156.%d" % rng.randint(10**7, 9*10**7), pid, modality, body, exam_t.strftime("%Y-%m-%d"),
                          "A%08d" % vid, rng.randint(8, 240), round(rng.uniform(3, 850), 1),
                          IMAGING_REPORTS[modality][rng.randrange(len(IMAGING_REPORTS[modality]))]))

    his.commit(); lis.commit(); pacs.commit()

    # EMR：约七成患者有结构化文书
    for pid, name, gender, birth_y_str, id_card, insurance in patients:
        if rng.random() > 0.72:
            continue
        vrows = list(his.execute("SELECT dept, diagnosis_code, diagnosis_name, chief_complaint FROM visit WHERE patient_id=? ORDER BY admission_time DESC LIMIT 1", (pid,)))
        if not vrows:
            continue
        dept, code_, dname, cc = vrows[0]
        t = rng.uniform(36.2, 37.1) + (rng.uniform(0.4, 1.3) if code_.startswith(("J", "N")) else 0)
        hr = rng.randint(68, 96)
        bp_s = rng.randint(145, 172) if code_ == "I10" else rng.randint(112, 134)
        modality_pair = MODALITY_BY_DEPT.get(dept, [("DR", "胸部")])
        modality = modality_pair[rng.randrange(len(modality_pair))][0]
        report_txt = IMAGING_REPORTS[modality][rng.randrange(2)]
        aux_exam = ["血常规+生化：详见LIS检验报告（%s）" % dname, "%s检查：%s" % (modality, report_txt), "心电图：窦性心律，T波轻度改变"]
        extra_dx = [dname] + (["原发性高血压"] if code_ != "I10" and rng.random() < 0.35 else [])
        dossier = {
            "patient_id": pid, "dept": dept,
            "chief_complaint": cc,
            "present_illness_history": "%s，%s，因“%s”就诊。起病以来神志清楚，精神可，饮食睡眠一般，大小便正常，体重无明显变化。" % (name, gender, cc),
            "physical_exam": "T %.1f℃、P %d次/分、R %d次/分、BP %d/%dmmHg。神志清楚，查体合作；双肺呼吸音粗，未闻及明显干湿啰音；心律齐，各瓣膜区未闻及病理性杂音；腹软无压痛。" % (t, hr, rng.randint(16, 24), bp_s, rng.randint(78, 95)),
            "auxiliary_examination": aux_exam,
            "preliminary_diagnosis": extra_dx,
            "treatment_plan": "予以%s等治疗；完善相关检查，密切观察病情变化，嘱低盐低脂饮食、规律作息。" % rng.choice(["抗血小板聚集","降糖、调脂","抗感染、对症支持","解痉止痛","活血化瘀"]),
        }
        with open(os.path.join(DATA_DIR, "emr", "%s.json" % pid), "w", encoding="utf-8") as fh:
            json.dump(dossier, fh, ensure_ascii=False, indent=1)

    # 保证近两周有足够数量的住院患者（供IoT监护与实时发现演示）
    recent_pids = rng.sample([p[0] for p in patients], k=42)
    for pid_ in recent_pids:
        row = list(his.execute("SELECT dept, diagnosis_code, diagnosis_name FROM visit WHERE patient_id=? LIMIT 1", (pid_,)))
        if not row:
            continue
        dept_, code_r, dname_r = row[0]
        adm_t = NOW - timedelta(days=rng.randint(0, 12), hours=rng.randint(6, 20))
        his.execute("INSERT INTO visit(patient_id,vtype,dept,doctor,admission_time,diagnosis_code,diagnosis_name,chief_complaint) VALUES (?,?,?,?,?,?,?,?)",
                    (pid_, "住院", dept_, DEPT_DOCTORS.get(dept_, ["王建国"])[0], adm_t.strftime("%Y-%m-%d %H:%M"), code_r, dname_r, COMPLAINTS[rng.randrange(len(COMPLAINTS))]))
    his.commit()

    # IoT：近两周有住院的纳管患者接入监护
    recent = [r[0] for r in his.execute("SELECT DISTINCT patient_id FROM visit WHERE vtype='住院' AND admission_time >= ? ORDER BY random() LIMIT 36", ((NOW - timedelta(days=14)).strftime("%Y-%m-%d %H:%M"),))]
    for k, pid in enumerate(recent):
        dept = list(his.execute("SELECT dept FROM visit WHERE patient_id=? AND vtype='住院' ORDER BY admission_time DESC LIMIT 1", (pid,)))[0][0]
        iot.execute("INSERT INTO devices VALUES (?,?,?)", ("DEV%02d" % (k + 1), rng.choice(["多参数监护仪","动态血压计","连续血糖监测仪"]), WARD_BY_DEPT.get(dept, "急诊抢救区")))
    for pid in recent:
        base_hr = rng.uniform(64, 92)
        ts = NOW - timedelta(days=7)
        while ts < NOW:
            hr_ = max(38.0, round(base_hr + rng.gauss(0, 5), 1))
            spo2 = min(100.0, round(rng.uniform(94.5, 99.6), 1))
            iot.execute("INSERT INTO vitals(patient_id,ts,heart_rate,spo2,temp) VALUES (?,?,?,?,?)",
                        (pid, ts.strftime("%Y-%m-%d %H:%M"), hr_, spo2, round(rng.uniform(36.1, 37.2), 1)))
            ts += timedelta(minutes=30)
    iot.commit()

    for con in (his, lis, pacs, iot):
        con.close()
    return {"patients": n_patients}


# ---------------------------------------------------------------- 实时业务流模拟
class StreamingSim:
    """每个 tick（默认约4秒）推进一次：体征持续写入；新患者跨系统逐步出现。"""

    STAGES = ["iot", "his", "lis", "pacs_emr"]   # stage N -> 已写入前 N+1 个系统

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.tick_n = 0
        self.pending = []          # {pid,name,...,stage,next_at}
        con = _conn("his.db")
        mx = list(con.execute("SELECT MAX(CAST(SUBSTR(patient_id,2) AS INTEGER)) FROM patient"))[0][0] or 180
        self.next_pid = int(mx) + 1
        con.close()

    def tick(self):
        self.tick_n += 1
        now = NOW + timedelta(seconds=self.tick_n * 4)
        iot_con = _conn("iot.db")
        pids = [r[0] for r in iot_con.execute("SELECT DISTINCT patient_id FROM vitals ORDER BY ts DESC LIMIT 60")]
        self._tick_vitals(iot_con, now, pids)

        if self.tick_n % 5 == 0 and sum(1 for p in self.pending if p["stage"] < len(self.STAGES)) < 3:
            self._start_arrival(now)
        for arr in list(self.pending):
            if now >= arr.get("next_at", NOW):
                self._advance(arr, now)

    # ---- vitals -------------------------------------------------------
    def _tick_vitals(self, con, now, pids):
        spike = (self.tick_n % 45 == 0 and len(pids) > 3)   # 周期性心律失常事件，供质量异常发现演示
        sample = self.rng.sample(pids, k=min(len(pids), max(6, len(pids) // 2)))
        for idx, pid in enumerate(sample):
            if spike and idx < 3:
                hr = round(self.rng.uniform(128, 158), 1)
                spo2 = round(self.rng.uniform(90.5, 94.5), 1)
            else:
                last = list(con.execute("SELECT heart_rate FROM vitals WHERE patient_id=? ORDER BY ts DESC LIMIT 1", (pid,)))
                base = last[0][0] if last and last[0][0] < 120 else self.rng.uniform(64, 90)
                hr = max(38.0, round(base + self.rng.gauss(0, 5), 1))
                spo2 = min(100.0, round(self.rng.uniform(94.5, 99.7), 1))
            con.execute("INSERT INTO vitals(patient_id,ts,heart_rate,spo2,temp) VALUES (?,?,?,?,?)",
                        (pid, now.strftime("%Y-%m-%d %H:%M"), hr, spo2, round(self.rng.uniform(36.0, 37.4), 1)))
        con.commit()

    # ---- new patient arrival pipeline ---------------------------------
    def _start_arrival(self, now):
        pid = "P%04d" % self.next_pid
        self.next_pid += 1
        code, dname, dept = DIAGNOSES[self.rng.randrange(len(DIAGNOSES))]
        arr = {"pid": pid, "stage": 0,
               "name": self.rng.choice(SURNAMES) + self.rng.choice(GIVEN1),
               "gender": "男" if self.rng.random() < 0.5 else "女",
               "birth_y": self.rng.randint(1942, 2008),
               "dept": dept, "code": code, "dname": dname}
        iot_con = _conn("iot.db")   # stage 0：IoT 先行（急诊监护接入）
        for m in (3, 2, 1):
            ts_ = now - timedelta(minutes=m)
            iot_con.execute("INSERT INTO vitals(patient_id,ts,heart_rate,spo2,temp) VALUES (?,?,?,?,?)",
                            (pid, ts_.strftime("%Y-%m-%d %H:%M"), round(self.rng.uniform(96, 138), 1), round(self.rng.uniform(93.5, 97.5), 1), round(self.rng.uniform(36.4, 38.2), 1)))
        iot_con.commit()
        arr["next_at"] = now + timedelta(seconds=self._delay())
        self.pending.append(arr)

    def _advance(self, arr, now):
        stage = arr["stage"]
        pid = arr["pid"]
        if stage == 0:   # IoT -> HIS：建档 + 就诊登记
            con = _conn("his.db")
            region = "320105"
            id_card = "%s%d%02d%02d********%c" % (region, arr["birth_y"], self.rng.randint(1, 12), self.rng.randint(1, 28), self.rng.choice("0123456789X"))
            con.execute("INSERT OR IGNORE INTO patient VALUES (?,?,?,?,?,?)",
                        (pid, arr["name"], arr["gender"], "%d-" % arr["birth_y"], id_card, "职工医保"))
            vtype = self.rng.choices(["急诊", "住院"], weights=[0.5, 0.5])[0]
            con.execute("INSERT INTO visit(patient_id,vtype,dept,doctor,admission_time,diagnosis_code,diagnosis_name,chief_complaint) VALUES (?,?,?,?,?,?,?,?)",
                        (pid, vtype, arr["dept"], DEPT_DOCTORS.get(arr["dept"], ["韩冰"])[0], now.strftime("%Y-%m-%d %H:%M"), arr["code"], arr["dname"], COMPLAINTS[self.rng.randrange(len(COMPLAINTS))]))
            con.commit(); con.close()
        elif stage == 1:   # HIS -> LIS：检验申请 + 结果回报
            his_con = _conn("his.db")
            vid = list(his_con.execute("SELECT MAX(visit_id) FROM visit WHERE patient_id=?", (pid,)))[0][0] or 0
            his_con.close()
            lis_c = _conn("lis.db")
            tno = "T%d" % self.rng.randint(10**7, 9*10**7)
            collect = now - timedelta(minutes=self.rng.randrange(5, 30))
            report = now - timedelta(minutes=2)
            lis_c.execute("INSERT INTO test_order VALUES (?,?,?,?,?,?,?)",
                          (tno, pid, vid, "全血", collect.strftime("%Y-%m-%d %H:%M"), report.strftime("%Y-%m-%d %H:%M"), "已审核"))
            for code_, nm, unit, lo, hi in self.rng.sample(LAB_ITEMS, 6):
                val = round(self.rng.uniform(lo * 0.85, hi * 1.12), 1)
                flag = "H" if val > hi else ("L" if val < lo else "")
                lis_c.execute("INSERT INTO result(test_no,item_code,item_name,value_num,unit,ref_range,flag) VALUES (?,?,?,?,?,?,?)",
                              (tno, code_, nm, val, unit, "%s-%s" % (lo, hi), flag))
            lis_c.commit(); lis_c.close()
        elif stage == 2:   # LIS -> PACS（70%）+ EMR 文书落库
            pacs_c = _conn("pacs.db")
            modality_pair = MODALITY_BY_DEPT.get(arr["dept"], [("DR", "胸部")])
            if self.rng.random() < 0.7:
                modality, body = modality_pair[0]
                pacs_c.execute("INSERT INTO study VALUES (?,?,?,?,?,?,?,?,?)",
                               ("1.2.156.%d" % self.rng.randint(10**7, 9*10**7), pid, modality, body, now.strftime("%Y-%m-%d"),
                                "A%s" % pid[-4:], self.rng.randint(8, 300), round(self.rng.uniform(3, 600), 1),
                                IMAGING_REPORTS[modality][self.rng.randrange(len(IMAGING_REPORTS[modality]))]))
            pacs_c.commit(); pacs_c.close()
            dossier = {
                "patient_id": pid, "dept": arr["dept"],
                "chief_complaint": COMPLAINTS[self.rng.randrange(len(COMPLAINTS))],
                "present_illness_history": "%s，%s，因急诊症状入院，查体见下文。" % (arr["name"], arr["gender"]),
                "physical_exam": "T 37.8℃、P 102次/分、R 20次/分、BP 156/94mmHg。神志清楚，急性病容。",
                "preliminary_diagnosis": [arr["dname"]],
                "treatment_plan": "完善检验与影像学检查，对症支持治疗，密切监护生命体征变化。",
            }
            with open(os.path.join(DATA_DIR, "emr", "%s.json" % pid), "w", encoding="utf-8") as fh:
                json.dump(dossier, fh, ensure_ascii=False, indent=1)
        arr["stage"] = stage + 1
        if arr["stage"] < len(self.STAGES):
            arr["next_at"] = now + timedelta(seconds=self._delay())

    def _delay(self):
        return self.rng.choice([8, 12, 16])


if __name__ == "__main__":
    generate_base()
    print("base data generated at", DATA_DIR)
