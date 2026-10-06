# ClewdR API 接入说明

文档版本：2026-10-01

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
| 401 | API 认证或上游认证失败 | 检查密钥；上游失败联系管理员 |
| 403 | 上游拒绝访问 | 联系管理员检查资源状态 |
| 429 | 等待队列已满、等待超时或上游限流 | 降低并发，采用有上限的退避重试 |
| 500 / 502 | 上游或通道异常，或自动尝试后仍无有效正文 | 空回复可提高输出预算；持续失败联系管理员 |
| 503 | 暂无可用资源 | 稍后重试或联系管理员 |
| 504 | 请求处理超时 | 缩短问题或输出长度，限制重试次数 |

全局并发上限为 4，单账号并发上限为 1；等待队列上限为 16，最长等待 15 秒。实际并发取决于可用资源数量。

服务在返回内容前最多自动尝试 3 次；已开始输出的流不自动重放。处理时间预算为 150 秒，客户端建议超时为 180 秒。转发响应提供 `X-Request-Id`；尝试耗尽的错误可包含 `request_id`、`attempts` 和 `retryable`。客户端应限制重试次数。

当前提供 Chat Completions 与 Claude Messages 接口，不提供 `/v1/responses`、Embeddings、图片生成或音频接口。
