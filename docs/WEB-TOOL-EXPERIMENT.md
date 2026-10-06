# 网页工具适配调研与实验

日期：2026-10-06。状态：基础实验通过，尚未接入生产。官方 ClewdR 源码与部署容器不变。

## 为什么做这个实验

上一轮已确认现有账号网页生成正常，但原生通道因 Free 资格失败，网页请求中的自定义工具未传入模型。这里单独验证能否由主项目把工具和历史写入提示，再将模型提出的操作转换成客户端工具块。P00 和原生工具待办继续保留；本实验不改变其结论。

## 方案来源与取舍

| 参考 | 核查对象 | 可复用思路 | 本轮取舍 |
| --- | --- | --- | --- |
| Anthropic 官方 | [工具结果协议](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls) | 调用方执行工具；tool_use 与 tool_result 以 ID 对应 | 使用这一外部结构，不声称网页上游原生生成了这些块 |
| LiteLLM | [add_function_to_prompt 配置](https://docs.litellm.ai/docs/proxy/config_settings)；调研时主仓库提交 126e79c9672480e11dd68def1d440debb899d7ea | 将函数定义加入不支持原生工具的模型提示 | 证明该方案有成熟网关先例；此配置本身不证明完整工具往返，没有部署 LiteLLM |
| 0G Compute Adapter | [固定 server.js](https://github.com/claraverse-space/0G-Compute-Adapter/blob/1adc4336674b8864911e8ebdec57b01022660ae7/server.js) | 工具定义入提示、响应解析、协议转换和历史转换 | 仅参考思路。其解析器支持从普通文字搜索多种调用格式；本实验仅接受完整约定 JSON，减少引用和示例被误解析的机会。它是小型参考项目，不作为成熟度或 Claude 兼容性的证明 |
| sub2api | [固定网关源码](https://github.com/Wei-Shaw/sub2api/blob/b8dece9000c68815a5b867ca5a1e6f236e173905/backend/internal/handler/gateway_handler.go) | 账号调度、转发、失败切换、输出开始后的限制 | 作为后续网关调研对象，本轮没有把其订阅能力推定为现有 sessionKey 的权限 |
| jsonschema | [标准验证库](https://python-jsonschema.readthedocs.io/en/stable/api/jsonschema/protocols/)；服务器 4.10.3 | Draft202012Validator 校验输入参数 | 复用库，避免手写类型和嵌套 Schema 校验；实验禁止外部引用 |
| claude2api-deploy | [部署说明](https://github.com/dmvait8534/claude2api-deploy) | 声称提供网页工具代理，说明指向 ZIP 部署包 | 本轮未审阅打包内容，不运行或接入；README 声称不是验收证据 |

没有发现能凭现有免费网页资格直接兑现完整官方 API 的已验证方案。当前最小实验只包含提示构建、完整 JSON 解析、Schema 校验和格式转换，隔离在 experiments 目录，生产 manager 不导入它。

## 真实测试结果

本轮共发送 25 次真实网页消息请求，使用已挂账号的临时官方容器及各自代理，没有自动修复格式或重试解析失败。

| 验收 | 账号或范围 | 结果与实际含义 |
| --- | --- | --- |
| 首轮请求文档工具 | 全部 11 个账号 | 11/11 输出合法 JSON 操作请求，工具名和文档 ID 正确 |
| 读取→修改→再次读取→最终回答 | cc1、ccb1、ccb9 | 三个账号各四轮通过；客户端模拟器实际把内存文档数量 7 改成 9，货币 EUR、总额 140 保持不变 |
| tool_choice=none | cc1 | 没有返回调用，明确表示未验证文档 |
| 指定 read_document | cc1 | 返回指定工具及正确 ID 参数 |
| 两个读取请求 | cc1 | 一次返回两个调用，两个文档 ID 正确；尚未执行真实并行工具或测试双结果回传 |
| 工具返回 is_error | cc1 | 接受合成的“文档不存在”错误结果，最终报告错误，没有编造文档内容 |
| 引用调用 JSON 示例 | cc1 | 仅解释示例，没有把示例转换成待执行操作 |
| 离线解析防护 | 14 项 | 全部通过，覆盖未知工具、非法参数、重复 JSON 键、截断、代码围栏、混合输出、错误选择、空回答、外部 Schema 引用等 |

首轮使用 any，后续读写使用 auto，因此具备基础选择控制证据，但不保证任意复杂任务都遵守。三个完整流程只是小型合成文档，不是 WordBuddy、真实 Word 文件或用户业务的集成验收。错误场景验证了错误传播，未证明冲突恢复和自动重试。

## 实现边界

- 原始网页响应仍为 text；本平台在校验后生成 tool_use ID 和 stop_reason。它们是适配产物。
- 历史与工具结果作为结构化 JSON 文本进入提示，不能等同原生的内容块处理或 prompt injection 防护。
- 返回模型身份仍为空，不能宣称本轮测试兑现了请求中的 claude-sonnet-4-6。
- Schema 校验发生在生成之后，格式错误会拒绝；不等同官方 strict 或约束采样。复杂 Schema、format 和递归支持尚未验收。
- 没有官方 thinking 签名、精确缓存/用量、Files、Batches、服务端代码执行或完整 SSE。
- 单个工具最多 8 个调用的封装限制只是实验防护；生产仍需历史 ID 匹配、容量上限、鉴权、取消和副作用处理。

## 环境与清理

实验在部署服务器进行，未使用本机 Claude Code、Claude 配置或本机模型出口。容器出站规则限制为账号代理目标、DNS 与已建立连接的返回流量。测试内容仅为合成协议和文档，不含本机身份、路径或用户真实文件。

生产 registry 两轮均未变化；最终 11 个生产账号保持 ready/running，未 OOM；管理服务与 Nginx active。测试容器、网络和配置副本已清理。上游调用会消耗账号额度，不能保证无上游记录。

## 后续门槛

基础可行性实验已经完成。正式原生兼容待办不删除。下一项是 TODO 中 E01：核查成熟方案的历史、失败和流式处理后，在主项目建立明确的实验策略，完成复杂历史、恶意文档、SDK、SSE、取消及实际应用验证。验收前不改生产对外文档为“支持完整工具”。

代码和运行说明：[experiments](../experiments/README.md)。真实脱敏证据：[测试记录](web-tool-experiment-2026-10-06.json)。
