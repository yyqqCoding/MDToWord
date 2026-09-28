# Agent 终态邮件通知 MCP

本文定义反馈处理完成后的邮件通知设计、MCP 工具契约和验收标准。通知属于 Agent
基础设施能力，不属于模型工具，不改变 Gate、Repair Agent、Sandbox 或发布的业务权限。

## 1. 目标与边界

当一条反馈进入最终状态后，Agent 发送一封邮件通知维护者。当前只实现 Email 通道，
邮件由同机部署的 MCP Server 通过 Resend API 发送。

MCP Server 与 Agent 部署在同一台服务器，只监听 `127.0.0.1`，不公开到公网。通知收件人、
发件人和 Resend API Key 均由 MCP Server 环境配置，不能由模型、反馈内容或 MCP 调用参数覆盖。

MCP Server 不是给模型使用的工具。Repair Agent 不获得 `send_email` 工具，系统提示词也不
描述该工具。LangGraph 在 Controller 持久化最终状态后执行通知节点，由受信本地代码调用
MCP Client，再由 MCP Server 调用 Resend。

## 2. 触发时机

以下状态均视为终态，并各发送一次 Email 通知：

```text
completed
failed
needs_human
security_rejected
budget_exhausted
cancelled
```

`created`、`gating`、`preparing_source`、`reproducing`、`repairing`、`validating`、
`publishing` 和 `publishing_issue` 等中间状态不发送通知。

最终状态必须先成功持久化，再调用通知节点。通知失败不能回写或改变已经持久化的业务状态。

## 3. LangGraph 与 MCP 数据流

```text
业务节点完成
  -> Controller 持久化终态
  -> notify_completion
  -> 本地 Policy 校验
  -> MCP Client 调用 send_email
  -> MCP Server 调用 Resend
  -> 返回 sent / already_sent / failed
  -> Graph 结束
```

通知节点只接收 Controller 生成的脱敏摘要，不读取用户原文，不读取 Markdown，不读取工具
消息，不读取模型提示词，也不接收任意文件路径。

## 4. MCP Server 配置

MCP Server 环境变量：

```env
NOTIFY_MCP_HOST=127.0.0.1
NOTIFY_MCP_PORT=8091
NOTIFY_MCP_TOKEN=随机内部认证值
NOTIFY_EMAIL_ENABLED=true
RESEND_API_KEY=re_xxxxxxxxx
NOTIFY_EMAIL_FROM=onboarding@resend.dev
NOTIFY_EMAIL_TO=维护者注册 Resend 的邮箱
```

未完成域名验证时，`NOTIFY_EMAIL_FROM=onboarding@resend.dev` 只能发送到 Resend 账号允许的
测试收件地址。当前收件人固定从 `NOTIFY_EMAIL_TO` 读取；工具参数中不得出现 `to` 或 `from`。

## 5. MCP 工具契约

当前只暴露一个工具：`send_email`。

请求参数：

```json
{
  "notification_id": "run-id:completed:email",
  "run_ref": "a1b2c3d4e5f6",
  "status": "completed",
  "route": "accepted_backend_bug",
  "category": "conversion_crash",
  "pr_url": null,
  "issue_url": null,
  "trace_url": "https://example.invalid/trace",
  "failure_code": null
}
```

参数约束：

- `notification_id` 非空、长度受限，并符合 `<run_id>:<terminal_status>:email` 格式；
- `status` 只能是已定义的终态枚举；
- `route` 和 `category` 只能是现有领域枚举；
- URL 只能使用受信 GitHub、Trace Site 域名；
- `failure_code` 只能是稳定错误码，不能是异常原文；
- 不接受 `to`、`from`、`cc`、`bcc`、附件、文件路径、HTML 模板或任意 Headers。

成功响应：

```json
{
  "status": "sent",
  "notification_id": "run-id:completed:email",
  "provider_message_id": "resend-message-id"
}
```

重复请求响应：

```json
{
  "status": "already_sent",
  "notification_id": "run-id:completed:email",
  "provider_message_id": "resend-message-id"
}
```

最终失败响应只返回稳定错误码，不返回 Resend 响应正文、密钥或用户内容：

```json
{
  "status": "failed",
  "notification_id": "run-id:completed:email",
  "error_code": "notification_provider_unavailable"
}
```

## 6. 邮件发送与幂等

