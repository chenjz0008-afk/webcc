# 平台文件资源

日期：2026-10-07。实现位置：web_files.py，官方 ClewdR 不修改。平台保存文件，在推理前把 file_id 展开成网页账号支持的文档或图片输入。文件不绑定某个 Claude 账号，选号切换不会丢失平台资源。

生产状态：2026-10-07 已发布文件后端，公网文本与 PDF 上传及引用、列表、详情、下载和删除实测通过。权限选择的前端新增项尚未重新构建。见 [公网测试记录](tools-public-live-2026-10-07.json)。

## 接口

| 操作 | 方法与地址 |
| --- | --- |
| 上传 | POST /v1/files，multipart/form-data 的 file 字段；可选 expires_in_seconds |
| 列表 | GET /v1/files；limit、page；兼容 after_id/before_id；支持 ids 或 ids[] |
| 元数据 | GET /v1/files/{file_id} |
| 下载 | GET /v1/files/{file_id}/content |
| 删除 | DELETE /v1/files/{file_id} |
| 推理引用 | POST /v1/messages 的 document/image，source 为 {"type":"file","file_id":"..."} |

接受 SDK 的 beta=true 查询参数。响应 X-WebCC-Adapter: managed-files-v1 表示平台管理的资源。分页同时提供 next_page 和旧 SDK 的 has_more/first_id/last_id。

平台允许下载自己上传的文件，downloadable=true；这与 Anthropic 只允许下载生成文件的规则不同。资源 ID 为平台 ID，不能传到官方 Anthropic 地址。

## 存储与隔离

- SQLite 保存不可变内容和元数据，事务内检查配额；文件和数据库目录均不公开。
- 平台主密钥归属 platform；独立调用密钥按密钥 ID 隔离。不同密钥不能查看、引用、下载或删除彼此资源，包括指定同一账号的两个密钥。
- 新增 files 权限用于文件管理；消息引用仍需 messages 权限。撤销或到期密钥不能继续访问。
- 文件 ID 不参与磁盘路径拼接；下载不执行文件内容，不抓取调用者 URL。
- 上传最大 20 MiB；每密钥最多 128 文件、256 MiB，总有效内容 1 GiB；文件操作最多同时 2 个。
- 已接收的类型为 PDF、PNG、JPEG、GIF、WebP 与 UTF-8 text/plain。签名及大小在选号前验证，不以文件头检查替代完整格式解析。
- expires_in_seconds 为 3600—7776000。到期或删除后不能新建引用；已展开并开始执行的请求可能仍包含文件内容。
- 过期内容在下次上传事务中清理，过期资源不再对外可读。数据库启用 secure_delete；这不承诺清除已有私有备份。
- 展开后请求仍受 32 MiB 限制。prompt-v1 已发布附件适配，支持 file_id 文档/图片和工具结果附件；合成 PDF 与 PNG 工具往返候选通过，最多 16 个附件，同类请求同时最多 2 个。

数据位置为 MANAGER_DATA/files.sqlite3。回滚代码保留数据库；恢复文件内容需恢复对应私有数据备份。全局密钥更换保留 platform 资源；调用密钥撤销后其资源仍占存储，需在撤销前删除或设置自动到期。

## 依赖与调研

复用 [python-multipart 0.0.32](https://pypi.org/project/python-multipart/) 解析 multipart，不自行拆分 MIME 边界；使用 Python 标准 SQLite 事务，不引入数据库服务。固定版本 wheel 的 SHA256 为 ff6d3f776f16878c894e52e107296ffc890e913c611b1a4ec6c44e2821fe2e23。

接口与字段参考 [官方 Files](https://platform.claude.com/docs/en/build-with-claude/files)、[上传](https://platform.claude.com/docs/en/api/files/upload)、[列表](https://platform.claude.com/docs/en/api/files/list) 和 [官方 Python SDK](https://github.com/anthropics/anthropic-sdk-python)。实现前检查了现有 SDK 的 beta=true 路径。

当前只完成平台资源生命周期与普通 Messages 引用。Claude 云端生成文件下载、跨请求沙箱状态和官方 Files 行为仍须分别验收。
