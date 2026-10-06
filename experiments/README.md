# 网页客户端工具实验

这是开发分支中的独立实验，不由生产 manager 导入，也没有对外端点。ClewdR 官方镜像和源码保持原样。

参考 LiteLLM 的函数定义入提示机制、0G Compute Adapter 的工具提示与输出转换思路，使用 jsonschema 验证参数。没有复制这些项目的业务代码或引入整套网关。来源、版本和取舍见 `docs/WEB-TOOL-EXPERIMENT.md`。

## 运行

在部署服务器的仓库根目录运行，需要 Python 3.11+ 和 jsonschema；真实测试还需要 root、现有 registry、Docker、iptables 和可用账号代理。

```sh
python3 -m unittest experiments.test_web_tools -v
sudo python3 -m experiments.live_tools
sudo python3 -m experiments.live_tools --boundaries
```

真实测试消耗账号额度。普通模式对 11 个 ready 账号检查初始工具请求，再对 cc1、ccb1、ccb9 执行四轮读写验证；边界模式使用 cc1 检查工具选择、双调用、错误结果和引用示例。工具仅操作内存中的合成文档，未接入实际 WordBuddy 或用户文件。

测试采用临时官方容器与账号副本，限制账号代理出口，完成后清理容器、网络和副本。生产 registry 不做写入。服务器报告分别保存于 `/var/lib/clewdr-manager/web-tool-experiment.json` 和 `web-tool-boundary-test.json`，权限 600。

## 边界

`build_prompt` 将声明和历史序列化为文字。`parse_response` 只接受完整 JSON，验证封装、工具名称、参数及选择规则，生成本平台工具 ID。客户端执行业务工具，模型只提出操作请求。解析器不从任意回答或代码示例里寻找可执行片段。

这不提供官方 strict、thinking 签名、精确模型身份、原生 usage 或完整 SSE。校验失败会拒绝响应，不承诺模型每次都生成合法格式。内部只有轻量实验约束，尚需验证复杂 Schema、上下文上限、历史 ID 合法性、恶意文档、取消与并发，才适合接入生产。外部 Schema 引用被禁止；支持范围限于本轮测试。
