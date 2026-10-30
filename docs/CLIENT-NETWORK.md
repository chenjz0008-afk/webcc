# 客户端联网

## 路径选择

| 场景 | 路径 |
| --- | --- |
| C Code 的 WebSearch | 标准 Messages 搜索工具 → WebCC → Brave；搜索子请求可直接返回真实来源 |
| MCP 客户端主动搜索或读取 | /network/mcp → search_web / fetch_url → 相同网络服务 |
| 程序需要 JSON 搜索结果 | POST /v1/network/search 或 /v1/network/fetch |
| Bash/curl 下载已知地址 | 客户端执行环境 → 私有 Tinyproxy → 指定上游代理 → 目标站点 |

未知来源先搜索，已知地址直接读取；不需要为已知网址重复搜索。Brave 是检索服务，curl 是页面下载工具。客户端自行决定工具选择，服务不会修改用户问题强迫每次联网。

## C Code 的 MCP 配置

以下命令在准备接入的执行环境中运行，避免将密钥保存到共享配置或提交仓库：

```bash
claude mcp add --transport http webcc-network \
  https://165.154.205.213/network/mcp \
  --header "Authorization: Bearer $CLEWDR_API_KEY"
```

使用其他 MCP 客户端时，配置相同 URL 和认证头。此服务只提供搜索与网页文本读取；模型接口和 MCP 工具配置相互独立。

## 服务器受控 curl

部署脚本：`sudo /var/lib/webcc-cluster/venv/bin/python /opt/clewdr-manager/install-tools-proxy.py`。如脚本未安装至运行目录，从仓库 deploy 目录执行。

私有代理容器名 webcc-tool-proxy，端口 8888；不发布公网或主机端口。执行容器加入内部网络 webcc-tools-clients：

```text
HTTPS_PROXY=http://webcc-tool-proxy:8888
HTTP_PROXY=http://webcc-tool-proxy:8888
NO_PROXY=<本环境的 API 中继地址>
```

此网络无直接外网出口，代理所在网络只允许指定上游 IP/端口。不挂载 Docker socket、用户主目录、SSH 配置或真实项目。API 认证由隔离中继保管，模型执行容器只持有临时中继凭据。

curl 继承代理即可使用；--noproxy、清空代理或直接发起 HTTP/TCP 连接不能获得外网通路。代理不可用时明确失败，不回退直连。HTTPS CONNECT 仅允许 443，关闭 Via 和 X-Tinyproxy，不添加客户端 IP 请求头。

默认 curl 域名白名单为 Python 文档、GitHub/API/raw/codeload、欧盟 Access2Markets、httpbin、Brave API、C 检查服务（claude.ai、api.anthropic.com）和本平台地址。内置 WebFetch 还需要放行实际目标站点，例如天气示例使用 wttr.in；切换 bypass 不会跳过该网络检查。增补域名写 MANAGER_CURL_DOMAINS 后重新执行部署脚本；重新创建代理前先停止相关测试/任务。已有自定义白名单不会自动合并新默认值。不要把这个无公网认证的私有代理直接对外发布。

服务器 fetch_url 与 /v1/network/fetch 支持有界的 HTML、纯文本和 JSON 来源。JSON 原文不经过 HTML 正文提取，不重新排版；文本/JSON 上限为 256 KiB，无效 JSON 明确失败。它们与客户端内置 WebFetch 是独立路径，不能混用验收结论。

更换 WebCC Base URL 不能自动约束用户电脑上的 curl。外部客户端须自行配置代理或使用上述服务器隔离环境；本次部署没有修改 Mac 的 C Code 或网络设置。

## 运维

BRAVE_SEARCH_API_KEY_FILE 指向服务器私有密钥文件；MANAGER_BRAVE_RPS 为跨进程共享速率，当前设 5。搜索最多四个并发，Redis 负责共享节奏；60 秒结果缓存按调用密钥隔离。Brave 429、代理异常或协调服务故障返回明确错误，不隔离正常 C 账号。

webcc-network-mcp.service 监听 localhost:9002，Nginx 仅转发 /network/mcp。MCP 使用既有独立密钥权限和资源隔离，不新增用户或密钥数据库。
