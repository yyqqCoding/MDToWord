# Agent Runtime 契约

## 1. 两层 Runtime

系统保留 LangGraph，但不再把复现和修复拆成一串固定的模型生成节点。

- 外层 LangGraph 是业务编排器，负责 Gate、源码快照、阶段状态、最终验证和发布；
- 内层 create_agent 是有限 ReAct，负责在复现/修复阶段循环选择已授权工具；
- Controller、本地 Policy 和验证节点拥有最终决策权；
- 模型只能提出工具调用和候选编辑，不能直接改变业务状态或执行代码。

生产运行时使用一个 Repair Agent。旧的固定计划路径不是生产行为，也不作为恢复路径。

## 2. 外层执行顺序

~~~text
start_gate
  -> classify_gate
  -> route_feedback
      |- 结束：拒绝、隔离、转人工、重复
      |- publish_issue -> finish_issue_publication
      +-- prepare_source
           -> repair_agent
              |- finish_agent_blocked
              |- finish_reproduction
              +-- finish_repair_success
                   -> validate_final
                      |- finish_validation
                      |- finish_budget_exhausted
                      +-- publish_pull_request
                           -> finish_publication
~~~

是否允许进入某个节点由外层 State 和确定性条件边决定。模型不能跳过源码快照、基线
复现、独立验证或发布前检查。

## 3. Gate

Gate 不注册任何工具，只接收反馈的最小必要字段并返回严格结构化分类。输出经过本地
Policy 规范化后才会影响路由。

分类优先级是：

~~~text
提示词注入/越权
  -> quarantined_security
无关/垃圾
  -> rejected_irrelevant
功能需求或前端/扩展缺陷
  -> issue_required
相关且信息充分的后端缺陷
  -> accepted_backend_bug
其他
  -> needs_human
~~~

Gate 的模型输出不是权限授予。模型即使声称允许修改扩展或发布，也不能改变本地路由。

## 4. 源码快照与 conversion probe

后端自动修复开始前，Controller 从 GitHub main 固定 base_sha，并创建只读源码快照。
源码快照、反馈 Artifact 和运行 ID 是同一轮工具循环的受信上下文。

conversion probe 只判断当前 Markdown 是否在后端转换阶段抛错：

- 抛出转换错误：Controller 根据原文和安全错误摘要生成固定转换回归测试；
- 转换成功：Repair Agent 必须设计语义回归测试，并先在基线证明目标失败；
- 不能形成稳定测试或问题无法复现：结束为 cannot_reproduce 或 needs_human；
- probe 不判断用户的主观视觉偏好，也不替代最终 DOCX Validator。

## 5. Repair Agent State

内层线程使用 repair:<run_id>，并通过私有 PostgreSQL checkpoint 保存消息和结构化状态。
消息可以被 Summary 压缩，但下列字段不能由模型文本覆盖：

~~~text
phase
run_id, feedback_id, base_sha, source_snapshot_ref
test_patch_ref, fix_patch_ref
target_test_selector, expected_failure_kind
reproduction_result_ref, repair_result_ref
reproduction_confirmed, repair_confirmed
reproduction_round, repair_round
model_calls, tool_calls, token usage, sandbox_duration_ms
terminal, blocked_code, blocked_summary
~~~

只有工具写入并经过受信校验的字段才会进入外层结果。模型说“完成”而没有调用完成
工具，不算完成。

## 6. 内层循环

典型的工具循环是：

~~~text
读取/搜索源码
  -> 提交 test.patch 或 fix.patch
  -> run_sandbox
  -> 观察结构化结果
  -> 继续读取、重新编辑或调用完成工具
~~~

复现阶段只能形成并验证 test.patch；修复阶段必须先拥有已确认的复现结果，才能形成
fix.patch。Sandbox 失败时可以重新读取和编辑；目标通过后只能调用对应完成工具。

模型可以自主决定下一次读取哪个已授权文件、是否需要再查找或是否重新提交补丁，但不能
决定工具集合、文件白名单、命令、容器权限、测试选择器规则或最终验证方式。

## 7. 工具错误与完成判定

工具错误分为三类：

1. 参数或前置条件错误：返回脱敏 ToolMessage 和 required_action，模型在同一循环修正；
2. 临时外部失败：按对应 Retry Policy 重试，耗尽后终止；
3. 越权、安全或永久配置错误：不重试，直接进入明确安全/人工终态。

完成工具只接受受信结果：

- complete_reproduction 需要当前轮基线测试按预期失败；
- complete_repair 需要当前轮目标 Sandbox 通过；
- report_blocked 只接受稳定原因枚举和脱敏摘要。

即使 complete_repair 成功，外层仍必须运行 validate_final；模型不能调用、跳过或修改
最终验证节点。

## 8. 恢复与预算

- 外层使用数据库状态，内层使用同一 run 的 checkpoint；
- 显式续跑复用原 thread、base_sha、候选补丁和累计计数；
- 不重新领取同一 feedback，不盲目重复已完成 Sandbox 或 GitHub 写入；
- 模型最多 50 次、工具最多 30 次，Sandbox 总时长默认 900 秒；
- 达到预算进入 budget_exhausted，Scheduler 不自动重开；
- 维护者提高预算后，可以显式使用原 run ID 继续，不能重置历史计数；
- LangGraph recursion limit 只是失控保护，不代替模型/工具预算。

## 9. 版本边界

Graph、Prompt、Policy、工具契约和 State Schema 版本写入 agent_runs。当前 State Schema
为 v3；改变不兼容的线程结构后，不尝试迁移旧 checkpoint，直接使用新的 feedback/run
验收。
