# WebCC API 接入说明

文档版本：2026-10-07

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

独立调用密钥由管理员分配，权限和可用账号以密钥配置为准。

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

启用流式响应时设置 `stream:true`；响应以 `message_stop` 事件结束，不使用 OpenAI 的 `[DONE]` 标记。

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
| 401 | API 认证或上游认证失败 | 检查密钥；上游失败联系管理员 |
| 403 | 密钥权限不足或上游拒绝访问 | 检查调用权限，或联系管理员 |
| 429 | 密钥速率超限、等待队列已满、等待超时或上游限流 | 降低并发，采用有上限的退避重试 |
| 500 / 502 | 上游或通道异常，或自动尝试后仍无有效正文 | 空回复可提高输出预算；持续失败联系管理员 |
| 503 | 暂无可用资源 | 稍后重试或联系管理员 |
| 504 | 请求处理超时 | 缩短问题或输出长度，限制重试次数 |

全局并发上限为 4，单账号并发上限为 1；等待队列上限为 16，最长等待 15 秒。实际并发取决于可用资源数量。

服务在返回内容前最多自动尝试 3 次；已开始输出的流不自动重放。处理时间预算为 150 秒，客户端建议超时为 180 秒。转发响应提供请求标识；Messages 同时返回 `Request-Id` 和 `X-Request-Id`；尝试耗尽的错误可包含 `request_id`、`attempts` 和 `retryable`。客户端应限制重试次数。

当前提供 Chat Completions 与 Claude Messages 接口，不提供 `/v1/responses`、Embeddings、图片生成或音频接口。

## 9. 工具调用

工具调用使用 `POST /v1/messages`，暂不支持 Chat Completions 的 `tools` / `tool_calls`。

### 9.1 请求配置

| 项目 | 配置 |
|---|---|
| 地址 | `https://165.154.205.213/v1/messages` |
| 鉴权 | `Authorization: Bearer <API Key>` 或 `x-api-key` |
| 请求头 | `X-WebCC-Tools: prompt-v1` |
| `model` | `webcc-prompt-v1` |
| `max_tokens` | 16—8192，示例使用 1024 |
| `tools` | 1—64 个工具，包含 `name`、可选 `description` 和 `input_schema` |
| `strict` | 工具定义中的可选布尔值，参数采用本地 Schema 校验 |
| `messages` | 交替的 user / assistant 消息；支持文本、tool_use、文本 tool_result |
| `system` | 可选文本 |
| `stream` | `false` 返回 JSON；`true` 返回 SSE |

可使用平台全局密钥。独立密钥需具备 `messages` 和 `experimental_tools` 权限。

`webcc-prompt-v1` 是网页工具适配标识，不对应保证可选的具体 Claude 型号。所有返回参数均经过本地 JSON Schema 校验；`strict=true` 不提供官方约束采样。响应头以 `X-WebCC-Adapter` 标明该策略。

### 9.2 支持范围

| 能力 | 用法 |
|---|---|
| 自动选择 | `tool_choice: {"type":"auto"}`，允许调用工具或直接回答 |
| 禁止调用 | `tool_choice: {"type":"none"}` |
| 必须调用 | `tool_choice: {"type":"any"}`，至少返回一个工具调用 |
| 指定工具 | `tool_choice: {"type":"tool","name":"read_document"}` |
| 限制并行 | 在 `tool_choice` 中加入 `disable_parallel_tool_use: true`，每次最多调用一个工具 |
| 并行调用 | 每次最多返回 8 个 tool_use；下一条 user 消息须回传全部对应结果 |
| 多轮调用 | 保留 assistant 工具块，再以相同 tool_use_id 回传结果 |
| 工具失败 | 回传 tool_result 并设置 `is_error: true` |
| 附件与工具混用 | user 内容和 tool_result 可携带 PDF document 或 image；支持内联 base64 与本密钥上传的 file_id |

网关返回调用提议，实际工具由调用方执行。读取文件、修改文档或访问业务系统均需由调用方提供实现和权限控制。工具名、参数或选择约束不合规时返回错误。

### 9.3 curl 示例

运行前设置第 3 节的环境变量。

