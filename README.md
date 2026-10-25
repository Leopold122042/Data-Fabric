# Data Fabric
For presentation and exploration only

## 双人开发与 A 管理工作台

代码已按管理域 A、执行域 B 拆分。文件所有权和对接接口见
[开发边界](docs/development-ownership.md)，本地实现与待对接内容见
[A 实现清单](docs/management-implementation.md)。公共入口由 A 统一接线。

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8321
```

访问 `http://127.0.0.1:8321/static/management/index.html`。
默认使用现有 `data/`，控制面持久化到 `runtime/`。默认不启动模拟增长；
两人分别配置 `MEDFABRIC_DATA_DIR` 和 `MEDFABRIC_CONTROL_DIR`，避免共用可写数据库。
当前身份是本机演示角色。外部 LLM、真实认证、B 控制和版本化方案接口尚未接入；桌面版已有云盘连接与目录扫描正常保留。

## Clean generated demo data

Stop all MedFabric server processes before cleanup. The default cutoff keeps
patient numbers up to and including P2000, not 2000 rows per table. Related visits,
orders, lab results, studies, vitals and EMR JSON for larger numbers are removed.
Device records and retained patients' history remain unchanged.

```powershell
python cleanup_data.py
python cleanup_data.py --apply
```

The first command previews counts without committing changes. The second saves a
complete database/EMR backup under `data_backups/`, applies cleanup, and compacts
SQLite files. Backups are excluded from Git. Do not delete the backup until the
result has been checked. For a nondefault server port, pass `--port PORT`.

If cleanup fails after changes begin, keep the server stopped and restore the
entire backup as `data/` (move the incomplete directory aside first, including
SQLite WAL/SHM sidecars); do not mix files from different snapshots.

With `MEDFABRIC_SIMULATE=1`, restarting the server resumes data generation
after the largest remaining HIS patient number. Simulation is disabled by default. Cleanup is a snapshot operation, not a permanent size limit.
Previously tracked files removed by cleanup will appear as deletions in Git;
review these together with the database changes when publishing a data snapshot.

## 外部数据接入

保留桌面版云盘连接、自动扫描目录、虚拟表及查询功能。打开 `/static/index.html#external`，详见 [配置与使用说明](docs/external-data.md)。`requirements.txt` 已包含外部接入依赖。

已有 `.venv-external` 可直接用 `.venv-external/bin/python -m pip install -r requirements.txt` 更新依赖，再执行 `.venv-external/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8321`。

## B 执行与交付

访问 `/static/execution/index.html`。支持方案确认、本地授权、执行任务、受控结果与凭证，另提供医疗小样本演示。详见 [B使用与实现边界](docs/execution-implementation.md)。新接口需重启服务生效。
