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
