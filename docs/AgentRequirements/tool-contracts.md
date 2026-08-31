# 工具与数据契约

本文定义 Repair Agent 可以调用什么，以及工具如何把不可信请求转换成受信结果。工具不是
Shell 别名；模型不能提交命令、工作目录、环境变量、网络地址、任意路径或 Job ID。

## 1. 统一执行顺序

每个调用都经过同一条本地链路：

~~~text
工具 Schema
  -> 当前 phase 与 checkpoint 授权
  -> 路径、补丁、状态和参数 Policy
  -> model/tool budget
  -> 受信执行
  -> 结果脱敏
  -> ToolMessage + checkpoint
~~~

数据库更新、GitHub 发布、Trace 写入和最终状态转换不作为模型工具；它们由外层
LangGraph 和受信适配器执行。

## 2. 工具清单

| 工具 | 作用 | 开放阶段 |
|---|---|---|
| search_source | 在只读快照中搜索符号或文本 | reproducing、repairing |
| read_source_file | 读取白名单文件的行范围 | reproducing、repairing |
| submit_test_edits | 生成并检查测试补丁 | reproducing |
| run_sandbox | 执行受信固定 Job | 有待验证补丁时 |
| submit_fix_edits | 生成并检查后端修复补丁 | repairing |
| complete_reproduction | 确认基线已按预期失败 | reproducing |
| complete_repair | 确认目标测试已通过 | repairing |
| report_blocked | 报告无法继续或需人工 | 任意内层阶段 |
| write_todos | 维护当前任务的 Todo | 任意内层阶段 |

不注册通用 Shell、Filesystem、网络、GitHub、数据库或发布工具。工具可见性由
PhaseToolPolicyMiddleware 收窄；执行函数还会再次校验，不能把 Middleware 当作唯一安全
边界。

## 3. 源码查询

### search_source

输入为 query、受限 path_scope 和 max_results。query 只能作为搜索文本，path_scope
只能取 Policy 已登记的范围。

### read_source_file

输入为仓库相对 path、start_line 和 end_line。路径必须通过读取白名单，行范围和文件大小
必须在预算内。

以下情况不做传输重试：

- 绝对路径、路径穿越、仓库外符号链接、隐藏密钥或黑名单路径；
- 白名单外路径（返回 source_request_invalid 和 required_action=search_source）；
- 文件不存在、行号错误、范围过大（返回 source_request_invalid 和
  required_action=correct_source_request）。

模型应根据 ToolMessage 在同一 run 修正参数。安全越权则进入 source_access_denied，
不要求模型解释或重试。

## 4. 结构化编辑

模型提交 Edit，而不是 unified diff：

~~~json
{
  "path": "backend/app/normalizer.py",
  "mode": "search_replace",
  "search": "唯一原文片段",
  "replace": "替换内容"
}
~~~

小型新文件或空目标文件才使用 full_file：

~~~json
{
  "path": "backend/tests/fixtures/feedback/example.md",
  "mode": "full_file",
  "content": "UTF-8 文本"
}
~~~

Workspace 在固定 base_sha 上应用编辑并生成唯一 patch。规则：

- search_replace 必须恰好命中一次；
- 同一响应内同一文件的编辑不能重叠；
- 禁止 NUL、二进制、符号链接、权限变化、重命名和子模块；
- 生成后的 patch 重新计算 SHA-256，后续 Sandbox 和发布只信任该 patch；
- 路径白名单、文件数量、增删行数、patch 大小、git diff --check 和测试削弱检查由本地
  Policy 负责。

## 5. 测试补丁

submit_test_edits 接受：

~~~text
edits: Edit[]
target_test_selector: string
expected_failure_kind: assertion | unexpected_conversion_error
reason: 人类可读的短说明
~~~

测试统一追加到 backend/tests/test_feedback_regressions.py，测试名只使用 feedback 的
短前缀和行为，不写完整 UUID、联系方式或完整反馈。反馈固件放在
backend/tests/fixtures/feedback/。

conversion probe 已确认后端直接抛错时，Controller 会固定生成转换回归测试，Agent 不再
重复设计同一测试；转换成功时，Agent 必须提交语义测试，并在基线 Sandbox 证明它确实失败。
DOCX 断言使用受信 Validator，模型不能传入任意 XPath、Python 回调或动态命令。

## 6. 修复补丁

submit_fix_edits 接受 edits、summary 和 risk。它只能修改：

~~~text
backend/app/normalizer.py
backend/app/pandoc_runner.py
~~~

不得修改测试、扩展、依赖、配置、Agent、Dockerfile 或部署文件。risk 只用于 PR 展示，
不改变安全策略；高风险补丁也必须通过同一套检查。

## 7. Sandbox Job

run_sandbox 只接受模型可读的 reason。phase、base_sha、patch 引用、测试选择器、命令、
限制、Job ID 和过期时间全部由 Controller 从 checkpoint 构造。

允许的 Job 类型：

| Job | 固定行为 |
|---|---|
| reproduce_target | 基线 + test patch，运行目标测试 |
| validate_target | 基线 + test + fix patch，运行目标测试 |
| validate_full | 基线 + test + fix patch，运行全量测试和 DOCX 检查 |
| compile_patch | 应用 patch 后编译和 diff 检查 |

Worker 收到结构化 Job 后校验认证、过期时间、Artifact 大小和 SHA-256，使用固定镜像和
固定 argv。target selector 先匹配安全正则，再作为独立 argv 参数，禁止 shell 拼接。

结果至少包括：

~~~text
job_id, status, exit_code, timed_out, started_at, finished_at, duration_ms
junit_summary, stdout_tail, stderr_tail, docx_summary
workspace_diff_sha256, resource_summary, error_code
~~~

JUnit 摘要包含总数、失败、错误、跳过、目标是否收集、目标结果和脱敏失败类型。完整
日志只保留在受控 Artifact；ToolMessage 只返回有限尾部和下一步建议。

同一 job_id 只能对应同一请求指纹。相同请求可幂等重试并复用已完成结果；不同指纹返回
conflict。Sandbox 不接受模型命令，也不与其他 Sandbox 并行。

## 8. 完成工具

complete_reproduction 只有在当前轮基线测试按 expected_failure_kind 失败时才成功；
complete_repair 只有在当前轮目标 Sandbox 通过时才成功；report_blocked 只接受稳定原因
枚举和脱敏摘要。

完成工具结束的是内层循环，不是整个业务运行。外层仍执行独立的 final validation，
然后才允许进入 PR/Issue Publisher。普通文字回复不能伪造完成。

## 9. 错误返回

工具错误优先返回结构化 ToolMessage：

~~~json
{
  "ok": false,
  "error_code": "tool_precondition_failed",
  "reason": "缺少 test_patch_ref",
  "required_action": "submit_test_edits"
}
~~~

参数错误、缺少前置产物和可修正的文件请求由模型在同一循环修正；临时网络失败交给
Middleware 按重试策略处理；越权、安全、认证、永久配置和未知编程错误停止当前运行。

工具结果不得包含 Secret、联系方式、完整用户文档、完整源码或未经截断的测试日志。
失败的工具调用仍计入工具预算并写入脱敏观测。
