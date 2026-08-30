# Agent Runtime 设计

## 1. Runtime 角色

LangGraph 是编排 Runtime，不是安全边界、模型 Provider、沙箱或业务规则所有者。它
负责保存状态、调度节点、表达条件分支、限制循环、处理中断和恢复。确定性步骤与
LLM 步骤在同一图中显式区分。

系统使用一个外层 Graph，不引入多个自治 Agent。Gate 是无工具严格分类节点；复现与修复
由同一个 `create_agent` 有限 ReAct 工具循环完成。其工具、Middleware、并行和 Summary
契约以 [repair-agent-loop.md](repair-agent-loop.md) 为唯一权威来源。

## 2. Graph State

Graph State 使用版本化的 Pydantic/TypedDict Schema，只保存恢复流程所需的小对象：

```text
schema_version
run_id, feedback_id, trace_id, claim_token, dry_run
status, route, category, area, risk
base_sha, extension_version
task_artifact_ref, source_snapshot_ref
gate_result, issue_draft_ref
test_patch_ref, reproduction_result
fix_patch_ref, validation_result
reproduction_round, repair_round
model_calls, tool_calls, token_usage, cost
validated_patch_sha256, pr_url, issue_url
last_error
```

`claim_token` 只保存到私有 PostgreSQL checkpoint，用于恢复节点的条件更新，不进入
Trace、日志或模型上下文。State 不保存密钥、联系方式、完整源码、完整 pytest 日志
或完整用户 Markdown。大对象存 Artifact，由 State 保存路径、哈希和脱敏摘要。

生产使用 PostgreSQL checkpointer；内存 checkpointer 只用于单元测试。每次运行的
`thread_id` 等于 `agent_run_id`。

## 3. 顶层图

```text
START
  -> claim_feedback
  -> start_trace
  -> gate_feedback
  -> route_feedback
       |- terminal_no_publication
       |- publish_issue -> finalize
       `- prepare_source
            -> reproduce_subgraph
            -> repair_subgraph
            -> validate_final
            -> publish_pr
            -> finalize
  -> END
```

### 3.1 当前实现边界

截至阶段 F/G 本地实现，实际 Graph 已实现 Gate、复现、修复、独立验证和发布：

```text
start_gate -> classify_gate -> route_feedback
  |- prepare_source -> reproduce_subgraph
  |  -> repair_subgraph -> validate_final
  |  -> publish_pull_request -> finish_publication -> END
  `- terminal_non_repair -> END
```

阶段 I 已在 `route_feedback` 增加 `issue_required -> publish_issue -> finish_issue_publication`
分支。它不准备源码、不启动 Sandbox，也不复用 PR 的验证发布路径。

Fake Provider 默认只执行 Gate；`--reproduce --provider configured` 执行到阶段 D，
`--repair --provider configured` 执行完整 D+E，且两者强制 `--dry-run`。只有显式
`--publish --provider configured` 不使用 dry-run 并执行完整 D+E+F。生产 Scheduler
另外受默认关闭的投产开关保护。CLI 的 `completed` 表示 Graph 已到终态，不等同业务
成功；`status`、`error_code`、`published` 与 `pr_url` 共同表达最终结果，只有
`published=true`、`error_code=null` 且 `pr_url` 非空才代表阶段 F 发布成功。
Issue 成功另外以 `issue_url` 非空且 `error_code=null` 表达，不能用
`published` 或 `pr_url` 冒充。

`--publish` 是 PR 与 Issue 共用的真实 GitHub 写入授权开关，但两个 Publisher 的输入、
权限令牌和恢复节点保持分离。未提供 `--publish` 时，`issue_required` 只能产生分类结果或
Fake Publisher 结果，不得因为 Issue 不修改代码就绕过 dry-run 边界。

### 3.2 确定性节点

以下节点不允许模型决定副作用：

- `claim_feedback`
- `start_trace`
- `route_feedback`
- `prepare_source`
- `validate_final`
- `publish_pr`
- `publish_issue`
- `finalize`

它们只接受已验证的领域对象，并由应用服务执行数据库、GitHub、Artifact 和状态操作。

### 3.3 LLM 节点

- `classify_gate`
- `repair_agent`

`repair_agent` 使用注册工具改变受信状态，不要求模型先输出固定计划 JSON。模型不能直接
写业务 Graph State；只有工具与 Controller 能返回允许的字段更新。Gate 仍使用严格 Schema。

## 4. Feedback Gate

Gate 分三层，顺序不可交换。

### 4.1 确定性入口校验

