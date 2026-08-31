# 修复 Agent 工具循环契约

## 1. 状态与替换范围

本文是后端缺陷复现与修复 Agent 的唯一权威契约。它直接替换
`agent-runtime.md` 中旧的 `plan_reproduction -> generate_test_edit -> generate_fix_edit`
固定模型节点，以及 `tool-contracts.md` 中要求模型先返回 `ReproductionPlan`、通用 Oracle
参数对象的旧接口。Gate、源码快照、补丁 Policy、Docker Worker、最终独立验证、发布与
Failure Recorder 保持原有受信边界。

本次升级不增加多个自治 Agent，也不移除 LangGraph。Controller 的外层 LangGraph 继续
保存业务状态；后端修复阶段使用 LangChain `create_agent` 构造一个由 LangGraph 驱动的
有限 ReAct 工具循环。旧 checkpoint 不做兼容迁移；不兼容运行明确失败并由维护者使用新
feedback/run 验收。

## 2. 处理流程

```text
Gate accepted_backend_bug
  -> 固定 base_sha 与只读源码快照
  -> 受信 conversion probe
       |- 转换抛错
       |    -> 保存原 Markdown、脱敏异常详情和确定性转换测试
       |    -> Repair Agent 读取源码、提交 fix、运行目标 Sandbox
       `- 转换成功
            -> Repair Agent 根据用户反馈检查源码并提交语义回归测试
            -> 运行基线 Sandbox，确认测试能够证明问题
            -> 提交 fix、运行目标 Sandbox
  -> 受信最终验证
       1. 新容器证明基线 + test patch 失败
       2. 新容器证明 test patch + fix patch 目标通过
       3. 新容器运行后端全量回归
       4. 执行已登记的受信 DOCX 专项检查
  -> 受信 Publisher 创建 PR
