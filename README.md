# Data Fabric
For presentation and exploration only

## 双人开发与 A 管理工作台

代码已按管理域 A、执行域 B 拆分。文件所有权和对接接口见
[开发边界](docs/development-ownership.md)，本地实现与待对接内容见
[A 实现清单](docs/management-implementation.md)。公共入口由 A 统一接线。

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python initialize_data.py
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8321
```

访问 `http://127.0.0.1:8321/static/management/index.html`。
Windows 首次安装使用 `python -m venv .venv` 和
`.venv\Scripts\python.exe -m pip install -r requirements.txt`，然后运行 `start.bat`
或 `powershell -File run_server.ps1`；两个入口都会先执行安全初始化。

若双击启动失败，窗口会保留具体错误和实际使用的 Python 路径。缺少依赖时，
使用窗口提示的解释器安装 `requirements.txt`。若 `.venv\Scripts\python.exe` 不存在，
先运行 `python -m venv .venv` 修复项目环境，再安装依赖；其他应用的 Python 环境不等同于本项目环境。

默认使用本机 `data/`，控制面持久化到 `runtime/`。默认不启动模拟增长；
两人分别配置 `MEDFABRIC_DATA_DIR` 和 `MEDFABRIC_CONTROL_DIR`，避免共用可写数据库。
当前身份是本机演示角色。外部 LLM、真实认证、B 控制和版本化方案接口尚未接入；桌面版已有云盘连接与目录扫描正常保留。

## 数据初始化与版本管理

Git 保留生成器、医疗业务场景定义和测试夹具。四个运行数据库
`data/{his,lis,pacs,iot}.db`、SQLite 临时文件、`data/emr/`、运行日志与缓存不进入版本管理。

`python initialize_data.py` 仅在目录不存在或为空时生成 180 名患者的基础数据，
生成完成并检查四个数据库后才发布到目标目录。服务启动时继续初始化需求、执行、交付和协作工单案例。
完整数据目录再次初始化时原样保留；非空但不完整、损坏的目录会报错，保留所有现有文件。
旧入口 `python medsim.py` 也采用相同规则，不再删除整个数据目录。

自定义目录可使用 `python initialize_data.py --data-dir PATH`；启动服务前须将
`MEDFABRIC_DATA_DIR` 指向同一目录。该环境变量同时影响初始化、服务与清理工具。
需要一套新数据时选择新的空目录；恢复已有业务记录时使用完整备份。
初始化中断如留下 `.目录名.initialize.lock`，先确认对应初始化进程已退出，再检查并移除锁文件后重试。

已有工作副本升级前，请将 `data/` 复制到仓库外备份：取消跟踪的提交在其他副本拉取时可能移除原受跟踪文件。
初始化能重建基础数据和预置场景，不能恢复用户新增的业务记录；当前工作副本执行 `git rm --cached` 后仍保留本地文件。

## 控制数据增长与备份

只有显式设置 `MEDFABRIC_SIMULATE=1` 才启用持续生成。以下上限在启动时读取，必须为正整数：

| 环境变量 | 默认值 | 达到上限后的行为 |
|---|---:|---|
| `MEDFABRIC_MAX_PATIENTS` | 2000 | 暂停新增患者，已开始的跨系统流程继续完成 |
| `MEDFABRIC_MAX_VITALS` | 100000 | 暂停新增监护记录及需要监护记录的新患者 |

上限以数据库中的实际记录数计算，重启不会重置额度。已有超限数据保留，不在启动时裁剪。
这些限制适用于单进程本机持续生成，不限制用户主动录入或外部系统导入；不要让多个实例共用可写数据目录。
默认启动脚本将日志输出到终端，不再持续追加仓库内的日志文件。

## 按患者编号清理数据

Stop all MedFabric server processes before cleanup. The default cutoff keeps
patient numbers up to and including P2000, not 2000 rows per table. Related visits,
orders, lab results, studies, vitals and EMR JSON for larger numbers are removed.
Device records and retained patients' history remain unchanged.

```powershell
python cleanup_data.py
python cleanup_data.py --apply
python cleanup_data.py --apply --backup-keep 3 --backup-max-mib 512
```

The first command previews counts without committing changes. The second saves a
complete database/EMR backup under `data_backups/` next to the selected data directory,
applies cleanup, and compacts SQLite files. For a nondefault server port, pass `--port PORT`.
No-op cleanup creates no new backup. The tool defaults to retaining at most three
successful managed backups within 512 MiB; both limits can be changed as shown above.
It always keeps the newest recovery point, even if that one backup exceeds the size budget.
Old unmarked backups, incomplete/failed recovery points and backups of other data directories
are preserved and excluded from automatic rotation. Only backups with the tool's completion
manifest are eligible; paths and links are checked before deletion. Backups are excluded from Git.

If cleanup fails after changes begin, keep the server stopped and restore the
entire backup as `data/` (move the incomplete directory aside first, including
SQLite WAL/SHM sidecars); do not mix files from different snapshots.

With `MEDFABRIC_SIMULATE=1`, restarting the server resumes data generation
after the largest remaining HIS patient number, subject to the limits above.
Patient-number cleanup is a separate maintenance action; it does not trim retained
patients' vital history. Neither startup nor backup rotation invokes patient cleanup automatically.

## 外部数据接入

保留桌面版云盘连接、自动扫描目录、虚拟表及查询功能。打开 `/static/index.html#external`，详见 [配置与使用说明](docs/external-data.md)。`requirements.txt` 已包含外部接入依赖。

已有 `.venv-external` 可直接用 `.venv-external/bin/python -m pip install -r requirements.txt` 更新依赖，先执行 `.venv-external/bin/python initialize_data.py`，再执行 `.venv-external/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8321`。

## B 执行与交付

访问 `/static/execution/index.html`。支持方案确认、本地授权、执行任务、受控结果与凭证，另提供医疗小样本演示。详见 [B使用与实现边界](docs/execution-implementation.md)。新接口需重启服务生效。
