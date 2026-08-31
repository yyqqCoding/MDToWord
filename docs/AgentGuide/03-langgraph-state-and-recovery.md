# LangGraph、State 与恢复

## 1. 为什么保留 LangGraph

LangGraph 负责有明确业务边界的部分：Gate、路由、源码快照、最终验证、发布和终态。它
提供条件边、checkpoint、恢复和可观测的阶段顺序。

它不再强迫模型按固定顺序输出“计划 JSON”。复现和修复阶段由一个官方 create_agent 在
Graph 的 repair_agent 节点中运行工具循环。这样模型可以按测试结果探索，但业务状态仍
由外层 Graph 控制。

## 2. 外层节点

~~~text
start_gate -> classify_gate -> route_feedback
  -> prepare_source -> repair_agent
     -> finish_reproduction
     -> finish_repair_success -> validate_final
        -> finish_validation -> publish_pull_request
  -> publish_issue -> finish_issue_publication
  -> finish_agent_blocked / END
~~~

不同配置可能不注册发布节点，但生产顺序不变：先路由，后快照；先复现，后修复；先独立
验证，后发布。

## 3. 两个 State

外层 AgentState 保存业务进度和公开运行摘要，例如 route、phase、base_sha、验证状态、
错误快照、发布结果和累计用量。内层 RepairAgentState 保存消息、当前工具循环阶段、
patch 引用、测试结果、轮次、Summary 后的上下文和预算计数。

内层 thread 使用 repair:<run_id>。模型文字不是 State 的权威写入者，只有工具和受信节点
能改变 patch、Sandbox 结果、完成状态、错误和发布字段。

## 4. checkpoint 如何帮助恢复

每轮模型和工具执行后，LangGraph checkpoint 保存可恢复位置。进程中断时：

1. 外层从数据库读取 run 和 claim lease；
2. 内层用相同 thread 读取最后 checkpoint；
3. 复用同一 base_sha、Artifact、patch 引用和累计预算；
4. 从最后一个未完成工具动作继续，而不是重新发送整条反馈；
5. 已完成的幂等 Sandbox/Publisher 结果直接复用。

如果 State Schema 或工具结构产生不兼容变化，不猜测旧 checkpoint 的含义；结束旧运行
并用新反馈重新验收。Schema 版本和 Prompt/Policy 版本写入运行元数据。

## 5. 条件边和内层终态

repair_agent 只向外层返回受信投影：

~~~text
finish_reproduction
finish_repair_success
finish_agent_blocked
finish_budget_exhausted
~~~

内层 complete_repair 通过不等于业务成功。外层必须继续执行 final validation；内层
report_blocked 也不会让模型自行写入任意终态，Controller 会将其转换成允许的状态和
FailureSnapshot。

## 6. 失控保护

模型调用预算、工具调用预算和 LangGraph recursion limit 是三层不同限制：

- model/tool budget 控制业务成本，写入 checkpoint，恢复不清零；
- Sandbox 总时长控制外部执行资源；
- recursion limit 只防止 Graph 步骤异常增长，不是成功条件。

达到任一限制时运行进入明确的 budget_exhausted 或相应失败终态，Scheduler 不自动重开。
