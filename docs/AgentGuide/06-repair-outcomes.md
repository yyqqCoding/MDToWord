# 06 · 复现与修复阶段的状态收场设计

本章说明 Repair Agent（修复代理）结束内层工具循环后，外层流程如何解释结果并推进业务状态。
核心原则是：内层只能提交受信结果引用，外层根据引用和状态条件收场；模型的自然语言不会
直接决定反馈终态。

本文首次引入的术语如下：收场节点（finish node）是把某一阶段结果写回业务状态的外层节点；
受信结果引用（result reference）是指向已校验测试、补丁或报告制品的引用；反馈终态是不会再
被普通调度自动推进的业务状态；运行终态是某个 AgentRun（处理运行）不再由 Scheduler（调度器）
恢复的状态；外层流程图（Graph）负责按这些受信结果选择下一阶段；隔离执行环境（Sandbox）中的
受信执行任务（Job）由本地代码固定测试内容；代码合并请求（PR）和问题或需求单（Issue）是
验证或发布阶段可能产生的协作对象。

## 1. 为什么需要单独的收场层

Repair Agent 的完成工具只能说明内层循环满足某个阶段条件，例如目标测试在当前 Job 中通过。
它不能直接完成以下业务动作：

- 修改 `feedback` 的业务状态；
- 计算最终验证结论；
- 创建 PR 或 Issue；
- 结束 AgentRun 并结算用量；
- 将模型文字解释为成功证据。

因此，外层 Graph 在内层结束后先进入固定收场节点，再决定下一条边：

```text
repair_agent
  -> 读取受信状态投影和制品存储（Artifact）引用
  -> finish_reproduction / finish_agent_blocked
  -> 进入修复、终态或独立验证
```

收场节点同时更新两个账本：

| 账本 | 表达的事实 | 典型状态 |
|---|---|---|
| `feedback` | 用户反馈当前处于什么业务阶段 | `reproducing`、`repairing`、`cannot_reproduce`、`failed` |
| `agent_runs` | 本次运行的执行阶段、结果和用量 | `REPRODUCING`、`REPAIRING`、`VALIDATING`、`COMPLETED` |

每次写入前都要重新确认 claim token（认领令牌，即当前运行拥有反馈更新权的随机标识）。如果
令牌已失效或状态已经被其他运行推进，当前收场不能覆盖已有结果。

## 2. 复现阶段的结果分流

### 2.1 复现成功：进入修复

当基线测试在固定源码版本上按预期失败，受信结果会标记为 `REPRODUCED`（已复现）。
`finish_reproduction` 完成以下动作：

1. 将反馈从 `reproducing` 推进到 `repairing`；
2. 将 AgentRun 从复现阶段推进到修复阶段；
3. 保存测试补丁、目标测试选择器和复现报告引用；
4. 将下一次内层调用的 `phase` 固定为 `repairing`。

阶段切换由外层状态决定，模型不能通过完成消息自行打开修复工具。只有复现报告已经被受信
结果分类器确认，修复补丁工具才有合法前置条件。

### 2.2 复现失败：不能伪造 Bug

复现结果不是“测试失败就算成功”，而是必须匹配预期失败类型、目标测试和容器完整性。常见
结果的收场如下：

| 受信复现结果 | 反馈状态 | 处理原因 |
|---|---|---|
| `REPRODUCED` | `repairing` | 获得了可重复的基线失败证据 |
| `NOT_REPRODUCED` | `cannot_reproduce` | 目标问题未被稳定复现 |
| `INVALID_TEST` | `cannot_reproduce` | 测试不符合契约，不能作为修复依据 |
| `BASELINE_REGRESSION` | `cannot_reproduce` | 基线或测试基座异常，证据不成立 |
| `SECURITY_REJECTED` | `security_rejected` | 工作区发生未授权改动，立即终止 |

`NOT_REPRODUCED`、`INVALID_TEST` 和 `BASELINE_REGRESSION` 在剩余复现轮次内可以回到内层修正；
复现最多两轮。达到上限仍未产生 `REPRODUCED` 时，收场为 `cannot_reproduce`（无法复现），
交给维护者判断，而不是根据用户描述直接修改代码。

## 3. Agent 主动阻塞的处理

`report_blocked`（报告阻塞工具）只能提交固定原因枚举和脱敏摘要。外层根据原因和受信计数处理：

| 阻塞原因 | 前置条件 | 反馈结果 |
|---|---|---|
| `cannot_reproduce` | 已耗尽复现轮次 | `cannot_reproduce` |
| `needs_human` | 至少尝试过一轮复现或修复 | `needs_human` |
| `external_dependency_required` | 模型不能自行证明；该原因由工具拒绝 | 不产生状态迁移 |
| `budget_exhausted` | 只能由受信预算计数确认 | 进入预算耗尽收场 |

