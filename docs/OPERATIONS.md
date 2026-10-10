# 配置与运维

## 服务器位置

| 项目 | 路径 / 地址 |
|---|---|
| 项目代码与前端产物 | `/opt/clewdr-manager` |
| 管理器配置 | `/etc/clewdr-manager.env`，600 权限 |
| 账号登记表 | `/var/lib/clewdr-manager/registry.json`，600 权限，包含内部凭证 |
| 独立账号配置 | `/var/lib/clewdr-manager/accounts/<id>/clewdr.toml`，600 权限 |
| 容器挂载 | 上述账号目录 → `/etc/clewdr`，读写，持久化 |
| 备份 | `/var/lib/clewdr-manager/backups/`，含敏感账号配置 |
| 入口 | Nginx HTTPS → `127.0.0.1:9000` |
| 工作容器 | `clewdr-<id>`，仅绑定服务器 127.0.0.1 的独立端口 |
| 原来的单进程部署 | 文件保留，`clewdr.service` 停止并禁用 |

## 管理器参数

编辑 `/etc/clewdr-manager.env` 后执行 `sudo systemctl restart clewdr-manager`。重启会中断正在传输的请求，选择空闲时操作。已生成的账号配置不会因为修改模板自动重写。

| 参数 | 当前默认值 | 含义和建议 |
|---|---|---|
| `MANAGER_BIND` | `127.0.0.1` | 只让本机 Nginx 访问，不直接暴露管理后端 |
| `MANAGER_PORT` | `9000` | 本机管理器监听端口 |
| `MANAGER_DATA` | `/var/lib/clewdr-manager` | 账号、状态、配置、备份根目录，不要删除 |
| `MANAGER_IMAGE` | 官方 v0.13.5 镜像；部署时固定 digest | 首次启动镜像。登记表里已批准的镜像优先，修改它不会直接覆盖既有账号 |
| `MANAGER_MAX_INFLIGHT` | `4` | 全局同时处理上游请求上限；每账号固定为 1，有几个健康账号才有对应有效并发 |
| `MANAGER_QUEUE_SIZE` | `16` | 最多等待的请求数，满了返回 429 |
| `MANAGER_QUEUE_SECONDS` | `15` | 等待空闲账号最多秒数，超时返回 429 |
| `MANAGER_RETRY_ATTEMPTS` | `3` | 每请求最多尝试的不同账号数，含首次，范围 1–5 |
| `MANAGER_FAILURE_THRESHOLD` | `3` | 连续通道失败达到此次数才隔离，成功生成清零，最小 2 |
| `MANAGER_REQUEST_SECONDS` | `150` | 读取完请求体后的总处理时间预算 |
| `MANAGER_WORKER_SECONDS` | `60` | 单次连接和读取等待上限，受总预算约束 |
| `MANAGER_UPDATE_SECONDS` | `86400` | 检查作者版本的周期，下限 60 秒，建议保持 86400。按已保存的 checked_at 计算到期，重启不会重置计时 |
| `MANAGER_FOLLOW` | `master` | 跟随官方 master 提交；可改 `release` 只跟正式发布 |
| `MANAGER_AUTO_UPDATE` | `true` | 自动验证并部署；false 定时仅检测。页面“检测并更新”仍主动执行升级 |
| `CLEWDR_PASSWORD` | 沿用原 API 密码 | 对外 API Bearer 或 x-api-key 认证，不能管理容器 |
| `CLEWDR_ADMIN_PASSWORD` | 沿用原管理员密码 | 前端和 `/admin/*`，不能当作对外 API 密码，两密码必须不同 |

当前服务器约 2 vCPU / 2 GiB RAM。单容器限制 256 MiB RAM、1 CPU 配额、64 进程，总请求并发 4。4 不是无限添加容器的内存保护：大量容器即使闲置也占资源，需要按实际内存增长扩容。没有自动横向扩容或跨服务器编排。

### 容器数量与并发容量

