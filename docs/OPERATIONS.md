# 配置与运维

## Claude Code 工具配置

设置 `MANAGER_WORKER_PROFILES_ENABLED=true` 后，客户端工具和独立审批请求使用按需创建的 `clewdr-<account>-tools` 容器。它使用账号当前原版镜像、原代理专用网络和配置副本，关闭内置搜索，并通过 `custom_prompt` 明确继续附件中的 API 对话。普通请求仍使用账号原容器。

私有配置副本位于账号目录的 `profiles/client-tools/`。辅助容器沿用原容器 256 MiB 上限。`MANAGER_WORKER_PROFILE_WARM` 控制每节点后台预热目标，默认 2、最大不超过全局模型并发，设为 0 关闭主动预热。维护循环每分钟刷新小型预热池；其他闲置配置十分钟后清理。低于 320 MiB 可用内存时暂停新增预热，低于 256 MiB 时将闲置池缩至两个，保留正在使用的容器。代理/sessionKey 更新、暂停和上游镜像更新会清理对应副本。

每账号使用独立生命周期锁，Docker 原子分配仅绑定 127.0.0.1 的动态端口；启动等待不再占用全局锁。预热复用后台账号租约，不增加业务请求计数，留出前台并发余量。远程节点通过 Redis 批量读取有到期时间的就绪提示，实际租约及容器检查仍是执行依据。仍共享全局 4、单账号 1 的租约；预热不增加模型并发额度。

预热占位单独标记，记录持有它的 PostgreSQL 连接进程与启动时间。服务启动和维护时可回收持有进程已退出的预热占位；普通任务占位保留原有处理规则。旧版应用可忽略新增字段；回退应用前先停止新版服务并清理已退出进程的预热占位。

回退时恢复备份的应用及环境文件，停止辅助容器并重启网关。无需替换 ClewdR 镜像或恢复 PostgreSQL 数据。服务器 Claude Code 验收使用独立配置、合成文件和无外网的内部容器网络，只允许访问服务器转发器。仅设置 Base URL 不等于阻断客户端其他网络请求，客户端隔离需要独立网络规则。

## 服务器位置

`webcc-runtime` 使用 `Restart=always`，进程退出后等待 5 秒重启；管理服务重启不会永久停止任务进程。人工 `systemctl stop webcc-runtime` 仍会保持停止。任务恢复继续使用既有 PostgreSQL 队列与心跳，不通过进程重启直接重复业务工具操作。

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

## 长任务历史与换号

标准 Messages 工具入口按展开后的上下文字节预算校验，不以 128 条消息提前拦截。默认 `MANAGER_TOOL_CONTEXT_BYTES=524288`，包含工具定义、系统指令及完整历史；可配置范围为 131072–2097152 字节。实验 `prompt-v1` 入口仍限制 128 条消息。

新生成的工具调用默认每条回复最多 32 个（`MANAGER_TOOL_MAX_CALLS`，范围 1–128），声明默认最多 256 个（`MANAGER_TOOL_MAX_DEFINITIONS`，范围 1–1024）。这与平台模型请求并发 4、单账号并发 1 是不同的限制。标准入口对已完成历史不重新施加本轮调用数量上限，仍校验 ID 配对、结构及字节预算。

严格参数路径可实时发送上游先生成的说明文字，工具调用继续等待完整结果校验，不提前交付。模型若先生成调用、后生成说明，网关不会调换其实际顺序，也不生成虚假进度。已输出后发生错误时返回流内错误，不自动重放请求。

调用方须在每轮发送完整的 Messages 历史，包括实际工具结果和最新文件版本。同一调用身份下换账号不删除或摘要历史，也不跨调用方共享历史。网关未提供透明的无限上下文；大型文档应使用范围读取，长会话的压缩由客户端显式管理，保留未完成工作和已执行动作。超过预算仍返回 413，不静默截断。Thinking 状态令牌仍绑定调用身份，不能通过更换调用密钥绕过隔离。

格式或参数错误在内容未交付时可以有界纠正，账号仍可参与纠正；实际上游故障继续按原策略换号。内容交付后的错误不会从头重放，避免客户端已执行的工具被重复调用。

标准工具历史接受官方 SDK 完整序列化中的空 `caller`、`toolset_name` 字段；直接调用元数据保留。历史只检查结构和调用结果配对，不用当前工具 Schema 重判已经执行的旧参数；新调用仍经过当前 Schema 校验。工具定义更新或延迟加载不会清除旧结果。

工具 JSON 后已确认的 `</parameter>` 尾标记在 JSON 边界之外适配，不修改 JSON 字符串或调用参数；额外 JSON、未知尾随内容和不完整标记仍视为无效。已确认的代理 `ProxyConnect` 故障在释放租约前计入失败并冷却 60 秒；永久隔离仍按原连续失败阈值执行，不直连回退。

## 启动就绪与响应耗时

