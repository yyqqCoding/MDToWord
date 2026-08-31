# 实施计划与验收记录

本文只记录当前阶段状态、可重复的验收入口和已经取得的证据。详细设计分别见
requirements、architecture、agent-runtime、repair-agent-loop、security-and-sandbox、
failure-handling-and-retries 和 observability；本文件不复制这些契约。

## 1. 当前状态

| 阶段 | 范围 | 状态 |
|---|---|---|
| A | 配置、领域模型、持久化、claim 和 Artifact | 已完成 |
| B | LangGraph 外层、Gate、checkpoint、Telemetry | 已完成 |
| C | 源码快照、受限工具、Patch Policy、Sandbox Worker | 已完成 |
| D | conversion probe、测试生成、基线复现 | 已完成 |
| E | 修复工具循环、目标/全量/DOCX 验证 | 已完成 |
| F | GitHub PR 发布与幂等 | 已完成 |
| G | Fake/真实 Provider 评估与自动模式 | 已完成 |
| H | 反馈入口 IP 限流 | 已完成 |
| I | 功能需求和前端问题 Issue 路由、公开投影 | 生产核心已验收 |
| J | 失败归因、三次传输重试、恢复与 FailureSnapshot | 已完成 |
| K | create_agent ReAct、主备模型、工具并行、Summary、预算 | 已完成 |

扩展商店上架、PR 合并、Render 部署和维护者 Word 视觉确认不属于 Agent 自动发布权限。

## 2. 当前生产链路

~~~text
反馈
  -> Gate（无工具、严格结构化输出）
  -> route_feedback
     -> rejected_irrelevant / quarantined_security / needs_human
     -> publish_issue
     -> prepare_source
        -> conversion probe
        -> Repair Agent（create_agent + tools）
           -> Sandbox 基线复现
           -> 修复补丁和目标验证
        -> 外层独立全量/DOCX 验证
        -> PR Publisher
~~~

生产 Agent 使用一个 Repair Agent，不引入多 Agent、通用 Shell、通用 Filesystem 或
额外 Skill。外层 LangGraph 负责业务阶段和终态，内层 ReAct 只负责当前阶段的探索与
工具调用。

## 3. 验收基线

自动测试默认使用 Fake Provider，不访问生产数据库、模型、GitHub、Langfuse 或 Worker。
代码变更的最低检查：

~~~bash
.venv/bin/python -m pytest agent/tests -q
.venv/bin/python -m compileall -q agent
~~~

涉及后端转换时再运行后端全量测试；涉及 Trace Site 或扩展时运行各自的 test、typecheck、
build；涉及 Docker/Sandbox 边界时运行 Docker 集成测试。环境缺失导致的 skip 或构建失败
必须如实记录，不能记为通过。

## 4. 关键验收矩阵

| 场景 | 必须证明 |
|---|---|
| 后端转换抛错 | probe 生成确定性转换测试，基线失败，修复后通过 |
| 转换成功但结果错误 | Agent 生成语义测试，基线失败后才允许修复 |
| 无关/注入/前端反馈 | 不创建源码快照、Sandbox 或 PR；按 route 终结 |
| 模型 timeout/连接/408/429/5xx | 主、主、备最多三次，退避 1 秒和 2 秒 |
| 模型认证、权限、Schema 永久错误 | 不做传输重试，记录位置和原因 |
| Sandbox 临时失败 | 同一 job_id 最多三次，不重复已完成执行 |
| 工具参数/缺少前置产物 | ToolMessage 指出 required_action，同一 run 修正 |
| 越权路径、未注册工具或越界补丁 | 执行前 security_rejected，不重试 |
| 目标测试通过 | 仍需外层全量 pytest、DOCX 结构和基线对照 |
| base_sha 已过期 | stale_base 一次性重排，不把旧补丁直接套到新 main |
| 模型/工具预算耗尽 | 写入 budget_exhausted，不自动清零；可显式恢复 |
| Scheduler/Worker 异常 | 不退出无痕；FailureSnapshot 指明 phase、node、component、code、attempt |

## 5. 阶段 I 生产证据摘要

Issue 路由和公开投影已经完成生产核心验收：

