# 工具接口新增能力

状态：2026-10-08 已发布，公网文件、JSON 输出、检索与密钥撤销验收通过。对外接入以 API-PUBLIC.md 为准。

| 能力 | 参数或接口 | 行为 |
| --- | --- | --- |
| 工具参数样例 | tools[].input_examples | 1—8 个对象；请求发送前按工具 Schema 校验 |
| 工具延迟加载 | tools[].defer_loading | 默认不加入模型工具定义；成功的工具结果返回 tool_reference 后加载 |
| 工具目录检索 | POST /v1/tools/search | 在调用方提交的目录内检索，返回定义与引用，不读取其他调用者的目录 |
| 结构化 JSON | output_config.format | type 为 json_schema，schema 声明 object；返回前执行本地校验 |
| 截断、拒答、格式错误 | error.code | output_truncated、output_refused、output_validation_failed；截断与拒答不自动重放 |
| 资源权限 | files | 独立密钥可配置上传、查询、下载及删除权限 |

实验工具请求使用 model=webcc-prompt-v1 和 X-WebCC-Tools: prompt-v1。JSON 校验在平台执行，不代表原生约束生成。业务工具仍由调用方执行，失败通过 tool_result.is_error=true 返回。

文件大小、每个密钥文件数量、归属配额、总配额、默认有效期、附件数量和附件工具并发可通过 manager.env.example 列出的变量调整。修改后重启服务，非法配置会拒绝启动。

服务器候选真实测试已通过 PDF、PNG、JPEG、单帧 GIF、WebP 工具往返，以及结构化 JSON 和工具发现。未由此声明动画全部帧被模型读取。