```bash
curl --fail-with-body --silent --show-error --max-time 180 \
  'https://165.154.205.213/v1/messages' \
  -H "Authorization: Bearer $CLEWDR_API_KEY" \
  -H 'X-WebCC-Tools: prompt-v1' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "webcc-prompt-v1",
    "max_tokens": 1024,
    "stream": false,
    "tools": [{
      "name": "read_document",
      "description": "读取指定文档",
      "strict": true,
      "input_schema": {
        "type": "object",
        "properties": {"id": {"type": "string", "enum": ["A"]}},
        "required": ["id"],
        "additionalProperties": false
      }
    }],
    "tool_choice": {
      "type": "tool",
      "name": "read_document",
      "disable_parallel_tool_use": true
    },
    "messages": [{"role": "user", "content": "读取文档 A，收到结果后只回复文档中的数字。"}]
  }'
```

响应的 `content` 包含 `tool_use`，其中 `id`、`name` 和 `input` 用于执行及回传。将原 assistant 内容块加入历史，再紧接一条 user 消息回传 `tool_result`，保留实际 `tool_use_id`。

### 9.4 完整往返示例

以下 Python 示例只使用标准库。客户端读取合成文档 A，并将数字 37 回传给模型；两次请求使用同一工具定义和完整历史。

```python
import json
import os
import urllib.request

URL = "https://165.154.205.213/v1/messages"
KEY = os.environ["CLEWDR_API_KEY"]
documents = {"A": {"number": 37}}
tools = [{
    "name": "read_document",
    "description": "读取指定文档",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"id": {"type": "string", "enum": ["A"]}},
        "required": ["id"], "additionalProperties": False,
    },
}]
history = [{"role": "user", "content": "读取文档 A，收到结果后只回复文档中的数字。"}]


def send(choice):
    payload = {
        "model": "webcc-prompt-v1", "max_tokens": 1024,
        "stream": False, "tools": tools,
        "tool_choice": choice, "messages": history,
    }
    request = urllib.request.Request(
        URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + KEY,
                 "Content-Type": "application/json",
                 "X-WebCC-Tools": "prompt-v1"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


message = send({"type": "tool", "name": "read_document",
                "disable_parallel_tool_use": True})
calls = [b for b in message["content"] if b["type"] == "tool_use"]
if len(calls) != 1 or calls[0]["name"] != "read_document" or calls[0]["input"] != {"id": "A"}:
    raise RuntimeError("工具调用不符合本例要求")
call = calls[0]
result = documents[call["input"]["id"]]
history.append({"role": "assistant", "content": message["content"]})
history.append({"role": "user", "content": [{
    "type": "tool_result", "tool_use_id": call["id"],
    "content": json.dumps(result),
}]})
answer = send({"type": "none"})
text = "".join(b["text"] for b in answer["content"] if b["type"] == "text")
if text.strip() != "37":
    raise RuntimeError("返回结果与文档内容不一致：" + text)
print(text)
```

预期输出：`37`。

### 9.5 限制

历史最多 128 条消息，文本提示数据最多 128 KiB，嵌套最多 32 层。附件独立传递，完整请求最多 32 MiB、最多 16 个附件；附件工具请求同时最多 2 个，繁忙时返回 429。工具结果须紧接工具调用，ID 不得缺失、重复或串用；业务修改的幂等控制由调用方负责。

工具流先等待并校验完整上游结果，再输出 SSE；等待期间发送 ping，以 message_stop 结束，失败发送 error 事件。

## 10. 联网搜索

普通 `POST /v1/messages` 已开启网页搜索和网页读取。需要实时信息时，在消息中明确要求搜索，并要求返回来源链接。

```bash
curl --fail-with-body -sS -N --max-time 180 \
  'https://165.154.205.213/v1/messages' \
  -H "x-api-key: ${CLEWDR_API_KEY}" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1536,
    "stream": true,
    "messages": [{
      "role": "user",
      "content": "请联网搜索 Brave Search API 当前价格和每月免费额度，简短回答并提供官方来源链接。"
    }]
  }'
```

网页搜索由上游执行，客户端读取最终文本，无需执行返回的 `web_search` 或 `web_fetch`。该功能使用普通 Messages 路径；第 9 节的 `prompt-v1` 用于客户端自定义工具。
