# 失败归因与重试契约

本文是当前失败分类、捕获、有限重试和最终失败快照的唯一权威来源。状态转换见
architecture.md，工具错误见 tool-contracts.md，观测字段见 observability.md。

## 1. 目标与边界

一次失败必须回答五个问题：

1. 哪个阶段和节点失败；
2. 哪个组件和操作失败；
3. 稳定错误码和失败类别是什么；
4. 当前是第几次尝试、还允许几次；
5. 系统重试、修正、重排还是终止。

失败处理只负责描述事实和决定“相同输入的短传输是否重试”。它不接管：

- Gate 路由；
- Provider 的一次格式修正；
- ReAct 业务循环；
- 受信转换测试和 fallback；
- Sandbox 业务结果判定；
- stale_base 重排；
- PR/Issue 幂等恢复。

这些行为仍由各自的受信调用点负责，并把实际 handling 交给 FailureRecorder 记录。

## 2. 三个小抽象

~~~text
Adapter   外部异常 -> FailureCause
Policy    FailureCause + RetryContext -> RETRY / STOP
Recorder  旁路记录 FailureEvent，不改变业务结果
~~~

Adapter 不知道 Graph 节点；调用点补充 phase 和 node；Controller 负责最终状态和数据库
快照。这样 Provider、Sandbox 和 Repository 不需要依赖上层 Graph。

## 3. FailureCause

~~~text
FailureCause
  code
  kind
  component
  operation
  safe_details
~~~

失败类别只有五种：

| kind | 含义 | 默认处理 |
|---|---|---|
| transient | 临时传输、限流或上游可用性问题 | 满足幂等和预算时重试 |
| invalid | 已收到结果，但格式/普通输入规则不接受 | 返回可执行纠正或终止 |
| business | 请求成功，但业务目标未达成 | 由 Graph/验证器决定下一轮 |
| security | 权限、路径、补丁或运行时完整性被拒绝 | 立即终止 |
| permanent | 认证、配置、上下文或不可恢复外部错误 | 立即终止 |

常用稳定错误码：

~~~text
timeout, rate_limit, provider_unavailable
invalid_response, context_too_large, safety_refusal
source_access_denied, source_auth_error, source_request_invalid
sandbox_unavailable, sandbox_timeout, sandbox_invalid_response
sandbox_job_conflict, sandbox_security_rejected
tool_not_authorized, tool_precondition_failed
budget_exhausted, target_validation_failed, full_validation_failed
stale_base, publication_failed, issue_publication_failed
unexpected_error
~~~

错误码由受信表驱动映射。未登记异常不按文案猜测，统一为
unexpected_error/permanent，并只公开原始异常类型。

## 4. 失败位置

Adapter 只能填写 code、kind、component、operation 和安全详情。调用点生成：

~~~text
LocatedFailure
  cause
  phase
  node
~~~

phase 使用 gating、reproducing、repairing、validating、publishing 等稳定阶段；node
使用真实注册的节点名，如 repair_agent、validate_final、finish_publication。不得从模型
响应、异常文案或 HTTP 正文推断位置。

最终 FailureSnapshot 必须包含：

~~~text
code, kind, component, operation
phase, node
handling
attempt, max_attempts
safe_details
~~~

如果异常在内层 create_agent 中发生，Runtime 必须从内层 checkpoint 补齐 phase、node 和
本次 invoke 的用量增量，再交给外层 Finalizer。历史线程累计值不能在显式续跑时重复计入。

## 5. 安全详情

safe_details 只允许有限的标量键值：

- key 由受信代码按错误码固定；
- 最多 8 个键，字符串和整数有长度/范围上限；
- 不允许嵌套对象、数组和任意模型字段；
- 不记录用户原文、description、contact、完整 Prompt、模型响应、源码、patch、
  stdout/stderr、Header、Cookie、URL 查询串或 Secret；
- 未知异常只记录异常类名。

invalid_response 的结构错误使用脱敏字段路径摘要：

~~~text
schema_errors = edits.0.content:string_too_long
~~~

校验器详细文案只用于同一模型轮次的纠正和受限本机日志，不进入
safe_details、Langfuse、数据库或公开 Trace。

## 6. RetryPolicy

~~~text
RetryContext
  attempt
  max_attempts
  budget_remaining
  deadline_remaining_seconds
  operation_id
  idempotent
~~~

