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

独立调用密钥由管理员分配，权限和可用账号以密钥配置为准。

## 2. 支持的模型

请求的 `model` 字段使用下表中的完整标识。

| 模型标识 | 类型 |
|---|---|
| `claude-haiku-4-5-20251001` | 标准模式 |
| `claude-sonnet-5` | 标准模式 |
| `claude-sonnet-4-6` | 标准模式 |

上述范围对应当前网页无需升级的 Haiku 4.5、Sonnet 5 和 Sonnet 4.6。当前免费账号通过原版 ClewdR 调用时，由 Claude.ai 默认模型处理请求；不提供强制选择具体型号或 Thinking 模式的保证。要求精确型号时发送 X-WebCC-Model-Policy: exact；当前返回 409，不发送模型请求。默认策略为 auto。

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

请求按共享等待顺序分配空闲账号。全局并发上限为 4，单账号并发上限为 1；等待队列上限为 16，最长等待 15 秒。实际并发取决于可用资源数量。

服务在返回内容前最多自动尝试 3 次；已开始输出的流不自动重放。处理时间预算为 150 秒，客户端建议超时为 180 秒。转发响应提供请求标识；Messages 同时返回 `Request-Id` 和 `X-Request-Id`；尝试耗尽的错误可包含 `request_id`、`attempts` 和 `retryable`。客户端应限制重试次数。

当前提供 Chat Completions 与 Claude Messages 接口，不提供 `/v1/responses`、Embeddings、图片生成或音频接口。

## 9. 工具调用

工具调用支持 `POST /v1/messages` 和 `POST /v1/chat/completions`，无需额外适配请求头。两个入口复用同一套参数校验和历史处理；工具由调用方执行。

### 9.1 请求配置

| 项目 | 配置 |
|---|---|
| 地址 | `https://165.154.205.213/v1/messages` |
| 鉴权 | `Authorization: Bearer <API Key>` 或 `x-api-key` |
| 请求头 | `Content-Type: application/json`；无需专用工具请求头 |
| `model` | 第 2 节中的模型名，例如 `claude-sonnet-4-6` |
| `max_tokens` | 正整数，示例使用 1024；实际输出受上游限制 |
| `tools` | 1—64 个工具，包含 `name`、可选 `description` 和 `input_schema` |
| `strict` | 工具定义中的可选布尔值，参数采用本地 Schema 校验 |
| `messages` | 交替的 user / assistant 消息；支持文本、tool_use、文本 tool_result |
| `system` | 可选文本或文本内容块数组 |
| `stream` | `false` 返回 JSON；`true` 返回 SSE |

可使用平台全局密钥。独立密钥需具备 `messages` 权限。

标准客户端工具请求自动采用 WebCC 工具适配。返回参数经过本地 JSON Schema 校验；`strict=true` 表示本地参数校验，完整调用验证后发布；设置 `eager_input_streaming:true` 可选择参数增量。不提供官方约束采样。响应头 `X-WebCC-Adapter: standard-tools-v1` 标明该策略。原有 `webcc-prompt-v1` 与专用请求头仍可使用，独立密钥需额外具备 `experimental_tools` 权限。

Thinking、effort、Beta 等请求控制保留。上游签名原样返回；无签名时返回以 `webccsig_v1_` 开头的平台状态令牌，供本网关校验后续历史，不能用于 Anthropic 官方接口。`cache_control` 可随请求传递；平台缓存复用工具定义和文档解析，不代表模型端缓存。响应 `model` 记录上游返回值，无法取得时为 `unknown`。

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
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-sonnet-4-6",
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
        "model": "claude-sonnet-4-6", "max_tokens": 1024,
        "stream": False, "tools": tools,
        "tool_choice": choice, "messages": history,
    }
    request = urllib.request.Request(
        URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + KEY,
                 "Content-Type": "application/json"},
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

历史最多 128 条消息，文本提示数据最多 512 KiB，嵌套最多 32 层。附件独立传递，完整请求最多 32 MiB、最多 16 个附件；附件工具请求同时最多 2 个，繁忙时返回 429。工具结果须紧接工具调用，ID 不得缺失、重复或串用；业务修改的幂等控制由调用方负责。

