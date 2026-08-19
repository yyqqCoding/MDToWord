# 创建Pull Request与人工合并

## 1. 进入发布的条件

发布节点不是模型工具。只有LangGraph已经取得有效`ValidationResult`，并且运行使用真实
发布模式，才有条件进入`publish_pull_request`。

Publisher要求：

```text
validation.passed == true
validated.patch存在
validated.patch的SHA-256匹配
base_sha存在
当前反馈仍由本次claim_token拥有
```

任何条件不满足都不会创建分支或PR。

## 2. 发布前检查main是否变化

任务开始时固定了`base_sha`。创建PR前重新读取GitHub `main`：

```text
current_main_sha == base_sha → 继续发布
current_main_sha != base_sha → stale_base
```

第一次遇到`stale_base`时，当前run结束，反馈重新进入`pending`，以后基于新`main`重新复现
和修复。第二次仍然过期则进入`needs_human`，避免任务在活跃仓库里无限重排。

系统不会让模型自行rebase，也不会把旧补丁直接套到新代码上。

## 3. GitHub App怎样创建PR

发布模块使用只安装到当前仓库的GitHub App：

1. 使用App私钥生成短期JWT；
2. 为当前仓库换取短期installation token；
3. 检查令牌只包含需要的`contents:write`和`pull_requests:write`；
4. 从`base_sha`创建确定性分支；
5. 应用并校验`validated.patch`；
6. 创建提交；
7. 推送分支；
8. 创建Pull Request；
9. 把PR地址保存到数据库和State。

模型看不到GitHub私钥、JWT或installation token，也不能自己组织GitHub API请求。

## 4. 怎样避免重复PR

发布过程使用稳定的反馈ID、run ID、分支名和补丁哈希。恢复发布前会检查已有分支、提交和
PR：

- 同一次操作重试时复用已有结果；
- 同一反馈和`validated_patch_sha256`不能创建多个打开的PR；
- GitHub返回冲突时先判断目标是否已经由本次运行创建；
- 只有确实不一致才记录发布冲突。

如果网络在“GitHub已经创建PR、Agent还没保存结果”的时间点断开，恢复逻辑会先查找已有
PR，而不是直接再创建一个。

## 5. 发布失败后怎样恢复

模型和Docker验证已经完成后，GitHub暂时失败不应该从头再跑。系统保留：

```text
base_sha
test.patch
fix.patch
validated.patch
validated_patch_sha256
validation_result_ref
```

同一个`run_id`恢复时，只重新打开发布节点，不重新调用模型或Sandbox。

## 6. PR包含什么

PR用于帮助维护者审核，通常包含：

- 用户问题的脱敏摘要；
- 修复说明和行为变化；
- 风险等级和人工检查提示；
- 复现测试与最终验证摘要；
- 运行Trace地址；
- Agent生成的业务代码和回归测试差异。

PR不得包含联系方式、密钥、完整用户原文或未脱敏测试日志。

## 7. Agent不会做什么

Agent不会：

- 自动合并PR；
- 修改GitHub Actions、Secrets或仓库管理设置；
- 自动触发额外部署权限；
- 在`main`变化时强制推送；
- 绕过维护者的Word视觉检查。

正确的末端流程是：

```text
Agent创建PR
    ↓
维护者检查代码、测试、Trace和Word实际效果
    ↓
维护者人工合并
    ↓
Render部署main的新版本
```

对应实现：

- [agent/publishing](../../agent/publishing)
- [agent/graph.py](../../agent/graph.py)
- [GitHub发布测试](../../agent/tests/test_github_publisher.py)