MCP Server 调用 Resend 的 Email API。Resend API Key 只存在 MCP Server 环境中，不写入
Agent 数据库、Git、日志或聊天记录。

通知 ID 是幂等键：

```text
<run_id>:<terminal_status>:email
```

MCP Server 持久化已发送的通知 ID 和 Resend `provider_message_id`。收到相同 ID 时不得
重复发送，直接返回 `already_sent`。Agent 或 MCP Server 的所有重试都必须复用同一个 ID。

重试归属：

- MCP Server 对 Resend 的超时、连接失败、429 和 5xx 最多重试三次，退避为 1 秒、2 秒；
- Agent 对 MCP Server 连接失败最多重试一次；
- 认证错误、参数校验错误和明确的 4xx 不重试；
- 通知最终失败只记录通知失败，不改变业务终态。

通知记录存储在 Agent 使用的 PostgreSQL/Supabase 中，最小字段为：

```text
notification_id (unique)
run_id
channel
status
attempts
provider_message_id
last_error_code
created_at
sent_at
updated_at
```

当前版本不实现 Scheduler 启动时的 pending 通知恢复；因此接受进程在终态提交后、通知
调用前立即退出造成的极短通知丢失窗口。该限制不影响业务状态的正确性。

## 7. 邮件内容

主题和正文由 MCP Server 根据白名单字段生成。邮件至少包含：

- 运行编号；
- 最终状态；
- Gate 路由和类别；
- PR 或 Issue 链接（如有）；
- Trace 链接（如有）；
- 稳定错误码（如有）。

邮件不得包含用户原始反馈、Markdown、模型上下文、工具输出、服务器路径、访问令牌或
异常堆栈。

## 8. 验收标准

### 功能

- 每个终态最多发送一封 Email；
- 所有中间状态不发送 Email；
- `completed`、`failed`、`needs_human`、`security_rejected`、`budget_exhausted` 和
  `cancelled` 均能触发通知；
- 通知正文包含运行摘要和可用的 PR/Issue/Trace 链接；
- 收件人只能来自服务器配置；
- Resend 发件人只能来自服务器配置。

### 安全

- `send_email` 不出现在 Repair Agent 工具列表和系统提示词中；
- MCP Server 只监听 `127.0.0.1` 并校验内部 Token；
- 任意用户输入不能覆盖收件人、发件人、邮件 Headers、附件或 URL；
- 日志、数据库和响应不得泄露 Resend API Key 或用户原文。

### 幂等与失败

- 同一 `notification_id` 重复调用只产生一封邮件；
- MCP Server 对 Resend 的临时错误按 1 秒、2 秒退避，最多三次；
- MCP Server 不可达时 Agent 最多重试一次；
- Resend 最终失败不会把已完成的 Agent 运行改成失败；
- 重复调用已发送通知返回 `already_sent`。

### 网站与观测

- Trace Site 仍以 Supabase 业务状态为准；
- 邮件通知失败不改变 Trace Site 展示的运行状态；
- 公开展示只使用脱敏字段，不展示收件人、API Key 或邮件正文原始数据。

## 9. 测试契约

### MCP Server 单元测试

- 合法终态参数可以生成 Resend 请求；
- 缺少 `notification_id`、非法状态、非法路由和非法类别被拒绝；
- 传入 `to`、`from`、附件或路径字段被拒绝；
- 非白名单 URL 被拒绝；
- `NOTIFY_EMAIL_TO` 缺失时服务启动失败或明确返回配置错误；
- Resend 2xx 返回 `sent` 并保存消息 ID；
- 同一幂等键第二次请求返回 `already_sent`；
- 429、5xx 和超时按规定重试；
- 4xx 参数错误不重试；
- Resend 最终失败返回稳定错误码。

### Agent 集成测试

- 每种终态都调用一次 `notify_completion`；
- 中间节点不调用通知；
- 通知异常不会覆盖终态或导致 Graph 重新进入修复流程；
- MCP 调用重试复用同一个 `notification_id`；
- Repair Agent 永远看不到 `send_email` 工具；
- 通知只收到脱敏白名单字段。

### 手工验收

使用维护者配置的 Resend 测试邮箱提交一条可丢弃反馈，确认：

1. 运行进入终态；
2. 收到一封邮件；
3. 重复触发同一终态不会收到第二封；
4. 模拟 Resend 失败时看到稳定错误码，业务状态仍保持原终态；
5. 邮件没有用户原文、Markdown、密钥或内部路径。
