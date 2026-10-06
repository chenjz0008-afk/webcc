# 网页客户端工具实验

这里保存独立验收程序。共享适配逻辑位于 web_tools；主网关仅在显式实验开关和请求头下导入它，默认关闭，尚未部署生产。ClewdR 官方镜像和源码保持原样。

参考 LiteLLM 的函数定义入提示机制、0G Compute Adapter 的工具提示与输出转换思路，使用 jsonschema 验证参数。没有复制这些项目的业务代码或引入整套网关。来源、版本和取舍见 `docs/WEB-TOOL-EXPERIMENT.md`。

## 运行

在部署服务器的仓库根目录运行，需要 Python 3.11+ 和 jsonschema；真实测试还需要 root、现有 registry、Docker、iptables 和可用账号代理。

```sh
python3 -m unittest experiments.test_web_tools experiments.test_tool_history -v
sudo python3 -m experiments.live_tools
sudo python3 -m experiments.live_tools --boundaries
sudo python3 -m experiments.live_tools --history
sudo python3 -m experiments.live_tools --gateway
```

真实测试消耗账号额度。普通模式对 11 个 ready 账号检查初始工具请求，再对 cc1、ccb1、ccb9 执行四轮读写验证；边界模式使用 cc1 检查工具选择、双调用、错误结果和引用示例。工具仅操作内存中的合成文档，未接入实际 WordBuddy 或用户文件。

测试采用临时官方容器与账号副本，限制账号代理出口，完成后清理容器、网络和副本。生产 registry 不做写入。服务器报告分别保存于 `/var/lib/clewdr-manager/web-tool-experiment.json` 和 `web-tool-boundary-test.json`，权限 600。

## 边界

`build_prompt` 将声明和历史序列化为文字。`parse_response` 只接受完整 JSON，验证封装、工具名称、参数及选择规则，生成本平台工具 ID。客户端执行业务工具，模型只提出操作请求。解析器不从任意回答或代码示例里寻找可执行片段。

这不提供官方 strict、thinking 签名、精确模型身份、原生 usage 或完整 SSE。校验失败会拒绝响应，不承诺模型每次都生成合法格式。内部只有轻量实验约束，尚需验证复杂 Schema、上下文上限、历史 ID 合法性、恶意文档、取消与并发，才适合接入生产。外部 Schema 引用被禁止；支持范围限于本轮测试。

## 历史检查

发送前校验调用与结果 ID、紧邻结果、重复调用、支持的文本内容及嵌套参数。多个结果可乱序，并支持空结果及 is_error；不符合约束时直接拒绝。历史必须为交替的 user/assistant 消息并以 user 结束；工具名保持声明有效，历史 ID 不重复。上限为 128 KiB 提示数据、32 层嵌套、128 条消息和 64 个工具，不代表真实模型上下文窗口。disable_parallel_tool_use 为 true 时拒绝模型返回多个调用。

`--history` 对 cc1、ccb1、ccb9 执行双文档读取、乱序结果、模拟版本冲突、调整后修改和读回；文档包含误导修改指令。测试器只允许对合成文档 B 做预定修改，拒绝错误修改提议，也拒绝重复修改。报告为服务器私有文件 `/var/lib/clewdr-manager/web-tool-history-test.json`。这不证明任意注入内容安全，也不证明工具实际并行执行。

## SDK 网关验收

`--gateway` 在服务器启动临时主网关，使用显式 key、Base URL 和 X-WebCC-Tools 请求头，以官方 Python SDK 执行同一六轮文档流程。cc1 使用 SSE 聚合，ccb1 和 ccb9 使用普通响应；每个账号再验证错误 ID 在上游调用前被拒绝。SDK 安装在测试临时目录，完成后删除；不使用本机 Claude Code 环境。报告为服务器私有文件 `/var/lib/clewdr-manager/web-tool-gateway-test.json`。

TypeScript SDK 测试入口为 typescript_cases.py 和 sdk_request.mjs；支持候选 TLS 和真实合成 Markdown 文件。必须在服务器隔离环境配置官方 SDK、显式候选 key/Base URL，以及受白名单保护的临时账号容器，不连接本机 Claude Code。依赖版本、HTTPS/文件范围和结果见 ../docs/TYPESCRIPT-SDK.md。
