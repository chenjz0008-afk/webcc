# 开发、发布与回滚

## 分支

`main` 保存经确认的版本。`server-baseline-2026-10-06` 是服务器部署快照标签；源码和静态产物来自服务器，README 与维护文档补充了说明。

`feat/claude-api-compatibility` 用于逐步兼容完整 Claude API。每个提交只处理一个明确问题，提交说明包含测试结果。推送分支不会触发生产部署。

## 检查

后端使用 `python3 -m unittest discover -s tests -v`，前端使用 `npm ci`、`npm run build` 和 `node test_accounts.mjs`。真实上游测试使用服务器隔离环境，不使用开发者本机的 Claude 登录态或项目资料。

保留一份功能矩阵，分别记录模拟测试、真实测试、模型和上游版本。不能把模拟回复当作真实请求成功。

## 发布

先推送提交并通过测试，再备份服务器项目、环境文件和私有数据。备份含凭据，只保存在服务器受限目录。通过候选环境确认后再发布，保存本次 Git 提交和 ClewdR 镜像摘要。

不要将 Git 工作目录直接覆盖到运行目录，也不要覆盖 `/etc/clewdr-manager.env` 或 `/var/lib/clewdr-manager`。配置与数据独立保存。

当前安装脚本会复制应用文件并启用服务；已运行服务的发布需要显式执行 `sudo systemctl restart clewdr-manager` 并检查服务和接口。原版镜像更新由 updater 独立处理。

## 撤销某个开发提交

在对应分支执行：

```bash
git revert <commit-sha>
git push
```

这会生成反向提交，保留历史。涉及多个相关提交时，应先在临时分支测试撤销后的组合。

## 回到部署基线

从标签建立恢复分支：

```bash
git switch -c rollback/server-baseline server-baseline-2026-10-06
python3 -m unittest discover -s tests -v
```

使用该版本发布应用，并使用 `deploy/server-snapshot.json` 记录的官方镜像摘要。需要镜像存在且兼容当前私有数据。数据库结构或状态格式改变时必须同时评估迁移，不可直接假设旧代码能读取新状态。

Git 回滚只恢复应用文件。账号资料、凭据、证书和运行状态需从私有备份恢复；已完成的外部操作无法通过 Git 撤销。