- `feedback_type` 只能是现有枚举；
- `description` 非空且不超过现有反馈 API 的 1000 字符上限；
- Bug 的 `markdown_content` 非空且 UTF-8 编码后不超过 50 KiB；
- 去除联系方式后构造 task；
- 计算内容指纹并检查精确重复；
- Bug 的 Markdown 约束不套用到 Feature；
- 不再按 `feedback_type=feature` 短路。两类表单都必须进入无工具模型分类，才能在任何
  GitHub 写入前识别无关内容与提示词注入。

### 4.2 无工具模型分类

模型只看到脱敏 task、后端职责摘要和分类 Schema，不读取仓库、不调用工具。输出：

```text
intent: bug_report | feature_request | unrelated | spam | unknown
area: backend | extension | cross_component | none | unknown
category: 后端类别 | feature_request | irrelevant_content |
          prompt_injection | visual_quality | extension_ui | unknown
relevance: 0..1
sufficient_information: bool
injection_suspected: bool
requires_extension_change: bool
reason: 简短说明
issue_title: string|null
issue_summary: string|null
```

`issue_title` 与 `issue_summary` 是候选脱敏摘要，不是直接 GitHub 写入凭据。只有本地 Policy
最终选择 `issue_required` 时二者才必须非空；其他路由必须为 null。标题为单行且不超过
80 字符，摘要不超过 600 字符，只复述明确需求与现象，不生成用户未提出的验收条件。
这些跨字段规则既写进 Gate Prompt，也由本地 Policy 分别给出可执行校验错误。实现时须
同步 bump `GATE_PROMPT_VERSION`；阶段 I 当前版本为 `gate-v9`。

### 4.3 本地路由

Policy Engine 只允许满足以下全部条件的反馈进入复现：

```text
intent == bug_report
category in backend_allowlist
relevance >= MIN_GATE_CONFIDENCE
sufficient_information == true
injection_suspected == false
requires_extension_change == false
```

模型给出的“允许自动化”或“允许发布”结论没有授权效力。路由优先级固定为：

```text
injection_suspected
  -> quarantined_security / category=prompt_injection
unrelated | spam
  -> rejected_irrelevant / category=irrelevant_content
feature_request + area in {backend, extension, cross_component} + 信息充分
  -> issue_required / enhancement
bug_report + area=extension + 信息充分
  -> issue_required / bug
bug_report + backend allowlist
  -> accepted_backend_bug
其他未知、低置信度或信息不足
  -> needs_human
```

前端展示、视觉、交互和布局需求统一归为 `feature_request + extension`；前端/扩展 Bug 为
`bug_report + extension`。二者均不启动 Sandbox。`out_of_scope` 仅在读取历史 checkpoint
或数据库记录时兼容，新分类不得返回。

Policy 负责把模型分类规范成稳定 `GateResult`：注入 → `none/prompt_injection`，无关或垃圾
→ `none/irrelevant_content`，功能/视觉需求 → `<area>/feature_request`，前端/扩展 Bug →
`extension/extension_ui`。模型给出这些已知意图却保留 `unknown` 时，Policy 必须规范化或
以逐字段消息拒绝，不能把歧义留给数据库和展示站。

注入、无关内容和 Issue 分流始终优先于后端复现判定。
在这些高优先级规则通过后，非空 Bug Markdown 的描述若包含明确后端转换报错或 Pandoc
失败签名，本地确定性证据可将模型不稳定的类别或低 `relevance` 校正为
`conversion_crash` 并进入有界复现；没有明确错误证据时仍按阈值转人工。唯一的充分性
校正是：模型已判定高相关 `bug_report/docx_structure`、无注入和前端依赖，同时 Markdown
含完整 Mermaid 图时，可把单一 `sufficient_information=false` 校正为继续复现。

## 5. 复现与修复工具循环

本节旧的固定 `ReproductionPlan`、测试生成和修复生成子图已由
[repair-agent-loop.md](repair-agent-loop.md) 直接替换。实现不得同时保留两套执行路径；
历史验收事实只保留在 `implementation-plan.md`，不在当前 Runtime 契约中保留第二套流程。

## 7. 最终验证与发布

`validate_final` 是全新沙箱中的确定性节点，不信任复现和修复子图报告的“通过”：

1. 从 `base_sha` 重建源码；
2. 检查并应用 test patch；
3. 重新证明仅测试补丁时目标失败；
4. 应用 fix patch；
5. 运行目标测试；
6. 运行后端全量 pytest；
7. 运行 DOCX 专项验证；
8. 生成最终 patch 与 SHA-256；
9. 输出 `ValidationResult`。

