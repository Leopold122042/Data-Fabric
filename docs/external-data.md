# 外部数据接入

整合 PythonProject9 的三个脚本：check.py 的 WebDAV 目录浏览、云盘.py 的 Excel 工作表映射、read.py 的 GitHub CSV 和 Titanic 字段映射。远程文件保持不变，下载内容载入当前浏览器会话的 DuckDB 内存数据库；这不是零拷贝，也不是源数据库实时订阅。手动刷新重新下载，查询使用最近一次成功快照。

## 启动

在本项目目录运行：

```sh
python3 -m venv .venv-external
.venv-external/bin/python -m pip install -r requirements-external.txt
.venv-external/bin/python initialize_data.py
.venv-external/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8321
```

如端口已有旧平台进程，先停止旧进程再启动，或使用其他端口。Windows 使用 `.venv-external\Scripts\python.exe` 执行对应命令。新增模块按需加载 DuckDB、requests、openpyxl 等依赖；未安装时原医疗模块仍可启动，外部页面提示安装依赖。

## 操作

进入顶部“外部数据接入”。GitHub 默认使用公开 Titanic CSV，选择自动识别或乘客映射后点击读取，得到 passengers 表。其他 UTF-8 CSV 选择原始字段模式，得到 github_csv 表。支持公开 GitHub blob 链接及 raw 链接，不支持私有仓库令牌。

南大云盘使用 WebDAV 用户名及 WebDAV 令牌，支持逐级浏览目录并选择支持格式的文件，也可直接填写路径。所有工作表读取第一条非空行为表头，重复和空列名自动处理，映射为 box_sheet_1 等表。Excel 各列为 VARCHAR；日期转为文本，公式使用文件中已缓存的计算值，不重新计算公式。所有单元格为空的工作表跳过。

```sql
SELECT * FROM passengers LIMIT 20;
SELECT passenger_class, COUNT(*) AS n,
       ROUND(AVG(CAST(survived AS INTEGER))*100, 2) AS survival_rate
FROM passengers GROUP BY passenger_class ORDER BY passenger_class;
SELECT * FROM box_sheet_1 LIMIT 20;
```

同一会话的两个来源可在 SQL 中显式关联，但平台不会把这些外部样例自动映射成医疗实体，也不会将它们混入五类医疗数据源的指标或公共事件流。逻辑目录保留来源、工作表、字段类型、行数及刷新时间，刷新记录仅在本会话可见。

## 范围和访问控制

只允许单条 SELECT / WITH 查询；禁用 DuckDB 外部访问和扩展自动安装。结果最多 200 行，查询最长 5 秒，单会话内存预算 128 MB。远程响应最多 20 MB，Excel 解压大小最多 80 MB、20 张工作表、256 列、500000 单元格，每张数据表最多 50000 行。

未连接时云盘凭据用于单次请求；点击连接云盘后凭据保留在当前会话的服务器内存中，断开或会话清理时释放，不写入源码、响应、日志或持久化配置。来源地址固定限制为 GitHub raw 和南大 Box，禁止重定向。不要将凭据提交到 Git。各浏览器会话逻辑表隔离，最多 8 个会话，空闲 30 分钟后在下次请求清理，重启进程清空；单进程运行，不使用多个 uvicorn workers。点击释放清空逻辑表和页面令牌。

会话隔离不是用户身份认证。外网使用必须通过 HTTPS 和受控入口；平台既有安全合规角色模拟不构成本页的真实登录认证。本功能只读源端，不上传、修改或删除 GitHub / 云盘文件。

## API 与测试

GET /api/external/status；POST /api/external/github/load、box/list、box/load、query、clear。均需同源请求及 X-Medfabric-External: 1，请保留 HttpOnly 会话 Cookie。连接时凭据只能放在请求体中；已连接的同一会话可省略凭据。

```sh
.venv-external/bin/python -m pip install httpx
.venv-external/bin/python -m unittest -v test_external_sources.py test_external_formats.py
```

测试使用生成的 Excel 和模拟 HTTP 响应，覆盖多格式解析、字段映射、重复表头、SQL 限制、刷新回滚、来源限制及会话隔离，不读取真实云盘。

## 多格式文件（新增）

GitHub 与南大云盘均支持以下格式，按文件扩展名自动识别；不支持任意二进制文件。GitHub 默认“自动识别格式与字段”，只有匹配 Titanic 字段的 CSV 才自动生成 passengers。

