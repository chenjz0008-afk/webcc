# 实验工具接口

此接口用于候选环境中的网页客户端工具适配，不是生产 API 能力声明。官方 ClewdR 容器保持原样。

## 架构

客户端通过主网关鉴权和选号。仅明确选择实验策略的请求进入 web_tools：验证历史和工具 → 构造网页提示 → 调用账号容器 → 校验完整结果 → 返回客户端工具块。网关不执行业务工具；客户端执行后带 tool_result 发起下一轮。

## 开启条件

服务端需安装 requirements-web-tools.txt 中的可选依赖，并设置 MANAGER_WEB_TOOLS_ENABLED=true。默认 false，不启用时正常启动不加载该依赖；开启时会在启动阶段检查依赖可导入。

请求同时指定：

| 项目 | 值 |
| --- | --- |
| 接口 | POST /v1/messages |
| 鉴权 | x-api-key 或 Authorization: Bearer |
| 请求头 | X-WebCC-Tools: prompt-v1 |
| model | webcc-prompt-v1 |
| max_tokens | 16—8192 |
| tools | 1—64 个客户端工具，name、description、input_schema，可选 strict 布尔值 |
| messages | 交替的 user/assistant，支持文本、tool_use、文本 tool_result |
| system | 可选文本 |
| stream | true 或 false |
| tool_choice | auto、none、any、tool，可带 disable_parallel_tool_use |

显式模型别名表示本平台的提示适配策略。网页上游未证明精确模型身份，不使用官方模型名冒充验证结果。Beta、查询参数、thinking、图像、文档块和额外参数在该模式下明确拒绝。普通模式不进入此适配器。

## 参数校验

strict 仅接受布尔值。该实验策略对所有工具返回参数进行本地 JSON Schema 校验；不合规结果返回错误，不交给客户端执行。strict=true 不启用官方约束采样，也不保证模型第一次生成一定成功。JSON 和 SSE 响应以 X-WebCC-Adapter: prompt-v1; schema-validated; no-native-strict; buffered 标明实际策略。

## Python SDK

以下示例连接候选网关；BASE_URL 和 key 使用候选环境实际值，不应理解为当前公网生产已开启。

```python
import anthropic

client = anthropic.Anthropic(
    api_key="YOUR_API_KEY",
    base_url="YOUR_BASE_URL",
    default_headers={"X-WebCC-Tools": "prompt-v1"},
    max_retries=0,
)
tools = [{
    "name": "read_document",
    "description": "读取文档",
    "input_schema": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    },
}]
history = [{"role": "user", "content": "读取文档 A 后告诉我数量。"}]
message = client.messages.create(
    model="webcc-prompt-v1", max_tokens=1024,
    tools=tools, messages=history,
)
# 客户端按 message.content 中的 tool_use 执行已授权的工具，
# 然后在下一条 user 消息中逐一回传对应 tool_use_id 的结果。
```

使用 client.messages.stream(...) 时可以通过 stream.get_final_message() 获取完整工具块。流式模式先等待并校验完整上游结果，再按 SSE 格式输出；等待期间每约 5 秒发送 ping。不能将这种缓冲输出称为上游实时生成。

## TypeScript SDK

官方 SDK 0.131.0 已在服务器候选环境通过普通和 SSE 工具往返。初始化时显式设置 apiKey、baseURL、defaultHeaders: {"X-WebCC-Tools": "prompt-v1"} 及 maxRetries: 0；请求 model 为 webcc-prompt-v1。普通请求使用 client.messages.create，SSE 使用 client.messages.stream(params).finalMessage()。传入工具和历史沿用上表；客户端执行工具后回传对应 tool_use_id。测试代码见 experiments/sdk_request.mjs，结果和 HTTPS 范围见 [SDK 验收](TYPESCRIPT-SDK.md)。

## 错误与重试

非法历史、字段或容量在选号前拒绝，不影响账号状态。模型输出不符合封装或 Schema 时返回 api_error，不把账号标为异常。取消会停止本次等待或关闭上游连接，由执行线程释放槽位。

HTTP/连接错误沿用主项目的阈值与冷却，500 达到阈值后才隔离。每轮不重复尝试已选账号；工具块尚未输出时可换账号。网关只生成操作提议，没有执行客户端工具的副作用；最终工具块输出后不重放。调用方仍需对真实修改实现权限、版本检查和幂等控制。

流式请求开始后发生错误会发送 error 事件，而不是伪造 message_stop 或正常完成。上游报错正文不会直接暴露给调用方。

## 已知范围

历史最多 128 条消息，提示数据最多 128 KiB，最多 32 层嵌套；历史工具 ID 必须唯一，结果紧接调用，多个结果可乱序。用量来自上游报告，包括包装提示和原始 JSON 输出，不是转换后内容的精确 token 计数。

本轮验证结果在独立测试报告中记录。实际 WordBuddy、TypeScript SDK、生产 HTTPS、任意提示注入防护以及完整官方 API 仍需后续验收。