容器没有设置数量上限。有效同时请求数为 `min(可用账号数, MANAGER_MAX_INFLIGHT)`，每个账号固定 1 个请求。CPU 配额是上限，不是每个容器预留一个核心；256 MiB 同样是单容器内存上限，不是启动时预分配。

2026-10-01 实测：2 核，可用内存约 1.46 GiB，cc1 空闲占用约 68 MiB，无 Swap。推荐先运行 4 个账号；5～6 个可作为轮换资源，在保持并发 4 的情况下观察繁忙时占用。这是容量估算，尚未进行多账号负载验收。更多账号需要实测，不能以空闲占用推算可靠上限。

项目外可选优化：清理确认无用的常驻服务；配置约 2 GiB Swap 作为内存突发缓冲；将服务器升级到 4 GiB 或 8 GiB；进一步扩展时增加工作服务器，但当前管理器尚不支持跨服务器管理。Swap 不能代替物理内存；不建议直接降低容器内存限制，长上下文可能导致容器内存不足。上述服务器优化尚未执行。

调整全局并发：执行 `sudoedit /etc/clewdr-manager.env`，修改已有的 `MANAGER_MAX_INFLIGHT=4`，例如改为 `MANAGER_MAX_INFLIGHT=6`，保存后在空闲时执行 `sudo systemctl restart clewdr-manager`。工作台会显示新上限。目前前端只有并发展示，没有修改入口。排队数量和等待时长分别由 `MANAGER_QUEUE_SIZE`、`MANAGER_QUEUE_SECONDS` 设置；扩大队列不会增加吞吐量。当前服务器建议先保留并发 4。

## 给每个新账号写入的上游配置

| ClewdR 参数 | 值 | 含义 |
|---|---|---|
| `ip` / `port` | `0.0.0.0` / `8484` | 容器内部监听，宿主机只公开给本机 |
| `password` / `admin_password` | 每账号随机独立值 | 内部认证，网关代换，不给对外调用方 |
| `proxy` | 每账号填写的专用代理 URL | 该容器发往上游的出口 |
| `cookie_array` | 仅该账号 sessionKey | 容器内不混用多个账号 |
| `max_retries` | **5** | 保留用户要求的原版重试上限；这是原版内部重试，网关不会将失败请求换账号再次生成 |
| `skip_restricted` | **true** | 原版跳过 restricted 账号 |
| `skip_first_warning` | **false** | 第一次 warning 不触发原版跳过 |
| `skip_second_warning` | **false** | 第二次 warning 不触发原版跳过 |
| `skip_rate_limit` | true | 原版按其逻辑跳过被限流账号 |
| `skip_non_pro` / `skip_normal_pro` | false / false | 不主动排除这些套餐类型 |
| `check_update` / `auto_update` | false / false | 容器内部不自行替换二进制，由管理器升级官方镜像 |
| `no_fs` / `log_to_file` | false / false | 保留配置持久化，关闭原版文件日志 |
| `preserve_chats` | false | 按原版逻辑处理临时会话，不保留生成聊天 |
| `web_search` | false | 默认不启用上游搜索 |
| `enable_web_count_tokens` | false | 不启用实验性网页 token 计算 |
| `sanitize_messages` | false | 不开启原版消息清洗 |
| `use_real_roles` | true | 使用原版的实际角色处理 |

UA、系统标签在 registry 保存，未写入原版不支持的 TOML 字段。为了做到上游零改动，现在不会按你输入的 UA 修改 TLS/HTTP 指纹。若今后确需匹配自己的 UA，优先等官方配置支持；否则需要扩展原版构建，必须对每次上游更新适配与验收，不能承诺零改动。

## 账号资料和 sessionKey 期限

`GET /admin/accounts/<id>/details` 返回管理员可查看的账号资料与凭证；只允许管理员认证，并禁止 HTTP 缓存。`POST` 同路径保存 `name`、`email`、`email_password`、`notes`、`session_expires_at`，以及可选 `proxy` / `proxy_username` / `proxy_password` / `sessionKey` / `user_agent` / `os_label`。到期时间是 Unix 秒时间戳。新账号未填实际期限时，默认按 session_imported_at + 29×86400 估算，session_expiry_source=estimated；历史未填写期限的账号启动时补齐。手动填写时记录 manual；资料编辑清空可保留 unknown。更换 sessionKey 未填实际期限时按本次更新时刻重新估算。不是 Cookie 自动续期。

