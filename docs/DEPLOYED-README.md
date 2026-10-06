# ClewdR 多容器管理器

独立项目：Python 标准库管理网关 + React / MUI 前端 + 官方 ClewdR Docker 容器。部署服务器为 Ubuntu Linux；本地 macOS 用于编辑和构建。

## 已部署入口

- 管理页面：<https://165.154.205.213/>
- OpenAI 兼容 API Base URL：`https://165.154.205.213/v1`
- 原生 Claude 接口：`https://165.154.205.213/v1/messages`
- 页面登录使用原有 `CLEWDR_ADMIN_PASSWORD`；对外调用使用原有 `CLEWDR_PASSWORD`。两者不同，密码没有写入本文。
- [对外调用和模型清单](API.md) · [运维、配置及更新说明](OPERATIONS.md) · [验收记录](VERIFICATION.md)

## 和 GitHub 上游的关系

上游：[Xerxes-2/clewdr](https://github.com/Xerxes-2/clewdr)。每个账号运行作者的 `ghcr.io/xerxes-2/clewdr` 官方镜像，**上游源码修改量为零**。本项目仅生成它支持的配置，使用公开 API，并在外部管理容器、路由、隔离和更新。

```mermaid
flowchart LR
  C[调用方 / 管理浏览器] --> N[Nginx HTTPS :443]
  N --> G[管理器 127.0.0.1:9000]
  G --> A[官方 ClewdR 容器 A]
  G --> B[官方 ClewdR 容器 B]
  A --> P[账号 A 的专用代理]
  B --> Q[账号 B 的专用代理]
```

原版 v0.13.5 的 OpenAI 流式响应没有 `[DONE]`。网关在收到合法 delta 并确认上游传输正常结束后补齐该标记；上游传输错误、SSE 错误事件、空流不按成功处理。原生 Claude 流式保留原事件，要求 `message_stop`。网关没有补造原版未提供的 usage、模型名等字段，因此属于部分 OpenAI 协议兼容。

## 添加后续账号

1. 打开管理页面，以管理员密码登录。
2. 填账号名称、专用代理地址、自己的 `sessionKey`，点击“检测代理出口 IP”查看出口 IP 和 HTTPS 耗时，再点击“创建并挂载”。后端创建前会重新检测，失败不会创建容器；IPv4 必须完整填写四段，代理必须有端口。
3. 或导入与你的 `cc1.txt` 格式相同的文本文件。首行取作 sessionKey，文件头的代理和 UA 可识别；其他站点 Cookie 不上传。
4. 创建后容器可用状态代表服务就绪。点击“生成验证”确认该账号能生成内容，验证会消耗少量上游额度。
5. 账号表格显示邮箱、状态、sessionKey 倒计时、代理和请求负载；点击“查看详情”打开右侧抽屉。概览用于检测与启停，“资料与凭证”可修改邮箱、密码、代理和 sessionKey，“容器”显示挂载与镜像，“浏览器”显示并允许修改完整 UA 与原始系统标签。异常账号变成“已隔离”，需要检查原因再点“人工恢复”。

## 账号资料与到期提醒

- 按用户规则，新账号默认将 sessionKey 导入时间加 29 天作为预计期限；历史未填写的账号也按原导入时间补齐。页面明确标记“预计”。填写浏览器 Cookie 的 Expires / Max-Age 后以手动期限为准。
- 倒计时每分钟更新；不足 7 天显示黄色，不足 3 天显示红色。到期时间最近的正常账号排在前面，暂停、停用、隔离、异常和已到期账号置灰并放到底部，不再显示活动倒计时。
- 已填写或预计期限到达后，网关停止为该账号分配新请求；已进行的请求继续。该状态不会自动刷新 Cookie，更新到期时间必须基于实际浏览器信息。
- 邮箱、邮箱密码、备注和期限可修改，保存资料不重启容器。代理账号和密码在详情里可查看、复制和修改；密码默认遮挡。
- 更换代理或 sessionKey 会在账号空闲后重新加载容器，失败尝试回退。其他账号继续服务。更换 sessionKey 时可填写实际期限；留空则从更换时刻加 29 天估算，再执行生成验证；保存凭证不会自动解除暂停、隔离或停用。
- 详情各项支持一键复制；代理连接串使用 `IP:端口:用户名:密码` 格式，默认遮挡，支持完整复制。代理 IP 和出口 IP 单行显示。浏览器页签保留 Win / Apple / Linux 等原始标签，并解析 UA 中的系统和浏览器版本；仍不改变官方容器的请求指纹。
- 邮箱凭证仅作内部资料存储，不执行自动登录。列表不返回邮箱密码、代理密码或 sessionKey；管理员详情接口返回凭证并设置 no-store。服务器私有文件权限为 600，目录为 700，备份同样包含敏感资料。

sessionKey 可从自己已登录 Claude.ai 的浏览器开发者工具 → Application/Storage → Cookies → `https://claude.ai` → `sessionKey` 的 Value 获取。填写值本身，不填整套 Cookie JSON，也不是 API Key。代理认证可直接写 URL，或在可选表单填写用户名和密码。

| 需求 | 当前实现 | 边界 |
|---|---|---|
| 多账号独立代理 | 每个容器单独的 TOML、cookie、代理 | 单机管理，代理连通性由服务商提供 |
| 异常处理 | 401/403 隔离；429 冷却；连续 3 次通道失败隔离；空回复不当作成功 | 最多尝试 3 个不同账号；输出后不重放；[处理方案](ERROR-HANDLING.md) |
| 高并发 | 每账号 1 个请求，总计最多 4，等待队列 16 | 只有一个账号时有效并发为 1；额度和上游限流仍生效 |
| UA / 系统标签 | 导入、保存、展示 | 当前仅元数据，未注入请求；原版 v0.13.5 使用内置 Chrome145 仿真 |
| 其他项目传信息 | 管理 REST API 创建容器 | 只接收明确支持的字段；不会还原整个浏览器 |
| 持续跟随作者 | 每 24 小时检测 master，官方构建通过后验证再升级 | 构建未完成、镜像不存在、验证失败时保留现版本 |
| 原版更新无冲突 | 不 fork、不修改上游源码 | 若作者改配置/API/模型则验收可能失败，需要适配管理器 |

### 代理示例

```text
socks5://127.0.0.1:1080
socks5://username:password@proxy.example.com:1080
socks5h://username:password@proxy.example.com:1080
http://username:password@proxy.example.com:8080
https://proxy.example.com:8443
IP:端口
IP:端口:用户名:密码
```

无协议的简写按 SOCKS5 处理。认证里 `@ : / # %` 等字符应 URL 编码，或使用分开的认证输入。管理器允许上述 URL 协议；部署时实际验证的是 cc1 的 SOCKS5，其他类型仍需按代理实际服务验证。HTTP 代理访问 HTTPS 目标需支持 CONNECT。SOCKS5h 以当前上游网络库行为为准，不承诺单独的 DNS 策略。不能把 HTTP 代理的简写误当 SOCKS5。

## 文件结构和维护

```text
manager.py                 账号配置、Docker 生命周期、认证和请求网关
updater.py                 官方版本检测、候选验收、逐个升级和回退
serve.py                   启动入口
manager.env.example        不含真实密码的配置模板
frontend/src/App.jsx       登录、会话、模块路由和轮询
frontend/src/pages/        五个独立后台模块
frontend/src/components/AdminShell.jsx 侧栏、Header、移动端菜单
frontend/src/api.js        管理 API 和 txt 文件头解析
frontend/src/components/   登录、创建表单、分区详情、密码字段
frontend/package-lock.json 固定前端依赖
static/                    npm build 产物，不纳入源码版本控制
tests/                    本地协议及状态测试（不使用真实账号）
deploy/                    systemd 单元和安装脚本
```

后端无额外 pip 依赖，需要 Python 3.11+、Docker Engine、Linux systemd；前端构建使用 Node.js 22.12+ 或兼容的新版本。

```bash
cd frontend
npm ci
npm run build
cd ..
python3 -m unittest discover -s tests -v
```

前端使用 React、MUI ThemeProvider/CssBaseline/组件，凭证仅保存在页面内存，刷新后重新登录；10 秒刷新一次列表。左侧菜单划分工作台、账号管理、资源挂载、版本更新和 API 接入；Header 提供刷新、身份与退出。AdminShell 管理布局，pages 负责各模块，components 保存复用组件。

这套结构已经实际验证当前账号的调用，无法承诺账号始终有效或无限并发。优先让正常账号稳定工作；新增账号和提升服务器资源后再逐步提高全局并发。