```

conversion probe 只回答“当前 Markdown 转换是否抛错”，不让模型预先猜 Oracle：

- 转换抛错时，异常类型、稳定错误码、截断后的安全摘要与原 Markdown作为不可信事实提供
  给模型；Controller 生成的转换测试被冻结为目标回归，不要求模型重新设计测试；
- 转换成功时，不能据此认定用户反馈错误。模型结合反馈、源码和生成产物设计可执行的语义
  测试；测试必须先在基线 Sandbox 证明失败，才允许进入修复；
- 模型无法构造可证明且受信的语义测试时，终态为 `cannot_reproduce` 或
  `needs_human`，不得用“转换成功”冒充问题已不存在；
- 不新增针对单个公式、Mermaid 或具体反馈的专用模型 Schema。类别差异由测试代码和既有
  受信 Validator 表达。

## 3. Agent 与受信状态

同一 run 只创建一个 Repair Agent checkpoint 线程，线程 ID 使用受信派生值
`repair:<agent_run_id>`。Agent 可以在复现与修复阶段保留工具历史和摘要，但以下结构化状态
始终由工具/Controller 写入，是恢复和完成判定的事实来源：

```text
phase: reproducing | repairing
run_id, feedback_id, base_sha, source_snapshot_ref
test_patch_ref, fix_patch_ref
target_test_selector, expected_failure_kind
reproduction_confirmed, repair_confirmed
last_sandbox_result_ref
reproduction_round, repair_round
model_calls, tool_calls, sandbox_seconds
terminal: completed | blocked | null
```

模型消息、Todo 和 Summary 不能覆盖这些字段。模型说“完成”不等于完成；只有对应完成工具
检查到受信字段齐全，外层 Graph 才能继续。

## 4. 工具集合

模型只能使用 `tool-contracts.md` 定义的结构化工具：

- 只读：`read_source_file`、`search_source`；
- 测试写入：`submit_test_edits`；
- 修复写入：`submit_fix_edits`；
- 执行：`run_sandbox`；
- 终结：`complete_reproduction`、`complete_repair`、`report_blocked`；
- 规划：官方 `write_todos`。

不注册 Shell、通用 Filesystem、任意命令、任意路径、网络、GitHub、数据库或发布工具。
工具可见性同时按阶段和受信子状态收窄：没有测试/修复 patch 时只开放对应提交工具；patch
提交后只开放 `run_sandbox`；Sandbox 失败后重新开放读取与下一轮提交；通过后只开放对应
完成工具。已登记于当前阶段、但尚缺前置产物的调用返回结构化
`tool_precondition_failed` ToolMessage，并要求模型在同一工具循环执行 `required_action`；它
不是安全拒绝。未登记工具或跨阶段调用仍为 `tool_not_authorized/security` 并立即终结。
每个工具在执行函数内部再次校验阶段、路径、预算和当前 patch，Middleware 不是唯一安全
边界。

并行只读批次中，`source_request_invalid` 只使对应调用返回错误 ToolMessage，其他成功读取
仍保留给模型；任一调用触发 `source_access_denied` 时仍终结整个 run。公开观测只记录原因
枚举和通过规范化、白名单校验后的路径，不记录危险原始路径。

### 4.1 并行调用

模型 API 必须允许一次响应返回多个 tool call，但本地按副作用类别决定能否并行：

| 同一批次 | 处理 |
|---|---|
| 多个只读工具 | 并行执行 |
| 多个只读工具 + 一个 `run_sandbox` | 拒绝；patch 待验证子状态只允许单独运行 Sandbox |
| 两个及以上 `run_sandbox` | 拒绝该批次，要求分轮调用 |
| 任一 patch 写入 + 其他任意工具 | 拒绝该批次 |
| 任一完成/阻塞工具 + 其他任意工具 | 拒绝该批次 |

生产 Worker 继续使用全局单并发锁；本次不为 2H2G 主机增加 Sandbox 并发。并行只优化互不
冲突的只读查询，不改变 patch、Artifact 或 Sandbox 的串行所有权。

## 5. Middleware 顺序与职责

优先使用官方 Middleware，但安全和主备语义由本地 Middleware 明确实现：

1. `TodoListMiddleware`：提供结构化 Todo；
2. `RepairSummarizationMiddleware`：在有效上下文窗口的 65% 触发 Summary；
3. `ContextBudgetMiddleware`：到 85% 时必须先成功总结，否则停止；
4. `ModelResilienceMiddleware`：模型临时错误执行主、主、备三次总 attempt；
5. `PhaseToolPolicyMiddleware`：按阶段授权和受信子状态收窄可见工具并再次校验调用；
6. `ParallelToolPolicyMiddleware`：拒绝有副作用冲突的同批调用；
7. `ToolRetryMiddleware`：只对幂等 Sandbox 临时传输失败做三次总 attempt；
8. `ToolErrorMiddleware`：把允许回传的工具参数和前置条件错误转换为脱敏可执行反馈；
9. `ModelCallLimitMiddleware`、`ToolCallLimitMiddleware`：执行本地预算；
10. `CompletionGuardMiddleware`：模型无工具直接作答时要求其继续调用完成或阻塞工具。

不使用 `ShellToolMiddleware` 或通用 `FilesystemMiddleware`。Middleware 的具体注册顺序以
LangChain 的 before/after 包裹语义验证，不能仅按列表顺序猜测执行顺序。

## 6. 模型与工具重试

每个 Repair Agent 模型轮次包含首次最多三次传输 attempt：

```text
attempt 1: 主模型
等待 1 秒
attempt 2: 主模型
等待 2 秒
attempt 3: 备用模型
```

仅 timeout、连接异常、408、429 和 5xx 进入下一 attempt。认证、权限、配置、上下文超限、
安全拒绝、无效 tool call、模型已成功返回但内容不满足业务要求，以及未知异常均不切换或
重试。主备接口继续使用统一 `openai_compatible` 口径，不增加供应商状态或
`provider_failover` 观测事件。

`run_sandbox` 的连接异常、408、429 和 5xx 使用官方 `ToolRetryMiddleware`，包含首次最多
三次、等待 1 秒和 2 秒，并复用同一 job ID、幂等键和请求指纹。Repair Agent 路径中的
Sandbox Client 自身重试必须关闭，避免嵌套成九次。认证、冲突、非法请求、无效 200、
Policy 或安全拒绝不重试。

## 7. Summary 与上下文预算

Summary 使用备用模型的独立调用，不引入第三个 Agent。有效窗口取主模型、备用模型
`max_input_tokens` 的较小值；先读取 LangChain model profile，未知自定义模型必须分别配置
`MODEL_CONTEXT_WINDOW` 和 `FALLBACK_MODEL_CONTEXT_WINDOW`，缺失时 preflight 失败。

- soft trigger：当前上下文估算达到有效窗口 65%；
- hard limit：达到 85% 时必须完成总结后才允许继续；
- keep：保留最近 20% 上下文；
- 不设置与具体模型绑定的固定总 Token 上限或固定 Summary 输出 Token 阈值；
- 系统 Prompt 与工具 Schema 保持稳定，运行数据放在用户消息/工具结果尾部，便于 Provider
  前缀 KV cache；
- Summary 只是压缩后的不可信上下文，不能替代 checkpoint、Artifact 和结构化计数。

Summary Prompt 必须生成以下固定章节：

```text
目标
用户明确要求
可信事实与引用
已完成事项及证据
当前结构化状态
失败尝试与原因
下一步
禁止事项与安全边界
仍不确定的事项
```

总结不得宣称没有受信证据的测试/修复已通过，不得保留密钥、联系方式、完整用户文档、
完整源码、完整 patch 或大段日志。soft 区间总结临时失败可继续使用尚未压缩的上下文；到
hard limit 仍无法总结必须以明确上下文预算错误停止，不能截断关键状态后猜测继续。

## 8. 停止与独立验证

Repair Agent 只负责形成候选测试、候选修复和目标 Sandbox 证据。以下条件停止工具循环：

- `complete_reproduction`：受信基线结果确认目标测试按预期失败；
- `complete_repair`：受信目标结果确认 test + fix 已通过；
- `report_blocked`：工具或 Policy 能证明需要人工、无法复现或超出允许范围；
- 模型/工具/沙箱/上下文预算耗尽；
- 安全、权限、认证或永久配置失败。

`complete_repair` 后仍必须执行外层 `validate_final`。它使用全新容器重新证明基线失败、
目标通过、全量回归及专项 DOCX 检查，并验证最终 diff/hash；模型不能调用、跳过或修改该
节点。最终验证失败不会由模型自动发布；在总预算允许时外层 Graph 可以带脱敏失败摘要
重新进入同一 Repair Agent，否则进入明确终态。

## 9. 真实模型预检

`mdtoword-agentctl model-smoke` 是显式、会产生少量模型费用的只读验收命令。它不得领取
反馈、读取反馈正文、访问源码快照、启动 Sandbox、写数据库、写 GitHub 或写 Artifact。
它只使用合成数据验证：

1. 主/备模型均有可用的上下文 profile 或显式窗口配置；
2. 两个接口均能返回符合 OpenAI tool-calling 协议的调用；
3. 一次响应可以请求两个只读探针工具，并记录是否真正并发执行；
4. usage 中的 prompt/completion token 可读取，缓存 hit/miss 字段存在时只输出计数；
5. Summary 包含固定章节、保留未完成事项与禁止边界、不泄漏合成 Secret；
6. 使用缩小的合成窗口验证 65% soft trigger 和 85% hard limit，不消耗真实窗口的 65%。

模型不愿并行调用两个探针时，命令应把能力记为明确失败或不支持，不能根据模型名称假定
支持。输出只包含布尔值、模型标识、窗口、耗时、计数与稳定错误码，不输出 Base URL、
API Key、原始 Prompt、原始响应或合成 Secret。

## 10. 验收

- Fake 模型证明模型 A 临时失败、A 临时失败、B 成功，且永久错误只调用一次；
- Fake 工具批次证明只读工具可并行、双 Sandbox 与写入冲突在执行前被拒绝；
- Fake 长上下文证明 65% 触发、20% 保留和 85% fail-closed；
- Summary 回归测试锁定全部章节、未完成任务、禁止事项和脱敏规则；
- checkpoint 恢复不重复提交 patch、Sandbox job 或完成工具；
- conversion probe 分别覆盖转换抛错与转换成功但语义不符两条分支；
- 完整 Agent 套件与 compileall 通过后，维护者在 Scheduler 关闭时运行
  `mdtoword-agentctl model-smoke`，再用可丢弃反馈完成真实 Sandbox/发布验收。
