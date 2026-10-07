# TypeScript SDK 与文件工具验收

日期：2026-10-06。官方 @anthropic-ai/sdk 0.131.0，Node.js v24.21.0。测试在服务器独立候选网关与临时官方 ClewdR 容器执行，容器复用对应账号的代理白名单网络。本轮没有发布实验 API。

## 已验证

| 场景 | 验证结果 |
| --- | --- |
| 普通 Messages | cc1 六轮完成：双读取、乱序结果、版本冲突、重新读取、修改及读回 |
| SDK SSE helper | cc1 六轮完成，通过 messages.stream().finalMessage() 获取完整工具块 |
| HTTPS 候选 | cc1 六轮 SSE 完成；临时 CA 显式信任，正常证书校验保持开启 |
| 不受信任的证书 | SDK 拒绝连接，网关选号与模型调用计数为零 |
| 文件工具 | 真实写入合成 A.md/B.md，读取、修改、读回；A 字节保持，B 仅数量和版本行改变 |
| 工具 ID 错误 | 三个成功流程的额外请求返回 400，未增加上游调用计数 |
| 请求结束 | 等待及并发槽位归零；临时容器、依赖、证书和文件已清理 |

服务器完整回归 114 项通过（58.537 秒）。

上述成功流程共 18 次真实模型请求，每次流程只有一次成功修改，均完成一次模拟版本冲突恢复。工具由测试客户端执行，网关只生成经 Schema 校验的操作提议。Markdown 是测试创建的合成文件，不读取用户本机或服务器已有文档；上游仅接收逻辑 ID 和文档内容，不接收文件路径。

初次 ccb1 测试在第一轮收到上游 500。后续独立 curl 复核确认 SOCKS5 服务拒绝用户认证，属于代理凭据问题；已在管理台写入代理检测失败结果，不称为 SDK 协议失败或 Claude 账号封禁。该失败请求与后续 cc1 的成功验收分别记录。

## 范围

HTTPS 测试在服务器 loopback 的候选 TLS 服务完成，不代表已将实验工具能力发布到生产 Nginx。现有生产普通 API 另行复测。SSE 仍为上游完整校验后的缓冲输出，不是原生实时 token 生成。

本轮 Markdown 文件工具并非 WordBuddy、Word 或 DOCX 集成验收。实际应用、复杂格式、多用户资源隔离、广泛注入与真实故障仍需继续；没有原生上游能力时不声明 thinking 签名、strict、Files、Prompt Caching 或服务端工具与官方等价。

原始记录：[普通/SSE](typescript-sdk-2026-10-06.json)、[HTTPS/文件](typescript-https-2026-10-06.json)。候选调用方式见 [实验接口](EXPERIMENTAL-TOOLS-API.md)。

官方接口依据：[TypeScript SDK](https://github.com/anthropics/anthropic-sdk-typescript)、[SDK 文档](https://platform.claude.com/docs/en/api/sdks/typescript)。运行依赖下载自 Node.js 官方与 npm，Node 归档已校验官方 SHA256；npm 安装禁用生命周期脚本。
