# Issue分流与公开展示

## 1. 为什么需要单独的Issue路线

Agent的自动代码能力只覆盖受信任的后端白名单。功能需求需要产品决策，前端/扩展缺陷需要
人工视觉与交互判断；把它们标成`out_of_scope`会丢失真实需求，把它们送进后端修复流程又会
扩大模型权限。

当前方案把“是否值得记录”和“是否允许自动改代码”分开：

```text
有价值但不自动改代码 → 创建脱敏Issue，维护者处理
明确可自动修复的后端Bug → Sandbox验证后创建PR
无关或攻击内容 → 直接进入对应安全终态
```

Issue是人工工作的入口，不是修复完成证明。

## 2. 表单类型不等于最终意图

公开反馈仍只有`bug`和`feature`两个表单类型，但二者都进入同一个无工具Gate。模型返回：

```text
intent: bug_report | feature_request | unrelated | spam | unknown
area: backend | extension | cross_component | none | unknown
category
relevance
sufficient_information
injection_suspected
requires_extension_change
reason
issue_title / issue_summary
```

因此Bug表单里写的功能建议仍能进入Issue，Feature表单里的垃圾内容或提示词注入仍会被拒绝。
`feedback_type`只帮助理解用户入口，不能绕过模型分类和本地Policy。

## 3. 本地Policy怎样规范分类

模型输出先经过严格Schema，再由Python规则统一相邻概念：

- 注入：`area=none`、`category=prompt_injection`；
- 无关/垃圾：`area=none`、`category=irrelevant_content`；
- 主观视觉质量：规范为`feature_request + extension`；
- 前端/扩展Bug：规范为`bug_report + extension + extension_ui`；
- 任意范围的功能需求：类别统一为`feature_request`，范围保留backend、extension或跨端。

最终路由矩阵：

| 输入结论 | Route | 业务终态 | GitHub写入 | Sandbox |
|---|---|---|---|---|
| 提示词注入 | `quarantined_security` | 安全拦截 | 无 | 无 |
| 无关或垃圾 | `rejected_irrelevant` | 已忽略 | 无 | 无 |
| 信息/置信度/范围不足 | `needs_human` | 转人工 | 无 | 无 |
| 功能需求 | `issue_required` | `issue_opened` | Issue | 无 |
| 前端/扩展Bug | `issue_required` | `issue_opened` | Issue | 无 |
| 可自动修复后端Bug | `accepted_backend_bug` | `pr_opened`或其他验证终态 | PR（验证通过后） | 有 |

`out_of_scope`只为历史读取和展示兼容保留；新运行不再生成该路线，也不会批量把历史记录补建
成Issue。

## 4. Issue状态机

```text
pending → claimed → gating
                    ↓ issue_required
                 publishing_issue
                    ↓
                 issue_opened
```

run侧使用`publishing_issue → completed`，并把`issue_url`写入run；feedback侧保存
`issue_opened + issue_url`。PR和Issue结果分字段保存，避免调用方把Issue当成已发布代码。

Issue分支不会执行以下节点：

```text
prepare_source
reproduce
repair
validate
publish_pull_request
```

所以它不创建源码snapshot、Sandbox Job、测试补丁或修复补丁。

## 5. 公开Issue怎样脱敏

Gate只在Issue候选、信息充分且范围明确时生成标题和摘要。结构化Schema限制长度和字段组合，
本地Publisher再检查敏感模式与提示注入片段。最终正文只允许：

- 脱敏标题和摘要；
- intent、area、category；
- 不可逆run reference；
- Graph/Policy/Prompt版本、模型用量和Trace URL；
- 内容指纹marker。

正文不接收原始description、Markdown、contact、完整feedback ID、密钥或日志。固定标签只从
`bug`和`enhancement`中选择；仓库缺少标签时失败关闭，不由Agent创建新标签。

## 6. 怎样保证不会重复创建

公开marker使用：

```text
run_ref + content_fingerprint
```

它能确认同一次用户内容，又不暴露完整feedback ID。Publisher在创建前查询开放和关闭Issue；
如果GitHub已经创建、但响应或数据库保存失败，同run恢复时会找到并复用原Issue。恢复只重跑
发布节点，不重跑Gate，也不会因为Issue已被人工关闭就再开一条。

## 7. GitHub最小权限

同一个GitHub App只安装到目标仓库，但运行时申请两种彼此隔离的短期令牌：

```text
PR token:    contents:write + pull_requests:write
Issue token: issues:write
```

GitHub固有的`metadata:read`可以出现在响应中，其他额外权限会被拒绝。App注册页增加
`Issues: Read and write`后，维护者还必须在Installed GitHub Apps中批准权限变更。只读预检
会分别申请两种令牌，但不创建GitHub资源：

```bash
.venv/bin/python -m agent.publishing.check
```

## 8. 数据库与公开投影

追加migration为`feedback`和`agent_runs`增加`area/issue_url`，扩展Issue相关状态，并按白名单
重建`agent_run_public`。Migration必须由维护者手工审查执行，应用启动和测试不会自动改表。

公开视图允许展示route、area、category和issue_url，但不会投影Issue候选标题/摘要或用户
原文。Trace Site服务端仍只通过白名单视图读取摘要，不查询`feedback`。

## 9. Trace Site怎样展示

页面先按route解释业务终态，再用area/category细化标题：

```text
issue_required + extension → 前端/扩展需求或缺陷 / 已创建Issue
rejected_irrelevant        → 无关内容 / 已忽略
quarantined_security       → 提示词注入 / 安全拦截
accepted_backend_bug       → 后端分类 / PR或验证终态
```

运行列表的终态列只显示徽标；GitHub PR/Issue链接放在详情页。Issue详情不展示代码阶段证据，
并明确说明前端/扩展代码未被自动修改。

概览使用全量分页后的Supabase数据：全部run数、唯一PR数、全部终态平均耗时和全部Token。
新增运行完成回调会失效统一缓存标签；回调丢失时还有60秒revalidate兜底。Langfuse只负责
脱敏调用明细，不参与这些业务统计。

## 10. 上线和验收顺序

1. 停止生产Scheduler；
2. 审查并手工执行Issue routing migration；
3. 增加并批准GitHub App的Issues写权限；
4. 运行PR/Issue双权限只读预检；
5. 部署Trace Site与Agent，显式重启Worker；
6. 使用可丢弃、无隐私的前端功能反馈做真实验收；
7. 对账feedback=`issue_opened`、run=`completed`、`pr_url=null`、`issue_url!=null`；
8. 检查公开Issue脱敏、Trace详情和概览统计；
9. 审计通过后由维护者输入`ENABLE`恢复Scheduler。

真实生产证据只记录在
[implementation-plan.md](../AgentRequirements/implementation-plan.md)，Guide不以示例替代验收。

## 11. 结合源码阅读

建议按以下顺序：

1. [Gate结构](../../agent/domain/gate.py)：Issue字段和跨字段Schema；
2. [Gate Policy](../../agent/domain/policy.py)：分类规范化与route优先级；
3. [Graph](../../agent/graph.py)：`publish_issue`和终态持久化；
4. [发布契约](../../agent/publishing/contracts.py)：`IssueDraft`与结果对象；
5. [GitHub适配器](../../agent/publishing/github.py)：最小权限、脱敏和marker复用；
6. [Migration 007](../../agent/migrations/007_issue_routing_and_public_projection.sql)：状态与公开投影；
7. [Trace映射](../../trace-site/src/lib/run-graph.ts)：阶段与终态展示；
8. [运行读取](../../trace-site/src/lib/server/runs.ts)：全量分页和概览聚合。
