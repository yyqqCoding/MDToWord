# 总体架构

## 1. 系统上下文

```text
Edge 插件
  -> FastAPI /feedback
  -> Supabase feedback
  -> Agent Controller 定时领取
       |- LangGraph Runtime
       |- Model Gateway
       |- Policy Engine
       |- Source Workspace
       |- Langfuse/结构化日志
       |- Sandbox Client --------> Docker Sandbox Worker
       `- GitHub Publisher ------> GitHub branch + Pull Request
                                      `-> 维护者审核与合并
```

GitHub 负责源码、分支、PR 和人工协作，不负责调度、执行、沙箱或 Agent 密钥。
Agent Controller 是常驻自托管服务；Sandbox Worker 是执行不可信代码的隔离边界。

## 2. 部署单元

### 2.1 Agent Controller

一个 Python 服务，负责：

- 轮询并原子领取反馈；
- 建立 `agent_run` 与完整 Trace 上下文；
- 执行 LangGraph；
- 构造脱敏模型上下文并调用 Model Provider；
- 在每个模型工具请求前执行 Policy；
- 从 GitHub `main` 取得固定 `base_sha` 源码快照；
- 从本地发布产物读取可选的插件版本元数据；
- 向 Sandbox Worker 提交固定 Job；
- 保存 Artifact、状态、Token 和错误；
- 验证通过后通过 GitHub App 创建分支和 PR。

Controller 不直接执行模型生成的测试或修改后的源码。GitHub 发布是 Controller 中的
受信模块，不作为模型工具暴露；只有确定性状态达到 `validated` 才能调用。

### 2.2 Docker Sandbox Worker

一个独立 Linux Worker，负责接收经过认证的结构化 Job、创建临时 Docker 容器、
执行固定命令、收集受限输出并销毁工作区。Worker 和任务容器都不持有模型、
Supabase、GitHub 或 Langfuse 凭证。

具体隔离规则见 [security-and-sandbox.md](security-and-sandbox.md)。

### 2.3 外部依赖

| 依赖 | 用途 | 适配边界 |
|---|---|---|
| Supabase/PostgreSQL | 反馈、运行状态、领取与幂等 | `FeedbackRepository` |
| 模型 API | 门禁、复现规划、测试与修复生成 | `ModelProvider` |
| Langfuse | Agent/LLM Trace、用量与评估 | `Telemetry` |
| GitHub | 读取源码、推送分支、创建 PR | `SourceRepository` / `PullRequestPublisher` |
| Docker Worker | 不可信代码执行 | `SandboxClient` |

领域状态、Policy 和验证器不依赖这些供应商的 SDK 类型。

## 3. 依赖方向

```text
LangGraph nodes
  -> application services
       -> domain policy / schemas / validators
       -> ports (repository, model, sandbox, telemetry, publisher)
            -> external adapters
```

LangGraph 只承担编排。分类规则、路径白名单、验证判定和状态转换必须能在不启动
LangGraph 的单元测试中独立验证。

## 4. 主流程

```text
poll
 -> claim
 -> gate
    |- reject / quarantine / out_of_scope / needs_human / duplicate
    `- accepted_backend_bug
         -> prepare_source(base_sha)
         -> reproduce(max 2 rounds)
         -> repair(max 2 rounds)
         -> validate(fresh sandbox)
         -> check_current_main_sha
         -> publish_pr
         -> pr_opened
```

运行模式是自动模式：门禁通过后不等待人工批准。任何安全拒绝、无法复现、预算耗尽
或验证失败都会在创建 PR 前终止。

## 5. 状态机

### 5.1 Feedback 状态

```text
pending -> claimed -> gating
  |- rejected_irrelevant
  |- quarantined_security
  |- out_of_scope
  |- needs_human
  |- duplicate
  `- reproducing -> repairing -> validating
       |- cannot_reproduce
       |- security_rejected
       |- failed
       `- validated -> publishing -> pr_opened

validated | publishing -> stale_base -> pending（只允许一次）
                                      `- needs_human（重排次数耗尽）
```