标准工具流在上游生成时输出真实 Thinking、文本和 `input_json_delta`。工具参数通过 Schema 校验后才发送 `content_block_stop`；完整消息以 `message_stop` 结束。调用方应等待完整工具块和消息完成后执行工具。流中失败发送 `error`，已输出调用不自动重放。旧 `prompt-v1` 和引用答复保留校验后输出模式。

### 9.6 参数样例与结构化 JSON

工具定义可带 input_examples，提供 1—8 个符合 input_schema 的对象。

结构化 JSON 请求使用 output_config.format。返回内容经本地 Schema 校验后放入 text 内容块。

```bash
curl --fail-with-body -sS --max-time 180 \
  'https://165.154.205.213/v1/messages' \
  -H "x-api-key: ${CLEWDR_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'X-WebCC-Tools: prompt-v1' \
  -d '{
    "model": "webcc-prompt-v1",
    "max_tokens": 1024,
    "output_config": {"format": {"type": "json_schema", "schema": {
      "type": "object",
      "properties": {"total": {"type": "integer"}},
      "required": ["total"], "additionalProperties": false
    }}},
    "messages": [{"role": "user", "content": "计算37加83，返回total。"}]
  }'
```

响应 text 的 JSON 内容为 `{"total":120}`。

### 9.7 工具目录检索

`POST /v1/tools/search` 接收 tools、query 和可选 limit，limit 为 1—16，默认 5。使用平台密钥或具有 experimental_tools 权限的独立密钥。

响应包含 tools、tool_references 和 count。定义设置 defer_loading=true 时，调用方先完成工具检索，再在对应 tool_result.content 中回传引用：

```json
[{"type":"tool_reference","tool_name":"save_result"}]
```

引用必须对应本次请求 tools 中声明的工具。检索结果只来自调用方提交的目录。

### 9.8 Chat Completions 工具往返

声明使用 `tools[].function.parameters`，选择使用 `auto`、`none`、`required` 或指定 function。`parallel_tool_calls:false` 限制并行。返回 `tool_calls` 后，以相同 `tool_call_id` 回传 `role:tool` 消息；后续请求可以不再声明已完成的工具。结构化答案使用 `response_format` 的 `json_schema`。

OpenAI Python SDK 示例：

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

网页搜索由上游执行，返回 `server_tool_use`、`web_search_tool_result` 或 `web_fetch_tool_result`，客户端无需执行这些工具。来源正文通过指定代理读取，引用与提取的原文逐字核对。`webccsource_v1_` 是平台来源凭据，可在本网关回传；并非 Anthropic 来源凭据。严格搜索次数和域名控制暂未启用。

## 11. 文件资源

独立密钥需要 files 权限。文件通过 multipart/form-data 的 file 字段上传，返回的 ID 可跨轮引用。文件仅允许所属密钥访问。

| 操作 | 接口 |
| --- | --- |
| 上传 | POST /v1/files |
| 列表 | GET /v1/files |
| 元数据 | GET /v1/files/{id} |
| 下载 | GET /v1/files/{id}/content |
| 删除 | DELETE /v1/files/{id} |

上传支持 UTF-8 文本、CSV、PDF、PNG、JPEG、GIF、WebP。UTF-8 CSV 可用 `text/csv` 上传，服务规范化为 `text/plain` 并保留文件名。默认单文件最多 20 MiB；上传可带 expires_in_seconds，范围 3600—7776000。资源删除或到期后，引用返回 404。

在 Messages 的 user 内容或工具结果中引用文件：

```json
{"type":"document","source":{"type":"file","file_id":"file_webcc_..."}}
```

图片将 type 改为 image。普通 Messages 可引用文件；附件与客户端工具混用时使用第 9 节配置。


## 12. 异步批量任务

独立密钥需要 batches 和 messages 权限。每批 1—100 项，custom_id 唯一；请求体最多 1 MiB，文件引用展开后最多 8 MiB，任务期限 24 小时。后台逐项处理，不提供官方批处理折扣。

