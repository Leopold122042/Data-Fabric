# 双人开发文件所有权

本次从 c9d6eca 拆分，继续使用 FastAPI 与原生 HTML/JavaScript。不需要为新增功能回到旧单文件编辑。

| 所有者 | 可以修改 | 约定 |
|---|---|---|
| A 管理域 | `management/**`、`api/management/**`、`contracts/management/**`、`static/management/**`、`discovery.py`、`semantics.py`、`tests/management/**` | 数据目录、概念映射、服务定义、需求与规格、评价与校准 |
| B 执行域 | `execution/**`、`api/execution/**`、`contracts/execution/**`、`static/execution/**`、`virtualization.py`、`orchestration.py`、`governance.py` | 查询执行、规划与调度、授权适配、流通交付与运行证据 |
| A 公共集成 | `app.py`、`platform_core/**`、`api/platform_routes.py`、`static/index.html`、`static/shared/**`、`static/workspace*`、`static/cockpit*`、`medsim.py`、依赖和启动配置、集成测试 | B提出接口变更，由A接线；不把业务继续塞回app.py或index.html |

## 本次实际迁移

- `app.py`只创建应用。`platform_core/bootstrap.py`装配路由、身份、控制库及生命周期；`Runtime`注入双方实现，不再由业务模块`import app`读取全局状态。
- 原管理接口迁`management/legacy.py`与`api/management/legacy_routes.py`；新增管理接口在`api/management/routes.py`。
- 原执行函数迁`execution/legacy.py`与`api/execution/legacy_routes.py`；B可在自己的包内继续拆分。旧调度与查询核心保持原文件。
- 服务种子在`management/services/seed.py`；资产说明在`management/catalog/assets.py`；固定流种子在`execution/flows.py`。`GovernanceState`通过只读快照访问A服务登记，`record_service`不再自动创建已发布服务或回写服务分数。
- `static/index.html`保留旧DOM与导航，但内联JS已拆到`static/shared/`、`static/management/legacy-catalog.js`与`static/execution/legacy-*.js`。这些兼容脚本仍有既有全局变量依赖，不能当作已完全解耦的新组件。双方新页面用独立文件，勿继续向旧DOM扩业务。
- A工作台位于`/static/management/index.html`。现有新驾驶舱`/static/workspace.html`及旧页签保持可访问。

