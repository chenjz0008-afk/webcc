# API 兼容完善执行清单

本轮在 WebCC 完成，ClewdR 保持原版。模型请求仅在服务器执行。用量计费、Brave 严格搜索约束和 WorkBuddy 桌面验收沿用此前的暂缓范围。

每项实现后执行协议、故障和服务器真实请求验收；通过的执行项从此清单移除，结果记入验收文档。

本轮执行项已完成并发布。后续原生能力边界与暂缓范围见 [协议说明](NATIVE-COMPATIBILITY.md)。

## 复用依据

- 标准协议：[Anthropic Messages](https://platform.claude.com/docs/en/api/messages)、[流式协议](https://platform.claude.com/docs/en/build-with-claude/streaming)、[结构化输出](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。
- 增量解析：[ijson](https://github.com/ICRAR/ijson)、[jiter](https://github.com/pydantic/jiter)。解析器负责 JSON 语法，WebCC 仅处理工具选择和块生命周期。
- 状态加密：复用现有 cryptography Fernet 与共享 PostgreSQL；缓存复用 Redis，单机模式采用有界进程缓存。
- 执行：复用已有 Procrastinate、MCP SDK、E2B SDK、文件和 Skills 模块，不引入第二套任务队列。

## 已通过

| 能力 | 验收 |
|---|---|
| 标准工具往返 | 官方 SDK 无专用请求头，真实调用并回传 120；工具选择和完整历史通过 |
| strict 与结构化输出 | 复杂 Schema、有限纠正、截断和拒答；已输出调用不重放 |
| 资源与恢复 | Files、Batches、Skills 生命周期；MCP 持久暂停和隔离；Beta SDK 上传文件、固定 Skills 快照、真实执行和生成文件下载 |
| 搜索与引用 | 真实网页工具结果、指定代理提取原文、逐字引用校验和来源历史 |
| 真正增量 | HTTP 参数在上游未完成时到达；文本、Thinking 与参数按实际生成输出 |
| Thinking 与状态令牌 | 真实 Thinking 及来源历史续问；内容、调用者和到期校验；原生签名保持原值 |
| 处理缓存 | Redis 共享复用、调用者隔离、到期和副本隔离；失败不缓存 |

215 项完整回归通过，无跳过。真实验收与修复记录见 [本轮记录](adapter-acceptance-2026-10-09.json)。

fefde29 已部署；89 个运行文件与提交一致。公网 SDK 工具两轮返回 120，14.005 秒；联网流式返回真实 Thinking 和 4 条经原文校验的引用，12.543 秒。11 个 ClewdR 配置、镜像与启动时间未变，临时密钥和占用均已清理。
