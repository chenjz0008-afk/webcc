# 高优先级 API 能力

分支：feat/high-priority-api。基线：0c363a3。仅使用网页账号，ClewdR 保持原版；用量与计费仍不纳入工作范围。

## 本轮验收

- [ ] 真实模型身份记录、严格选型检查和明确的默认模型策略。
- [x] 通用 SSE 内容块编码；E2B 运行等待、工具参数、结果、结束和流内错误。真实模型规划、E2B 执行 37+83、SSE 输出 120 与 message_stop 已通过。
- [ ] 网页搜索/抓取真实工具块适配、来源引用与不支持约束的明确拒绝。
- [x] 官方 MCP SDK 的远程连接、目录、Bearer 参数隔离、筛选、调用和输出隔离；DeepWiki 目录、读取及真实模型执行循环已通过。
- [ ] MCP 认证服务真实验收、Messages 延迟加载和持久 pause_turn 恢复；当前直接发现端点与有限同步循环不代表这些已完成。
- [ ] 文档字符/页码定位引用，校验引文原文与位置；不将平台生成引用称为 Anthropic 原生输出。
- [ ] Files、Skills、Batches 的资源路由与 SDK 兼容范围核查。
- [ ] 结构化输出错误分类及复杂 Schema 验收；不声称本地校验提供原生约束采样。
- [ ] 回归、真实账号、公开 MCP、E2B 和公网验收；发布、文档与可回滚提交。

## 已确认的上游限制

ClewdR master 的 transform_request 对非 Pro 能力账号不发送 model。最新真实 SSE 请求 claude-sonnet-4-6，返回 model 为 claude-sonnet-5-5；模型名自述不用于鉴定。

ClewdR 非流式输出只合并 completion 文本；流式直接保留网页事件。因此工具和引用适配须从真实 SSE 构建，并对网页 tool_use/tool_result、citation_start_delta 等进行明确转换。限制次数和域名不能只靠提示宣称已强制执行。

## 复用与依据

- [ClewdR 请求转换](https://github.com/Xerxes-2/clewdr/blob/master/src/claude_web_state/transform.rs)
- [ClewdR 响应转换](https://github.com/Xerxes-2/clewdr/blob/master/src/types/claude_web/response.rs)
- [MCP Python SDK v1 文档](https://py.sdk.modelcontextprotocol.io/v1/client/)
- [Claude MCP 协议](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)
- [DeepWiki 官方服务](https://docs.devin.ai/work-with-devin/deepwiki-mcp)
- [Claude 搜索协议](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [Claude 抓取协议](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool)
- [Claude 引用协议](https://platform.claude.com/docs/en/build-with-claude/citations)


## 本轮架构

新增 mcp_connector.py 负责官方 SDK 的连接和输入输出边界；mcp_messages.py 只负责有界工具循环。模型推理仍通过 runtime_forward.py 和既有网关选取空闲账号，未改 ClewdR。message_stream.py 统一已验证内容块的 SSE 编码，客户端工具和 E2B 共用，避免重复维护协议。

MCP 同一请求内复用连接和目录，认证不跨调用者复用；持久任务认证加密保存，结束后清除；失败执行不自动重放。目录与执行分别验证，避免权限失效后使用旧目录。连接并发和模型请求有界，复用 PostgreSQL/Redis 的账号占用与配额，没有添加新队列或数据库。

MCP 在模型规划校验后发送调用块、远程返回后发送结果；E2B 已支持真实执行期间的 stdout。参数校验与最终回答仍有缓冲，不提供原生 Thinking 增量。精确选型 exact 明确拒绝，不伪造模型身份。搜索约束、文档原生引用与官方资源接口兼容仍保留在待办，未以相似字段宣称完成。


## 七项收尾验收（2026-10-09）

所有功能保持在 WebCC；不修改 ClewdR 或账号容器配置。基础设施继续复用 PostgreSQL、Redis、Procrastinate 与 E2B。真实测试只在服务器进行。

- [ ] 搜索/抓取：网页来源与结果块适配继续核查；Brave 的严格域名、次数限制按用户决定暂缓，网页自带搜索仍可使用。
- [x] 文档引用：文本字符、PDF 页码和逐字匹配；相近段落与错误引用负例；扫描 PDF 明确范围。
- [x] 流式：真实执行中 stdout/状态输出、增量工具块、取消和流内错误；记录首个有效事件时间，不将 ping 算作首个结果。
- [x] SDK：官方 Python SDK 的 Files、Skills、Batches 创建、分页、查询、下载、删除和隔离；保留旧 JSON Skill 接口。
- [x] 结构化输出：递归与复杂 Schema、拒答、截断、不合法输出；按 Schema 拒绝不合规结果，不伪称原生采样。
- [ ] 并发故障：独立进程竞争、进程中断、重复交付、未知执行结果；记录容量与延迟。
- [x] MCP：官方 SDK 连接复用、认证隔离、真实认证测试服务、延迟发现、持久暂停恢复；副作用未知时不重放。

参考：Anthropic API/SDK、MCP Python SDK、Brave Search API、E2B SDK。复杂能力的完成门槛是协议测试加真实调用，不以“已写代码”替代。

验收：190 项回归全部通过，无跳过。官方 Python SDK 1.12.1、真实账号、E2B 和 DeepWiki 验收见 [记录](finish-acceptance-2026-10-09.json)。真实 stdout 首段 9.17 秒，这是一次样本，不是延迟保证。跨进程共享锁、进程中断和防重放已通过；第二台物理服务器尚未验收。

生产发布：68262fa。公网官方 SDK 引用与 MCP SSE 通过；80 个运行文件摘要一致；11 个 ClewdR 容器未改配置、未换镜像、未重启。部署备份：/var/backups/webcc/finish-tools-20261009T065838Z。
