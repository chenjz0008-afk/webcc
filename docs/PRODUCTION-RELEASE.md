# 2026-10-07 发布记录

实现提交：33aa3beb63dd1b0d3e6cc401ecf2bc6ed099bd5d。

## 发布

构建并发布当前 React/MUI 前端及主网关代码，开启 MANAGER_WEB_TOOLS_ENABLED=true。官方 ClewdR 容器与账号代理配置保持原样；账号 ID 保留。服务健康检查通过。

发布前在服务器 /var/backups/webcc 下保存应用、环境和私有数据备份，目录仅管理员可读。私有备份没有上传 Git。部署包装首次因换行转义错误退出，生产未改动；修复并验证语法后重新发布成功。

## 公网验证

测试请求从服务器发往 https://165.154.205.213，模型请求使用已有 ccb9 账号及其受保护代理网络，不使用本机 Claude 配置。临时调用密钥只允许该账号与消息/实验工具权限，验收后已撤销。

| 验证 | 结果 |
| --- | --- |
| 管理页面与健康接口 | 正常 |
| strict=true 工具提议 | HTTP 200，工具名、参数正确，响应标记本地校验 |
| tool_result 回传与最终回答 | HTTP 200，返回 37 |
| 标准 document PDF 输入 | HTTP 200，两页数字、合计与引用标记正确 |

PDF 引用标记是文档中的文本，不是原生 citations。工具与 PDF 混用、原生 Thinking 签名、Files、缓存和服务端工具不在本次支持范围。

完整结果见 [发布摘要](production-release-2026-10-07.json)。工具调用要求显式模型别名和请求头，见 [接口](EXPERIMENTAL-TOOLS-API.md)。

## 回滚

备份位置记录于服务器 /var/lib/clewdr-manager/release-33aa3be.json。回滚时恢复该备份的应用和环境文件，再重启 clewdr-manager 并检查健康接口。账号库仍在独立数据目录，不能用旧备份覆盖发布后的用户操作；数据恢复需单独核对。
