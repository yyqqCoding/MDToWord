# MD To Word Agent

当前实现到阶段 C：除阶段 B 的 Feedback Gate、可恢复 Runtime、真实 Provider 和
Langfuse 外，已经具备固定 SHA 源码快照、受控读取/结构化编辑、Patch Policy、认证且
幂等的 Sandbox Worker，以及固定命令的 Docker Runner。

当前 Controller CLI **仍只执行 Gate**。阶段 C 组件会从阶段 D 开始接入复现 Graph；
目前不会自动读取源码、启动 Docker、修改代码或创建 PR。详细边界和验收记录见
[implementation-plan.md](../docs/AgentRequirements/implementation-plan.md)。

## 1. 安装与自动测试

在仓库根目录执行：

```bash
uv sync --extra dev
.venv/bin/python -m pytest agent/tests -q
```

完整后端回归：

```bash
# Linux/macOS 后端独立 venv
cd backend && .venv/bin/python -m pytest -v

# 当前 Windows venv + WSL 工作区（从仓库根目录）
backend/.venv/Scripts/python.exe -m pytest backend/tests -v
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
`reproducing`，等待阶段 D 将已实现的阶段 C 组件接入复现 Graph：

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

- Agent 自动测试：135 passed（包含真实 Docker 隔离测试）；后端自动测试：42 passed；
- `gate-v2` 真实复测将“仅测试、不需要修复”路由为 `rejected_irrelevant`；
- Prompt Injection 真实复测路由为 `quarantined_security`，`tool_calls=0`；
- Langfuse 每次真实 Gate 包含 root Agent 和 `classify-intent` Generation，且抽查未发现
  完整 Markdown、描述或 contact；
- 阶段 C 真实 Docker 隔离测试：1 passed；
- 维护者暂不填写模型单价，因此数据库成本验收仍为延后项。

## 6. 阶段 C Docker 验收

阶段 C 当前验收结果为 `135 passed`，其中真实 Docker 隔离测试为 `1 passed`，没有
跳过项。复测时先在 Docker Desktop 的 Settings → Resources → WSL Integration 中启用
当前发行版，然后在仓库根目录执行：

```bash
docker build -f agent/sandbox/Dockerfile \
  -t mdtoword-sandbox:stage-c .

export SANDBOX_IMAGE_DIGEST="$(
  docker image inspect --format '{{.Id}}' mdtoword-sandbox:stage-c
)"

.venv/bin/python -m pytest \
  agent/tests/test_docker_integration.py -v -m docker
```

构建前可先用 `docker pull python:3.11-slim` 验证 Docker Engine 的网络。若出现
`proxyconnect tcp` 或 Docker Engine 连接主机 `127.0.0.1` 被拒绝，说明问题位于 Docker
Desktop/WSL 到主机代理的边界，不是项目代码或 `SANDBOX_*` 配置。此时应使用仅对
Docker 生效且能从 Docker VM 访问的代理配置，或使用临时本地转发；不要为了构建修改
其他 CMD 依赖的用户级代理，也不要把本机 IP、代理凭据写入 Dockerfile、`.env.example`
或提交记录。构建完成后可检查镜像未固化代理变量：

```bash
docker image inspect mdtoword-sandbox:stage-c \
  --format '{{json .Config.Env}}'
```

结果必须是 `1 passed`，不能是 skipped。该测试真实验证容器无外网、无业务 Secret、
非 root、只读根文件系统、能力清空、`no-new-privileges`、内存/CPU/PID/超时限制、同一
Job 只执行一次，并确认临时 workspace 已销毁。

Worker 的独立启动入口为：

```bash
.venv/bin/python -m agent.sandbox.worker_http
```

启动前只向 Worker 注入 `.env.example` 中的 Worker-only `SANDBOX_*` 配置，不要加载
Supabase、模型、Langfuse 或 GitHub Secret。开发环境默认绑定 `127.0.0.1:8090`；部署时
只能暴露到 Controller 可访问的内部网络。
