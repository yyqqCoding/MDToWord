# 总体架构

## 1. 组件关系

~~~text
浏览器扩展
  -> Render Feedback API
       -> Supabase feedback
            -> 私有 ECS Scheduler
                 -> Controller / 外层 LangGraph
                    |- Gate（无工具）
                    |- 固定源码快照
                    |- Repair Agent（create_agent + ReAct）
                    |- 独立验证
                    |- PR / Issue Publisher
                    |- PostgreSQL checkpoint
                    +-- Langfuse / 结构化日志
                         |
                         +-- Sandbox Client -> 私有 ECS Worker -> Docker 容器
~~~

Render 只承载公开转换和反馈接口。Controller、Worker、Docker Socket 和 Agent Secret 位于
独立私有主机；Worker 只监听 127.0.0.1:8090，任务容器看不到 Controller 的凭据。

GitHub 负责源码、分支和人工协作，不负责调度或执行。Supabase 是业务状态事实来源；
LangGraph checkpoint 负责工具循环的断点恢复；Artifact 保存大对象；Langfuse 是脱敏分析
副本，不能单独恢复业务运行。

## 2. 组件职责

### 2.1 Feedback API

- 接收现有反馈字段并做大小、格式和 IP 限流校验；
- 将通过校验的反馈写入 Supabase；
- 不调用模型、不执行源码、不访问 Sandbox；
- 限流为当前单进程滑动窗口，无法取得可信客户端 IP 时失败关闭。

### 2.2 Scheduler

- 优先寻找可恢复的活动运行，再领取新的 pending 反馈；
- 通过数据库事务、租约、claim token 和单并发锁避免重复处理；
- 进程重启后使用原 run_id 和 checkpoint 恢复；
- 不吞掉取消、终止或进程控制信号；普通异常交给 Controller 记录和终结。

### 2.3 Controller 与外层 LangGraph

Controller 负责装配依赖、读取配置、持久化运行摘要和调用 Graph。外层 Graph 的职责是：

- 执行 Gate 和确定性路由；
- 固定 base_sha、取得源码快照并建立受信 Artifact；
- 调用一个 Repair Agent 工具循环完成复现和修复；
- 执行不受模型控制的最终验证；
- 根据路由调用 Issue 或 PR Publisher；
- 把状态、用量、失败和发布结果写回 Supabase。

Graph 只编排，不是安全边界。工具、路径、补丁、状态和发布条件由领域 Policy 与受信
服务再次校验。

### 2.4 Repair Agent

Repair Agent 是内层 LangGraph 驱动的 create_agent。模型可以在复现和修复阶段循环调用
已注册工具，但不能直接访问文件系统、Shell、网络、GitHub、数据库或凭据。工具返回结果
作为 Observation 回到同一线程，直到调用完成/阻塞工具或触发受信停止条件。

### 2.5 Sandbox Worker

Worker 接收认证的固定 Job，并在一次性 Docker 容器中执行固定命令。容器无网络、非 root、
只读根文件系统、清空 Linux capability、有限 CPU/内存/进程数和明确超时；执行后销毁临时
工作区。Worker 不解析模型自然语言，也不接受命令字符串。

### 2.6 外部适配器

| 依赖 | 作用 | 受信边界 |
|---|---|---|
| Supabase/PostgreSQL | 反馈、运行、claim、终态和幂等 | Repository |
| OpenAI-compatible API | Gate、ReAct 模型和 Summary | Provider / ChatModel |
| Langfuse | 脱敏模型、工具和阶段观测 | Telemetry |
| GitHub | 源码读取、PR 和 Issue | SourceRepository / Publisher |
| Docker Worker | 测试、修复和验证执行 | SandboxClient |
| Mermaid CLI + Chromium | 已审核的后端渲染能力 | 受信平台模块 |

## 3. 两层控制流

外层 Graph 决定“任务处于哪个业务阶段”；内层 ReAct 决定“当前阶段下一步调用哪个已授权
工具”。两者不能互相越权：

