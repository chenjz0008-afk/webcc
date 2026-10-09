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

MCP 连接每次独立创建，认证不跨请求复用，不写入数据库；失败执行不自动重放。目录与执行分别验证，避免权限失效后使用旧目录。连接并发和模型请求有界，复用 PostgreSQL/Redis 的账号占用与配额，没有添加新队列或数据库。

当前 MCP Messages 与 E2B SSE 在结果确认后编码输出，等待时发送 ping；不是原生增量推理或实时沙箱 stdout。精确选型 exact 明确拒绝，不伪造模型身份。搜索约束、文档原生引用与官方资源接口兼容仍保留在待办，未以相似字段宣称完成。
