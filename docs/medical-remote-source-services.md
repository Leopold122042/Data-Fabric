# 六源医疗数据与查询服务

版本：0.1.14。六个来源均为项目生成的合成医疗演示数据：HIS、LIS、PACS、EMR、IoT、临床业务协同库。

## 存放位置

| 来源 | 远端位置 | 数据对象 |
|---|---|---|
| HIS | 南大云盘 `私人资料库/MedFabric/medical-demo-sources/v0.1.14/his.db` | 患者、就诊、医嘱 |
| EMR | 南大云盘 `私人资料库/MedFabric/medical-demo-sources/v0.1.14/emr.jsonl` | 125 份文书，每行一个 JSON 对象 |
| LIS | GitHub `medical-demo-sources/v0.1.14/lis.db` | 检验申请、检验结果 |
| PACS | GitHub `medical-demo-sources/v0.1.14/pacs.db` | 影像检查索引 |
| IoT | GitHub `medical-demo-sources/v0.1.14/iot.db` | 设备、生命体征 |
| 临床协同 | GitHub `medical-demo-sources/v0.1.14/clinical_operations.sqlite` | 慢病随访、科研队列、影像时效、检验报告 |

GitHub 仓库 `Leopold122042/Data-Fabric` 当前为公开仓库，因此其中只放项目生成的合成数据；HIS 和 EMR 放在南大云盘。GitHub 路径按版本冻结，更新数据时创建新版本目录并同步服务配置。

## 上传 HIS / EMR

1. 打开“数据接入”，点击“连接云盘”，在弹窗中输入南大云盘 WebDAV 用户名和令牌。
2. 点击“上传 HIS 与 EMR 到南大云盘”。服务从当前 `MEDFABRIC_DATA_DIR` 读取 HIS SQLite 和 EMR JSON 文件；HIS 会先生成包含 WAL 已提交内容的独立 SQLite 快照，再转换为可直接读取的单文件格式，EMR 转为 JSONL，随后写入固定版本目录。快照临时文件上传后立即删除。
3. 令牌不写入浏览器存储、源码或磁盘；它只在当前浏览器会话对应的服务端内存中保留，空闲 30 分钟或断开后失效。其他电脑需分别连接自己的云盘会话。

上传接口为 `POST /api/external/medical/upload`。它只接受平台方演示身份，校验本机源文件结构后上传；目标目录固定，不接受任意路径。

## 调用六源服务

查询中心调用已登记服务 `SVC-MEDICAL-REMOTE-QUERY`：

```json
{
  "params": {
    "source_ids": ["his", "lis", "pacs"],
    "sql": "SELECT p.patient_id, p.name, v.dept FROM his.patient p LEFT JOIN his.visit v ON v.patient_id=p.patient_id LIMIT 20"
  },
  "purpose": "research",
  "cross_org": false
}
```

接口路径：`POST /api/data-services/SVC-MEDICAL-REMOTE-QUERY/invoke`。HIS/EMR 查询要求同一浏览器先连接南大云盘；GitHub 来源可直接读取。`source_ids` 明确限定本次拉取的来源，SQL 只允许 `SELECT` / `WITH`，最多返回 200 行、执行 5 秒。来源表使用 `his.patient`、`lis.result`、`pacs.study`、`emr.dossiers`、`iot.vitals`、`operations.research_cohort` 等限定名。

每次服务调用重新 GET 选中的来源文件，校验登记表和字段结构，然后把源表行复制到一次性 SQLite 内存连接中查询；结束时释放连接。返回数据包含来源位置、文件 SHA-256、读取字节数、服务耗时和查询时间，便于确认实际读取了哪些来源。未选来源不会读取；来源不可用或结构不符时整次查询失败，不使用本地旧副本或演示结果补齐。源文件必须是独立 SQLite 文件格式；服务不会把 WAL 侧车文件单独当作数据库读取。

服务仍经过现有平台服务调用审计与输出治理。结果按列名执行脱敏；平台的来源读取审计与业务数据调用审计分别留存。当前实现是“按需下载源文件、请求内联邦计算”，并非 SQLite / WebDAV / GitHub 的数据库端 SQL 下推。
