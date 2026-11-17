# 服务器部署与版本约定

## 目标环境

- 部署目录：`/mnt/medfabric`
- Conda 环境：`medfabric`（`/home/fuwuqi/.conda/envs/medfabric`，Python 3.11）
- 服务端口：`8321`
- 模型服务：`http://114.212.173.201:8080/v1`
- 模型：`qwen3.8-flash-next-iq3_s`（OpenAI 兼容 Chat Completions）
- 模型密钥：服务器私有文件 `/home/fuwuqi/.config/medfabric/api.env`
- 数据目录：`/mnt/medfabric/work/demo-data`
- 控制目录：`/mnt/medfabric/runtime`

服务默认绑定 `127.0.0.1`，避免未认证的演示身份直接暴露到公网。通过 SSH 隧道访问：

```sh
ssh -N -L 18321:127.0.0.1:8321 fuwuqi@114.212.171.117
```

保持隧道终端运行，在浏览器打开：
`http://127.0.0.1:18321/static/management/intake.html`

## 本机局域网演示

Windows 本地演示启动脚本 `start.bat` 和 `run_server.ps1` 默认监听 `0.0.0.0:8321`，并开启 `MEDFABRIC_DEMO_ALLOW_PRIVATE_NETWORK=1`。同一私有网络的其他设备可访问：

- 新展示页：`http://<运行平台电脑的局域网IP>:8321/static/report/index.html`
- 管理工作台：`http://<运行平台电脑的局域网IP>:8321/static/management/index.html`

将 `<运行平台电脑的局域网IP>` 替换为运行服务电脑的局域网 IPv4 地址。局域网演示入口只接受私有 IP，且只为远端演示设备提供平台工作台身份；数据授权、敏感字段访问审批和查询执行校验仍由数据平面处理。不要将该演示服务配置为公网可访问。若局域网设备仍无法连接，检查运行服务电脑的防火墙是否允许 Python/Uvicorn 的专用网络入站连接，不要关闭整个防火墙。

远端 `/mnt/medfabric` 服务不随此设置开放局域网或公网，仍绑定 `127.0.0.1` 并通过 SSH 隧道访问。

## 后续更新流程

每次更新先在本地项目分支修改和核对代码，再按以下顺序部署：

1. 更新 `VERSION`，并在 `CHANGELOG.md` 中按版本记录改动和日期。
2. 将本地项目代码上传到 `/mnt/medfabric`；不上传 `.git/`、`.venv/`、`2080ti.txt`，不覆盖远端 `runtime/` 和 `work/demo-data/`。
3. 按 `environment.yml` 更新 Conda 环境，重启 `medfabric.service`。服务器使用用户级 Conda 环境目录；不要更改共享 Conda 安装目录权限。
4. 检查服务状态、启动日志、当前版本文件和本机回环 HTTP 响应，再记录部署结果。

服务器无法直接访问 Conda/PyPI 时，可在联网的开发机下载适配 Linux x86_64、Python 3.11 的 wheel 到临时 wheelhouse，安全传输后使用 `PIP_NO_INDEX=1` 和 `PIP_FIND_LINKS` 执行 `conda env create`；不要关闭 TLS 证书校验。

不要把服务器密码、SSH 私钥或令牌写入代码、版本记录、服务日志或上传包。只有在明确需要重置演示数据时才单独替换远端数据目录。
