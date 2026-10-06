# 已挂账号真实验收

`account_qualification.py` 在部署服务器上执行，需要 Python 3.11+、root、Docker、iptables 和现有账号 registry。它会消耗真实账号额度，不能作为普通单元测试自动运行。

```sh
sudo python3 tests/live/account_qualification.py
```

脚本按账号顺序创建一个临时官方容器，复制 TOML、使用该账号的代理，对模型目录、网页真实文本、自定义工具和原生 Messages 进行请求。网页测试先于原生测试，避免原生资格失败停用副本中的 cookie 后干扰网页结论。HTTP 200 和模型目录可访问都不是工具验收通过的标准。

测试进程通过服务器本地端口访问容器，出站规则仅允许代理目标、DNS 和已建立连接的返回流量。脚本拒绝启用 IPv6 的测试网络。DNS 使用服务器配置，规则不等于对所有网络信息和上游记录的绝对匿名保证。账号配置副本和容器日志不能上传仓库。

临时资源在 finally 中清理。报告保存于服务器 `/var/lib/clewdr-manager/account-depth-test.json`，权限 600；检查 registry 是否变化。生产容器与主项目不做部署修改。上游可能记录调用、扣减额度，临时容器不隔离这些账号侧影响。

测试失败保留为失败。只有取得真实 tool_use 后才能继续执行工具结果、多轮修改、异常恢复和应用集成验收。本轮另外用诊断实例复核原生 Free 状态和文档定点改写，结果见 `docs/ACCOUNT-QUALIFICATION.md`。
