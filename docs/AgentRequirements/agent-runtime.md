# Agent Runtime 设计

## 1. Runtime 角色

LangGraph 是编排 Runtime，不是安全边界、模型 Provider、沙箱或业务规则所有者。它
负责保存状态、调度节点、表达条件分支、限制循环、处理中断和恢复。确定性步骤与
LLM 步骤在同一图中显式区分。

MVP 使用一个 Graph，不引入多个自治 Agent。复现和修复分别是一个具有独立工具集
和轮次上限的 Agentic 子图。

## 2. Graph State

Graph State 使用版本化的 Pydantic/TypedDict Schema，只保存恢复流程所需的小对象：

```text
schema_version
run_id, feedback_id, trace_id, claim_token, dry_run
status, route, category, risk
base_sha, extension_version
task_artifact_ref, source_snapshot_ref
gate_result
reproduction_plan
test_patch_ref, reproduction_result
fix_patch_ref, validation_result
reproduction_round, repair_round
model_calls, tool_calls, token_usage, cost
validated_patch_sha256, pr_url
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
       |- terminal_non_repair
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
  -> prepare_source -> reproduce_subgraph
  -> repair_subgraph -> validate_final
  -> publish_pull_request -> finish_publication -> END
```

Fake Provider 默认只执行 Gate；`--reproduce --provider configured` 执行到阶段 D，
`--repair --provider configured` 执行完整 D+E，且两者强制 `--dry-run`。只有显式
`--publish --provider configured` 不使用 dry-run 并执行完整 D+E+F。生产 Scheduler
另外受默认关闭的投产开关保护。CLI 的 `completed` 表示 Graph 已到终态，不等同业务
成功；`status`、`error_code`、`published` 与 `pr_url` 共同表达最终结果，只有
`published=true`、`error_code=null` 且 `pr_url` 非空才代表阶段 F 发布成功。

### 3.2 确定性节点

以下节点不允许模型决定副作用：

- `claim_feedback`
- `start_trace`
- `route_feedback`
- `prepare_source`
- `validate_final`
- `publish_pr`
- `finalize`

它们只接受已验证的领域对象，并由应用服务执行数据库、GitHub、Artifact 和状态操作。

### 3.3 LLM 节点

- `classify_gate`
- `plan_reproduction`
- `generate_test_edit`
- `revise_test_edit`
- `generate_fix_edit`
- `revise_fix_edit`

所有模型输出经过严格 Schema 校验；Schema 失败最多重试一次格式修正，不进入业务
修复轮次。模型不能直接写 Graph State，节点先校验再返回允许的字段更新。

## 4. Feedback Gate

Gate 分三层，顺序不可交换。

### 4.1 确定性入口校验

- `feedback_type` 只能是现有枚举；
- `description` 非空且不超过现有反馈 API 的 1000 字符上限；
- Bug 的 `markdown_content` 非空且 UTF-8 编码后不超过 50 KiB；
- 去除联系方式后构造 task；
- 计算内容指纹并检查精确重复；
- 功能反馈可直接判定 `out_of_scope`，无需进入代码修复。

### 4.2 无工具模型分类

模型只看到脱敏 task、后端职责摘要和分类 Schema，不读取仓库、不调用工具。输出：

```text
intent: bug_report | feature_request | unrelated | spam | unknown
category: 后端类别 | extension_ui | visual_quality | unknown
relevance: 0..1
sufficient_information: bool
injection_suspected: bool
requires_extension_change: bool
reason: 简短说明
```

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

模型给出的“允许自动化”结论没有授权效力。注入、无关内容和前端范围外判定始终优先。
在这些高优先级规则通过后，非空 Bug Markdown 的描述若包含明确后端转换报错或 Pandoc
失败签名，本地确定性证据可将模型不稳定的类别或低 `relevance` 校正为
`conversion_crash` 并进入有界复现；没有明确错误证据时仍按阈值转人工。唯一的充分性
校正是：模型已判定高相关 `bug_report/docx_structure`、无注入和前端依赖，同时 Markdown
含完整 Mermaid 图时，可把单一 `sufficient_information=false` 校正为继续复现。

## 5. 复现子图

```text
plan_reproduction
  -> inspect_source/read-only tools
  -> generate_test_edit
  -> policy_check_test_edit
       `- Mermaid invalid response/edit -> trusted drawing template
  -> run_reproduction_in_sandbox
       |- target_failure -> reproduction_confirmed
       |- test_passed -> revise_test_edit (最多 2 轮)
       |- invalid_test -> revise_test_edit (最多 2 轮)
       `- security_rejected -> END
```

`ReproductionPlan` 必须包含：问题假设、确定性 Oracle、目标测试名称、预期失败类型、
需要读取的文件以及是否可能要求前端同步。

