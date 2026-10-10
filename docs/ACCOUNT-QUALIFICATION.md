# 已挂账号能力验收

日期：2026-10-06。分支：`feat/claude-api-compatibility`。本轮没有修改或部署生产代码，没有修改官方 ClewdR 项目。

## 结论

11 个已挂账号均取得真实普通回复。网页通道自定义客户端工具验收未通过；原生 `/code/v1/messages` 复核时 11 个账号均返回 `no_cookie_available`，脱敏阶段日志均出现 `Free`。

P00 尚未通过。“工具读取文档→结果回传→修改→读回”、并行工具和实际文档应用验收没有可用的结构化工具入口，因此暂不进入这些依赖任务。普通回复和纯文本改写不代表完整 Claude API 兼容。

## 逐账号结果

| 账号 | 网页真实普通回复 | 网页自定义工具 | 原生通道复核 |
| --- | --- | --- | --- |
| cc1 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| cc2 | 首轮网络错误；复测通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb1 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb2 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb3 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb4 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb5 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb6 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb7 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb8 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |
| ccb9 | 通过 | 未返回 tool_use | 500 / no_cookie_available；Free |

cc2 首轮网页和原生请求出现 `wreq_error`，重新创建副本后网页取得 Hello 和工具测试文本回复，原生则出现 Free / no_cookie_available。故首轮网络错误具有暂态特征，尚未定位到具体代理、DNS 或 TLS 环节，不能据此判定账号封禁，也不能承诺该连接已经稳定。

## 测试方法与证据

使用账号各自 TOML 的临时副本和原官方镜像，逐账号顺序执行。先等带鉴权的模型目录 HTTP 200，确认 HTTP 服务已启动；然后测试网页文本和 tools，最后测试原生入口。原生资格失败会在副本内停用 cookie，不能用失败后同一实例的网页请求判断账号是否正常。初始探测中发现这一顺序问题后，重新创建副本执行上述验收。

| 场景 | 请求与预期 | 实际结果 | 判定 |
| --- | --- | --- | --- |
| 普通回复 | 要求只返回 Hello；预算 2048 | 11 个账号最终均收到 Hello | 基础生成通过，未做长期稳定性结论 |
| 自定义文档工具 | 声明 read_document(id)，要求读取 test-001 | 返回普通 text，模型表示该工具不可用；未返回 tool_use | 失败 |
| 强制工具选择 | cc1 设置 tool_choice.type=any | HTTP 200，但仍返回普通 text | 失败；不能把 200 视为遵守工具选择 |
| 文本定点改写 | 将 CONTRACT-001 数量 7 改为 9，保留 EUR 和总额 140 | 返回完整文本，目标值改变，其他字段保留 | 纯文本改写通过，没有修改实际文件 |
| 原生 Messages | 相同官方镜像、账号副本、代理，发送短文本 | 复核 11 次均 500 / no_cookie_available，日志含 Free | 未取得原生调用资格 |
| 模型身份和结束原因 | 请求 claude-sonnet-4-6，检查响应字段 | 网页响应 model 为空，stop_reason 读取为 null | 未验证指定模型身份，存在协议字段缺口 |
| 工具结果、多轮、并行、文件读回 | 必须先取得合法 tool_use ID | 前置条件失败 | 未执行，保留待办 |

在正式验收顺序及诊断复核中共发送 50 次消息 HTTP 请求，包括失败响应和重复诊断；不包含服务就绪检查与初始顺序探测。每次请求均使用无本机身份信息的合成测试内容，不包含用户真实文档。

原生失败定位依据：[当前部署固定版本 get_organization](https://github.com/Xerxes-2/clewdr/blob/061c6d8ac9187148f50c8d806b56962a7f222b6c/src/claude_code_state/organization.rs) 会检查组织 capabilities，缺少 pro、enterprise、raven、max 时返回 Free；[try_chat](https://github.com/Xerxes-2/clewdr/blob/061c6d8ac9187148f50c8d806b56962a7f222b6c/src/claude_code_state/chat.rs) 处理无效资格后会归还带错误原因的 cookie。结合本轮阶段日志，错误发生在获得原生调用资格的流程，不能解释成 sessionKey 被封。未取得 OAuth token 和实际原生生成结果。

工具验收使用 [Anthropic 官方结构](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)：真实 tool_use 包含 ID、工具名和 input，调用方以对应 tool_result 回传。文本声称执行过操作、输出 JSON 示例或 HTTP 200 都不满足该验收。

## 环境与清理

测试在部署服务器执行，未运行本机 Claude Code、读取本机 Claude 登录态或配置。模型请求从临时官方容器经该账号代理发出，Docker 出站规则仅允许代理目标、DNS 和已建立连接的返回流量；未配置本机 185.* 出口作为代理。这里不承诺上游完全不记录请求或账号关联信息。

临时账号目录权限受限，容器日志不持久化；诊断阶段仅提取 Free 等阶段标记。上游会消耗账号额度，临时副本不隔离上游计量。

两轮报告均记录生产 registry 未变化。最终检查：11 个生产账号仍 ready，容器 running，均未 OOM；管理服务和 Nginx active；临时测试容器、网络和账号副本已清理。没有触发主项目的账号隔离逻辑。

脱敏记录：逐账号验收（历史记录已归档）、原生诊断与最终检查（历史记录已归档）。可复用测试入口：[说明](../tests/live/README.md)。

## 下一步条件

先研究并验证具备真实工具协议的上游，或明确接受网页近似适配的限制。网关和协议转换组件不能自动补齐网页通道丢失的工具能力。本轮不绕过原项目套餐检查，不增加基于文字推断工具执行的代码。成熟方案调研见 [实现调研](IMPLEMENTATION-RESEARCH.md)。