- Feature feedback 经过 Gate 后进入 issue_required，不创建源码快照、Sandbox 或 PR；
- GitHub Issue 使用固定标签和幂等 marker，正文只含脱敏摘要；
- Trace Site 从 Supabase 分页聚合运行、Token、终态及唯一 PR/Issue；
- 历史 out_of_scope 仅保留读取展示，不批量补建 Issue；
- 扩展商店发布仍由维护者单独完成。

具体 migration、权限调整和外部写入由维护者在生产环境审查后执行，不由测试或应用启动
隐式执行。

## 6. 阶段 J 失败处理证据摘要

统一 FailureCause、LocatedFailure 和 FailureSnapshot 后，以下信息会同时进入日志、运行
记录和脱敏观测：

~~~text
kind, code, component, operation
phase, node, attempt, max_attempts, handling
safe_details, model_calls, tool_calls, token usage
~~~

已验证的边界包括：

- provider timeout 和 Sandbox 暂时不可用均按 1/2 秒退避，最多三次；
- auth、security、configuration、context 超限、非法响应和未知异常不做盲目重试；
- source_auth_error 不会伪装为普通 source_revision_error；
- 未列出的异常由 Controller 最外层捕获并写入 FailureSnapshot；
- schema_errors 只记录字段路径等安全细节，不把校验器原文或用户数据上传 Trace；
- stale_base 保留既有一次性重排语义，不属于本文的传输 RETRY。

## 7. 阶段 K ReAct 证据摘要

当前 Repair Agent 使用官方 create_agent 和受限工具循环：

- 主模型、主模型、备用模型组成一个模型轮次的三次总 attempt；
- 只读源码查询可以并行；补丁写入、Sandbox 和完成工具保持串行；
- Summary 在有效上下文窗口 65% 触发，保留最近 20%；达到 85% 停止；
- 50 次模型调用、30 次工具调用和 Sandbox 900 秒是默认运行预算；
- checkpoint thread 为 repair:<run_id>，显式恢复不清零累计预算；
- 外层仍执行独立 final validation 和受信 Publisher。

真实验证记录：

- model-smoke 已验证主/备 profile、tool calling、只读并行、usage/cache 和 Summary 阈值；
- 真实 run 7a0acabc-217a-4be1-a1a3-0926282866e1 完成基线复现、修复、独立验证；
- 对应 PR #5 已由维护者合并到 main，合并提交为 d0ef01f；
- 生产曾出现 source_auth_error、timeout、budget_exhausted、stale_base 和
  source_access_denied，均能在运行页和日志中指明阶段与处理决定。

## 8. 版本与恢复原则

Graph、Prompt、Policy、工具契约、State Schema 和 Sandbox image digest 写入运行元数据。
修改其中任何一项都要先更新权威需求，再更新实现和验收证据。

恢复时：

1. 使用原 feedback/run ID 和 repair:<run_id>；
2. 读取 checkpoint、Artifact、patch hash 和累计预算；
3. 不重复已完成的 Sandbox、PR 或 Issue 副作用；
4. 若 base_sha 失效，按 stale_base 流程重新获取 main；
5. 若线程结构不兼容，不猜测迁移旧 checkpoint，结束旧 run 后使用新反馈重新验收。

## 9. 生产部署验收

~~~bash
sudo git -C /opt/mdtoword pull --ff-only origin main
sudo bash /opt/mdtoword/deploy/agent/deploy.sh
sudo mdtoword-agentctl audit
sudo mdtoword-agentctl model-smoke
sudo mdtoword-agentctl enable
~~~

脚本负责停止领取、安装锁定依赖、重启 Worker 和审计；enable 由维护者输入 ENABLE。
只有 audit 和可丢弃的真实全流程验收都通过后，才恢复自动领取。

## 10. 后续只接受有证据的增量

新需求必须明确：

- 解决哪个用户或运维问题；
- 哪个组件拥有规则；
- 是否改变工具、权限、数据或状态；
- 自动测试和真实验收如何证明；
- 失败时如何恢复以及是否需要新增敏感数据。

没有对应验收证据的“已完成”不写入本文件；历史排障细节写入
docs/AgentProblem/，不反向改变当前契约。