| 操作 | 接口 |
| --- | --- |
| 创建 / 列表 | POST / GET /v1/messages/batches |
| 查询 / 删除 | GET / DELETE /v1/messages/batches/{id} |
| 取消未执行项 | POST /v1/messages/batches/{id}/cancel |
| 下载 JSONL 结果 | GET /v1/messages/batches/{id}/results |

```bash
curl --fail-with-body -sS --max-time 180 \
  'https://165.154.205.213/v1/messages/batches' \
  -H "x-api-key: $CLEWDR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"requests":[{"custom_id":"sum","params":{"model":"claude-sonnet-4-6","max_tokens":64,"messages":[{"role":"user","content":"Add 37 and 83. Reply only 120."}]}}]}'
```

创建返回批次 ID。查询 processing_status 为 ended 后下载 results_url；每行包含 custom_id 与 succeeded、errored、canceled 或 expired 结果。结果未完成返回 409。已发出的模型请求不因取消而重放。

## 13. 代码执行与生成文件

独立密钥需要 runs 权限；输入或生成文件需要 files 权限。办公模板预装 pandas、numpy、openpyxl、pypdf，执行前检查依赖，不开放运行期联网安装。代码仅在 E2B 执行，默认禁止联网，任务期限为 30—600 秒，默认 300 秒。

```bash
curl --fail-with-body -sS --max-time 180 \
  'https://165.154.205.213/v1/runs' \
  -H "x-api-key: $CLEWDR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"code":"total=37+83\nprint(total)\nopen(\"output/report.txt\",\"w\").write(str(total))","outputs":["report.txt"]}'
```

创建返回 202 与运行 ID。GET /v1/runs/{id} 查询 state；ended 时 result.stdout 为程序输出，files 是生成文件列表，使用第 11 节接口下载。上述程序输出和文件内容均为 120。

标准 Messages 可声明 `code_execution_20260521` 工具，并在 user 内容加入 `{"type":"container_upload","file_id":"file_webcc_..."}`。文件放在沙箱 `input/` 目录；生成文件保存到 `output/`，返回资源 ID 后按第 11 节下载。此流程需要 messages、runs 和 files 权限。

POST /v1/runs/{id}/cancel 取消并清理执行环境。DELETE /v1/runs/{id} 仅删除已终止且清理完成的任务记录；生成文件独立删除。无法确定执行结果时返回 unknown，不自动重新运行代码。

## 14. 程序化工具调用

/v1/runs 的 code_execution_type 默认 code_execution_20260120，可设为 code_execution_20260521；tools.allowed_callers 必须允许所选版本。

在 /v1/runs 声明 tools 后，Python 程序可 await 工具名({...})；返回值为客户端提交的字符串。支持 asyncio.gather 并行调用，每轮最多 8 个执行中的调用、单次程序最多 32 次工具调用。

```json
{
  "code": "import json\nx=json.loads(await get_number({}))\nprint(x['value'])",
  "tools": [{
    "name": "get_number",
    "input_schema": {"type":"object","additionalProperties":false},
    "allowed_callers": ["code_execution_20260120"]
  }]
}
```

运行进入 waiting 时，pending_tools 提供 id、name、input 和 caller。客户端真实执行工具后提交：

```text
POST /v1/runs/{id}/tool_results
```

```json
{"tool_results":[{"tool_use_id":"实际 pending_tools 的 id","content":"{\"value\":120}","is_error":false}]}
```

提交返回 202，继续查询运行状态。错误结果设置 is_error=true。重复、未知、跨密钥或已取消的结果会被拒绝；批量提交具备事务原子性。

