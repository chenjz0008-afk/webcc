# WebCC

WebCC 是一个用于管理多个 Claude 网页账号的后台和 API 网关。你可以在页面里录入账号和代理，查看容器、出口 IP、有效期和请求情况，也可以通过一个平台密钥调用账号池。

每个账号独立运行一个官方 ClewdR 容器。WebCC 负责账号资料、容器管理、请求分配和版本更新；ClewdR 负责连接上游。两者分别维护，本项目没有修改 ClewdR 的源码。

本仓库从 2026 年 10 月 6 日服务器正在运行的项目导出。包含后端、前端源码、当时部署的静态页面、测试和部署配置。账号库、登录密码、sessionKey、代理凭据、证书私钥和历史备份保留在服务器，不进入 Git。

完整工具流程的模块边界、网页内置能力和后续验收见 [工具能力架构](docs/TOOL-CAPABILITY-ARCHITECTURE.md)。

## 架构

```mermaid
flowchart TD
    Browser[管理页面 / API 调用者] --> Nginx[Nginx HTTPS :443]
    Nginx --> Manager[WebCC · 127.0.0.1:9000]
    Manager --> Store[PostgreSQL · 状态、密钥与文件]
    Manager --> Limits[Redis · 共享请求限流]
    Manager --> WorkerA[官方 ClewdR · 账号 A]
    Manager --> WorkerB[官方 ClewdR · 账号 B]
    WorkerA --> ProxyA[账号 A 的代理]
    WorkerB --> ProxyB[账号 B 的代理]
    ProxyA --> Claude[Claude 上游]
    ProxyB --> Claude
```

后端使用 Python 标准库，通过 Docker CLI 管理容器。前端使用 React、MUI 和 Vite。Nginx 提供 HTTPS，并将页面、管理接口和模型请求转发给 WebCC。账号容器使用随机本地端口，只有网关入口对外提供服务。

2026-10-08 生产已切换到 PostgreSQL 和 Redis。文件按当前部署选择保存为 BYTEA。中心调度保存持久占用，进程重启不会自动释放未确认结束的任务。节点 Agent 使用 Starlette、HTTPX 和 Uvicorn，目前在隔离环境验收，尚未启用跨服务器生产路由。迁移步骤见 [迁移与恢复](docs/POSTGRESQL-MIGRATION.md)，扩容边界见 [多服务器架构](docs/MULTI-SERVER-ARCHITECTURE.md)。

| 部分 | 职责 |
| --- | --- |
| `manager.py` | 账号和凭据、代理检查、容器启停、平台认证、并发、请求转发和异常处理 |
| `egress.py` | 账号容器代理出口白名单与启动前防火墙恢复 |
| `updater.py` | 检查 ClewdR 官方版本、候选容器验证、逐个更新和失败回退 |
| `frontend/src/pages/` | 工作台、账号、资源、更新和 API 接入页面 |
| `frontend/src/components/` | 后台布局、账号表单、详情抽屉、凭据字段和复制组件 |
| `static/` | 线上前端构建快照（2026-10-07 同步）；开发时由 Vite 重新生成 |
| `deploy/` | 安装脚本、systemd 单元、服务器 Nginx 配置和快照清单 |
| `tests/` | 使用模拟账号服务的后端测试，不需要真实 Cookie |
| `docs/` | 部署时原 README、后续兼容方案和分支维护说明 |

## 当前服务器

下面的信息对应这次导出时的部署，不代表安装脚本会自动创建同样的服务器。

| 项目 | 当前配置 |
| --- | --- |
| 公网地址 | `165.154.205.213` |
| 系统 | Ubuntu 24.04 LTS，x86_64 |
| CPU | 2 个 vCPU |
| 内存 | 约 2 GB，系统可见 1965 MiB |
| 系统盘 | 约 58 GiB，导出时使用约 2.5 GiB |
| Swap | 未配置 |
| 管理页面 | https://165.154.205.213/ |
| API Base URL | `https://165.154.205.213/v1` |
| 后端监听 | `127.0.0.1:9000` |
| 项目目录 | `/opt/clewdr-manager` |
| 私有数据目录 | `/var/lib/clewdr-manager` |
| 环境文件 | `/etc/clewdr-manager.env` |
| systemd 服务 | `clewdr-manager.service` |
| HTTPS | Nginx，证书路径位于 `/etc/letsencrypt/live/165.154.205.213/` |
| 当前 ClewdR 提交 | `061c6d8ac9187148f50c8d806b56962a7f222b6c` |
| 当前镜像摘要 | `sha256:a598cc2954171913fe278ac6e4cedf8dfe6fe7a20437798362765676ed9e5813` |

详细的文件校验和、上游版本和非敏感运行参数见 [服务器快照](deploy/server-snapshot.json)。当前 systemd 配置见 [服务快照](deploy/server-systemd.service)，Nginx 配置见 [Nginx 快照](deploy/server-nginx.conf)。Nginx 快照包含全局配置上下文，迁移时应按新机器的文件布局拆分使用。

管理页面用 `CLEWDR_ADMIN_PASSWORD` 登录，模型接口用 `CLEWDR_PASSWORD` 调用。这两个密码用途不同；真实值从服务器环境文件管理，仓库只提供模板。

## 已实现的功能

账号页面采用表格和右侧抽屉，可以录入、导入和修改账号资料。抽屉提供代理连接串、邮箱凭据、sessionKey、UA、系统标签和容器详情，各项支持复制。敏感字段默认遮挡。

生产已启用账号出口白名单，代理故障不允许直连；新增账号要求固定公网 IPv4 代理。配置、迁移与验收见 [出口保护](docs/EGRESS.md)。