复现成功的含义是：仅在基线应用测试补丁后，指定测试确实执行并发生与计划一致的
断言失败。导入错误、语法错误、fixture 缺失、超时、容器失败或与问题无关的异常
都不是成功复现。

两轮仍无法复现时进入 `cannot_reproduce`，不生成修复。

Mermaid 模型输出耗尽一次格式修正仍不符合严格 Schema 时，或第一轮模型编辑无法形成合法
Python/文本补丁时，由 Controller 生成固定测试与 `.md` fixture，使用计划已登记的
`assert_minimum_drawing_count`。该模板不读取网络、环境变量或任意 XPath，仍经过相同
Patch Policy 和全新 Sandbox；其他类别及 Sandbox 中发生的无效测试不会触发此回退。

## 6. 修复子图

```text
repair_scope_policy
  -> generate_fix_edit
  -> policy_check_fix_edit
       `- 未预装的 external dependency / deployment change -> needs_human -> END
  -> run_target_validation_in_sandbox
       |- passed -> final_validation
       |- failed -> summarize_failure -> fresh workspace -> revise_fix_edit
       `- security_rejected -> END
```

最多两轮。每轮模型上下文只包含：脱敏反馈摘要、复现计划、目标失败摘要、当前允许
源码、测试补丁摘要、上一轮修复摘要和剩余预算；不累积完整历史。

生产与 Sandbox 镜像包含同版本的受信 Mermaid CLI、Chromium 和中文字体。Mermaid
drawing Oracle 已确认复现时，Controller 额外向修复模型提供只读
`backend/app/mermaid_renderer.py`，允许模型在 `pandoc_runner.py` 接入固定 API，并继续
进入相同的目标、基线、全量和 DOCX drawing 验证。模型不能修改渲染器或依赖清单。

模型不能修改测试补丁。每轮使用从 `base_sha` 新建的工作区，不在上一轮修改上继续
叠加。修复若新增未预装的外部可执行程序、Pandoc filter 或需要部署变更，由本地 Policy 直接
转为 `needs_human`，不启动 Sandbox、也不消耗第二轮模型请求。失败摘要最多 4 KB，并将
工具输出继续标记为不可信数据。

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

## 8. Provider 边界

`ModelProvider` 对 Runtime 暴露统一能力：

```text
generate_structured(messages, response_schema, tools, timeout)
  -> content/tool_calls, usage, model, provider_request_id
```

Provider 负责厂商协议、结构化响应、Tool Call 转换、限流重试和 usage 归一化。
Runtime 禁止根据模型名称分支业务规则。MVP 一个模型；替换模型只改变配置与适配器。

Provider 错误标准化为：

```text
auth_error, rate_limit, timeout, invalid_response,
context_too_large, provider_unavailable, safety_refusal
```

认证错误不重试；限流和短暂故障指数退避有限重试；非法结构只做一次格式修正。

阶段 B3 已实现 OpenAI 兼容 Chat Completions Provider。Gate 使用
`response_format=json_schema` 和 `strict=true`，Prompt 版本为 `gate-v2`，并始终传入空
工具集合。Provider 真实 usage 累计到 `agent_runs`；若响应不含成本，则按本地配置单价
估算，未配置单价时成本保持 `0`。

## 9. 预算与停止条件

默认值由 Policy 配置，Runtime 在每个 LLM 和工具节点前检查：

```text
MAX_REPRODUCTION_ROUNDS=2
MAX_REPAIR_ROUNDS=2
MAX_FORMAT_RETRIES=1
MAX_MODEL_CALLS_PER_RUN=8
MAX_TOOL_CALLS_PER_RUN=30
MAX_TOTAL_TOKENS_PER_RUN=<按所选模型配置>
MAX_SANDBOX_SECONDS_PER_RUN=900
```

任一上限触发后进入 `budget_exhausted`，不能由模型请求继续。

## 10. 幂等与恢复

LangGraph 节点可能因恢复而重新执行。所有副作用使用稳定的
`operation_id = run_id:node:logical_attempt`：

- claim 使用数据库 token 和唯一约束；
- Sandbox Client 重复提交返回同一 Job 或已完成结果；
- Artifact 使用原子临时文件加 rename；
- PR 创建前按 feedback、branch 和 patch hash 查重；
- 发布失败只允许同 run 恢复 `publication_*` checkpoint，不重新执行模型或 Sandbox；
- finalize 使用目标状态条件更新。

运行恢复时先从外部系统查询 operation 状态，不能无条件重复副作用。Graph、Prompt、
Policy 和沙箱镜像版本写入 run，便于解释结果；MVP 不支持跨不兼容 State Schema
恢复，不兼容时明确失败并新建 run。

生产 Checkpointer 只允许使用私有 `agent_runtime` Schema。初始化必须通过显式的
`python -m agent.cli checkpoint setup` 完成；服务启动不自动建表，发现同名 checkpoint
表存在于 `public` 时拒绝启动。