邮箱密码只存在私有登记表和管理员详情响应，不进入列表响应或对外 API。sessionKey 和代理认证仍以容器 TOML 为准；编辑时更新原账号，不新增重复容器。资料修改不重启，连接配置变化先检测代理，再暂停分配并等待空闲，备份和重建容器，失败回退。暂停、隔离和停用状态保持；生成验证时间在连接配置变化后清空。

已到期账号在快照显示 expired，调度与自动更新候选选择均排除；该显示不改写原始账号状态，原来的隔离/停用原因优先保留。`POST /admin/accounts/<id>/disable` 人工标记不可用并停止容器，恢复需显式 resume；到期凭证不能直接恢复。已有会话不会因到期强制中断。字段均允许管理员修改或清空，前端默认遮挡密码，打开抽屉才请求详情。

## 代理检测

参考 Sub2API [TestProxy](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/service/admin_proxy.go) 的出口 IP 和耗时检测方式，采用服务器经指定代理请求 `https://api.ipify.org?format=json`。`POST /admin/proxy/test` 接收 `proxy`、可选 `proxy_username` / `proxy_password`，返回 `ok`、`ip`、`latency_ms`、`checked_at`；只接受管理员认证。`POST /admin/accounts/<id>/proxy-test` 复测已挂载账号，保存结果并更新表格和详情。

创建账号会在写入配置、登记表和创建容器前检测实际 HTTPS 出口，失败不创建资源。代理检测最多同时执行两项，连接超时 8 秒，总超时 20 秒；错误不会回显代理凭证。耗时是完整 HTTPS 检测耗时，不是网络 ping；不检测 IP 风险评分或账号生成资格。复测代理失败只记录结果，不自动恢复或隔离账号。已有账号可在详情抽屉点击“检测代理”补充记录。服务器需安装 curl；此工具只用于管理器检测，不改变官方 ClewdR 容器。

## 调度、隔离和恢复

参考 Sub2API 官方 [gateway_scheduling.go](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/service/gateway_scheduling.go) 与 [concurrency_service.go](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/service/concurrency_service.go) 的空闲槽位、负载和 LRU 策略。本项目为每账号 1 槽，负载只有空闲/占用两类；优先空闲账号，再选最久未使用账号，选择和占用在同一锁内完成。满载请求进入全局有界队列，每次唤醒重新选空闲资源，不提前绑到某个忙账号。没有引入 Sub2API 的账单、亲和会话或多平台系统。

- 仅 `ready` 账号参与分配，每账号同一时刻最多一次上游请求；全局上限和队列限制共同生效，模型列表请求也使用该通道。
- 401/403 隔离并切换；429 冷却并切换；5xx、连接、超时和传输异常连续 3 次才持久隔离。空回复切换但不隔离。普通调用 400 不重试、不隔离。生成验证沿用同一故障分类，但只验证指定账号。
- 隔离不会自动重新启用，重启管理器、重复导入不会解封；相同 sessionKey 的重复导入返回 409。
- 当前保留两个 warning=false，因此“任何 warning 都停用”并未实现；原版内部跳过 restricted 和网关异常隔离共同工作。若原版以 HTTP 200 正常内容报告特殊限制且没有错误事件，网关不做内容猜测。
- 向客户端输出前可切换其他账号，默认最多尝试 3 个不同账号；流已开始输出后不重放。失败的上游也可能消耗额度。详见 [错误处理方案](ERROR-HANDLING.md)。
- “暂停”先移出调度再等待当前请求，随后停止容器。超过 60 秒未空闲会返回 409，账号仍暂停，可稍后再执行。
- “人工恢复”会启动服务并重新加入调度，**不等于额度已恢复**；检查会话/代理后执行生成验证。

## 作者更新流程

