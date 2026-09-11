# 09 · 失败处理与恢复设计

本章说明系统如何区分临时故障、业务不通过、安全拒绝和永久错误，并在进程重启后恢复原运行。
失败处理不是在主流程之外追加的异常分支，而是与每个受信调用点、状态迁移和预算一起设计的
收敛机制。

本文首次引入的术语如下：失败原因（FailureCause）是外部异常转换后的稳定事实；失败策略
（RetryPolicy）决定相同输入是否允许短传输重试；检查点（checkpoint）保存流程进度；失败快照
（FailureSnapshot）是写入运行记录的最终脱敏失败事实；终结器（Finalizer）负责在无法继续时
合并用量并关闭运行；修复代理（Repair Agent）是执行内层模型工具循环的组件；控制器（Controller）
负责协调运行收场；调度器（Scheduler）负责恢复活动运行；运行记录（AgentRun）保存一次处理实例；
验证器（Validator）负责解释受信测试结果；外部服务适配器（Adapter）负责转换外部异常；供应商适配器
（Provider）、数据访问适配器（Repository）和发布适配器（Publisher）分别连接模型、数据库和 GitHub；
记录器（Recorder）负责旁路记录失败尝试；定位失败记录（LocatedFailure）补充失败发生的阶段和节点；
失败事件（FailureEvent）是发送给观测系统的单次失败记录；模型计量单位（Token）用于记录用量。

## 1. 失败处理要回答的问题

每次失败至少需要确定：

1. 发生在哪个阶段和节点；
2. 哪个组件和操作失败；
3. 稳定错误码和失败类别是什么；
4. 当前是第几次尝试、是否还有预算；
5. 应该重试、修正、重新排队还是终止。

失败处理只负责描述事实和判断“相同受信输入能否再次传输”。它不替代反馈分类器（Gate）路由、
观察—行动循环（ReAct）业务循环、隔离执行环境（Sandbox）测试判定、`stale_base` 重新排队或
发布幂等恢复。

## 2. 失败事实的三层结构

系统将失败处理拆成三个小抽象：

```text
Adapter（外部适配器）
  -> 将外部异常转换为 FailureCause
调用点
  -> 补充 phase 和 node，形成 LocatedFailure
Recorder（记录器）
  -> 旁路记录 FailureEvent，不改变业务结果
```

Adapter 不需要知道外层流程图（Graph）的节点；Graph 调用点补充实际阶段和节点；Controller 最终将
不可恢复错误写成 FailureSnapshot。这样可以避免 Provider、Sandbox、Repository 和 Publisher
各自复制一套状态机。

## 3. 五类失败及默认处理

稳定的 `FailureKind`（失败类别）只有五种：

| 类别 | 含义 | 默认处理 |
|---|---|---|
| `transient`（临时故障） | 网络、限流或上游短暂不可用 | 满足幂等、预算和次数条件时重试 |
| `invalid`（结果无效） | 已收到结果，但格式或普通输入规则不接受 | 返回纠正动作或终止，不做相同请求重试 |
| `business`（业务未达成） | 请求成功，但测试或业务目标未满足 | 由 Graph/Validator 决定修正、收场或失败 |
| `security`（安全拒绝） | 权限、路径、补丁或运行完整性越界 | 立即终止，不给模型解释放行 |
| `permanent`（永久错误） | 认证、配置、状态或不可恢复外部错误 | 不重试，转人工或运维处理 |

错误码到类别由受信映射表固定。未登记的异常统一归为 `unexpected_error`/`permanent`，只
记录异常类型，不根据异常文案猜测是否可重试。

## 4. 失败位置与脱敏详情

失败位置使用稳定的 `phase`（阶段）和 `node`（Graph 节点），例如 `repairing` + `repair_agent`
或 `validating` + `validate_final`。位置由调用点提供，不能从模型响应、异常文本或 HTTP 响应
正文推断。

