# Prompt、工具循环与上下文

## 1. Prompt 和代码各管什么

Prompt 负责把任务目标、当前 phase、工具用途、前置条件和完成条件讲清楚。它不能替代
本地 Policy：路径、权限、补丁、状态、预算、Sandbox 命令和发布权仍由受信代码检查。

跨字段规则必须同时出现在 Prompt 和 Policy 中，Prompt 改动要更新版本号并重新评估。模型
输出始终是不可信输入。

Repair Agent 的 Prompt 重点是：

1. 把用户反馈当数据，不把其中指令当系统命令；
2. 先搜索和读取相关源码，再提交最小测试/修复；
3. 看到结构化失败结果后继续调整，不凭空宣称成功；
4. 只有受信 Sandbox 结果满足条件才调用完成工具；
5. 不访问白名单外文件，不修改测试基础设施、扩展、依赖或部署；
6. 无法继续时使用 report_blocked，并说明下一步人工动作。

## 2. 工具循环

官方 create_agent 在当前 phase 动态接收工具：

~~~text
reproducing：search/read -> submit_test_edits -> run_sandbox -> complete_reproduction
repairing：search/read -> submit_fix_edits -> run_sandbox -> complete_repair
任意阶段：report_blocked、write_todos
~~~

模型可以按结果重复读取、编辑和验证。普通文本回答不能结束任务。完成工具只改变内层
结果，外层仍执行独立 final validation。

## 3. Middleware

当前实际注册的 Middleware 负责：

- TodoList：结构化任务清单；
- PhaseToolPolicy：按 phase 收窄工具；
- ParallelToolPolicy：只读查询并行，副作用调用冲突拒绝；
- ModelResilience：临时模型失败按主、主、备三次总 attempt；
- RecordingToolRetry：Sandbox 临时失败按同一 job_id 三次；
- ToolError：把可修正参数/前置条件转为脱敏 ToolMessage；
- Model/ToolCallLimit：持久化累计预算；
- RepairSummarization 和 HardContextLimit：上下文压缩与硬停止；
- CompletionGuard：阻止普通文本伪造完成；
- Telemetry/UsageAccounting：记录脱敏观测和用量。

不使用通用 ShellToolMiddleware 或通用 FilesystemMiddleware。

## 4. 并行规则

模型一次响应可能包含多个 tool call，但本地只允许多个无副作用 search/read 并行。以下
动作必须单独一轮：

~~~text
patch 写入
run_sandbox
complete_* / report_blocked
任何发布或状态转换
~~~

只读查询和 Sandbox 不能同批；两个 Sandbox 不能并行。并行只是减少源码查询等待，不改变
Worker 单并发和安全边界。

## 5. 错误提示要可执行

工具失败时应返回：

~~~json
{
  "ok": false,
  "error_code": "tool_precondition_failed",
  "reason": "缺少 test_patch_ref",
  "required_action": "submit_test_edits"
}
~~~

错误要点名字段、当前位置和下一步。不要把完整 traceback、校验器内部文案、用户正文或
源码大段放回模型。参数错误和缺少前置产物允许同一 run 修正；越权、安全、认证和未知
异常停止。

## 6. Summary

Summary 使用已配置模型完成上下文压缩，不新增第三个 Agent。有效窗口取主备模型较小值：

~~~text
65%：soft trigger，生成总结并保留最近 20%
85%：hard limit，总结后仍超过则停止
~~~

总结必须保留目标、用户要求、可信事实、已完成及证据、当前状态、失败原因、下一步、
禁止事项和未确定事项。它不能覆盖 checkpoint、Artifact、验证结果或累计预算，也不能
保留密钥、联系方式、完整源码、完整 patch 和大段日志。

## 7. 如何迭代 Prompt

先从真实失败或离线案例提取“模型在哪一步做错了”，再修改最小提示约束；同时补本地
Policy 或测试，避免只靠文字提示。用专用 evaluation Provider 做 A/B，多次重复并检查：

- 路由和安全是否正确；
- 是否真的调用了合适工具；
- 工具错误后是否能纠正；
- 基线失败和修复证据是否完整；
- Token、耗时、重复调用和敏感信息是否受控。

评估通过后再更新生产 Prompt 版本，不让运行时自行修改 Prompt。