1. 每 24 小时查询官方 GitHub master commit。
2. 等该提交的官方 `build` 工作流成功，拉取官方 `sha-<7位>` 镜像并固定仓库 digest。
3. 临时移出一个正常账号，等待请求完成；复制配置创建候选容器。同一账号不同时用于原容器与候选生成。
4. 检查模型接口、真实普通生成、真实流式生成，消耗少量该账号额度。
5. 通过后逐个升级正常账号，保留配置与旧镜像；重建启动失败时恢复旧配置和镜像。
6. 暂停/隔离账号保持状态，日后人工恢复时再同步已批准版本。失败信息显示在前端更新区域。

“始终更新”体现为持续检测和验证后更新；官方构建尚未成功、无法下载或验收失败时不能强行升级。管理器本身需要正常运行且能够访问 GitHub / GHCR。单账号候选验收及重建期间会短暂没有可用账号，外部可能收到 503；多账号滚动升级可继续使用其他健康账号。

如果升级或创建过程中服务重启，未完成的 `pending/updating` 账号会转成暂停，需人工检查和恢复。如残留 `clewdr-check-<id>` 候选，先确认没有进行中的更新，再停止并删除该候选容器；保留工作容器和账号配置。

## 运维命令

```bash
sudo systemctl status clewdr-manager --no-pager
sudo systemctl status docker nginx --no-pager
sudo journalctl -u clewdr-manager -n 50 --no-pager
sudo docker ps --filter label=clewdr-manager=true
sudo nginx -t
curl -s http://127.0.0.1:9000/healthz
```

工作容器关闭 Docker stdout 日志（`--log-driver none`），因为当前原版启动日志包含会话/密码；避免长期写入 Docker 日志。前端不会返回 sessionKey、内部密码或代理认证信息。配置和登记表仍含实际凭证，只在服务器本地以 600 文件 / 700 目录保存。备份时同样保密；不要公开 registry 或完整 `docker inspect`/TOML。

### 管理 API，供其他项目接入

Bearer 使用管理员密码，所有操作应只从你信任的管理系统发起。

| 方法 | 路径 | 含义 |
|---|---|---|
| GET | `/admin/accounts` | 脱敏列表、挂载和更新状态 |
| POST | `/admin/accounts` | 创建：`name`、`sessionKey`、`proxy`，可选 `proxy_username`、`proxy_password`、`user_agent`、`os_label` |
| POST | `/admin/accounts/<id>/pause` | 暂停调度并停止容器 |
| POST | `/admin/accounts/<id>/resume` | 管理员明确恢复 |
| POST | `/admin/accounts/<id>/probe` | 消耗少量额度的真实生成验证 |
| POST | `/admin/update` | 异步检测并更新，202 返回后查看列表中的 update |

也可提交 `text` 字段由后端解析同格式文件头。没有 Docker 任意指令执行、删除账号、替换既有 sessionKey 的管理接口；当前新增/暂停/恢复覆盖用户要求的最小管理流程。容器元数据可以平滑接入，注入完整浏览器环境不在现实现中。

### 部署同一项目到其他服务器

准备 Python 3.11+、Docker、Nginx 和 Node.js 构建环境，`frontend/npm ci && npm run build` 后上传项目到目标服务器。执行 `sudo bash deploy/install.sh`；首次运行生成模板后填写 `/etc/clewdr-manager.env` 两个不同强密码，再运行安装脚本。配置 Nginx 指向 127.0.0.1:9000，关闭代理 buffering、设置 180 秒读取超时和 32m 请求体大小，并提供有效 HTTPS。编辑代码后需 `sudo systemctl restart clewdr-manager`；install 脚本不会自动重启已运行服务。

### 回退到本次部署前入口

本次 Nginx 原配置备份路径见 验收记录（历史记录已归档）。先停止新入口流量并等待当前调用完成，然后恢复对应 Nginx 配置、`sudo nginx -t`、`sudo systemctl enable --now clewdr`、`sudo systemctl reload nginx`。原来进程的账号池为空，回退只是恢复原入口，不能承诺它能立即生成。新管理器账号目录保留，不能把新目录误删。