阻塞原因不是自由文本错误码。缺少受信前置条件时，工具调用本身失败；越权、安全和永久配置
错误则不要求模型继续解释，直接由外层失败处理。

## 4. 修复阶段的结果分流

### 4.1 修复成功：进入独立终验

当目标测试在修复 Job 中通过，`complete_repair`（完成修复工具）才可以成功。外层
`finish_repair_success` 将：

- 反馈从 `repairing` 推进到 `validating`；
- AgentRun 从修复阶段推进到验证阶段；
- 保存修复补丁、目标测试结果、改动文件和修复摘要引用；
- 保留本次运行的模型、工具和 Sandbox 用量。

这一步不创建 PR，也不把目标测试通过当作最终成功。下一步必须由外层 `validate_final` 节点
在全新容器中重新验证。

### 4.2 修复失败：按受信结果收场

修复阶段最多两轮。每轮目标测试结果经过本地分类器处理，不解析模型自然语言或未经截断的
标准输出：

| 修复结果 | 反馈状态 | 说明 |
|---|---|---|
| `TARGET_PASSED` | `validating` | 候选修复进入独立终验 |
| `TARGET_FAILED` | `failed` | 达到轮次上限仍未通过 |
| `INVALID_RESULT` | `failed` | 没有得到可接受的目标测试结果 |
| `NEEDS_HUMAN` | `needs_human` | 需要维护者判断或超出自动范围 |
| `SECURITY_REJECTED` | `security_rejected` | 工作区或补丁越权，立即终止 |

如果目标测试失败且尚有修复轮次，外层可以回到修复阶段；修复轮次用尽后进入明确终态。测试
失败和安全拒绝不能混为同一种错误：前者是业务结果不满足，后者是执行边界被破坏。

## 5. 预算耗尽是独立收场路径

模型调用、工具调用或 Sandbox 总时长达到上限时，系统生成稳定的
`budget_exhausted`（预算耗尽）结果，由 `finish_budget_exhausted` 处理：

```text
受信计数达到上限
  -> 停止当前 Agent/Graph 继续执行
  -> feedback: repairing/validating -> failed
  -> agent_runs: -> BUDGET_EXHAUSTED
  -> 保存累计用量和失败摘要
```

预算耗尽不同于“目标测试失败”：它表示系统主动刹车，不能由 Scheduler 自动重新领取同一条
反馈。维护者检查情况并提高预算后，可以使用原 `run_id` 显式续跑，历史计数不能重置；具体
恢复策略见 [09 · 失败处理与恢复](09-failures-and-recovery.md)。

## 6. 状态地图

```text
reproducing
  ├─ REPRODUCED ----------------------> repairing
  ├─ NOT_REPRODUCED / INVALID_TEST ----> cannot_reproduce
  ├─ report_blocked(needs_human) ------> needs_human
  └─ SECURITY_REJECTED ----------------> security_rejected

repairing
  ├─ TARGET_PASSED --------------------> validating
  ├─ TARGET_FAILED / INVALID_RESULT ----> failed
  ├─ report_blocked(needs_human) ------> needs_human
  └─ SECURITY_REJECTED ----------------> security_rejected

repairing / validating
  └─ budget exhausted ------------------> failed + BUDGET_EXHAUSTED(run)
```

复现、修复和运行终态的命名层次不同：反馈可以处于 `cannot_reproduce`、`needs_human` 或
`security_rejected`；AgentRun 通常以 `COMPLETED`、`FAILED`、`BUDGET_EXHAUSTED` 或
`SECURITY_REJECTED` 结束。页面展示和调度应读取各自的事实来源，不能用一个字段推断另一个。

## 7. 设计取舍

| 设计 | 解决的问题 | 代价 |
|---|---|---|
| 内层结果与外层收场分离 | 模型不能直接写业务状态或发布结果 | 需要维护结果引用和状态转换 |
| 复现最多两轮、修复最多两轮 | 避免同一问题无限试错 | 部分复杂问题需人工处理 |
| 安全拒绝单独终止 | 防止将越权行为当作普通测试失败 | 一旦触发不能自动挽回 |
| 预算单独收场 | 保护成本和运行资源 | 续跑需要维护者显式决策 |

## 8. 实现关联

| 能力 | 主要实现 |
|---|---|
| 内层结果投影 | `agent/repair_agent/runtime.py` |
| 复现收场与状态迁移 | `agent/graph.py` 的 `finish_reproduction`、`finish_agent_blocked` |
| 修复收场 | `agent/graph.py` 的 `finish_repair_success`、`finish_repair_failure` |
| 预算收场 | `agent/graph.py` 的 `finish_budget_exhausted` |
| 反馈/运行状态约束 | `agent/domain/enums.py`、`agent/domain/transitions.py` |

修复成功只能说明候选补丁通过了内层目标测试。下一章说明外层如何用独立容器建立最终证据。