创建账号前会检测代理出口 IP 和 HTTPS 耗时。账号独立使用配置目录和代理。UA 与系统标签目前用于资料展示，没有替换 ClewdR 内置的请求指纹。

sessionKey 的默认预计期限按导入时间加 29 天计算。这是平台提醒规则，不是上游确认的实际 Cookie 有效期。账号到达填写或预计的期限后不再接收新请求；sessionKey 需要人工更新。

当前请求策略如下：

| 配置 | 当前行为 |
| --- | --- |
| 总并发 | 最多 4 个请求 |
| 每账号并发 | 1 个请求 |
| 等待队列 | 16 个，等待最多 15 秒 |
| 网关尝试 | 每个请求最多尝试 3 个不同账号 |
| 网关总预算 | 150 秒；单次连接和读取受 60 秒及剩余预算限制 |
| 连续故障 | 连续 3 次服务器或连接类失败后隔离账号 |
| 401 / 403 | 隔离，检查后人工恢复；不据此直接认定账号被封 |
| 429 | 冷却后重新参与调度 |
| 空回复 | 不作为成功返回，尝试其他资源；不单独计为隔离故障 |
| 已开始输出 | 不切换账号重放请求 |
| 上游更新 | 每 24 小时检查官方 master，候选验证通过后逐个更新 |

ClewdR 自身的重试与网关的不同账号重试是两个层次。现有账号配置保持 `max_retries = 5`、`skip_restricted = true`。其他账号配置以服务器各自的 TOML 为准。

更新验证失败时保留现有版本。官方镜像更新不会替换 WebCC 自己的 Python 代码或前端；管理平台需要通过本仓库单独发布。

## API 与当前边界

目前提供网页账号的 OpenAI 兼容聊天接口和 Anthropic Messages 入口。调用示例和模型范围见 [API 文档](API.md)，错误策略见 [错误处理说明](ERROR-HANDLING.md)。

当前属于部分协议兼容，不能等同完整官方 Claude API。网页通道没有完整传递自定义工具和工具结果；免费账号可能由上游自动选择模型。模型名、结束原因和用量也存在上游转换限制。

`/code` 路由存在，但此次服务器隔离测试没有在 100 秒内取得真实回复，尚未确认可用。完整工具、Files、Batches、官方缓存和服务端工具属于后续兼容工作，不能从普通聊天成功推断支持。

剩余工作与逐项验收要求见 [待办清单](TODO.md)。完成并验收后从清单删除，结果保留在 [变更记录](CHANGELOG.md)。

后续目标是让 Anthropic SDK 通过平台 Base URL 和平台密钥使用明确支持的功能，按模型及能力选择合适上游。方案见 [Claude API 兼容计划](docs/CLAUDE-API-PLAN.md)。

## 与外部项目的关系

| 项目 | 本项目如何使用 |
| --- | --- |
| [ClewdR](https://github.com/Xerxes-2/clewdr) | 实际运行的上游代理，每账号使用官方镜像。本仓库不是其 fork，没有修改上游源码 |
| [Sub2API](https://github.com/Wei-Shaw/sub2api) | 参考代理出口检测、并发与账号调度思路，没有部署它的数据库、计费或整套后台 |
| React / MUI / Vite | 前端实际依赖，版本由 `frontend/package-lock.json` 固定 |
| Docker / Nginx / systemd | 服务器实际使用的容器、HTTPS 和进程管理组件 |
| [LiteLLM](https://docs.litellm.ai/docs/pass_through/anthropic_completion) 等适配器 | 兼容方案研究对象，当前没有接入，不是运行依赖 |
| Anthropic | 当前账号连接的服务提供方，本项目是独立管理工具 |

## 构建和测试

需要 Python 3.11+。前端需要 Node.js 22.12+ 或其他满足当前 Vite 要求的版本。生产部署还需要 Linux、Docker、systemd 和 Nginx。

```bash
cd frontend
npm ci
npm run build
node test_accounts.mjs
cd ..
python3 -m unittest discover -s tests -v
```

构建和单元测试不需要真实账号。真实生成测试会访问上游和消耗额度，应在隔离环境执行，并分别记录模拟和真实结果。

## 部署和版本维护

```bash
sudo bash deploy/install.sh
```

首次安装会创建 `/etc/clewdr-manager.env` 模板。填写两个不同的强密码后再次运行。脚本依赖已安装的 Docker，不负责配置 Nginx、证书或导入账号。部署前先生成 `static/`，迁移时调整地址、证书路径和环境参数。

当前运行版本在 Git 中保存为 `server-baseline-2026-10-06` 标签，后续兼容开发使用 `feat/claude-api-compatibility` 分支。小提交分别完成修改和测试，通过后再考虑发布；分支推送不会自动部署服务器。

源码回滚使用 Git，账号资料和运行状态使用服务器私有备份。代码回滚不能恢复 Cookie，也不能撤销已经执行的上游请求。具体步骤见 [开发与回滚](docs/DEVELOPMENT.md)，日常操作见 [运维说明](OPERATIONS.md)。

## 网页工具实验策略

候选代码提供默认关闭的 prompt-v1 实验策略，独立于官方 ClewdR。共享逻辑在 web_tools，主网关负责鉴权、选号、取消和失败处理，客户端执行自己的业务工具。实际模型身份、官方 strict 和 thinking 签名不属于本模式。

[实验接口](docs/EXPERIMENTAL-TOOLS-API.md)说明开启条件和 SDK 用法；[候选验收](docs/WEB-TOOL-GATEWAY.md)记录普通与 SSE 真实流程。测试需安装 requirements-web-tools.txt 的可选依赖，真实 SDK 测试还需 anthropic；正常模式默认关闭，不依赖 SDK。当前生产未启用此策略。