策略只有两个输出：

~~~text
RETRY  使用完全相同的受信输入再次传输
STOP   不再做相同输入重试
~~~

只有同时满足以下条件才 RETRY：

~~~text
kind == transient
operation_id 可幂等
attempt < max_attempts
预算仍有剩余
剩余 deadline 能容纳等待和下一次请求
~~~

格式修正、业务修订、受信 fallback、stale_base 和人工恢复不是 RetryPolicy 的职责。

## 7. 模型重试和备用 API

Repair Agent 每个模型轮次最多三次传输：

~~~text
attempt 1：主模型
等待 1 秒
attempt 2：主模型
等待 2 秒
attempt 3：备用模型
~~~

只有 timeout、连接异常、408、429 和 5xx 进入下一次 attempt。认证、权限、配置、上下文
超限、安全拒绝、无效 tool call、业务结果不满足要求和未知编程错误不重试。

备用 API 仍使用统一 openai_compatible Provider，不记录供应商切换事件，也不按模型名称
分支。第三次失败记录真实 attempt=3/max_attempts=3 后终止。

每次模型请求的超时由 REPRODUCTION_MODEL_TIMEOUT_SECONDS 控制，默认 180 秒，允许
30～300 秒；它是单次请求上限，不是整次 Agent 运行的 deadline。

Gate 的结构化响应格式修正是成功传输后的独立一次纠正，不占用上述传输 attempt。Repair
Agent 的工具参数错误通过 ToolMessage 返回同一循环，不切换备用 API。

## 8. Sandbox 重试

Repair Agent 的 run_sandbox 只通过 Middleware 做重试，底层 Agent Sandbox Client 关闭
嵌套重试：

- 连接异常、408、429、5xx：首次包含在内最多三次，等待 1 秒、2 秒；
- 使用相同 job_id、Idempotency-Key 和请求指纹；
- Worker 已保存的相同 job 直接返回结果，不重复执行容器；
- 401、409、非法请求、无效 200、Policy/安全拒绝不重试；
- Worker 仍串行执行，重试不能扩大 Sandbox 并发。

Sandbox 的业务测试失败、目标未收集、测试超时和全量回归失败不是传输失败，由 Graph
和验证器决定是否重新编辑或终止。

## 9. 捕获边界

### Adapter

Provider、Sandbox、Repository、Publisher 和本地 Policy 把已知异常转换为稳定领域错误。
不捕获 CancelledError、KeyboardInterrupt、SystemExit 等控制信号。

### Graph 调用点

调用点补 phase/node，记录实际 handling，并把不能在本节点恢复的错误继续向上抛出。
不能在各节点复制一套最终数据库状态机。

### Controller 单次运行

Controller 捕获所有 AgentError 和普通 Exception。普通未知异常转为
unexpected_error/permanent/runtime，只保留异常类名，并先从 checkpoint 补齐位置和用量
增量，再调用 Finalizer。

### Scheduler

Scheduler 不让一个普通运行异常杀死常驻进程；异常运行终结后继续领取下一项。取消和
进程控制信号继续向上交给 systemd，不被当作业务失败。

## 10. 最终状态和恢复

- 成功运行的最终 failure 为空；
- 最终失败写入 agent_runs.failure，同时保留旧 error_code/error_message 字段；
- Recorder 是 fail-open 观察者，写日志或 Langfuse 失败不能改变主流程；
- Finalizer 失败不能静默吞掉，必须让运行进入可诊断的运维错误；
- budget_exhausted 不由 Scheduler 自动重开，维护者提高预算后可用原 run_id 显式续跑；
- stale_base 仍按既有规则最多重排一次，不属于短传输 RETRY；
- 发布失败只恢复发布 checkpoint，不重新执行模型、Sandbox 或最终验证；
- 安全、认证、配置和未知永久错误转人工或安全终态，不通过重试绕过。

## 11. 验收

- 临时模型失败的 attempt 顺序为主、主、备，退避为 1 秒、2 秒；
- 认证和越权错误只调用一次；
- Sandbox 重试复用同一 job_id，成功结果不重复执行；
- 未登记异常不会退出 Scheduler，FailureSnapshot 仍有完整位置；
- invalid_response 记录 schema_errors 路径而不泄露校验器文案；
- 显式续跑只累计本次增量；
- 成功运行无最终 failure，失败运行可通过 run_id 定位和恢复。