`FailureSnapshot` 至少包含：

```text
code, kind, component, operation
phase, node
handling
attempt, max_attempts
safe_details
```

`safe_details`（安全详情）只允许受信代码定义的有限标量字段，最多八个键，并具有长度和数值
范围限制。不能写入用户原文、联系方式、完整 Prompt、模型响应、源码、补丁、标准输出/错误输出、
请求头、Cookie、查询串或 Secret（秘密凭据）。`invalid_response` 只保留类似
`edits.0.content:string_too_long` 的脱敏字段路径，详细校验文案只用于当前模型轮次纠正。

## 5. RetryPolicy：只重试相同输入的短传输

RetryPolicy 接收 `RetryContext`（重试上下文）：

```text
attempt, max_attempts
budget_remaining
deadline_remaining_seconds
operation_id
idempotent
```

它只有两个输出：`RETRY`（重试）或 `STOP`（停止）。同时满足以下条件才能重试：

```text
FailureKind == transient
  ∧ operation_id 对应的操作可幂等
  ∧ attempt < max_attempts
  ∧ 仍有预算
  ∧ 剩余 deadline 足以容纳退避和下一次请求
```

格式修正、业务修订、受信 fallback、`stale_base` 重排和人工恢复不是 RetryPolicy 的职责，
它们由对应的受信调用点决定。

## 6. 模型与 Sandbox 的具体重试

### 6.1 模型传输重试

一个修复代理（Repair Agent）模型轮次最多三次传输尝试：

```text
第 1 次：主模型
等待 1 秒
第 2 次：主模型
等待 2 秒
第 3 次：备用模型
```

只有超时、连接异常、HTTP 408、429 或 5xx 进入下一次尝试。认证、权限、配置、上下文超限、
安全拒绝、无效工具调用、业务结果不满足和未知编程异常不重试。Gate 的结构化格式修正属于
成功传输后的独立纠正，不与 Repair Agent 的传输次数混合。

### 6.2 Sandbox 传输重试

`run_sandbox` 的连接异常、408、429 和 5xx 最多三次，等待 1 秒、2 秒，并复用同一个
`job_id`、`Idempotency-Key`（幂等请求键）和请求指纹。Worker 已保存相同任务结果时直接返回，
不重复执行容器。

401、409、非法请求、无效成功响应和安全拒绝不重试。Sandbox 的业务测试失败、目标测试未
收集、测试超时和全量回归失败是业务结果，由 Graph/Validator 决定是否编辑或终止，而不是
当作传输故障反复发送。

## 7. 长流程恢复：外层与内层检查点

短传输重试不能解决进程被杀、主机重启或断电。长流程恢复使用两套职责不同但位于同一私有
PostgreSQL 的检查点：

| 层 | 保存内容 | 恢复方式 |
|---|---|---|
| 外层 LangGraph | 业务阶段、状态、制品引用和累计用量 | Scheduler 查找活动 AgentRun，从外层节点继续 |
| 内层 `create_agent` | 消息、工具状态、补丁引用和内层计数 | 使用 `repair:<run_id>` 原线程继续工具循环 |

恢复时 Scheduler 先执行 `find_resumable`（查找可恢复运行），再领取新反馈。显式续跑复用原
`run_id`、`base_sha`、候选补丁、检查点和累计预算，不创建新的反馈认领身份。

外层节点尚未返回时，外层累计值可能没有包含本次内层调用的全部增量。Controller 恢复时从
内层检查点补齐本次增量，并以单调方式合并模型调用、工具调用、Token 和 Sandbox 时间，避免
重复计数或丢失成本。

## 8. 防止无限恢复：最终失败终结

如果某个运行每次恢复后仍在同一节点抛错，Scheduler 不能无限重启它。Controller 捕获
代理运行错误（`AgentError`）和普通异常后，先补齐阶段、节点和用量，再由 Finalizer 写入最终
`FailureSnapshot`，并将 AgentRun 置为明确终态。

