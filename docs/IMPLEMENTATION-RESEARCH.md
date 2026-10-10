# 实现前调研规则与技术参考

## 2026-10-10 Claude Code 协议修复

[Claude Code 网关协议](https://code.claude.com/docs/en/llm-gateway-protocol)要求兼容消息中的 system、上下文扩展和完整 SSE。WebCC 保留历史顺序；Messages 与 Chat Completions 共用工具验证与流式适配，不创建第二套执行器。

[权限模式](https://code.claude.com/docs/en/permission-modes)说明独立审批请求及无可解析结果时的阻止行为。网关只识别完整的 severity 请求协议，单次最多 30 秒；网页通道内部为该请求保留至少 1024 输出上限，校验真实返回的 0–100 判定。超时、空回复或拒答不会变成允许；这不是原生服务端审批证明。

[ClewdR 转换代码](https://github.com/Xerxes-2/clewdr/blob/master/src/claude_web_state/transform.rs)将对话放入 paste.txt，实际正文来自 custom_prompt，并使用全局 web_search。真实 PDF 测试曾将附件误解为待分析材料。客户端工具规划使用同镜像、同账号代理的独立配置，关闭内置搜索，并以可见指令继续附件中的 API 对话；普通网页搜索继续使用原配置。两种配置共用同一 PostgreSQL 账号租约，不能同时使用一个账号。闲置配置最多保留两个，十分钟未用回收；活动配置受全局并发约束。

[细粒度工具流](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)允许参数增量尚未构成完整 JSON。WebCC 在完成工具块前校验完整参数；已发布的调用不因后续错误自动重放。禁用 Thinking 时移除对应块并重排可见索引；没有可用正文的截断不能显示为空白成功。

[公开工具执行分析](https://github.com/liuup/claude-code-analysis/blob/main/src/services/tools/StreamingToolExecutor.ts)仅作为执行顺序、取消和副作用边界的参考，不作为 Anthropic 私有实现或上游能力的证据。此次未修改或复制 ClewdR 源码。

调研日期：2026-10-06。本文件记录候选方案和采用条件，不表示相关能力已经实现。不同上游权限、服务器资源和更新成本下不存在统一最优项目。

每个待办在编码前补齐以下记录：官方请求与响应约束、至少一个相关成熟实现、适用版本或提交、现有账号真实结果、复用与自行实现的取舍、最小改动范围、成功和失败验收条件。没有证据的功能继续保留在 TODO 中。

## 当前参考

| 范围 | 主要参考 | 本项目的选择原则 | 尚需验证 |
| --- | --- | --- | --- |
| Messages、客户端工具与工具结果 | [Anthropic 工具协议](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls) | 使用原生内容块和 ID 对应关系，保留工具历史；客户端执行自己的业务工具 | 已挂账号能否实际返回 tool_use 并消费 tool_result |
| 多上游协议适配 | [LiteLLM Messages](https://docs.litellm.ai/docs/anthropic_unified)、[透传接口](https://docs.litellm.ai/docs/proxy/pass_through) | 原生 Anthropic 通道优先透传；协议转换仅在真实功能可兑现时采用 | 新依赖的内存开销、转换损失、各上游的功能支持 |
| 多账号选择、失败切换与流式安全 | [sub2api 网关源码](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/handler/gateway_handler.go) | 参考账号尝试状态、并发槽释放和流开始后停止切换的处理；保持现有管理平台 | 用固定提交复核相关 service 层与取消路径，再对照当前网关测试 |
| 重试、限流与冷却 | [LiteLLM Router](https://docs.litellm.ai/docs/routing) | 明确各层重试责任，按错误类型和总耗时限制尝试次数 | 本项目 ClewdR max_retries=5 保持不变；包装层需评估重试叠加 |
| 文档应用验收 | [Anthropic 工具循环](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works) | 测试“读取→定点修改→读回”、错误恢复和多轮往返 | 普通文本改写通过不能替代应用工具验收 |
| 当前网页与原生订阅通道 | [ClewdR 固定源码](https://github.com/Xerxes-2/clewdr/tree/061c6d8ac9187148f50c8d806b56962a7f222b6c) | 官方容器保持不改；通过隔离实例先核查实际上游权限 | 当前已挂账号资格及工具协议缺口见本轮测试报告 |

## 初步结论

Anthropic 客户端工具要求模型返回结构化 tool_use，由调用方执行后以 tool_result 回传。把模型输出中的代码、JSON 或“我已读取文档”文字转换成成功调用，不能证明符合这一协议。

LiteLLM 提供 Messages 接口和多上游适配，可作为候选组件。是否接入要由功能测试和资源评估决定；目前不直接部署第二套完整网关。sub2api 的网关实现可用于研究调度、失败切换和流式安全，其订阅通道也需要实际账号资格，不能仅凭项目说明推定现有网页 sessionKey 可用。

本轮先解决 P00。Files、Batches、缓存、服务端工具、MCP 等功能在各自任务开始前分别查阅官方文档和对应实现，不由工具调用测试推导支持。

## 网页工具基础实验

已补充固定版本和取舍，并用现有账号执行真实实验，见 报告（历史记录已归档）。基础可行性通过不替代原生资格或实际应用验收。实验代码隔离在 experiments 中，生产不导入。

## E01 · 工具历史的接入前检查

2026-10-06 复核 [官方结果处理](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)：客户端工具结果按 tool_use_id 对应，紧接调用所在的 assistant 消息，在 user 消息中先放结果再放文本。结果允许为空，并以 is_error 表达执行失败。工具结果是非可信资料，不能提升为系统指令。

再次核查 [LiteLLM 配置](https://docs.litellm.ai/docs/proxy/config_settings) 的 add_function_to_prompt：只证明存在把工具定义放入提示的成熟实现，不能据此承诺原生工具、流式和历史能力。沿用上一轮固定版本参考，复用 jsonschema 校验嵌套参数；暂不引入完整 LiteLLM 服务。

最小改动限定 experiments：发送前检查历史 ID、缺失和重复结果、结果顺序及支持的文本内容；复杂 Schema 继续由标准库处理。结果可乱序，但必须与上一轮所有调用一一对应。当前实验要求消息交替、工具定义不变、ID 在给定历史中唯一，末条为 user；这些是本阶段约束，不宣称官方接口要求完全相同。

单次提示数据最多 128 KiB、32 层嵌套、128 条消息、64 个工具；这些是封装的容量限制，不能当作模型真实 token 上限。超限直接拒绝，不截断或删除历史。不支持的图片、thinking、服务端工具和额外字段明确拒绝。协议不合格请求不发送到账号，更不用于账号隔离判断。

真实门槛为两个工具调用、乱序结果回传、一次版本冲突恢复、修改后回读以及恶意文档文本。只操作内存合成文档，版本冲突由测试器模拟；客户端不提供任意文件或命令执行。通过后才能推进实验网关；SDK、SSE、取消和实际应用仍需独立验收。

## E01 · 主网关实验入口与 SDK

2026-10-06 核查 [Anthropic SSE 协议](https://platform.claude.com/docs/en/build-with-claude/streaming)、[官方 Python SDK](https://github.com/anthropics/anthropic-sdk-python) 与 [SDK 配置](https://platform.claude.com/docs/en/api/sdks/python)：工具流包含内容块起止和 input_json_delta，SDK 可以聚合最终消息；流内错误必须保持 error 事件。SDK 允许明确传入 base_url 和 key，无需读取本机 Claude Code 配置。

复核 [sub2api 固定网关](https://github.com/Wei-Shaw/sub2api/blob/b8dece9000c68815a5b867ca5a1e6f236e173905/backend/internal/handler/gateway_handler.go) 的 wrapReleaseOnDone、账号等待和 FailoverCanceled：请求取消应回收槽位，失败切换应区分已向客户端输出的内容。采用这些生命周期原则，继续复用本项目现有选号与失败计数，不引入 Go 服务或复制其业务代码。

网页提示模式无法从生成中的任意 JSON 片段证明参数合法，因此选择缓冲上游完整响应，校验后生成 SDK 可读的 SSE；等待时可发送 ping。它不是原生逐 token 生成，也不是 Anthropic fine-grained 工具流。实际工具仍由客户端执行；格式失败和客户端取消不计账号异常，HTTP/连接故障复用已有阈值。尚未输出工具块时可以切换账号，输出工具块后不重放。

入口限定 /v1/messages，要求 MANAGER_WEB_TOOLS_ENABLED=true、X-WebCC-Tools: prompt-v1 和明确模型别名 webcc-prompt-v1。默认开关关闭，普通请求维持原来的转发方式。工具适配移到独立 web_tools 包供网关和实验共享，避免生产调用测试程序。该别名表示适配策略，不表示实际 Claude 模型身份；用量保留上游报告，包括额外提示开销，不称为官方计费精确值。

验收包括默认网关回归、普通 SDK 与 SSE 工具往返、错误 ID 发送前拒绝、格式错误不隔离、跨账号切换、三次 500 阈值、排队和生成中的客户端断开。不据这些结果宣称图片、文档块、thinking、strict、Files、真实文档应用或完整官方协议已经支持。

SDK 版本核查：2026-10-06 的 [官方 PyPI 发布](https://pypi.org/project/anthropic/)为 1.11.0；[该版项目依赖](https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/pyproject.toml)已改用 httpx2。测试使用 SDK 自带 DefaultHttpxClient(trust_env=False)，而不是旧 httpx.Client；显式提供临时 API key 和服务器 loopback Base URL，关闭 SDK 自动重试。依赖只安装到临时目录。

## 2026-10-06：代理出口限制

依据 Docker 官方 iptables 文档核查当前服务器为 iptables-nft 兼容后端。使用专用 bridge、DOCKER-USER 与 INPUT/FORWARD 防火墙链；先安装规则再启动容器，启动恢复失败不放行 Docker。官方 ClewdR 固定提交 061c6d8 的 config/clewdr_config.rs 使用 wreq Proxy::all；本地 SOCKS5 DNS 依赖在临时测试中导致 500，改为 SOCKS5h 后不开放直接 DNS 仍真实回复成功。

来源：https://docs.docker.com/engine/network/firewall-iptables/ ，https://github.com/Xerxes-2/clewdr/blob/061c6d8ac9187148f50c8d806b56962a7f222b6c/src/config/clewdr_config.rs 。实现和边界见 EGRESS.md。

## 2026-10-06：TypeScript SDK 与 HTTPS 文件流程

依据官方 TypeScript SDK 及 helpers 接口使用 messages.create 和 messages.stream().finalMessage()，实际 npm 版本 0.131.0、Node v24.21.0。用独立模型别名与显式请求头测试近似通道，未伪装成官方 strict 或原生模型身份。Node 运行时经官方 SHA256 校验，npm 禁用安装脚本。候选 HTTPS 保持证书校验，使用临时 CA，并验证不信任 CA 时不会进入模型调用。真实合成 Markdown 文件按逻辑 ID 访问，校验目标编辑和其他字节不变；不把此流程称为 WordBuddy 或 DOCX 验收。

来源：https://github.com/anthropics/anthropic-sdk-typescript ，https://platform.claude.com/docs/en/api/sdks/typescript 。结果见 TYPESCRIPT-SDK.md。

## 2026-10-07 · C01 与 C24

对照 [Claude 错误协议](https://platform.claude.com/docs/en/api/errors) 的状态类型、错误正文和 `request-id`，以及 [LiteLLM 虚拟密钥](https://docs.litellm.ai/docs/proxy/virtual_keys) 的独立密钥、资源权限和速率控制。TypeScript SDK 固定 0.131.0；实际异常请求 ID 使用 `requestID`，流助手使用 `request_id`，通过真实 SDK 验证，避免依赖猜测的属性名。

当前两核、约 2 GB 的服务器沿用现有网关，不新增 LiteLLM 服务或 PostgreSQL。独立密钥保存随机值的摘要，账号范围直接传给现有选号器，失败切换同样受范围约束。权限集中在鉴权入口，排队请求在获取资源前复核撤销与到期；已开始的请求允许完成。速率窗口使用加锁的单进程内存队列，重启会重置，不宣称提供分布式计费或官方 workspace 管理能力。

验收包括：两名调用者不能跨账号范围、失败切换不能越界、管理接口不能被调用密钥访问、限速原子性、重启后撤销持久化、排队撤销、SDK 六轮真实文件工具流程及 TLS 校验。Files 等未来资源接口的所有权隔离须在该接口实现时继续验收。方案及证据见 [调用密钥](CALLER-KEYS.md)。

## 2026-10-07 · 文档定位、并发读取及错误恢复扩展

按 [官方工具结果协议](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls) 匹配 tool_use_id 并使用 is_error 回传工具失败；结果反序回传，用实际文件验证定位和修改，不以模型文字声明作为通过依据。读取工具使用线程池执行；记录时间区间，设置 30 ms 测试等待以确认任务重叠，不作为模型速度或业务吞吐证明。

新增相近的标准与优先审阅段落，段落 ID、原文及行号来自实际文件。局部修改只允许 B-P2 的留存天数改变，并回读检查；A、其他段落、表格、列表及引用链接保持。测试执行器拒绝 MISSING 与 PRIVATE，验证模型正确披露不存在和拒绝读取，不代替真实应用权限系统验收。

格式检查复用 [markdown-it 14.1.0](https://github.com/markdown-it/markdown-it/releases/tag/14.1.0)，比较解析结构、链接属性和期望 HTML；不编写新 Markdown 解析器，不把 HTML 结构检查称为 DOCX 或视觉排版验收。依赖仅存在于服务器临时测试环境，不加入生产服务。

## C08 / C09 / C15 · 网页范围复核（2026-10-07）

参照 [官方 strict](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)、[PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support) 和 [ClewdR 固定转换实现](https://github.com/Xerxes-2/clewdr/blob/061c6d8ac9187148f50c8d806b56962a7f222b6c/src/claude_web_state/transform.rs)。沿用已调研的 LiteLLM 提示适配思路和 jsonschema，不引入第二套网关。官方约束采样无法由本地参数校验替代，响应显式标记 no-native-strict。

修改前 cc1 七种工具选择通过；PNG 与已有 PDF 上传路径通过，但标准 document 输入被忽略。最小修复是在主项目转换输入，不改 ClewdR。非法编码、URL/file_id、原生引用和扩展在选号前拒绝，失败不计入账号异常；同份合成 PDF 在修改后的独立账号路径复验。完整范围及后续依赖见 网页能力说明（历史记录已归档）。

## C16 / C19 / C20 · 文件和内置能力（2026-10-07）

文件接口对照当前官方 Files 文档和 Python SDK 的 beta=true 路径，复用 python-multipart 0.0.32 与标准 SQLite。选择轻量平台资源库，保持 ClewdR 零修改。候选 PDF 和文本真实引用均返回 120；下载原字节一致，密钥隔离、删除后拒绝和生产账号库未改通过。实现与差异见 [Files](MANAGED-FILES.md)。

先验证网页内置能力再引入服务：官方说明网页免费账号包含搜索、网页读取、代码执行和文件生成。开启并重载后，线上返回真实 web_search；同账号 bash_tool 已执行且返回正确 CSV 内容。故当前不需要为这两种基本能力购买额外服务。Brave/E2B 保留为协议控制和独立生命周期的候选，不将它们加入生产依赖。版本、费用、源码与测试见 搜索与沙箱（历史记录已归档）。

## 2026-10-07 · 附件与客户端工具组合

对照官方工具结果内容块及 Files 输入引用；复用已有 FileStore 所有权校验、web_documents 的 PDF 转换和原版 ClewdR 媒体上传。不引入另一个解析器、OCR 服务或第二套文件库。工具历史保留附件对应标记，二进制附件单独进入网页请求；模型结果仍经过现有 JSON Schema 与 tool_choice 校验。普通文本历史预算保持 128 KiB，完整请求上限 32 MiB，最多 16 个附件。主机不执行模型生成的命令。

网页沙箱文件调研参考公开 claude-exporter 的组织/会话绑定接口，并核对固定 ClewdR 创建会话的流程。下载前必须证明绑定可取得且会话仍有效；不以 present_files 的路径冒充已下载字节。整体模块边界及验收顺序见 [工具架构](TOOL-CAPABILITY-ARCHITECTURE.md)。


## 2026-10-09：持久任务与 E2B

| 方案 | 采用方式 | 选择依据 |
| --- | --- | --- |
| Procrastinate 3.10.0 | PostgreSQL 事务入队、独立 Worker、停滞作业恢复 | 已有 PostgreSQL，避免增加第二套任务状态；作业只保存 ID |
| E2B Python SDK 2.53.1 | 基础 Sandbox、文件、进程、暂停恢复、销毁与显式代理 | 复用官方虚拟机隔离和生命周期；客户端工具等待使用小型异步桥接 |
| Agent Skills | SKILL.md frontmatter、文本资源和不可变版本快照 | 不复制第三方 Skills；保留资源结构并限制路径和容量 |
| Claude 程序化工具调用 | async 工具、caller、工具结果关联 | 当前网页模型只负责规划，Python 和工具结果真实执行 |

官方资料和边界见 [运行时文档](DURABLE-RUNTIME.md)。取消中的沙箱计入限额，无法确认是否已启动的代码不重放。停滞任务依据 Procrastinate 心跳恢复，运行状态机进一步阻止副作用重复。

Procrastinate 的相同执行锁会让未来计划作业阻塞后续即时作业；因此采用每任务 PostgreSQL 执行锁，增加恢复回归。E2B 禁止外网时，透明网络层可能接受 TCP 连接，隔离验收须检查 TLS 或应用层通信。

## 2026-10-10 外贸验收修复

复用 Messages 工具适配，新增 Chat Completions 的薄转换层；保持客户端真实执行、工具 ID 配对和本地参数校验。工具输出中的等价重复名称、已知 `tool_use` 元数据和说明文本分别处理，业务字段不猜测修复。

- [Claude 工具流文档](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)：细粒度输入可能是不完整 JSON，需累计、检查停止原因后执行。`strict` 调用在 WebCC 中逐调用完整校验后发布；普通增量仍保留。Thinking 在工具封装可用前暂存，避免无效规划已经提交而无法纠正。不会将参数校验后的输出声称为逐字符生成。
- [Claude 结构化输出](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)：网页通道的本地校验与官方约束采样不同；引用输出截断归为生成失败，不能报成用户入参错误。
- [Redis Lua](https://redis.io/docs/latest/develop/programmability/eval-intro/)：复用既有 Redis 的原子脚本建立共享 FIFO 等待顺序，保留 PostgreSQL 的最终账号占用裁决。交互与后台分队列；受限账号正在占用的等待者让出入口，避免阻塞其他空闲账号。队列有界、取消清除、失联票据到期；Redis 故障不绕过调度。
- [E2B 模板](https://docs.e2b.dev/template/quickstart)：固定办公依赖模板，执行前检查导入；运行中不安装依赖或盲目重放程序。
- 来源验证使用同一显式代理获取 DNS 地址并固定公开 HTTPS 地址，抓取与解析均限制为 2 MiB；逐字引用仍复用现有定位器。

Python 继续负责协议与编排。实际账号等待以秒计，本地 1000 次转换/校验/流封装组合的中位耗时约 0.37 ms、P95 约 0.44 ms；这仅是局部 CPU 测量，不替代服务器端到端结果。后续按网关 CPU、排队与上游耗时分别决定是否更换组件语言。

进一步核对用户提供仓库的 [`toolExecution.ts`](https://github.com/liuup/claude-code-analysis/blob/main/src/services/tools/toolExecution.ts)：执行前 `inputSchema.safeParse`，失败经 `formatZodValidationError` 形成模型可见错误；结合 [`StreamingToolExecutor.ts`](https://github.com/liuup/claude-code-analysis/blob/main/src/services/tools/StreamingToolExecutor.ts) 的 queued/executing/completed/yielded 状态和 [并行结果恢复分析](https://github.com/liuup/claude-code-analysis/blob/main/analysis/04i-session-storage-resume.md)，采用校验、执行、交付和恢复分层。这里是公开客户端代码的设计参考，账号共享队列仍由 WebCC 的 PostgreSQL/Redis 实现。

真实复测发现模型把业务字段放在 `input` 外，三次泛化纠正仍失败。补充通用的校验阶段、字段路径和规则反馈，原始参数不搬动或猜测；仅在该回复尚未交付可执行内容时纠正。日志只记请求 ID 与阶段，不记反馈中的业务内容。错误样本固定为回归测试。

再次复测发现 `" expected_version"` 带前导空格，单条 required 错误没有完整说明未知字段。采用 [jsonschema iter_errors](https://python-jsonschema.readthedocs.io/en/stable/errors/) 返回有界的完整字段错误，并补齐 calls/index/input 路径，原输入保持不变。该分析仓库同样把校验失败交给模型纠正，不能据此承诺不会生成坏参数。官方 strict 的语法约束采样属于服务端能力；仅复制客户端执行器或更换为 TypeScript 不会获得它。参考 [Anthropic 工具设计](https://www.anthropic.com/engineering/writing-tools-for-agents)，将根因样本纳入真实业务验收，先检查结果再追加改动。
## 2026-10-10：引用定位的职责调整

连续引用验收捕获到网页 `bash_tool` 启动事件，未取得命令和执行结果，不据此断言程序已完成。原引用提示要求模型计算 Unicode 字符偏移；改为模型提供页码和逐字引句，复用平台已有定位器计算起止。唯一引句可以省略偏移，重复引句仍需明确定位；原响应字段保持一致。此职责划分是 WebCC 的实现选择，不将其归为 Claude Code 分析仓库的算法。

原版 ClewdR 的[请求转换](https://github.com/Xerxes-2/clewdr/blob/master/src/claude_web_state/transform.rs)按全局 `web_search` 配置声明网页搜索，当前代码未显示逐请求关闭接口。本次只读核查，没有修改 ClewdR。未知网页工具继续保留防重放边界，JSON 和 SSE 采用相同规则。
