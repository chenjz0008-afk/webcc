# 网页搜索与沙箱

核查日期：2026-10-07。模型请求在服务器执行，账号容器继续使用代理专用网络。官方 ClewdR 源码不修改。

## 方案选择

优先使用网页账号已有能力。外部搜索和沙箱只在内置能力不能满足接口要求时接入，不为同一能力重复付费。

| 能力 | 已取得的证据 | 当前边界 |
| --- | --- | --- |
| 网页读取 | 隔离容器开启 web_search 后，返回 web_fetch、成功 tool_result 及 Brave 官方价格 | 与官方 API 的 server_tool_use/web_fetch_tool_result 协议不同 |
| 网页搜索 | 11 个账号已开启并重载；公网返回两次 web_search、成功 tool_result 和正确的官方价格 | 已验证关键词搜索；官方 API 的参数、结果块和引用协议仍需单独适配 |
| 网页代码执行 | 线上 SSE 返回 bash_tool，退出码 0，真实 stdout 为 2979 和三行平方数据 | 没有验证官方 container ID、跨请求状态和程序化工具调用 |
| 生成文件 | present_files 返回 CSV local_resource，路径为 Claude 云端 /mnt/user-data/outputs/webcc-sandbox-proof.csv | 返回资源块不等于调用方已下载文件；需要下载与归属验收 |

代码执行发生在 Claude 云端沙箱。该测试没有向模型提供本机或服务器文件、环境变量、账号密钥，也没有在 WebCC 主机执行模型生成的代码。

普通 Messages 的网页工具事件与 prompt-v1 的客户端工具是两条不同路径。prompt-v1 当前明确要求模型使用调用者声明的工具，不由网页沙箱替代客户端业务操作。

## 费用

| 方案 | 官方当前价格 | 本项目采用条件 |
| --- | --- | --- |
| 网页内置搜索、读取及代码执行 | 按网页账号方案与额度使用，无额外 Brave/E2B 费用 | 优先验证和复用；搜索、长文读取会消耗网页额度 |
| Brave Search | Search 每千次 5 美元，每月 5 美元抵扣 | 需要独立、受控搜索结果接口时作为可选提供方 |
| E2B Hobby | 无固定月费，一次性 100 美元额度；默认 2 CPU/4 GiB 每秒 0.000046 美元，约每小时 0.1656 美元 | 需要独立沙箱生命周期、下载或客户端程序化工具桥接时评估 |
| E2B Pro | 每月 150 美元，另收运行费 | 当前规模无需购买 |

E2B 按沙箱处于运行状态的时间收费；一次 60 秒任务约 0.00276 美元，1000 次约 2.76 美元，不含启动、等待和未及时关闭的额外时间。免费额度及价格以服务商控制台为准。

## 依据

- [Claude 网页搜索和读取](https://support.claude.com/en/articles/10684626-enable-and-use-web-search)
- [Claude 网页代码执行和文件生成](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude)
- [ClewdR 固定请求转换](https://github.com/Xerxes-2/clewdr/blob/061c6d8ac9187148f50c8d806b56962a7f222b6c/src/claude_web_state/transform.rs)
- [Brave Search 价格](https://brave.com/search/api/)
- [E2B 价格](https://e2b.dev/pricing)
- [Claude 导出器公开沙箱文件接口说明](https://github.com/glebmish/claude-exporter/blob/main/docs/sandbox-files.md)：仅用于后续下载方案调研，未验证本项目下载兼容。

Claude Code 源码泄露的公开报道不证明网页账号取得了官方 API 权限。本项目不下载或依赖未经授权的专有源码。实现依据是官方文档、账号实际工具事件及合法开源项目。

## 生产记录

搜索：[公网结果](web-search-production-2026-10-07.json)。沙箱：[执行结果](web-sandbox-production-2026-10-07.json)。临时调用密钥均已撤销。配置私有备份位于服务器 /var/backups/webcc/web-search-20261007T143939Z。

管理服务允许 AF_NETLINK，供已有出口规则初始化使用。账号仍使用独立代理网络，不开放失败直连。