启动先检查工具预热池，再打开 HTTP 监听；启动等待最多 20 秒，超时后健康接口仍可访问，就绪接口继续报告未就绪，预热在后台完成。后台每 15 秒维护预热池，与 Git 更新检查分开。预热只检查容器与 HTTP 就绪，不发送模型问候，也不证明账号额度或上游会话可用。账号巡检、Git 更新周期、每号并发 1 和全局并发 4 保持原配置。

`GET /healthz` 检查进程存活；`GET /readyz` 返回预热就绪状态，不通过时为 503。部署后应等待后者为 200。返回 warm、target、configured_target、eligible 和 memory_limited；低内存仍执行原来的两容器保留策略。仅作为远程调度网关、不挂本地账号的节点应设置 MANAGER_WORKER_PROFILE_WARM=0。本地没有可用资源时不会把空池标成已预热。

有足够内存时当前预热目标为 4，并不表示全部账号均常驻工具容器。限定账号范围的密钥可能仍遇到冷启动。后台故障检测有周期，容器也可能在就绪检查后退出；请求入口继续执行真实端点检查和原有恢复策略。

| 日志字段 | 含义 |
| --- | --- |
| worker_prewarm_startup | 开始监听前的预热耗时和就绪状态 |
| worker_profile_access | 实际预热命中或冷准备耗时，区分业务请求和预热 |
| request_timing.stages.queue | 排队与分配账号，重试累加 |
| worker_prepare | 本机执行端点准备或远程节点连接对象准备 |
| upstream_headers | 发出上游请求到收到响应头，含 ClewdR 内部网络与网页等待 |
| upstream_read | 读取上游结果；工具流还包含增量回调及背压等待 |
| validation / adapter | 本地结果校验或协议转换；可能包含输出或引用读取 |
| firsts.upstream_content / upstream_text | 上游首个真实内容或正文到达 |
| firsts.client_content / client_text | 网关实际写出首内容或流式正文；心跳、空思考不计入 |

日志只保存请求 ID、路由、状态、账号 ID、时长和命中状态，不保存正文、密钥或代理凭据。各阶段可能有并行或嵌套，不能直接相加当成总耗时。字段按通道记录；工具通道的 upstream_text 可能是协议 JSON，不能当成用户正文。服务端计时不包含客户端到网关的完整网络往返；非流式 client_content 代表完整结果开始交付，不是模型首字。严格工具模式仍在完整校验后交付。

从网关外部无法精确拆开 ClewdR 内部的代理握手、上游排队与模型预填充；upstream_headers 应作为组合阶段分析，不能将其全算成代理或模型耗时。存活与就绪分开的依据见 [Kubernetes 官方说明](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)，项目无需引入 Kubernetes。

同一节点的 Docker 出口链是共享系统资源，策略文件与锁统一使用节点环境 MANAGER_DATA（默认 /var/lib/clewdr-manager）。临时候选实例可使用独立 Manager 数据目录，但不能据此创建局部出口策略并覆盖共享链。容器回收验收必须同时检查回收前后完整允许集合和默认 DROP。

### Claude Code 的 auto 与 bypassPermissions

客户端工具调度会读取本节点 ClewdR 工具容器保存的会话池状态，并通过 PostgreSQL 共享 `tools_unavailable_until`。已退休的会话暂时排除，冷却中的会话遵循上游重置时间并预留池轮询间隔。该状态只影响工具执行配置，不增加通道失败计数、不修改 Cookie，也不恢复隔离账号。只有当前凭据匹配的状态才生效；更换 sessionKey 会清除旧的工具可用性标记。

Claude Code 2.1.296 的交互会话可能请求原生服务端安全判定：发送 safeguards，等待 safeguard_results。网页账号通道未提供该原生能力，不能用普通工具调用或单独分类请求的通过结果代替。按官方建议，在客户端现有 env 配置中加入以下设置，并新开会话：

```json
{"CLAUDE_CODE_AUTO_MODE_SERVER": "0"}
```