### 5.2 Agent Run 状态

```text
created -> gating
  |- completed（非修复终态）
  `- preparing_source -> reproducing -> repairing -> validating
       -> publishing -> completed

publishing -> stale_base

任意活动状态可转为:
failed | cancelled | budget_exhausted | security_rejected
```

状态转换的唯一所有者是 Controller。模型只返回建议或结构化编辑，不能直接写状态。

## 6. 数据所有权

### 6.1 `feedback`

保留现有用户字段，增加或使用以下 Agent 字段：

```text
status, category, risk, content_fingerprint,
attempt_count, stale_requeue_count, claimed_at, claim_token,
last_error_code, last_error_message,
pr_url, resolved_at, updated_at
```

不增加 `expected_behavior` 和逐条 `source_version`。精确重复使用原始
`feedback_type + markdown_content + description` 归一化后的 SHA-256 指纹判断。

### 6.2 `agent_runs`

每次尝试独立记录：

```text
id, feedback_id, claim_token, trace_id, status, route, category, dry_run,
task_artifact_ref, base_sha, extension_version,
provider, model, graph_version, prompt_versions, policy_version,
langfuse_trace_id, classification, reproduction, validation,
model_calls, tool_calls, input_tokens, output_tokens, estimated_cost,
validated_patch_sha256, artifact_path, pr_url,
error_code, error_message, started_at, finished_at
```

`extension_version` 从 Controller 可见的 `extension/dist/manifest.json` 读取；该构建
产物不属于 GitHub `base_sha`，不存在时写 `unknown`。Langfuse 是分析副本；上述
数据库字段是任务状态与最终汇总的事实来源。

### 6.3 Artifact

MVP 使用 Controller 管理的本地运行目录：

```text
<artifact_root>/<agent_run_id>/
  task.redacted.json
  gate.json
  reproduction-plan.json
  test.patch
  fix.patch
  validated.patch
  reproduction-junit.xml
  validation-junit.xml
  validation.json
  result.json
```

目录不包含联系方式，默认保留 14 天。模型只通过受控上下文读取必要摘要，不直接
访问 Artifact 文件系统。

## 7. 基线与发布一致性

任务开始时从 GitHub `main` 读取并固定 `base_sha`。所有编辑、沙箱执行和最终补丁
均基于该 SHA。发布前只做一次简单检查：

```text
current_main_sha == base_sha -> 创建分支和 PR
current_main_sha != base_sha -> 本次 run 结束为 stale_base，feedback 重新排队一次
```

不自动 rebase，不维护复杂分支同步协议。最终发布内容以
`validated_patch_sha256` 对应的 `validated.patch` 为准。
同一 feedback 第二次遇到 `stale_base` 时进入 `needs_human`，不得无限重排。

## 8. 故障与恢复

- 数据库领取使用原子 claim、租约超时和最大尝试次数；
- `operation_id = run_id + node + attempt`，所有外部副作用幂等；
- Controller 重启后从持久化 LangGraph checkpoint 与数据库状态恢复；
- Sandbox 超时或崩溃只丢弃该临时容器，不复用工作区；
- Langfuse 不可用不阻断主流程，最终用量仍写入 `agent_runs`；
- GitHub 发布失败保留已验证 Artifact，状态为 `failed`，可由同一运行幂等重试；
- 同一 `validated_patch_sha256` 和 feedback 不得创建多个打开的 PR。

## 9. 关键取舍

- 使用 LangGraph 而非自研通用 Runtime，因为本系统需要持久化、循环和恢复；
- 使用显式状态图包住有限 ReAct，不采用拥有任意 Shell 的开放式 Agent；
- 使用 Docker Worker 而非自研沙箱；
- 只修后端，避免插件商店审核周期进入自动闭环；
- 使用一个模型完成 MVP，先通过 Langfuse 和离线评估获得拆分模型的证据；
- 保持人工合并，因为 DOCX 结构检查不能完全替代 Word 视觉检查。