也可在普通 /v1/messages 声明 code_execution 工具，使用第 2 节模型标识，由网页模型规划 Python 程序，无需专用请求头。标准入口需 runs、messages、files 权限；旧 X-WebCC-Runtime: e2b-v1 入口继续保留，支持 JSON 和 SSE；设置 stream=true 后返回工具参数增量、执行结果和 message_stop，等待期间发送 ping。等待工具时返回 tool_use，客户端通过 container.id 回传 tool_result。超过同步等待期限时，JSON 返回 202；SSE 返回 pause_turn 和 container.id，后续通过 /v1/runs 查询。程序执行期间通过 text_delta 输出真实 stdout，随后返回 code_execution_tool_result。模型规划和参数校验完成后才返回工具参数，不提供原生 Thinking。此模式是平台执行适配，不是官方沙箱协议的完整替代。

## 15. Skills

独立密钥需要 skills 权限。支持原有 JSON files 映射，以及官方 Python SDK 的 multipart files[] 上传。SDK 上传的所有文件须位于同一个顶层目录，包含 SKILL.md 和 UTF-8 文本资源。

| 操作 | 接口 |
| --- | --- |
| 上传 / 列表 | POST / GET /v1/skills |
| 查询 / 删除 | GET / DELETE /v1/skills/{id} |
| 新版本 | POST /v1/skills/{id}/versions |
| 查询 / 删除指定版本 | GET / DELETE /v1/skills/{id}/versions/{version} |
| 下载版本 ZIP | GET /v1/skills/{id}/versions/{version}/content |

```json
{"files":{"SKILL.md":"---\nname: total-report\ndescription: Calculate a total\n---\nRun scripts/total.py.","scripts/total.py":"print(37+83)"}}
```

JSON 创建返回 id 和 version；SDK 创建返回 latest_version_id。版本查询支持 latest，列表使用 limit、page 与 next_page 分页。接受 SDK 的 beta=true 查询参数。在 /v1/runs 中使用 skills:[{"skill_id":"实际ID","version":"实际版本"}] 绑定版本。脚本位于 skills/{name}/，输入位于 input/，输出写入 output/。已提交任务保存不可变版本快照，后续更新或删除不改变该任务。

每包最多 64 个资源、合计 256 KiB；每项最多 64 KiB。运行依赖需预装在沙箱模板中。这里只管理用户上传的 Skills，不预置第三方 Skill 内容。


## 16. 远程 MCP

独立密钥需要 mcp 权限；标准 Messages 还需要 messages 权限，声明 mcp_servers 和 mcp_toolset 即自动接入。旧专用入口仍需要 experimental_tools 权限。服务域名由管理员配置 MANAGER_MCP_HOSTS；HTTPS 连接通过指定代理发送，不转发客户端 IP、User-Agent 或其他请求头。authorization_token 仅用于指定服务，不写入日志。同步请求结束后关闭连接；持久任务的认证参数加密保存，任务结束、取消或结果未知时删除。更换平台管理密钥后，已有持久任务的认证参数无法解密。

| 操作 | 接口 |
| --- | --- |
| 工具目录 | POST /v1/mcp/tools，正文包含 server |
| 直接调用 | POST /v1/mcp/call，正文包含 server、name、arguments |
| 模型选择并执行 | POST /v1/messages，X-WebCC-Tools: mcp-v1，model=webcc-mcp-v1 |

```bash
curl --fail-with-body -sS --max-time 180 \
  'https://165.154.205.213/v1/messages' \
  -H "x-api-key: $CLEWDR_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'X-WebCC-Tools: mcp-v1' \
  -d '{
    "model":"webcc-mcp-v1",
    "max_tokens":2048,
    "mcp_servers":[{"type":"url","name":"deepwiki","url":"https://mcp.deepwiki.com/mcp"}],
    "tools":[{"type":"mcp_toolset","mcp_server_name":"deepwiki","default_config":{"enabled":false},"configs":{"read_wiki_structure":{"enabled":true}}}],
    "messages":[{"role":"user","content":"调用 deepwiki 的 read_wiki_structure 读取 modelcontextprotocol/python-sdk，然后列出前三个文档标题。"}]
  }'
```

响应 content 包含 mcp_tool_use、mcp_tool_result 和最终 text。工具真实执行后才提供结果；执行状态无法确认时不自动重放。工具参数经过 JSON Schema 校验。