```text
运行异常
  -> 转换稳定错误码和 FailureKind
  -> 从外层/内层 checkpoint 补齐位置和用量
  -> 写入 agent_runs.failure
  -> 运行进入 FAILED / SECURITY_REJECTED / BUDGET_EXHAUSTED
  -> Scheduler 继续处理其他任务
```

未知普通异常归为永久运行错误，但不能让常驻 Scheduler 进程退出。取消、KeyboardInterrupt、
SystemExit 等进程控制信号不应被吞掉或伪装成业务失败。

## 9. 特殊恢复规则

部分失败可以由维护者显式恢复，但不能由 Scheduler 自动重开：

| 情况 | 恢复方式 | 不能自动恢复的原因 |
|---|---|---|
| PR/Issue 发布失败 | 只恢复发布检查点，复用验证结果和幂等标识 | 验证已完成，重跑模型和容器没有收益 |
| `stale_base` | 最多重新排队一次，第二次转人工 | 前提版本已经改变，必须重新建立证据 |
| `budget_exhausted` | 维护者提高预算后使用原 `run_id` 显式续跑 | 自动重开会无界消耗模型和容器资源 |
| 认证、权限、配置或未知永久错误 | 转运维/人工处理 | 重试不能修复配置或绕过安全边界 |

恢复必须复用已有证据和历史计数，不能通过创建新 run 来绕过预算、状态或失败记录。

## 10. 预算耗尽

运行预算分为模型调用、工具调用和 Sandbox 总时长。达到任一上限时，内层或外层产生稳定的
`BudgetExceededError`（预算耗尽错误），并由 `finish_budget_exhausted` 收场：

- 反馈从 `repairing` 或 `validating` 进入 `failed`；
- AgentRun 进入 `BUDGET_EXHAUSTED`；
- 保存模型/工具/Token/Sandbox 的累计用量；
- Scheduler 不自动重新领取或重开该运行。

预算耗尽是主动刹车，不等同于目标测试失败。维护者可根据已有证据决定是否提高预算并显式续跑。

## 11. FailureRecorder 与可观测性

FailureRecorder（失败记录器）是旁路的 fail-open（记录失败不阻断主流程）观察者。每次失败
尝试记录错误码、类别、阶段、节点、尝试次数、处理方式、退避和安全详情，但不记录敏感正文。

如果日志或 Langfuse（可观测后端）不可用，记录器只写受限本机 warning，不能阻断业务收场。
反之，Finalizer 写入业务失败快照失败时不能静默吞掉，必须进入可诊断的运维错误。

## 12. 设计取舍

| 设计 | 解决的问题 | 代价 |
|---|---|---|
| 错误码映射而非文案猜测 | 保证重试决策稳定 | 新错误必须登记映射 |
| RetryPolicy 只处理短传输 | 避免把业务失败误当网络故障 | 各业务阶段仍需维护自己的收场规则 |
| 外层/内层双检查点 | 进程重启后保留完整上下文 | 用量合并和线程关联更复杂 |
| Finalizer 明确终止 | 防止 Scheduler 无限恢复同一节点 | 未知异常会更快进入人工处理 |
| 发布/预算显式恢复 | 保留人工判断和成本边界 | 运维需要检查 run_id 和已有证据 |

## 13. 实现关联

| 能力 | 主要实现 |
|---|---|
| 失败类别、策略和记录器 | `agent/domain/failures.py` |
| 模型/Sandbox 重试中间件 | `agent/repair_agent/middleware.py` |
| Controller 异常终结与恢复 | `agent/controller.py` |
| 外层/内层检查点 | `agent/checkpoint.py`、`agent/repair_agent/runtime.py` |
| 预算收场 | `agent/graph.py` 的 `finish_budget_exhausted` |

最后一章从横切角度总结权限、沙箱和可观测设计，说明这些机制如何共同约束整个生命周期。