也可用 `CLAUDE_CODE_AUTO_MODE_SERVER=0 claude --permission-mode auto` 临时验证。此设置保留 auto 的客户端分类审批，不是 bypass，不放开代理或权限。仅设置在服务器环境文件中不能影响远端客户端。该变量是官方临时配置，升级 CLI 后应重新核对。[官方说明](https://code.claude.com/docs/en/auto-mode-classifier-billing#make-the-session-eligible)。

auto 审批按完整安全监控协议识别，接受 JSON 和 SSE 请求，复用原审批执行配置。短审批必须取得完整、有效的真实判定后才能交付；SSE 在校验后发送，不代表判定过程的实时增量。空回复、截断和无效判定不会变成放行；普通聊天流式保持原路径。同一 HTTP 长连接上的请求分别初始化审批状态。

审批尚未交付时，账号无可用会话或通道故障复用现有账号切换机制，总尝试次数和请求期限不增加。已完成但无效、截断的判定不自动重放；真实拒绝原样返回。HTTP 长连接的后续请求重新初始化重试状态。

bypassPermissions 跳过客户端工具审批，不需要也不会由网关伪造放行。认证、资源隔离、代理出口和工具自身的网络检查仍生效。WebFetch 的域名安全检查与 auto 分类器是不同环节；切换 bypass 不能保证解决域名检查、代理白名单或站点读取失败。诊断时应分别保存审批回复、工具结果与最后正文，不能只以 CLI 退出码为零认定整个任务通过。

## 独立 Claude Code 测试页

可选入口 `/claude-test/` 使用原版 ttyd 提供真实服务器终端，不加入管理台导航，不实现另一套聊天或 Agent 循环。部署脚本是 `deploy/install-cli-test.py`，运行前须已有经验证的服务器 CLI、测试镜像及代理网络；脚本下载官方 ttyd 发布资产并记录版本与 SHA256。

Nginx 独立 Basic Auth 保护 HTTP 与 WebSocket，ttyd 仅监听回环地址并检查 Origin。用户名和随机密码写入服务器私有目录，不进入源码或 URL。页面最多一个连接，选择 auto、bypassPermissions 或 Plan 后启动非 root、只读根目录、无 Docker Socket 的隔离容器；只挂载测试工作区、该模式的 CLI 会话目录和只读 CLI 安装目录。

浏览器不会运行本机 Claude Code。模型请求走 WebCC 公网地址，工具出网走指定代理。测试使用独立的 messages/models 密钥，安装后约 24 小时过期；服务同时设 24 小时运行上限，单次连接上限 30 分钟。该入口用于内部单用户测试，不提供多人工作区隔离。

`systemctl stop webcc-cli-test` 停止入口。撤销 `/var/lib/webcc-cluster/cli-playground/access.json` 中对应的调用密钥后，现有 CLI 也无法继续请求。重新开放须明确轮换测试凭据，不复用过期密钥。该目录含秘密和会话，禁止提交 Git。刷新会断开当前终端；同模式的历史保留供 CLI `/resume` 使用，正在执行的任务不保证因浏览器重连自动恢复。

依据：[ttyd 官方仓库](https://github.com/tsl0922/ttyd)。

### Claude Code 的语言风格

测试页在 auto、bypassPermissions 和 Plan 中统一选用 `English progress` 输出风格：过程说明和工具简述使用英文，最终回答跟随用户语言；明确的语言要求优先。代码、文件名、引用、工具结果和错误消息保持原文。风格文件位于 `deploy/cli-test/english-progress.md`，设置 `keep-coding-instructions: true`，保留 Claude Code 的编程指令，不增加固定话术或逐步播报要求。

这是客户端设置，不是网关翻译，也不会改变 Chatbox 或其他 API 客户端的语言。外部 Claude Code 如需相同风格，将文件放入其配置目录的 `output-styles/`，在 settings.json 中设置 `"outputStyle": "English progress"`，重新启动会话。原生界面标签由 CLI 自己渲染；子代理使用各自的指令。模型遵循风格的程度仍需验收，不能保证每次输出语言完全一致。

依据：[Claude Code 输出风格说明](https://code.claude.com/docs/en/output-styles)。

### Claude Code 的 API 重试

测试页显式设置 `CLAUDE_CODE_MAX_RETRIES=10`，使用 CLI 原生重试、退避和倒计时，不在 WebCC 生成重试提示文字。该值也是官方默认值；外部 CLI 如未覆盖它，无须额外配置。需要固定该值时，在客户端现有 settings.json 的 env 中加入 `"CLAUDE_CODE_MAX_RETRIES": "10"`，重新启动会话即可。

这是客户端重试上限，首次请求不算重试；部分故障会提前结束。临时连接错误、限流和服务故障由客户端按原生规则处理，429 可提供 Retry-After。错误参数、无刷新凭据的认证失败，以及已交付完整内容块后的中断不应一律重放。流式响应在 HTTP 200 之后使用 error 事件，不能再改 HTTP 状态；完整工具调用后的恢复应保留已执行结果，而不是重新执行原始请求。

WebCC 每个 HTTP 请求的现有资源切换上限仍为 3，ClewdR 的 max_retries 仍为 5；不再增加一层十次重试，不启用无限重试 watchdog。客户端重新发送会产生新的 HTTP 请求，因此客户端预算不等于所有内部上游尝试的总和。并发、租约、请求期限和重复执行保护继续独立生效。curl、Chatbox、WorkBuddy 和 SDK 的重试由各自客户端决定，不会因修改服务器 CLI 设置而获得同样的倒计时或次数。

最终 HTTP 错误保留上游合法的 Retry-After 秒数或带时区的 HTTP 日期；非法头值丢弃，后续尝试和成功响应不继承前次等待时间。工具通道复用标准错误类型映射，529 保持 overloaded_error，504 保持 timeout_error。流式头部已经发送时仍返回标准 error 事件，客户端可能按原生规则切换非流式请求，不能将此过程计成另一个网关重试循环。

依据：[官方错误与重试说明](https://code.claude.com/docs/en/errors)、[环境变量](https://code.claude.com/docs/en/env-vars)、[API 错误协议](https://platform.claude.com/docs/en/api/errors)。
