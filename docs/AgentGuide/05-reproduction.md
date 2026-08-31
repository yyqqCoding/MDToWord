# 复现：先证明问题存在

## 1. 复现的目的

模型不能仅凭用户描述就生成修复。复现阶段要在固定的 main 快照上产生一个离线、确定、
可重复的测试，并证明基线确实失败。失败证据是后续修复的起点。

## 2. conversion probe

Controller 先对原始 Markdown 运行受信转换探测：

- 若转换直接抛错，Controller 根据安全错误摘要固定生成转换回归测试；
- 若转换成功，Repair Agent 根据用户的现象和期望设计语义测试；
- 若问题是主观视觉偏好且没有可验证 Oracle，结束为 cannot_reproduce 或 needs_human。

这样把“转换程序崩溃”和“结果不符合预期”分开，避免模型为两类问题编造同一套测试。

## 3. Agent 如何工作

复现阶段只开放：

~~~text
search_source
read_source_file
submit_test_edits
run_sandbox
complete_reproduction
report_blocked
~~~

典型流程：

1. 先 search 相关函数和已有测试；
2. 按需 read 小范围源码；
3. 用结构化 Edit 追加一个最小回归测试；
4. run_sandbox 在基线快照应用 test.patch；
5. 读取 JUnit 和脱敏结果；
6. 失败符合预期时调用 complete_reproduction；
7. 测试通过、收集失败或权限/预算耗尽时修正或 report_blocked。

模型可以自主选择查询顺序，但不能选择 pytest 命令、工作目录、白名单或测试基础设施。

## 4. 什么算“复现成功”

成功必须同时满足：

- 目标测试被收集并执行；
- 后端转换错误对应 unexpected_conversion_error，断言问题对应 assertion；
- 基线结果与 expected_failure_kind 一致；
- 没有 ImportError、SyntaxError、fixture 缺失、超时或测试本身的错误；
- workspace diff 只有预期 test.patch。

测试直接通过不证明“用户说错了”；可能是测试没有命中问题。Agent 应调整测试，仍不能
证明时结束为 cannot_reproduce。

## 5. 测试写入规则

测试统一追加到 backend/tests/test_feedback_regressions.py，固件放在
backend/tests/fixtures/feedback/。编辑必须能生成可审查 patch，不能删除或弱化已有测试，
不能读取环境 Secret、联网或调用非确定性服务。

转换成功但结果错误的测试可使用受信 DOCX 断言，如固定的文本、样式、公式节点或部件
数量。模型不能提供任意 XPath、Python 回调或动态命令。

## 6. 复现失败如何处理

| 情况 | 结果 |
|---|---|
| 测试参数错误 | ToolMessage 告知 required_action，同一 run 修正 |
| Sandbox 暂时不可用 | 同一 job_id 按三次短重试 |
| 测试补丁越权 | security_rejected，不重试 |
| 基线无法证明目标失败 | cannot_reproduce |
| 证据不足或需求主观 | needs_human |
| 已确认失败 | 进入 repairing，开放修复工具 |

复现不是“生成计划”的终点，而是给修复 Agent 提供可信失败证据和最小回归测试。
