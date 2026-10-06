# 实现前调研规则与技术参考

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

已补充固定版本和取舍，并用现有账号执行真实实验，见 [报告](WEB-TOOL-EXPERIMENT.md)。基础可行性通过不替代原生资格或实际应用验收。实验代码隔离在 experiments 中，生产不导入。

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
