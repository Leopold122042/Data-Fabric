# MedFabric · 医疗数据编织平台（第一至四部分：架构 × 编织 × 服务流通 × 安全合规）

> 《基于数据编织技术的数据基础设施创新路径研究》（课题13，南京大学）—— **第一至四部分**的可运行原型。以**三甲医院多系统场景**为例，覆盖架构模型、数据编织关键技术、数据服务与流通模式、安全保障与合规治理，并在一页式“数据驾驶舱”上实时呈现运行闭环。

## 快速启动
```bat
medfabric\start.bat        :: Windows：自动装依赖(首次)、起服务并打开浏览器 http://127.0.0.1:8321/
```
或手动：`set PYTHONPATH=E:\数据编织\_pylibs && cd medfabric && python -m uvicorn app:app --host 127.0.0.1 --port 8321`

## 与申报书第一部分的对应关系
| 申报书要求 | 平台实现 |
|---|---|
| “资源层—编织层—服务层”分层架构模型 | `medsim.py`(五套异构源) / `discovery·semantics·virtualization`(编织机制) / `/api/services*` + UI“架构总览”页 |
| 多源异构数据统一管理与高效利用 | HIS(Oracle模拟)/LIS(MySQL模拟)/PACS(PostgreSQL+DICOM文件库模拟)/EMR(MongoDB文档模拟)/IoT(Kafka流模拟)，共180患者、490+就诊、2.7k检验结果等，物理分散存储 |
| 主动元数据驱动为核心机制 | `discovery.py`：每4秒对五源做**结构+内容双维指纹比对** → 事件流（增量/Schema漂移/质量异常/**跨系统身份对齐**），SSE实时推送UI；StreamingSim持续注入新患者，观察“IoT→HIS→LIS→PACS/EMR”依次落库被动态发现 |
| 数据虚拟化为支撑 | `virtualization.py`：由语义映射推导逻辑视图，Patient360跨5源**零拷贝联邦取数**（附来源溯源+耗时），诊断术语对齐检索 |
| 智能化编排为协同 | “感知-建模”闭环已打通；事件→动作的编排策略在“智能编排协同(预留)”卡片与后续部分展开 |
| 统一语义建模与逻辑关联机制 | `semantics.py`：实体类型体系 + 33条字段级跨源映射（物理表不改，仅建立语义层）+ ICD-10/检验项目术语自动打标 + 患者核心知识图谱（边带置信度） |
| “物理集中汇聚”→“逻辑关联整合” | UI“虚拟查询·Patient360”页：数据保持原位、按需联邦访问；架构总览展示各源行数随实时业务流增长而无需搬迁 |

## 第二部分「数据编织关键技术」对应关系（本次新增）
| 申报书要求 | 平台实现 |
|---|---|
| 基于主动元数据与知识图谱的语义建模与数据关系刻画 | `semantics.py` + `/api/catalog`、`/api/graph`：全生命周期主动元数据维度——结构信息、字段级跨源映射（6组实体）、质量事件、访问控制登记（各源分级+脱敏策略，见“智能编排调度”页治理元数据卡）与任务血缘链；ICD-10/检验项目术语对齐 |
| 数据虚拟化驱动的逻辑集成与统一访问 | `virtualization.py` + **SVC-FED-QUERY**：Patient360 单实体联邦视图之上，新增声明式跨源聚合（科室/诊断 × 就诊数/异常检验项/影像检查），SQLite ATTACH 跨文件原位计算、结果整合并附 provenance |
| 自动化编排与智能调度 | **`orchestration.py`**：5类规则（质量异常复核P0 / 身份对齐语义登记P1 / Schema漂移重映射校验P1 / 检验异常扫描P2 / 定时巡检P2），事件命中→优先级任务队列→串行执行器；每180s自动巡检 + UI手动触发；规则可在线启停，统计（命中/完成/平均时延）实时刷新 |
| 多技术协同的一体化机制（感知-建模-执行-反馈闭环） | 编排任务完成后回写 `orchestrated_action` 事件进入元数据流；服务调用计入统一统计并可被规则内部触发（如异常复核自动调 SVC-VITALS-TREND），驾驶舱“闭环”条带实时呈现六环节计数 |
| （演示增强）一页式管理看板 | **数据驾驶舱**页（默认首页 `/`）：8项KPI + 五源行数增长曲线(4s采样环形缓冲) + 事件类型分布/诊断Top8 + 编排任务与元数据事件实时面板；另有 `#orch` 智能编排调度页 |

## 第三部分「数据服务与流通模式」对应关系
| 申报书要求 | 平台实现 |
|---|---|
| 服务抽象建模与注册发布 | `governance.py` 服务登记簿：服务分级、所有者、生命周期、质量分、适用范围、语义标签；UI“服务流通”页支持登记并发布 |
| 服务组合与按需供给 | 3条医疗服务流（临床患者全景、科研队列分析、质量异常复核）；`POST /api/data-services/flows/{flow_id}/run` 执行服务链并返回阶段结果 |
| 跨域流通协同与使用计量 | 受策略约束的 `POST /api/data-services/{sid}/invoke`；记录调用主体、组织、授权结果、脱敏状态、时延和血缘流通记录 |
| 价值反馈优化 | 服务调用次数、成功率、平均响应、复用分与价值积分实时汇总，形成“抽象—供给—流通—反馈”闭环 |

## 第四部分「安全保障机制」对应关系
| 申报书要求 | 平台实现 |
|---|---|
| 分级分类、动态脱敏、隐私保护 | 6类医疗数据资产分为 L1-L4；患者姓名、电话、证件号、患者ID及病历文本按角色动态掩码；`POST /api/security/mask-preview` 可查看策略效果 |
| 细粒度动态授权 | 临床医生/分析师/科研人员/运维四类策略，按角色、资产分级、用途、跨组织标志实时返回 allow / allow_masked / deny |
| 全链路审计与责任追溯 | 每次访问决策、服务调用、流通阶段都生成审计号、主体、时间、操作、决策、理由与策略链；`GET /api/security/audit` |
| 风险预警与处置 | 越权、用途不符、跨域未授权自动生成风险；UI支持人工复核关闭；`GET /api/security/risks`、`POST /api/security/risks/{rid}/resolve` |

## 目录结构
```
medfabric/
├─ app.py               FastAPI入口：REST + SSE事件推送 + 监控线程(4s扫描) + 服务层调用统计
├─ medsim.py            数据资源层：五套医疗源生成(seed=42可复现) + StreamingSim实时业务流
├─ discovery.py         编织层·主动元数据引擎：适配器/指纹比对/事件总线EventBus
├─ semantics.py         编织层·语义建模：ENTITY_TYPES、FIELD_MAP、术语对齐、知识图谱构建
├─ virtualization.py    编织层·虚拟化：逻辑视图目录 + Patient360联邦查询(零拷贝)
├─ orchestration.py     第二部分·智能编排：规则引擎/优先级任务队列/定时巡检/治理元数据
├─ governance.py        第三+四部分·服务登记/流通计量/授权/脱敏/审计/风险处置
├─ static/index.html    前端单页（驾驶舱/架构/发现/图谱/Patient360/编排/服务/流通/安全）
├─ data/                （运行生成）his.db / lis.db / pacs.db / iot.db / emr/*.json
└─ start.bat            Windows一键启动
```

## 主要API
- `GET /api/overview` 源状态+规模统计；`POST /api/discovery/run` 手动触发扫描
- `GET /api/events?after=`、`GET /api/stream`(SSE) 元数据事件流
- `GET /api/catalog`（实体类型+字段映射+逻辑视图）、`GET /api/graph`（知识图谱JSON）
- `POST /api/query/patient_360 {patient_id}` Patient360联邦查询；`GET /api/search?q=` 术语对齐检索
- `GET /api/services`、`POST /api/services/{id}/invoke` 服务目录与调用（含 SVC-FED-QUERY 跨源联邦统计）

## 第二部分新增API

- `GET /api/cockpit` 一页式驾驶舱数据：KPI、五源行数时序(4s采样)、事件类型分布、诊断Top8、闭环六环节计数、最近编排任务
- `GET /api/orchestrator` 编排规则+治理元数据+计数器；`POST /api/rules/{id}/toggle` 启停规则；`POST /api/orchestrator/patrol` 手动巡检
- `GET /api/tasks?limit=60` 任务执行流（含触发事件血缘 seq → tid）

## 第三、四部分新增API

- `GET /api/data-services` 服务登记簿、组合流、调用计量、价值反馈与数据资产
- `POST /api/data-services/register` 登记/发布服务；`POST /api/data-services/{sid}/invoke` 按策略授权后调用
- `POST /api/data-services/flows/{flow_id}/run` 运行服务组合流并返回每个阶段的授权、结果和流通记录
- `GET /api/security/overview` 安全资产、策略、审计和风险总览
- `POST /api/security/authorize` 动态授权评估；`POST /api/security/mask-preview` 动态脱敏预览
- `GET /api/security/audit` 审计查询；`GET /api/security/risks` 风险查询；`POST /api/security/risks/{rid}/resolve` 风险处置

## 说明
- 演示数据为本地模拟（SQLite/JSON），接口形态按真实医院系统抽象设计，后续可替换适配器对接真实库；重启会重新生成基准数据集并重置实时流。
