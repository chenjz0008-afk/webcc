# 网页账号能力范围

更新时间：2026-10-07。实现位于 WebCC 主项目，官方 ClewdR 容器不修改。实现提交 33aa3be 已于 2026-10-07 部署生产，工具实验策略需调用方显式选择。

## 本轮实现

工具策略要求显式开启 MANAGER_WEB_TOOLS_ENABLED=true，并携带 X-WebCC-Tools: prompt-v1、model=webcc-prompt-v1。模型提议由网关校验，业务工具由客户端执行。

| 能力 | 实现与边界 |
| --- | --- |
| 工具选择 | auto、none、any、指定工具，以及禁用并行；网关拒绝不满足选择约束的返回，不把提示约束称为官方采样控制 |
| strict | 接受布尔字段；复用 jsonschema Draft 2020-12 校验返回参数。不是官方 grammar-constrained sampling，不保证每次生成成功 |
| 图片 | 普通 /v1/messages 的内联 PNG 路径实测；不由一个 PNG 推定全部格式、大小和工具组合可用 |
| PDF | 普通 /v1/messages 的 base64 application/pdf document 块转换为 ClewdR 已有上传路径，保留文件字节、标题和上下文 |
| PDF 限制 | 单文件最多 20 MiB，网关整个请求最多 32 MiB；检查编码及文件头，实际文件解析由上游完成。只接受 user 文档；URL、file_id、开启原生引用和缓存扩展返回 400 |

PDF 转换不下载调用方 URL，不提供 Files 资源 ID。实验工具策略已发布附件适配，PDF 与 PNG 工具组合及工具结果附件候选实测通过，公网验收单独记录。

## 验收结果

服务器隔离候选：142 项回归通过，耗时 77.280 秒。ccb9 的 11 次真实模型请求全部通过：七种工具选择、本地 strict、PNG、标准 PDF 输入及旧上传输入。两页 PDF 返回 north=37、south=83、sum=120 与两个正确引用标记；这些标记是文档内文本，不是原生 citations。

官方 TypeScript SDK 0.131.0 / Node v24.21.0；工具选择使用缓冲 SSE，媒体使用普通响应。候选 HTTPS、非法 ID 选号前拒绝、密钥撤销、账号范围、槽位释放通过。生产账号库不变，临时容器与目录均清理。

修改前的 cc1 基线中标准 PDF 输入失败，其他九次模型请求通过；保留失败记录，不将其计为通过。见 [基线](basic-capabilities-baseline-2026-10-07.json) 与 [修改后结果](basic-capabilities-2026-10-07.json)。未测试 PDF 与工具混用、PDF 的流式/大文件极限以及原生引用。

## 与完整 Claude API 的差距

任务编号沿用 TODO.md，避免把 C15、C16 等编号混淆。

| 任务 | 网页账号当前状态 | 可采用的后续实现 | 原生等价限制 |
| --- | --- | --- | --- |
| C08 工具选择 | 实验策略已实现 | 保留校验、扩展真实失败和应用验收 | 网页生成不是官方工具采样 |
| C09 strict | 本地参数校验 | 扩充支持的 Schema 与失败处理 | 无官方约束采样 |
| C11 Thinking 与签名 | 未完成原生验收 | 单独验证现有上游块与历史行为 | 不生成或伪造 Anthropic 签名 |
| C15 图片、PDF、引用 | 内联图片/PDF 部分实现 | 覆盖页面、布局、图片格式；单独建设引用定位 | 文本引文不是原生 citations |
| C16 Files | 平台资源生命周期生产后端已发布并实测通过 | 用户隔离的本地资源库、上传后展开到网页请求 | 自建 ID 不是官方 Files ID；需验证删除、配额与账号切换 |
| C18 Prompt Caching | 尚无原生缓存证据 | 可优化本地存储与请求构造 | 不能返回伪造的 cache_read/cache_creation 用量 |
| C19 搜索、抓取 | 网页搜索和读取已实测；原生协议待适配 | 复用搜索服务与受控抓取，以明确的自建工具执行 | 网页按钮不证明 API 服务端结果、引用与 pause_turn 支持 |
| C20 代码执行、生成文件 | 网页 bash_tool 与 CSV 资源已实测；下载待适配 | 无网络的隔离沙箱、资源额度、归属及回收 | 服务器资源需评估；自建沙箱不是 Anthropic container |
| C21 MCP、发现、延迟加载 | 未实现 Connector | 复用 MCP SDK，映射允许的客户端工具并管理认证 | 不声称官方 Connector；需要连接与跨用户隔离验收 |
| C22 程序化工具调用 | 未实现 | 依赖沙箱与可恢复执行状态，再实现工具调用桥接 | 不是仅增加 allowed_callers 字段即可兑现 |
| C23 Skills | 未实现 API 生命周期 | 沙箱运行受控技能目录，明确版本与产物归属 | 不冒充官方 Skills API；依赖代码执行与 Files |

下一阶段先建立 C16 自建文件资源生命周期，再验证 C19 受控搜索抓取；C20 沙箱、C21 MCP 与 C22/C23 必须单独设计和实测。不会因为请求字段能解析就删除对应待办。

## 协议与源码依据

- [官方严格工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)：约束采样与本地校验不同。
- [PDF 输入](https://platform.claude.com/docs/en/build-with-claude/pdf-support)、[Files](https://platform.claude.com/docs/en/build-with-claude/files)：内联文档与资源生命周期分别验收。
- [ClewdR 固定转换源码](https://github.com/Xerxes-2/clewdr/blob/061c6d8ac9187148f50c8d806b56962a7f222b6c/src/claude_web_state/transform.rs)：已有 base64 PDF 上传路径，普通 document 块未进入该路径。
- [MCP Connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)、[工具发现](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)：发现与认证有独立协议。
- [Prompt Caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching)：缓存统计来自上游，不由响应缓存替代。
- [代码执行](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool)、[程序化工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)、[Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)：依赖执行环境与资源生命周期。

## 2026-10-07 补充

生产已开启 11 个账号的网页搜索，新账号默认开启。公网返回实际 web_search 和来源列表。现有账号同时可调用 Claude 云端 bash_tool，已生成 CSV 资源；生成文件下载尚未完成。优先复用内置能力，暂不需要 Brave Search 或 E2B。见 [搜索与沙箱](WEB-SEARCH-SANDBOX.md)。

平台文件资源已完成生产后端发布及公网 PDF/文本引用、原字节下载等验收。该资源库不同于 Claude 云端沙箱文件，不把两者混为同一下载能力。见 [平台 Files](MANAGED-FILES.md)。

## 附件与工具组合发布

附件适配已部署生产，复用现有平台 Files；候选 PDF、PNG 和工具结果 PDF 的 6 次真实往返通过。120 项完整回归与新增并发门槛的 37 项定向回归通过。主应用不执行模型生成代码，账号代理网络不变。普通文本历史预算仍为 128 KiB；附件独立传递，完整请求最多 32 MiB、16 个附件，最多同时 2 个附件工具请求。原生引用和 thinking 签名仍未提供。见 [架构](TOOL-CAPABILITY-ARCHITECTURE.md)。
