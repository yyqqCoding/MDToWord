# 发布Pull Request或Issue

## 1. 进入发布的条件

发布节点不是模型工具。Gate路由和运行模式共同决定两条互斥发布路径：

```text
accepted_backend_bug → 复现/修复/验证 → publish_pull_request
issue_required       → publish_issue（不读取源码、不进入Sandbox）
```

两条路径都只有在显式`--publish`或生产Scheduler中才会写GitHub。PR路径必须先取得有效
`ValidationResult`；Issue路径必须先取得经过本地Policy复核的脱敏`IssueDraft`。

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

## 4. GitHub App怎样创建Issue

功能需求和前端/扩展Bug不会进入代码修复白名单。发布节点把Gate产生的严格结构转换为：

- 最长80字符的脱敏标题；
- 最长600字符的脱敏摘要；
- `bug`或`enhancement`固定标签；
- intent、area、category和运行元数据；
- 不可逆`run_ref`与内容指纹marker。

Issue正文不拼接原始description、Markdown、contact或完整feedback ID。发布前还会拒绝
邮箱、电话、Bearer/Secret模式和提示注入片段。仓库没有预先存在的固定标签时发布失败，
Agent不会擅自创建标签、分配人员、关闭Issue或修改项目面板。

Issue使用独立短期令牌，只申请`issues:write`；它不携带PR路径所需的
`contents:write/pull_requests:write`。成功后feedback=`issue_opened`、run=`completed`，
`issue_url`单独保存，`pr_url`保持空值。

## 5. 怎样避免重复PR或Issue

PR发布使用稳定的反馈ID、run ID、分支名和补丁哈希。恢复前会检查已有分支、提交和PR：

- 同一次操作重试时复用已有结果；
- 同一反馈和`validated_patch_sha256`不能创建多个打开的PR；
- GitHub返回冲突时先判断目标是否已经由本次运行创建；
- 只有确实不一致才记录发布冲突。

如果网络在“GitHub已经创建PR、Agent还没保存结果”的时间点断开，恢复逻辑会先查找已有
PR，而不是直接再创建一个。

Issue不能公开完整feedback ID，因此使用`run_ref + content_fingerprint`隐藏marker。创建前
同时搜索开放和关闭Issue；同一反馈恢复、网络响应丢失或人工关闭后再次恢复，都复用同一个
Issue，不重复创建。

## 6. 发布失败后怎样恢复

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

Issue发布失败也保留同一run的`IssueDraft`和marker；恢复从`publish_issue`继续，不重跑Gate。
历史`out_of_scope`记录不会批量补建Issue，只有维护者逐条复核并单独批准后才能处理。

## 7. PR与Issue分别包含什么

PR用于帮助维护者审核，通常包含：

- 用户问题的脱敏摘要；
- 修复说明和行为变化；
- 风险等级和人工检查提示；
- 复现测试与最终验证摘要；
- 运行Trace地址；
- Agent生成的业务代码和回归测试差异。

PR不得包含联系方式、密钥、完整用户原文或未脱敏测试日志。

Issue只包含脱敏需求摘要、稳定分类、运行引用和Trace地址，不包含源码、补丁或代码验证阶段
证据。这样读者不会把“已创建Issue”误解成“代码已经修复”。

## 8. Agent不会做什么

Agent不会：

- 自动合并PR；
- 修改GitHub Actions、Secrets或仓库管理设置；
- 自动触发额外部署权限；
- 在`main`变化时强制推送；
- 绕过维护者的Word视觉检查；
- 自动实现或关闭Issue；
- 根据Issue路线修改前端/扩展代码。

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

Issue路线的末端是：

```text
Agent创建脱敏Issue
    ↓
维护者确认需求、优先级和实现方式
    ↓
维护者人工修改、评审和发布
```

对应实现：

- [agent/publishing](../../agent/publishing)
- [agent/graph.py](../../agent/graph.py)
- [GitHub发布测试](../../agent/tests/test_github_publisher.py)
- [GitHub Issue发布测试](../../agent/tests/test_github_issue_publisher.py)

## 9. 结合源码看幂等发布

[agent/publishing/github.py](../../agent/publishing/github.py)的`publish()`先用固定分支和隐藏
marker查找已有PR，再检查`main`是否仍等于验证时的`base_sha`：

```python
branch = _branch_name(request)
marker = _publication_marker(request)

existing = await self._find_existing_pull(branch, marker, headers)
if existing is not None:
    return existing

current_sha = await self._read_ref_sha(self._main_branch, headers)
if current_sha != request.validation.base_sha:
    return PublicationResult(
        disposition=PublicationDisposition.STALE_BASE,
        branch=branch,
    )
```

marker同时绑定反馈ID和已验证补丁哈希：

```python
return (
    "<!-- mdtoword-agent "
    f"feedback={request.feedback_id} "
    f"patch={request.validation.validated_patch_sha256} -->"
)
```

所以网络超时后恢复时不会只按PR标题猜测，而是用确定性分支和补丁哈希确认是不是同一次发布。

Issue对应的`publish_issue()`先按隐藏marker查询全部状态的已有Issue，再决定是否POST；正文
由`build_issue_content()`从`IssueDraft`和受控运行证据组装，不接收原始反馈字段。两种发布
共享GitHub App签名逻辑，但令牌权限、请求契约和持久化结果彼此独立。