## 运行与数据隔离

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8321
```

Windows可继续使用原启动脚本（其原有虚拟环境需安装requirements.txt）。Python建议3.12。

- 默认不启用自动写入医疗源的模拟增长，也不启动自动流通和定时巡检。显式设置`MEDFABRIC_SIMULATE=1`恢复这些演示动作。
- `MEDFABRIC_DATA_DIR`指定源数据目录；默认仍为项目`data/`。数据缺失时提示先准备数据，不自动生成覆盖。
- `MEDFABRIC_CONTROL_DIR`指定控制面目录，默认项目`runtime/`。A写`management/control.db`，B写`execution/control.db`，均已忽略进Git。
- 两个人应使用不同数据与控制目录。测试从临时目录生成虚构数据，不使用仓库现有数据库。
- 当前服务器按单进程本地演示使用。旧执行队列和SSE仍在内存中，不支持多worker共享；真实身份、跨机构部署由B后续接入。
- 工作台的四个角色是明确标识的本机演示账号，非真实认证。默认只允许回环客户端访问A管理API并拒绝跨站Origin；实际部署须注入可信身份适配器，不能把演示角色头当作登录。

## A初次交付时的接口契约（现状见B实现清单）

契约声明在`contracts/management/ports.py`与`contracts/execution/ports.py`。

1. 从`runtime.management.semantic.resolve(concept_id)`取得已确认、版本一致的映射；不要再直接改`FIELD_MAP`。原Patient360仍使用原静态语义，通用规划器需接此接口。
2. 从A的`store.require('service', id, version)`和`store.require('spec', id, version)`读取固定定义。A已提供持久化版本、`store.events(after)`及回放游标；B需持久化自己的消费游标并处理旧规格/服务失效。
3. 调用A的`register_design({...})`提交`view/workflow/constraint/plan`快照，携带`object_id/object_kind/object_version/environment/definition/dependencies`。已有版本不可覆写。
4. 通过B自己的`Evidence.record`写运行证据。必须携带实际加载版本与环境；`status/task_scope/actor/latency_ms/observed/authorization/delivered/quality_passed`按实际提供，不伪造缺失数据。A只通过`list/get`读取，不写B库。当前发布样例验证已接真实本地handler并写证据；旧流程的全部运行证据接入仍归B。
5. 给`management.reconciliation.controller`注入ControlPort。必须返回真实命令状态；当前未接入时暂停和局部修正返回503，A不会显示已完成。
6. 给`management.services.plan_reader`注入PlanPort。稳定组合登记要求相同方案版本至少三次成功证据，保留子图和参数；当前B尚无版本化方案，入口明确报未接入。
7. `execution/validation.py`是本地演示验证桥，只能执行已有handler，真实模型、隐私与权限验证需替换此适配器。不能以通用输出类型检查代替模型有效性验证。

B原有未统一授权的旧调用路径、撤销阻断、六项交付复核、凭证，以及完整医疗小样本演示不属于本次A实现。此次只为服务生命周期兼容增加了_dispatch状态检查，并提供本地样例验证桥；这不等于完成B安全改造。

## 验证命令

```sh
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python -m unittest test_cleanup_data -v
node --test test_graph.js test_exploration.js test_cockpit_model.js
```

`-t .`不可省略，否则tests/management会遮蔽业务management包。新模块业务测试放对应所有者目录；新交叉流程放`tests/integration/`。不要执行仓库里历史_apply、_patch脚本来合并这次结构。

## 桌面版外部接入的归属（2026-10-23）

当前实际项目：`/Users/chenjiale/Desktop/medfabric/Data-Fabric`。保留此版本原有云盘和虚拟表能力，未覆盖 `data/` 或 `private_catalog/`。原 GitHub 路径副本未在此次迁移中继续修改。

- A：`external_sources.py`（连接与解析）、`external_discovery.py`（扫描及目录）、`static/external.js`、`static/external-workspace.css`、`static/virtual-fields.js`，对应外部源、扫描和页面测试。
- B：`external_virtual.py`、`virtual_planner.py`、`virtual_cache.py`、`external_rar.py`，对应虚拟查询、计划、缓存和压缩读取测试。
- A 统一接线：`external_api.py`、`requirements-external.txt`、`static/index.html`；B 通过既有函数或新增独立路由提供能力，避免同时改同一文件。
- 外部已保存目录保持原会话/用户访问语义，不自动复制私有云盘路径到公共医疗目录。两个页面互有导航入口，外部页位于 `/static/index.html#external`。
- 云盘连接使用此版本既有 WebDAV 能力；“未接入”仅指尚未提供的 LLM、其他外部系统及 B 新控制接口，不能理解为云盘功能不存在。

## 外部能力与平台融合

- 驾驶舱主导航提供外部接入及 A 管理工作台入口。驾驶舱、旧数据发现/图谱/查询/服务页、A 目录/语义/服务页共用 `static/shared/external-integration.js` 和 `.css`，由 A 维护。
- 各页展示同一份保存目录和当前会话连接状态；查询链接携带 `external_table` 定位来源表或统一表。扫描更新后通知共享资源视图刷新，不自动查询源数据。
- 平台方在已连接云盘的会话勾选表，点击“将所选表结构纳入平台目录”，明确共享表名及字段结构。新增 `/api/external/platform/publish` 经既有同源及会话校验；拒绝其他角色、未连接用户和不属于该用户的表。
- A 的 `management/catalog/external.py` 仅白名单导入结构，通过不透明来源 ID 接入版本、字段、映射与影响分析；不保存凭据、路径、行数据或样本行数。内部登记不是通用可读 API 资源。
- 元数据监控每轮同步已纳入的表结构，变化保留对象 ID、增加版本、触发已确认映射复核；源定义删除留下墓碑。未选择的新增表仍需显式纳入。云盘源文件本身的变化仍需先通过既有扫描更新保存目录。
- 当前融合覆盖资源发现、结构治理和查询导航。外部资源不会自动成为已发布服务或自动混入患者统计；B 通用执行器和发布验证接入仍按原契约推进。

## B 本地实现更新（2026-10-24）

B工作台 `/static/execution/index.html` 已接线。PlanPort、ControlPort、本地任务/授权/交付/凭证及逻辑视图已实现，以上初次交付的“未接入”描述保留为历史契约说明。当前能力、验证范围和仍待外部接入项目以 [B实现清单](execution-implementation.md) 为准。`tests/execution/` 由B维护。
