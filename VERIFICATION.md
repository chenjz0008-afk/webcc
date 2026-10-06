# 本次部署验收

验收日期：2026-10-01，UTC 13:23 起。机器：165.154.205.213，Ubuntu 24.04。原始脱敏记录：[verification.json](verification.json)。

| 项目 | 结果 |
|---|---|
| 后端协议与状态测试 | 21 项 unittest 全部通过，约 20 秒 |
| 前端构建 | React + MUI + Vite production build 成功 |
| 实际浏览器登录 | 现有管理员密码登录成功，显示 cc1 就绪、运行中、独立代理/端口/挂载/版本 |
| 外网 HTTPS 页面 / JS | 均 HTTP200；证书正常校验，没有跳过 TLS 校验 |
| 首个账号导入 | cc1.txt 首行 sessionKey；只提取所需文件头资料，独立容器运行 |
| 代理出口 | 实际 SOCKS5 查询出口为 217.67.64.179，与提供的代理 IP 相同 |
| 普通生成 | 公网 `/v1/chat/completions`，Sonnet4.6，HTTP200，回答 `OK.` |
| OpenAI 流式 | 公网 HTTP200，SSE，收到文本及 `[DONE]` |
| 原生消息 | `/v1/messages`，HTTP200，message 文本 `OK.` |
| 原生流式 | 公网 `/v1/messages`，HTTP200，SSE，收到 `message_stop` |
| 模型列表 | 公网 `/v1/models` 和 `/code/v1/models` 各 20 个 ID |
| 身份验证 | 错误 API Key 401；普通 API Key 不能管理容器，401 |
| 自动更新 | 官方 master `061c6d8ac9187148f50c8d806b56962a7f222b6c`，候选普通/流式验收通过，状态 current |
| 服务 | clewdr-manager / Docker / Nginx active，原 clewdr inactive 且禁用 |
| 用户指定配置 | max_retries=5，skip_restricted=true，两个 warning=false |

## 后台升级补充验收（UTC 13:49）

- 新增五模块侧栏导航、完整 Header、账号搜索/筛选与创建弹窗、资源挂载表格和 API 接入说明；实际浏览器登录、模块切换及创建弹窗显示均通过。
- 默认与服务器实际 `MANAGER_UPDATE_SECONDS=86400`；按保存的 checked_at 计算检测时间，重启不重置。
- 4 个同步并发请求分别占用 4 个不同测试资源；连续 12 次请求在 3 个测试资源之间各分配 4 次；忙碌和隔离账号不参与选择。测试资源为本地合成账号，不宣称服务器已挂载四个真实账号。
- 按对外文档参数发起公网 curl 得到 `OK.`，OpenAI 流式完整结束，20 个模型 ID 保持一致，指定重试/warning/restricted 参数未变。
- 参考 Sub2API 官方调度源码的空闲槽位、负载和 LRU；保留单机原子锁和有界队列，不引入其无关系统。

## 运行实例

- 账号：`cc1` / `42230244b096`
- 容器：`clewdr-42230244b096`
- 宿主机端口：`127.0.0.1:34077` → 容器 `8484`
- 挂载：`/var/lib/clewdr-manager/accounts/42230244b096` → `/etc/clewdr`，读写持久化
- 上游版本：v0.13.5
- 官方镜像 digest：`ghcr.io/xerxes-2/clewdr@sha256:a598cc2954171913fe278ac6e4cedf8dfe6fe7a20437798362765676ed9e5813`
- 自动跟随：master，每 24 小时检测；验收时最新提交已验证
- Nginx 原配置备份：`/etc/nginx/sites-available/clewdr.before-manager-20261001T131910Z`

## 已解决的问题和实际边界

1. 原版 OpenAI SSE 没有 `[DONE]`，初始网关将正常 EOF 判断为错误。已定位原版源码、补齐外层网关结束标记、加入无 DONE 原版响应测试、重新部署并显式恢复账号，随后真实流式通过。上游源码保持零修改。
2. Nginx reload 过程中第一次页面验收遇到旧响应，等待生效后已确认新页面和 JS 返回正确，实际浏览器已成功登录。
3. 单账号自动候选验收期间有一次 503；更新完成后正常。这是短暂移出调度的设计边界，文档已说明。
4. 一次 32-token 非流式请求 HTTP200 但文本为空；64-token 重新验证得到 `OK.`。只能确定观察到的现象，不能断言空文本完全由预算导致。对外文档要求检查内容并给足输出预算。
5. 仅 Sonnet4.6 完成真实生成，其他 19 个 ID 为接口实际列出，未逐个验证。Code 模型列表可获取，Code 生成未测。
6. UA / 系统标签只是已保存的元数据。没有还原 TLS、字体、图形、完整 Cookie 或浏览器环境。
7. 验收采用少量真实请求和本地状态/协议测试，没有持续高压压测；当前单账号有效并发为 1，不承诺无限额度或永不失效。

![真实 MUI 管理页](frontend-preview.jpg)
