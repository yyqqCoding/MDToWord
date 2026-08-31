# ReAct 修复工具循环契约

本文只定义内层 create_agent 的工具循环。外层阶段、状态和发布条件见
agent-runtime.md；工具参数和补丁结构见 tool-contracts.md。

## 1. 设计目标

Repair Agent 需要一定的探索能力：它可能先搜索符号，再读取不同文件，运行测试后根据
结果调整补丁。因此不能把每一步都写成固定模型节点。

但自主性只存在于“当前阶段选择下一项已授权动作”这一层。文件白名单、工具权限、补丁
规则、Sandbox 命令、预算和完成条件仍由本地代码控制。

## 2. 工具集合

| 工具 | 作用 | 可用阶段 |
|---|---|---|
| search_source | 在白名单源码中有界搜索 | reproducing / repairing |
| read_source_file | 读取白名单文件的指定行 | reproducing / repairing |
| submit_test_edits | 提交回归测试补丁 | reproducing |
| run_sandbox | 执行当前受信 Sandbox Job | patch 待验证时 |
| submit_fix_edits | 提交后端修复补丁 | repairing |
| complete_reproduction | 确认基线复现 | reproducing |
| complete_repair | 确认候选修复 | repairing |
| report_blocked | 报告无法继续或需要人工 | 任意内层阶段 |
| write_todos | 管理当前任务 Todo | 任意内层阶段 |

不注册 Shell、Filesystem、网络、GitHub、数据库、任意命令或发布工具。

## 3. 工具可见性与二次校验

工具可见性由两层 Policy 共同决定：

1. 阶段 Policy：复现期不开放修复提交，修复期不开放测试提交；
2. 子状态 Policy：没有 patch 时开放编辑，patch 待验证时只开放单个 Sandbox，通过后只
   开放完成工具。

工具即使未被模型看到，执行函数内部也必须再次校验阶段、路径、patch、预算和当前
checkpoint。Middleware 不是唯一安全边界。

缺少受信前置产物时，已登记工具返回 tool_precondition_failed，并带 required_action；
模型可以在同一循环完成前置动作。未登记工具、跨阶段工具或越权路径仍是安全拒绝并终止。

## 4. 并行规则

模型 API 可以在一次响应中返回多个 tool call，但本地只允许无副作用的只读查询并行：

| 同批调用 | 结果 |
|---|---|
| 多个 search/read | 并行执行 |
| 只读 + Sandbox | 拒绝该批次，Sandbox 单独运行 |
| 多个 Sandbox | 拒绝，要求分轮 |
| patch 写入 + 任意其他工具 | 拒绝 |
| 完成/阻塞工具 + 任意其他工具 | 拒绝 |

生产 Worker 仍通过全局锁串行执行 Sandbox。并行只减少源码查询延迟，不扩大服务器并发。

## 5. Middleware 职责

生产内层 Agent 注册以下 Middleware：

1. TodoListMiddleware：让模型维护结构化任务清单；
2. RepairSummarizationMiddleware：上下文达到有效窗口 65% 时总结，保留最近 20%；
3. HardContextLimitMiddleware：总结后仍达到 85% 时停止，避免截断后猜测；
4. ModelResilienceMiddleware：临时模型错误按主、主、备执行三次总 attempt；
5. PhaseToolPolicyMiddleware：收窄当前阶段可见工具并检查调用；
6. ParallelToolPolicyMiddleware：拒绝副作用冲突的同批调用；
7. RecordingToolRetryMiddleware：只为幂等 Sandbox 临时传输失败提供三次总 attempt；
8. ToolErrorMiddleware：把可恢复参数/前置条件问题转换为脱敏 ToolMessage；
9. ModelCallLimitMiddleware 和 ToolCallLimitMiddleware：执行持久化总预算；
10. CompletionGuardMiddleware：禁止模型用普通文本绕过完成工具。

不使用 ShellToolMiddleware 或通用 FilesystemMiddleware。

## 6. 模型与 Sandbox 重试

单个 Repair Agent 模型轮次的传输重试为：

~~~text
attempt 1：主模型
等待 1 秒
attempt 2：主模型
等待 2 秒
attempt 3：备用模型
~~~

只有 timeout、连接异常、408、429 和 5xx 进入下一次 attempt。认证、权限、配置、上下文
超限、安全拒绝、无效 tool call、业务结果不满足要求和未知编程异常不重试。

Sandbox 的连接异常、408、429 和 5xx 也最多三次，使用同一 job_id、请求指纹和幂等键。
Sandbox Client 在 Repair Agent 路径关闭自身重试，避免 Middleware 嵌套成九次。401、409、
非法请求、无效 200 和安全拒绝不重试。

每次模型请求的 timeout 由 REPRODUCTION_MODEL_TIMEOUT_SECONDS 控制，默认 180 秒，允许
30～300 秒；它是单次请求上限，不是整个 Agent 运行的总时长。

## 7. Summary 契约

Summary 由备用模型完成，不引入第三个 Agent。有效上下文窗口取主模型和备用模型
profile 中较小的 max_input_tokens。自定义模型没有 profile 时，必须配置
MODEL_CONTEXT_WINDOW 和 FALLBACK_MODEL_CONTEXT_WINDOW。

Summary 必须保留以下信息：

~~~text
目标
用户明确要求
可信事实与引用
已完成事项及证据
当前结构化状态
失败尝试与原因
下一步
禁止事项与安全边界
仍不确定的事项
~~~

Summary 是压缩后的不可信上下文，不能替代 checkpoint、Artifact、验证结果或用量计数。
不得保留密钥、联系方式、完整用户文档、完整源码、完整 patch 或大段日志；不得把没有
受信证据的结果写成成功。

## 8. 停止条件

工具循环在以下情况结束：

- complete_reproduction 或 complete_repair 通过受信 Sandbox 检查；
- report_blocked 报告无法复现、需要人工或超出范围；
- 模型、工具、Sandbox、上下文或 Graph 预算耗尽；
- 安全、权限、认证或永久配置错误。

完成工具只能结束内层阶段，最终验证和发布仍由外层受信节点执行。

## 9. 验收重点

- 主模型临时失败两次后，备用模型只在第三次接管；
- 永久错误只调用一次，不因模型名称或错误文本盲目重试；
- 只读查询可并行，写入与 Sandbox 冲突在执行前被拒绝；
- Sandbox 重试复用同一 job_id，成功结果不会重复执行；
- Summary 触发比例、固定章节、脱敏和未完成事项均可验证；
- checkpoint 恢复不重复 patch、Sandbox 或完成动作。
