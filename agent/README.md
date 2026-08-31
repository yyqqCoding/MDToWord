# MD To Word Agent

Agent 是独立部署的反馈处理运行时。它把用户反馈分成后端转换缺陷、功能/前端需求、
无关内容和安全风险；只有可验证的后端缺陷才进入代码复现、修复、验证和 PR 流程。

当前架构是：

~~~text
Supabase pending feedback
  -> Scheduler / Controller
  -> 外层 LangGraph：Gate、路由、快照、最终验证、发布
  -> 内层 create_agent：受限 ReAct 工具循环
  -> Sandbox Worker：固定 Job 的隔离容器
  -> PR / Issue / 人工终态
~~~

详细契约从 [docs/AgentRequirements/README.md](../docs/AgentRequirements/README.md) 开始，
维护者操作见 [docs/AgentGuide/README.md](../docs/AgentGuide/README.md)，面试问题见
[docs/AgentProblem/InterviewGuide/agent-interview-questions.md](../docs/AgentProblem/InterviewGuide/agent-interview-questions.md)。

## 1. 本地自动测试

~~~bash
uv sync --extra dev
.venv/bin/python -m pytest agent/tests -q
.venv/bin/python -m compileall -q agent
~~~

自动测试默认使用 Fake Provider，不访问生产 Supabase、模型、GitHub、Langfuse 或
Sandbox。按变更范围追加后端、Trace Site、扩展和 Docker 测试。

## 2. 数据库和 checkpoint

Migration 文件位于 agent/migrations/。测试和应用启动不会自动执行 migration；数据库
owner 审查、备份后手工执行。完成业务 migration 后，显式初始化私有 checkpoint Schema：

~~~bash
.venv/bin/python -m agent.cli checkpoint setup
~~~

AGENT_DATABASE_URL 使用 PostgreSQL Direct Connection 或 Session Pooler DSN，不是公开
SUPABASE_URL。checkpoint 不应建立在 public Schema，也不能把数据库凭据交给 Worker。

## 3. CLI 示例

Gate-only dry run：

~~~bash
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run --provider configured
~~~

真实后端修复与发布：

~~~bash
.venv/bin/python -m agent.cli run \
  --feedback-id <uuid> --provider configured --publish
~~~

恢复同一运行：

~~~bash
.venv/bin/python -m agent.cli run \
  --resume-run-id <run-id> --provider configured
~~~

除明确批准的真实 publish 外，开发和评估使用 dry-run；真实外部写入使用可丢弃数据。
Repair Agent 会复用原 checkpoint、patch hash 和累计预算，不重新领取一个全新反馈。

## 4. Provider 配置重点

模型必须是支持 tool calling 和严格结构化输出的 OpenAI-compatible 接口。主/备模型都
需要名称、API、Base URL 和上下文窗口；备用模型在主模型两次临时传输失败后接管第三次。
错误重试最多三次，退避 1 秒和 2 秒，永久错误不盲目重试。

REPRODUCTION_MODEL_TIMEOUT_SECONDS 是每次 Repair Agent 请求上限，默认 180 秒，允许
30～300 秒。MODEL_CONTEXT_WINDOW 和 FALLBACK_MODEL_CONTEXT_WINDOW 用于 Summary 的
65% soft trigger 和 85% hard limit。默认模型调用 50 次、工具 30 次、Sandbox 900 秒。

生产配置只写入受保护环境文件，不提交 .env。生产主机更新和审计命令见
[deployment-and-operations.md](../docs/AgentRequirements/deployment-and-operations.md)。

## 5. Sandbox Worker

本地可使用 agent/sandbox/Dockerfile 和 worker_http 启动 Worker；生产 Worker 由独立
Linux systemd 服务管理，只监听 127.0.0.1:8090。Worker 使用固定镜像、无网络、非 root、
只读根文件系统和临时 workspace，不持有业务 Secret。

Worker 只接收结构化 Job，不接收模型命令。run_sandbox 的临时连接错误复用同一 job_id
最多三次；测试、修复和最终验证都在新容器中执行。

## 6. 修改边界

Agent 自动 PR 的代码白名单由权威安全文档定义，当前只面向后端转换实现、反馈回归测试
和固件。模型不能修改扩展、依赖、配置、Agent、Dockerfile、部署或安全策略；越权补丁
在执行前拒绝。

代码、Prompt、Policy、工具 Schema 或镜像变化时，先更新权威需求和验收记录，再更新实现。
