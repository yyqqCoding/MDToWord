# MD To Word Agent

当前实现到阶段 B3：具备严格 Feedback Gate、本地确定性路由、可恢复的 Gate-only
LangGraph、PostgreSQL Checkpointer、单并发 Scheduler、Fake Provider、OpenAI 兼容
Provider 和故障开放的 Langfuse Cloud Trace。

当前 CLI **只执行 Gate**。它不会读取源码、启动 Docker、修改代码或创建 PR；这些能力
从阶段 C 开始实现。详细边界和验收记录见
[implementation-plan.md](../docs/AgentRequirements/implementation-plan.md)。

## 1. 安装与自动测试

在仓库根目录执行：

```bash
uv sync --extra dev
.venv/bin/python -m pytest agent/tests -q
```

完整后端回归：

```bash
cd backend
.venv/bin/python -m pytest -v
```

## 2. 数据库初始化

SQL migration 为 [001_agent_foundation.sql](migrations/001_agent_foundation.sql) 和
[002_gate_runtime.sql](migrations/002_gate_runtime.sql)。测试和应用启动都不会自动执行
migration；数据库 owner 应在审查和备份后手工执行。

`AGENT_DATABASE_URL` 必须是 PostgreSQL Direct Connection 或 Session Pooler DSN，
不是 `SUPABASE_URL`。它只属于 Agent Controller，不得提供给扩展或后端转换服务。
完成 migration 后显式初始化第三方 checkpoint 表：

```bash
.venv/bin/python -m agent.cli checkpoint setup
```

成功输出应为：

```json
{"schema": "agent_runtime", "status": "checkpoint_ready"}
```

命令会显式切换并验证私有 `agent_runtime` Schema；如果发现 checkpoint 表误建在
`public`，会拒绝继续，避免把运行状态暴露到公共 Schema。

## 3. Fake Provider Gate 测试

默认 Provider 是 Fake，默认路由为 `needs_human`。其他路由仅用于确定性测试。请使用
可丢弃的 `pending` 反馈；`accepted_backend_bug` 会按阶段设计将反馈停在
`reproducing`，等待尚未实现的阶段 C：

```bash
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --fake-route accepted_backend_bug
```

## 4. 真实 Provider 与 Langfuse Cloud

配置名和注释见仓库根目录 [.env.example](../.env.example)。只把缺少的配置复制到本地、
已被 Git 忽略的 `.env`，不要覆盖已有配置，也不要提交或把 Key 粘贴到日志/聊天中。

- `MODEL_BASE_URL` 填以 `/v1` 结尾的 API 根路径，不填完整的
  `/chat/completions`；
- `LANGFUSE_HOST` 必须与 Cloud 项目区域一致，例如美国区
  `https://cloud.langfuse.com` 或日本区 `https://jp.cloud.langfuse.com`；
- `SUPABASE_AGENT_KEY` 与 Feedback API 凭据必须不同，只能由自托管 Controller 使用；
- 如果兼容接口不返回 `usage.cost`，只有配置模型的美元/百万 Token 单价后，数据库
  `agent_runs.estimated_cost` 才会大于 `0`。Langfuse 自行推算的展示成本不会回写数据库。

加载 `.env` 后，对可丢弃的 `pending` 反馈运行真实 Gate：

```bash
set -a
source .env
set +a
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --provider configured
```

真实 Provider 仍然没有任何工具权限，使用严格 JSON Schema，格式错误最多修正一次。
Provider usage 写入 `agent_runs`；Langfuse 只接收哈希和结构化摘要，不发送完整
Markdown、联系方式、Prompt 或密钥。Langfuse 导出失败不改变 Gate 路由；模型/API
重试耗尽会把运行和反馈终结为 `failed`，避免 Scheduler 无限恢复同一运行。

## 5. 当前验收结果

- Agent 自动测试：88 passed；后端自动测试：42 passed；
- `gate-v2` 真实复测将“仅测试、不需要修复”路由为 `rejected_irrelevant`；
- Prompt Injection 真实复测路由为 `quarantined_security`，`tool_calls=0`；
- Langfuse 每次真实 Gate 包含 root Agent 和 `classify-intent` Generation，且抽查未发现
  完整 Markdown、描述或 contact；
- 维护者暂不填写模型单价，因此数据库成本验收仍为延后项。