| 格式 | 映射方式 | 示例表名 |
| --- | --- | --- |
| CSV | 首行为字段名，普通 CSV 保留文本字段 | github_csv / box_csv |
| XLSX | 每个非空工作表一张表 | github_sheet_1 / box_sheet_1 |
| JSON | 对象数组每个对象一行；单个对象一行，字段取并集，缺失值为 NULL | github_json / box_json |
| JSONL、NDJSON | 每条非空行是一个 JSON 对象 | github_jsonl / box_ndjson |
| TSV | 首行为字段名，以制表符分列 | github_tsv / box_tsv |
| TXT | 每行一条记录，line_number 和 content 两列，保留空行 | github_txt / box_txt |
| Parquet | 保留列名，值映射为文本，空值为 NULL | github_parquet / box_parquet |

文本文件须为 UTF-8。JSON 嵌套对象和数组保留为 JSON 文本，不自动展开；包含 records/data 等包装字段时也按普通对象保存。通用格式的列为 VARCHAR，数值计算使用 TRY_CAST；JSON 不接受 NaN/Infinity。Parquet 需要 pyarrow，已加入依赖列表，限制解压大小为 80 MB。当前不支持 PDF、Word、图片的内容提取或 OCR。

读取成功后点击逻辑表旁的“预览前 20 行”。同一来源每次刷新会替换该来源的上一次文件，另一来源的表保留；解析失败保留旧快照。

```sql
SELECT * FROM box_json LIMIT 20;
SELECT * FROM github_txt WHERE content LIKE '%关键词%' LIMIT 20;
```

## RAR 压缩包

GitHub 和南大云盘都可以填写 `.rar` 文件地址，点击“读取或刷新文件”后显示压缩包内的文件名、大小和是否可读取。点击支持条目旁的“读取为逻辑表”，再到逻辑表目录预览或执行 SQL。支持包内 CSV、XLSX、JSON、JSONL、NDJSON、TSV、TXT、Parquet；其他文件只列出，不执行，不自动递归展开嵌套压缩包。

解析器基于 libarchive，支持其可解码的完整、未加密 RAR4 / RAR5。加密包、缺少分卷或不支持的压缩方式会报错。Mac 使用系统 libarchive，本机已安装 Python 依赖 libarchive-c；迁移到其他系统时还需提供系统 libarchive 动态库。

压缩包最大 20 MB，最多 1000 项，声明解压总大小最多 80 MB，单个选中文件实际解压最多 20 MB，单次解码最长 30 秒。解码在独立子进程中进行，不把条目写到磁盘，拒绝绝对路径、上级路径、链接和重复路径。再次选择文件时会重新下载并核对压缩包摘要；若源文件变化，必须刷新列表后重选。

读取目录不会替换已有逻辑表；只有内部文件成功解析后才替换同一来源的旧表。来源记录包含“压缩包路径 :: 内部文件路径”。RAR 列表仅在当前页面显示，释放会话时清除。

新增验证：`.venv-external/bin/python -m unittest test_external_rar -v`。

## ZIP 压缩包

GitHub 和南大云盘现均支持 `.zip`，与 RAR 使用相同入口：填写文件地址，点击“读取或刷新文件”，在压缩包列表中选择“读取为逻辑表”，然后预览或查询。使用 Python 标准库 zipfile，无需新增安装依赖。

支持标准库可解码的未加密 ZIP；加密 ZIP、损坏文件、不支持的压缩算法会报错。包内不支持的格式和嵌套压缩包只列出，不递归解压。单包 20 MB、最多 1000 项、声明解压总大小 80 MB、选中文件实际最多 20 MB、30 秒超时等限制与 RAR 一致。拒绝危险路径、链接、特殊文件和重复文件名；在内存中解压并验证选中文件，不写出内部路径。

选择条目前重新下载并校验摘要，文件变化须重新读取列表；成功载入后替换同来源的旧表，失败保留旧表。中文文件名支持 ZIP 标准编码；非标准旧编码文件名可能显示乱码，需要源端使用 UTF-8 重新打包。

测试：`.venv-external/bin/python -m unittest test_external_sources test_external_formats test_external_rar test_external_zip -q`。接口测试使用模拟下载和生成的压缩包，不访问真实云盘。


## 旧版 Excel XLS

支持 GitHub、云盘直接读取以及 RAR/ZIP 内选择 `.xls`，使用 xlrd 解析真正的二进制旧版 Excel。每个非空工作表生成一张逻辑表，第一条非空行作为表头，重复或空表头自动命名；字段保存为文本，日期转换为日期时间文本，公式使用缓存值而不重新计算。限制与 XLSX 相同。不执行宏，不支持加密文件或仅把 HTML 改名为 XLS 的文件。

安装依赖后重启服务，并重新读取压缩包列表，XLS 条目会显示读取按钮。测试文件生成工具 xlwt 仅用于开发测试，不是运行依赖。
