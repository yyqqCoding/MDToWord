# 失败归因、重试与稳定性

## 1. 先区分三件事

遇到异常时先回答：

1. 发生在什么 phase、node、component 和 operation？
2. 它是暂时传输失败、模型/工具输入问题、业务结果不满足，还是安全/永久错误？
3. 可以在同一 run 修正、应该重试、需要重排，还是必须停止？

FailureSnapshot 统一记录：

~~~text
kind, code, component, operation
phase, node, attempt, max_attempts, handling
safe_details, model/tool calls, token usage and timing
~~~

unknown 异常也必须经过最外层捕获，不能让 Scheduler 退出后留下空白 run。

## 2. 三类处理

| 处理 | 适用 | 例子 |
|---|---|---|
| 同一 run 修正 | 参数或受信前置条件缺失 | 行号错误、缺少 test_patch_ref |
| transport retry | 相同输入可安全重发的暂时失败 | timeout、连接断开、408、429、5xx |
| stop/requeue/human | 越权、认证、永久配置或业务无法证明 | source_access_denied、invalid_response、budget_exhausted |

业务测试失败由 ReAct 循环继续候选；stale_base 是发布阶段既有的一次性重排，不等同于
短传输重试。

## 3. 模型重试

一次模型轮次最多三次总 attempt：

~~~text
attempt 1：主模型
等待 1 秒
attempt 2：主模型
等待 2 秒
attempt 3：备用模型
~~~

只对 timeout、连接异常、408、429、5xx 重试。认证失败、越权、安全拒绝、上下文超限、
Schema/Policy 无效响应和未知编程异常不重试。第三次仍失败时写入完整 FailureSnapshot。

主备模型对外统一为一个 Provider 口径；实际使用哪个接口只留在受控诊断信息中，不改变
业务错误码。

## 4. Sandbox 重试

Sandbox Client 在 Repair Agent 路径关闭自身重试，由 RecordingToolRetryMiddleware 统一
处理。连接异常、408、429、5xx 最多三次，复用同一 job_id、请求指纹和幂等键，等待 1/2
秒。已完成的 Job 直接复用结果，不重复执行。

401、409、非法请求、无效 200、容器安全拒绝和参数错误不重试。Worker 串行执行，防止
2H2G 主机上并行容器挤占资源。

## 5. 工具和 Schema 错误

缺少前置产物、文件不存在、行号错误和可修正参数会返回脱敏 ToolMessage，明确
required_action。模型可在同一循环完成前置动作或修正参数。

invalid_response 表示已经收到响应但严格 Schema 或本地 Policy 不通过；它不是网络超时。
如果是字段校验，safe_details 只记录 schema error path；含内部校验器文案的 hint 不上传
Trace。可进行一次格式修正，但不能无限重试。

## 6. 预算、上下文和重启

模型调用、工具调用、Sandbox 时长和 Graph recursion limit 分开计算。达到预算进入
budget_exhausted，不自动把计数清零。上下文达到 65% 先 Summary，达到 85% 仍超限则停止。

进程重启后复用数据库 run、claim lease 和 repair:<run_id> checkpoint。恢复保留累计预算、
patch、Sandbox 和发布结果；维护者调整预算后可显式续跑。

## 7. 维护者如何排查

先看运行页和 agent_runs 的 phase/node/code/handling，再看 Scheduler、Worker 日志、
checkpoint 和脱敏 Langfuse。常见判断：

- source_auth_error：读取 Token 失效或权限不足，修凭据后恢复；
- source_revision_error：main 响应或版本契约无效，检查源码适配器；
- source_access_denied：请求越过读取白名单，不能让模型重试；
- provider timeout：确认模型 URL、单次 timeout、主备配置和上游状态；
- budget_exhausted：查看累计计数，决定提高预算还是人工结束；
- stale_base：按最新 main 重新复现，不直接套旧 patch。

不要手工把 failed 状态改回 pending 以绕过幂等和审计；使用受支持的 resume-run-id 流程。

## 8. 稳定性验收

测试应证明：

1. 三次总 attempt 和 1/2 秒退避准确；
2. 永久错误只执行一次；
3. Sandbox 重试不重复已完成 Job；
4. 所有异常都有位置、处理决定和安全细节；
5. 前置条件错误可以在同一 run 修正；
6. 重启恢复不重复 patch、验证和发布；
7. 预算耗尽不再调用模型或工具。
