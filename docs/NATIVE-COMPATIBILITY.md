# 原生协议与平台适配

目标是在 WebCC 提供可实际使用的 Claude API 能力。ClewdR 保持原版，仅使用现有网页账号。不通过删除参数、生成虚假字段或提前拒绝请求来宣称功能完成。

## 请求与返回

普通 Messages 保留请求扩展、Beta 参数及上游返回的 Thinking、签名、工具块和缓存字段。平台适配须逐项完成真实验收。HTTP 200、字段透传和功能生效是三种不同的结论。

## 实现顺序

| 内容 | 实现路线 | 验收门槛 |
| --- | --- | --- |
| 客户端工具 | 复用现有工具规划、Schema 校验与结果历史；补标准入口 | 真实调用、结果回传、并行、取消与失败恢复 |
| MCP、代码执行、资源 | 复用已验收的 MCP/E2B、PostgreSQL 与队列 | 标准客户端完成整个流程，资源归属与清理正确 |
| 搜索、抓取 | 读取网页真实事件与来源，补公开响应结构 | 来源来自真实工具，不能填造加密来源凭据 |
| Thinking | 保留上游原始块、流式增量与真实签名 | 工具交互中原样回传并成功继续；无签名不能标为原生通过 |
| 缓存 | 验证网页 cache_control；平台前缀处理缓存单独说明 | 有真实复用行为；平台缓存不宣称减少上游模型计算 |
| 并发、取消、生命周期 | 共享状态与有界队列，明确持久执行状态 | 独立实例竞争、断线、重启、资源到期及防重放 |

Brave 的严格域名与次数控制按用户决定暂缓。原生约束采样与精确模型身份依赖上游提供，平台 Schema 校验不替代约束采样。

## Thinking 签名

Thinking 可包含模型思考摘要。signature 是上游提供的不透明加密字段，用于恢复和验证思考内容。涉及工具时，应将原块原样回传；signature_delta 是流式传输该字段的事件。

平台可以生成自己的状态令牌，但该令牌不能由 Anthropic 验证，不能称为 Anthropic 原生签名。平台生成的解释应标为解释或摘要，不冒充上游原生思考。

依据：[Anthropic Thinking 文档](https://platform.claude.com/docs/en/build-with-claude/thinking#thinking-encryption)。

## 标准客户端工具

普通 /v1/messages 请求包含自定义 tools 时，自动复用现有工具规划与 Schema 校验，无需专用请求头或平台模型名。调用方执行 tool_use 后，以原 ID 回传 tool_result；支持现有工具选择、并行及附件流程。普通 messages 权限即可使用客户端工具，不授予 MCP 或沙箱执行权限。

标准客户端工具采用 ijson/jiter 解析真实上游增量，生成过程中输出 Thinking、文本与工具参数，Schema 校验成功后完成工具块。若输出后发生错误，结束流且不重放调用。文档引用等待逐字校验；MCP 返回执行进度，E2B 返回真实 stdout。旧显式工具入口保留缓冲模式。工具定义及历史转换为网页模型可理解的协议。

## 本次实测

199 项回归通过，包含缓存标记与 effort 测试。两个独立网关和 Node Agent 通过共享限流、两个真实账号并发、跨节点转发及占用释放测试，合计 5.626 秒。

服务器官方 Python SDK 1.12.1 标准工具流式调用通过；客户端实际计算 37+83，保留 assistant 历史并回传结果，第二轮正确回答 120。两轮合计 10.761 秒，仅 messages 权限，无专用请求头。实际返回模型为 claude-sonnet-5-5；这次未返回 Thinking。此前普通通道探测收到 Thinking，但未收到签名。缓存实际复用仍未证实。

d8adc72 已部署，健康检查通过。备份：/var/backups/webcc/standard-tools-20261009T075347Z。公网官方 SDK 标准工具两轮通过，11.241 秒；测试密钥已撤销删除。11 个 ClewdR 镜像一致，容器均未重启。详见 [记录](native-compatibility-check-2026-10-09.json)。

## 本轮架构

标准声明由 messages_api 路由至已有客户端工具、文档引用、MCP 或 E2B 执行器，资源归属仍由 PostgreSQL 检查，持久任务仍使用现有队列。未修改 ClewdR。

history_state 对缺少上游签名的真实 Thinking 生成 WebCC Fernet 状态令牌，绑定调用密钥、内容摘要和模型，24 小时到期。原生签名保持原值。平台令牌验证本网关历史，不能恢复网页未提供的隐藏推理或供 Anthropic 官方接口验证。

processing_cache 仅加密缓存工具定义校验、PDF 提取和来源正文。共享部署复用 Redis；无共享后端时使用有界进程缓存。默认每项 5 分钟、来源 1 分钟，最多 32 项，每项明文最多 256 KiB；不缓存失败或模型动态答复，不声明模型 KV 缓存命中。

native_events 根据真实网页事件转换搜索和抓取结果。来源正文通过指定代理读取，公共 HTTPS 地址经 DNS 校验及地址固定；引用从提取的原文中选取并逐字验证。平台来源凭据明确使用 webccsource_v1_ 前缀。网页未提供官方完整来源正文或加密凭据时，不将平台转换称为官方原生凭据。

复用依据：[ijson](https://github.com/ICRAR/ijson)、[jiter](https://github.com/pydantic/jiter)、[Trafilatura](https://trafilatura.readthedocs.io/en/stable/quickstart.html)。