只有 `ValidationResult.passed=true` 才有边进入 `publish_pr`。发布节点不是模型工具，
模型无法提前或直接触发。

Issue 分支与上述 PR 分支相互独立：`issue_required` 不需要 `base_sha`、测试补丁、修复补丁
或 `ValidationResult`，只接受 Gate 产生且通过本地 Policy 的 `IssueDraft`。受信
`publish_issue` 节点再次脱敏并校验固定仓库、标签与幂等 marker；成功后写入
`issue_opened/issue_url`，失败以 `issue_publication_failed` 终结并保留可恢复 checkpoint。

## 8. Provider 边界

`ModelProvider` 对 Runtime 暴露统一能力：

```text
generate_structured(messages, response_schema, tools, timeout)
  -> content/tool_calls, usage, model, provider_request_id
```

Gate Provider 负责严格结构化响应。Repair Agent 使用 LangChain ChatModel 与
`ModelResilienceMiddleware` 处理 tool calling、主/备用接口和 usage；业务规则不得根据
模型名称分支。具体重试顺序以 `repair-agent-loop.md` 为准。

Provider 错误标准化为：

```text
auth_error, rate_limit, timeout, invalid_response,
context_too_large, provider_unavailable, safety_refusal
```

认证错误不重试；限流和短暂故障指数退避有限重试。Gate 的非法结构仍做一次格式修正；
Repair Agent 的业务输出通过工具 Schema 与本地 Policy 校验后作为工具错误返回，不使用
旧的整段计划 JSON 格式修正。
429响应带秒数形式`Retry-After`时，Provider在10秒上限内尊重更长等待；非法或负数使用
本地退避，超过10秒则截断为10秒，避免单次模型调用无限占住单并发Scheduler。

当前OpenAI兼容Chat Completions Provider使用`response_format=json_schema`和
`strict=true`，并始终传入空工具集合。当前Prompt版本为`gate-v9`、
`reproduction-plan-v4`、`test-generation-v5`和`fix-generation-v4`。Provider真实usage
累计到`agent_runs`；若响应不含成本，则按本地配置单价估算，未配置单价时成本保持`0`。

## 9. 预算与停止条件

默认值由 Policy 配置，Runtime 在每个 LLM 和工具节点前检查：

```text
MAX_REPRODUCTION_ROUNDS=<本地配置>
MAX_REPAIR_ROUNDS=<本地配置>
MAX_FORMAT_RETRIES=1  # 仅 Gate 严格输出
MAX_MODEL_CALLS_PER_RUN=50
MAX_TOOL_CALLS_PER_RUN=30
MAX_SANDBOX_SECONDS_PER_RUN=900
```

任一上限触发后进入 `budget_exhausted`，不能由模型请求继续，也不会被 Scheduler 自动
恢复。维护者提高对应 thread 总预算后，可以显式指定同一 `--resume-run-id`；恢复必须
复用原 checkpoint、累计计数和候选补丁，不能重新领取 feedback 或重置已用预算。
上下文不使用固定总 Token 上限；按主备模型有效窗口的 65%/85% 比例总结和停止，详见
`repair-agent-loop.md`。

## 10. 幂等与恢复

LangGraph 节点可能因恢复而重新执行。所有副作用使用稳定的
`operation_id = run_id:node:logical_attempt`：

- claim 使用数据库 token 和唯一约束；
- Sandbox Client 重复提交返回同一 Job 或已完成结果；
- Artifact 使用原子临时文件加 rename；
- PR 创建前按 feedback、branch 和 patch hash 查重；
- Issue 创建前按 `run_ref` 与内容指纹 marker 查重；
- 发布失败只允许同 run 恢复 `publication_*` checkpoint，不重新执行模型或 Sandbox；
- Issue 发布失败只恢复 `issue_publication_*` checkpoint，不重新执行 Gate；
- finalize 使用目标状态条件更新。

运行恢复时先从外部系统查询 operation 状态，不能无条件重复副作用。Graph、Prompt、
Policy 和沙箱镜像版本写入 run，便于解释结果；MVP 不支持跨不兼容 State Schema
恢复，不兼容时明确失败并新建 run。

生产 Checkpointer 只允许使用私有 `agent_runtime` Schema。初始化必须通过显式的
`python -m agent.cli checkpoint setup` 完成；服务启动不自动建表，发现同名 checkpoint
表存在于 `public` 时拒绝启动。