~~~text
外层：Gate -> snapshot -> repair_agent -> validate -> publish
内层：read/search -> submit patch -> run_sandbox -> read/search -> ...
~~~

外层固定的验证、发布和状态节点不会因为模型文本而跳过。内层模型的完成声明只有在
完成工具检查到受信结果后才生效。

## 4. 业务流程

~~~text
pending
  -> claim
  -> Gate
      |- rejected_irrelevant / quarantined_security / needs_human
      |- issue_required -> 脱敏 Issue -> issue_opened
      +-- accepted_backend_bug
           -> prepare_source
           -> conversion_probe
           -> repair_agent
                |- cannot_reproduce / needs_human
                +-- candidate_fix
           -> validate_final
                |- failed / budget_exhausted
                +-- passed
           -> publish_pull_request
           -> pr_opened
~~~

conversion probe 把后端问题分成两类：

- 当前转换抛错：Controller 生成固定转换测试，Agent 直接定位和修复；
- 当前转换成功：Agent 必须根据反馈设计语义测试，并先证明基线失败。

语义不稳定、无法复现或超出受信能力时，系统终止自动修复，不猜测用户意图。

## 5. 状态与数据所有权

### 5.1 Feedback 状态

~~~text
pending -> claimed -> gating
  |- rejected_irrelevant
  |- quarantined_security
  |- needs_human
  |- issue_opened
  +-- reproducing -> repairing -> validating
       |- cannot_reproduce
       |- failed
       |- budget_exhausted
       +-- pr_opened
~~~

发布前发现 main 已变化时进入 stale_base，最多重新排队一次；第二次转人工，不自动
rebase 旧补丁。

### 5.2 事实来源

| 数据 | 权威来源 | 作用 |
|---|---|---|
| Feedback 路由和终态 | Supabase | 用户任务的业务状态 |
| 外层状态和运行汇总 | Supabase agent_runs | 页面、调度和最终结果 |
| 内层消息与工具状态 | 私有 PostgreSQL checkpoint | 同一 ReAct 线程恢复 |
| 补丁、JUnit、验证结果 | Controller Artifact | 大对象和发布凭据 |
| 模型/工具耗时和用量 | Langfuse + agent_runs | 观测和成本分析 |

模型消息、Summary 和 Langfuse 都不能覆盖 Supabase 中的路由、状态、验证和发布结论。

### 5.3 运行状态核心字段

~~~text
run_id, feedback_id, claim_token, trace_id
status, route, area, category, risk
base_sha, source_snapshot_ref
test_patch_ref, fix_patch_ref, validation_result_ref
reproduction_round, repair_round
model_calls, tool_calls, token usage, sandbox_duration_ms
validated_patch_sha256, pr_url, issue_url
last_error_code, failure snapshot
~~~

大文本只通过 Artifact 引用传递；公开页面只读取脱敏投影。

## 6. 一致性与幂等

- 数据库 claim 使用事务、锁、租约和 token；
- 内层线程固定为 repair:<run_id>，显式续跑不创建新线程；
- Sandbox 使用稳定 job_id、请求指纹和幂等键，重复请求返回已保存结果；
- Artifact 使用临时文件加原子 rename；
- PR 按 feedback、分支和补丁哈希查重；
- Issue 按 run reference 和内容指纹 marker 查重；
- 发布前查询外部副作用状态，不能因为响应丢失而盲目重复写入；
- validated_patch_sha256 绑定最终发布内容，发布器不接受模型原始编辑。

## 7. 关键取舍

- 小规模个人项目使用单 Scheduler、单 Worker 并发和进程内入口限流；
- 使用一个主模型加一个备用 OpenAI-compatible API，不按供应商名称分支；
- 使用有限 ReAct，而不是开放式 Shell Agent；
- 让模型负责探索和提出候选编辑，让受信代码负责权限、执行、验证和发布；
- PR 自动创建但不自动合并，保留代码审核和实际 Word 视觉确认；
- 不预先引入多 Agent、消息队列、Redis、自有沙箱或自动进化训练系统。
