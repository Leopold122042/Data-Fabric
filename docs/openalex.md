# OpenAlex 接入

平台在服务端连接 OpenAlex Works API，将公开医学与健康科学文献纳入平台目录。首次启动会同步；之后默认每 24 小时检查一次。默认筛选 OpenAlex Medicine field 27，并保留按发表日期排序的最近 1,000 条记录。它是可更新的工作快照，不是 OpenAlex 全库副本。

API key 存放在项目根目录被 Git 忽略的 `.env` 中，变量名为 `OPENALEX_API_KEY`。连接器启动时自动读取，浏览器和接口响应不会返回 key。也可以用同名进程环境变量覆盖本地文件值。OpenAlex key 支持 Bearer Authorization；官方建议不要把 key 放进共享代码。

## 逻辑表

| 逻辑表 | 用途 |
|---|---|
| `openalex_works` | 论文标识、标题、DOI、发表日期和年份、文献类型、语言、引用次数、期刊、开放获取状态、摘要、更新时间 |
| `openalex_authors` | 作者 ID、名称、ORCID |
| `openalex_institutions` | 机构 ID、名称、国家或地区代码 |
| `openalex_sources` | 期刊或其他出版来源 ID、名称和类型 |
| `openalex_authorships` | 论文、作者、署名顺序、机构和原始署名之间的关系 |
| `openalex_topics` | 论文主题、主题分数、学科领域和子领域 |
| `openalex_references` | 论文及其引用的 OpenAlex ID |
| `openalex_yearly` | 当前同步快照按年份汇总的论文数与引用数 |

目录字段使用 OpenAlex 公开字段映射，并通过平台的逻辑表查询、专业查询和自然语言查询使用。作者、机构、主题、来源和引用关系均以 OpenAlex ID 建立，不会自动和医院患者记录关联。

## 可调用服务

- `SVC-OPENALEX-SEARCH`：按标题/摘要关键词、发表年份、作者、机构、主题和开放获取状态筛选。
- `SVC-OPENALEX-DETAIL`：按 OpenAlex ID 或 DOI 返回论文、作者、机构、主题和引用清单。
- `SVC-OPENALEX-STATS`：按年份返回当前同步范围的论文数量和引用总数。

服务使用当前本地快照。同步状态、最近成功时间、覆盖条数和逻辑表列表显示在“数据服务”页面。筛选器可通过 `MEDFABRIC_OPENALEX_FILTER` 调整；条数上限通过 `MEDFABRIC_OPENALEX_MAX_RECORDS` 设置（100–10,000），周期通过 `MEDFABRIC_OPENALEX_SYNC_SECONDS` 设置（至少一小时）。
