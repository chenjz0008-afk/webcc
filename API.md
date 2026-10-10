# WebCC API 接入说明

文档版本：2026-10-10

## 1. 连接配置

| 项目 | 配置 |
|---|---|
| 服务地址 | `https://165.154.205.213` |
| OpenAI Base URL | `https://165.154.205.213/v1` |
| API Key | `YOUR_API_KEY` |
| 接口协议 | OpenAI Chat Completions、Claude Messages |
| 默认模型 | `claude-sonnet-4-6` |
| 建议超时 | 180 秒 |
| 请求编码 | UTF-8 |
| 请求格式 | `application/json` |

客户端要求 Base URL 时填写 `/v1` 地址；要求完整聊天地址时填写 `https://165.154.205.213/v1/chat/completions`。客户端的 API Key 配置项填写密钥本身。

HTTP 请求使用以下认证头：

```text
Authorization: Bearer <API Key>
```

本文所列密钥用于 API 调用，应仅提供给授权调用方。

## 2. 支持的模型

请求的 `model` 字段使用下表中的完整标识。

| 模型标识 | 类型 |
|---|---|
| `claude-haiku-4-5-20251001` | 标准模式 |
| `claude-sonnet-5` | 标准模式 |
| `claude-sonnet-4-6` | 标准模式 |

上述范围对应当前网页无需升级的 Haiku 4.5、Sonnet 5 和 Sonnet 4.6。当前免费账号通过原版 ClewdR 调用时，由 Claude.ai 默认模型处理请求；不提供强制选择具体型号或 Thinking 模式的保证。

## 3. 环境变量

macOS / Linux（Bash、zsh）：

```bash
export CLEWDR_API_KEY=YOUR_API_KEY
```

Windows PowerShell：

```powershell
$env:CLEWDR_API_KEY = 'YOUR_API_KEY'
```

下述 curl 示例使用 Bash 语法。PowerShell 中使用 `curl.exe`，将命令合并为一行，或使用反引号续行。

## 4. 普通聊天

接口：`POST /v1/chat/completions`

```bash
curl --fail-with-body --silent --show-error --max-time 180 \
  'https://165.154.205.213/v1/chat/completions' \
  -H "Authorization: Bearer $CLEWDR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hi! Please greet me in one short sentence."}],
    "max_tokens": 2048,
    "stream": false
  }'
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `model` | 是 | 第 2 节中的模型标识 |
| `messages` | 是 | 对话消息数组，包含 `role` 与 `content` |
| `max_tokens` | 建议填写 | 输出预算上限，可包含思考开销；普通问答建议 2048，较长分析建议 4096 起 |
| `stream` | 否 | `false` 返回 JSON；`true` 返回 SSE |

正文位于 `choices[0].message.content`，返回的 `model` 字段可能为空。非流式 `finish_reason` 和 `usage` 可能未反映思考开销及预算耗尽状态，调用方应检查正文是否完整。

## 5. 流式聊天

接口：`POST /v1/chat/completions`

```bash
curl --fail-with-body --silent --show-error --no-buffer --max-time 180 \
  'https://165.154.205.213/v1/chat/completions' \
  -H "Authorization: Bearer $CLEWDR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hi! Please greet me in one short sentence."}],
    "max_tokens": 2048,
    "stream": true
  }'
```

响应类型为 `text/event-stream`。将各事件中 `choices[].delta.content` 的文本按顺序拼接。

事件示例：

```text
data: {"choices":[{"delta":{"content":"OK."}}]}

data: [DONE]
```

`data: [DONE]` 表示完整结束。连接中断且未收到该标记时，应按不完整响应处理。增量事件未必包含 `id`、`object`、`index`、`usage` 等字段，客户端解析器需允许这些字段缺省。

## 6. Claude Messages

接口：`POST /v1/messages`

```bash
curl --fail-with-body --silent --show-error --max-time 180 \
  'https://165.154.205.213/v1/messages' \
  -H "x-api-key: $CLEWDR_API_KEY" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hi! Please greet me in one short sentence."}],
    "max_tokens": 2048,
    "stream": false
  }'
```

读取 `content` 数组中 `type` 为 `text` 的内容块。该接口同时接受 `Authorization: Bearer <API Key>` 认证。

启用原生流式时设置 `stream:true`；响应以 `message_stop` 事件结束，不使用 OpenAI 的 `[DONE]` 标记。

## 7. Python 示例

以下示例使用 Python 标准库，运行前设置第 3 节中的环境变量。

```python
import json
import os
import urllib.request

payload = {
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hi! Please greet me in one short sentence."}],
    "max_tokens": 2048,
    "stream": False,
}
request = urllib.request.Request(
    "https://165.154.205.213/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": "Bearer " + os.environ["CLEWDR_API_KEY"],
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=180) as response:
    result = json.load(response)
content = result["choices"][0]["message"]["content"]
if not content:
    raise RuntimeError("响应未包含有效文本")