支持 Bearer authorization_token、default_config.enabled、configs.{工具名}.enabled 和 defer_loading。延迟加载的工具通过 webcc_tool_search 发现，返回 tool_search_tool_result 与 tool_reference。连接和目录在同一请求内复用，认证参数不跨调用者共享。仅支持 Streamable HTTP 和文本结果。

同步模式最多两个服务、32 个启用工具、8 次调用和四轮模型请求；总期限最多 140 秒。连接初始化最多 20 秒，后续单次远程操作最多 30 秒。目录最多 128 KiB，单次工具文本最多 64 KiB。

设置 stream=true 返回 SSE，模型规划通过校验后发送工具参数，远程执行返回后立即发送结果块。参数不会在校验前交给客户端执行。该接口通过网页模型与平台执行器实现，不提供原生约束采样、Thinking 签名或官方 Connector 的全部扩展。


### 16.1 持久暂停与恢复

首次请求加入 max_iterations（1—4，默认 3）启用持久模式。达到本轮预算后返回 stop_reason=pause_turn 与 container.id。整个会话最多 16 轮、32 次工具调用，有效期 600 秒。

| 操作 | 接口 |
| --- | --- |
| 查询状态与当前结果 | GET /v1/mcp/sessions/{id} |
| 恢复 waiting 会话 | POST /v1/mcp/sessions/{id}/resume |
| 取消 | POST /v1/mcp/sessions/{id}/cancel |
| 删除已终止会话 | DELETE /v1/mcp/sessions/{id} |

也可通过 Messages 恢复：

```json
{"model":"webcc-mcp-v1","container":"实际 container.id","stream":true}
```

恢复请求使用 X-WebCC-Tools: mcp-v1，继续已有历史，仅返回本轮新增内容。会话仍在 processing 时先查询状态，不能重复恢复。远程执行中断且结果无法确定时，状态为 unknown，不自动重放，也不允许恢复；由调用方核对外部资源。跨密钥访问返回 404。

## 17. 文档定位引用

POST /v1/messages 使用 X-WebCC-Tools: citations-v1 与 model=webcc-citations-v1。接受 UTF-8 文本文档、内联 base64 PDF，以及所属密钥的 file_id；文档需启用 citations.enabled。独立密钥需要 messages 和 experimental_tools 权限；引用文件还需 files 权限。

```json
{
  "model":"webcc-citations-v1",
  "max_tokens":2048,
  "messages":[{"role":"user","content":[
    {"type":"document","title":"预算","source":{"type":"text","data":"北区预算为37。\n南区预算为83。"},"citations":{"enabled":true}},
    {"type":"text","text":"北区预算是多少？引用原文。"}
  ]}]
}
```

文本引用返回 char_location，字符索引从 0 开始，结束索引不包含在引用内。PDF 返回 page_location，页码从 1 开始，结束页码不包含在引用内。每段引用须与指定文档、指定页面原文逐字匹配；无法验证的结果返回 502。stream=true 返回文本及 citations_delta，校验完成后才输出。

每次最多 8 份文档；PDF 单文件最多 20 MiB、20 页，提取文本最多 64 KiB。不支持加密 PDF 或扫描件 OCR。标准入口可与客户端工具组合，工具结果回传后再返回经过校验的答复与引用。这是平台定位与校验的引用，不包含官方签名或加密来源信息。

## 18. Thinking 历史与处理缓存

将 assistant 返回的完整 content 原样传入下一轮，包括 Thinking、signature 和工具块。平台状态令牌绑定调用密钥、Thinking 内容和实际模型，24 小时到期；改写内容、使用其他调用密钥或轮换管理密钥后无法恢复。网页未返回 Thinking 时不补写思考内容。

工具定义和 PDF 文本解析缓存默认保存 5 分钟；来源正文缓存 1 分钟。缓存按调用密钥隔离、加密保存，有容量上限；解析失败不缓存，缓存不可用时直接处理。上传文件、删除文件、权限和到期检查每次执行，不被缓存绕过。缓存不保存动态模型答复，也不声明官方模型端缓存命中。
