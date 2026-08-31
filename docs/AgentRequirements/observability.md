# 可观测性与评估

## 1. 目标

一次运行必须能够回答：

- 走了哪条路由；
- 哪个阶段、节点、模型和工具执行了什么；
- 消耗了多少模型调用、工具调用、Token、Sandbox 时间和成本；
- 哪次失败触发了重试、修正、重排或终止；
- 最终为何创建或没有创建 PR/Issue。

Supabase 保存业务状态和汇总；私有 checkpoint 保存 ReAct 线程；Artifact 保存大对象；
Langfuse 和结构化日志保存脱敏观测。Langfuse 不可用不能改变业务结果。

## 2. 运行标识

| 标识 | 作用 |
|---|---|
| feedback_id | Supabase 用户反馈 |
| session_id | 同一反馈多次运行的稳定关联值 |
| agent_run_id | 一次运行，也用于外层和内层恢复关联 |
| trace_id | 从领取到终态的端到端 Trace |
| observation_id | 单个节点、模型或工具观测 |
| job_id | 一次 Sandbox Job |
| operation_id | 外部副作用幂等键 |

动态 ID 不进入 observation 名称；名称保持稳定，轮次和版本写入 metadata。

## 3. Trace 树

~~~text
feedback-repair-run
  |- claim-feedback
  |- classify-intent
  |- prepare-source
  |- repair-agent
  |    |- repair-agent-model
  |    |- search-source / read-source-file
  |    |- submit-test-edits / submit-fix-edits
  |    |- run-sandbox
  |    +-- complete-reproduction / complete-repair / report-blocked
  |- validate-final
  |    |- reproduce-baseline
  |    |- run-target-tests
  |    |- run-full-tests
  |    +-- validate-docx
  |- publish-pr or publish-issue
  +-- finalize
~~~

Repair Agent 的模型、源码工具和 Sandbox 工具会跨 reproducing/repairing 阶段复用，因此
阶段统计必须优先使用受信 phase 字段，不能只按 observation 名称猜测。

## 4. Generation 观测

每次模型调用记录：

~~~text
operation, provider, model, provider_request_id
prompt_version, graph_version, policy_version
phase, round, latency_ms, status, retry_count
input_tokens, output_tokens, cached_input_tokens
reasoning_tokens, total_tokens, cost
~~~

Gate 使用无工具的结构化调用；Repair Agent 使用 ChatModel 的 tool calling；Summary
也是独立模型调用。Provider 返回的 usage 是首选，缓存和推理 Token 要落在互不重复的
bucket 中。

模型请求失败时只记录 error_code、error_type 和脱敏 schema_errors；invalid_response 的
schema_errors 只包含字段路径与规则名。校验器详细文案只进受限本机日志和同一轮模型
纠正消息，不上 Langfuse 或公开页面。

## 5. Tool 观测

每次工具调用记录：

~~~text
tool_name, phase, round, call_id
duration_ms, status, error_code
authorized, denial_reason
input_summary, output_summary
job_id, exit_code, timed_out
~~~

输入和输出只保留路径、选择器、大小、哈希、计数、状态和稳定错误码。源码、patch、完整
用户文本、完整 stdout/stderr、命令、环境变量和凭据不进入 Trace。

失败尝试仍要记录 attempt、max_attempts、handling、delay 和安全详情，便于解释“为什么
最终成功但 Trace 有一次 ERROR 子事件”。

## 6. 数据脱敏

默认 TRACE_CONTENT=false。统一 Masking 规则：

- contact、Authorization、Cookie、API Key、Token 和环境变量永不发送；
- 用户 Markdown 和 description 用哈希、字节数和类别摘要替代；
- 源码和 patch 用路径、增删行数和 SHA-256 替代；
- ToolMessage、模型原文、Prompt、Issue 正文和完整日志不上传；
- stdout/stderr 只保留截断且脱敏的尾部摘要；
- 公开 Trace Site 不投影 safe_details，数据库只保存受信最终 FailureSnapshot；
- development、staging、production 使用不同 environment 标签。

如果需要调试完整内容，必须由维护者为单次运行显式开启，并在结束后恢复默认；模型和
用户输入不能触发此开关。

## 7. 数据库摘要

agent_runs 保存：

~~~text
status, route, area, category, risk
provider, model, graph_version, prompt_versions, policy_version
base_sha, source_snapshot_ref, validation_result_ref
model_calls, tool_calls, input_tokens, output_tokens
total_tokens, estimated_cost, sandbox_duration_ms
validated_patch_sha256, pr_url, issue_url
error_code, error_message, failure
started_at, finished_at
~~~

这些字段是页面和调度的事实来源。Langfuse 的异步状态、索引延迟或缺失 observation
不能覆盖数据库结论。

## 8. 公开 Trace Site

展示站只显示脱敏运行摘要和允许的 observation 白名单：

- 阶段名称、状态、耗时和工具数量；
- 模型/工具调用计数、Token 计数和稳定错误码；
- 复现、验证、PR/Issue 的受信结果；
- 不展示原始反馈、contact、源码、patch、Prompt、密钥、完整错误正文或 safe_details。

运行结束后 Agent 推送 run_id/status；站点从 Supabase 读取摘要，从 Langfuse 异步补抓
观测。Langfuse 索引延迟时，详情页按需重抓；展示缺失不等于阶段没有执行。

## 9. 指标与评估

MVP 统计：

- 各路由和类别分布；
- Prompt Injection 隔离召回率和误报率；
- conversion probe 复现率、cannot_reproduce 比例；
- 一轮/两轮修复通过率；
- 工具授权拒绝率、Patch Policy 拒绝率；
- Provider/Sandbox 临时失败与重试次数；
- PR 创建率、Issue 创建率和重复率；
- 每阶段 P50/P95 耗时；
- 每次运行 Token、成本和 Sandbox 时间。

离线评估集至少覆盖后端 Bug、转换崩溃、语义不符、功能需求、前端 Bug、无关内容、信息
不足和 Prompt Injection。每条用例固定期望路由、类别、工具权限和可验证结果。

至少比较：

~~~text
route/category accuracy
schema compliance
injection recall / false-positive rate
reproduction success
validated repair rate
tool selection and authorization correctness
average token / cost / latency
~~~

模型、Prompt、Policy、Graph、工具契约或 Sandbox 镜像变更前，必须用同一评估集对比，
不能只凭一次成功运行升级。

## 10. 生命周期与对账

- Telemetry 发送失败 fail-open，只记录脱敏 warning；
- Controller 在运行结束、通知前显式 flush；
- Trace 和 Artifact 默认保留 14 天，数据库保留脱敏汇总；
- 定期对账 Provider usage、agent_runs 和 Langfuse totals；
- PR、Issue 和 Sandbox 通过 run_id、operation_id、job_id 和 patch hash 对账；
- 所有观测版本字段必须与实际运行时一致。