print(content)
```

## 8. 错误处理与并发

| HTTP 状态 | 含义 | 处理方式 |
|---|---|---|
| 400 | 请求格式或参数错误 | 检查 JSON、模型与字段 |
| 413 | 文件、展开请求或工具上下文超过限制 | 使用文件引用或分段读取 |
| 401 | API 认证或上游认证失败 | 检查密钥；上游失败联系管理员 |
| 403 | 上游拒绝访问 | 联系管理员检查资源状态 |
| 429 | 等待队列已满、等待超时或上游限流 | 降低并发，采用有上限的退避重试 |
| 500 / 502 | 上游或通道异常，或自动尝试后仍无有效正文 | 空回复可提高输出预算；持续失败联系管理员 |
| 503 | 暂无可用资源 | 稍后重试或联系管理员 |
| 504 | 请求处理超时 | 缩短问题或输出长度，限制重试次数 |

请求按共享等待顺序分配空闲账号。全局并发上限为 4，单账号并发上限为 1；等待队列上限为 16，最长等待 15 秒。实际并发取决于可用资源数量。

服务在返回内容前最多自动尝试 3 次；已开始输出的流不自动重放。处理时间预算为 150 秒，客户端建议超时为 180 秒。转发响应提供 `X-Request-Id`；尝试耗尽的错误可包含 `request_id`、`attempts` 和 `retryable`。客户端应限制重试次数。

当前提供 Chat Completions 与 Claude Messages 接口，不提供 `/v1/responses`、Embeddings、图片生成或音频接口。

## 9. 客户端工具调用

Chat Completions 和 Messages 均支持声明客户端工具，无需额外适配请求头。工具在调用方执行，执行结果须按返回的调用 ID 回传。

| 功能 | Chat Completions | Messages |
|---|---|---|
| 声明参数 | `tools[].function.parameters` | `tools[].input_schema` |
| 工具选择 | `auto`、`none`、`required`、指定 function | `auto`、`none`、`any`、指定 tool |
| 并行控制 | `parallel_tool_calls` | `disable_parallel_tool_use` |
| 返回调用 | `message.tool_calls` | `content[].tool_use` |
| 回传结果 | `role:tool`、`tool_call_id` | `tool_result`、`tool_use_id` |
| 流式参数 | `delta.tool_calls[].function.arguments` | `input_json_delta.partial_json` |

`strict:true` 使用本地 JSON Schema 校验，完整调用验证后发布参数。结构化答案支持 Chat Completions 的 `response_format` 和 Messages 的 `output_config.format`。校验失败时不会返回完整有效工具调用；不保证官方约束采样效果。

工具往返示例（OpenAI Python SDK）：

```python
import json
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["CLEWDR_API_KEY"],
    base_url="https://165.154.205.213/v1",
    timeout=180,
    max_retries=0,
)
tools = [{"type": "function", "function": {
    "name": "read_product",
    "description": "Read the product by exact SKU.",
    "strict": True,
    "parameters": {
        "type": "object", "properties": {"sku": {"enum": ["B500"]}},
        "required": ["sku"], "additionalProperties": False,
    },
}}]
messages = [{"role": "user", "content": "读取 B500，告诉我单价和 MOQ。"}]
first = client.chat.completions.create(
    model="claude-sonnet-4-6", messages=messages, tools=tools,
    tool_choice="required", max_tokens=512,
)
message = first.choices[0].message
messages.append(message.model_dump(exclude_none=True))
for call in message.tool_calls or []:
    arguments = json.loads(call.function.arguments)
    if call.function.name != "read_product" or arguments != {"sku": "B500"}:
        raise ValueError("Unexpected tool request")
    # Replace this example lookup with your application's real product service.
    product = {"sku": "B500", "price": 3.20, "moq": 1000}
    messages.append({"role": "tool", "tool_call_id": call.id,
                     "content": json.dumps(product)})
answer = client.chat.completions.create(
    model="claude-sonnet-4-6", messages=messages, tools=tools, max_tokens=512,
)
print(answer.choices[0].message.content)
```

流式请求设置 `stream:true`，按工具 `index` 累积参数。完整响应结束并确认参数有效后再执行工具。断流或流内错误后，先核对已执行动作，避免重复写入。

## 10. 文件与执行环境

Files 提供上传、查询、下载和删除，并按调用密钥隔离。UTF-8 CSV 可使用 `text/csv` 上传，服务会规范化为 `text/plain` 并保留文件名。沙箱计算输入使用 `container_upload`；内容阅读使用 `document.source.file_id`。

办公沙箱预装 pandas、numpy、openpyxl 和 pypdf，执行前检查依赖。沙箱内不开放联网安装；未知依赖会返回明确错误。服务端执行所用密钥需有 `messages`、`files` 和 `runs` 权限。

工具文本上下文默认上限为 512 KiB，展开请求总上限为 32 MiB。大文档建议使用文件引用和范围读取。来源抓取使用指定代理，对读取成功的原文进行引用定位；读取失败的来源不标记为已验证。

完整资源、执行、Thinking 与缓存边界见 [标准能力说明](docs/NATIVE-COMPATIBILITY.md)，详细实验工具接口见 [工具接口](docs/EXPERIMENTAL-TOOLS-API.md)。
