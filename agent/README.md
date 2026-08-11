# MD To Word Agent

当前实现到阶段 D 自动复现：Gate 接受后可按固定 SHA 读取源码、规划并生成受限回归
测试，在认证 Docker Worker 中最多执行两轮，并用 JUnit 和受信 DOCX 断言生成复现报告。

Controller CLI 默认仍只执行 Gate。只有显式添加 `--reproduce --provider configured`
才会启动阶段 D；当前不会生成修复、修改源码或创建 PR。详细边界和验收记录见
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

SQL migration 为 [001_agent_foundation.sql](migrations/001_agent_foundation.sql)、
[002_gate_runtime.sql](migrations/002_gate_runtime.sql) 和
[003_reproduction_runtime.sql](migrations/003_reproduction_runtime.sql)。测试和应用启动
都不会自动执行 migration；数据库 owner 应在审查和备份后手工执行。已有阶段 B
数据库只需追加执行 `003_reproduction_runtime.sql`，它只重建 Agent 可恢复状态索引。

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
可丢弃的 `pending` 反馈；没有 `--reproduce` 时，`accepted_backend_bug` 只会把反馈停在
`reproducing`，不会读取源码或启动 Sandbox：

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
- 阶段 D 的长源码请求默认允许 180 秒，可用
  `REPRODUCTION_MODEL_TIMEOUT_SECONDS` 在 30～300 秒内调整；Gate 使用独立的短请求超时。

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

## 5. 阶段 D 自动复现

先由数据库 owner 审查并执行 `agent/migrations/003_reproduction_runtime.sql`，再使用
阶段 D 镜像启动 Worker：

```bash
docker build -f agent/sandbox/Dockerfile -t mdtoword-sandbox:stage-d .
export SANDBOX_IMAGE_DIGEST="$(
  docker image inspect --format '{{.Id}}' mdtoword-sandbox:stage-d
)"
.venv/bin/python -m agent.sandbox.worker_http
```

Worker 需要单独的最小环境，只包含 `SANDBOX_IMAGE_DIGEST`、`SANDBOX_JOB_ROOT`、
`SANDBOX_BIND_*` 和 `SANDBOX_WORKER_CREDENTIAL`。不要把加载了 Supabase、模型或
Langfuse Secret 的 Controller `.env` 整体传给 Worker。

Controller 还需配置 `GITHUB_READ_TOKEN`。建议使用只授权
`yyqqCoding/MDToWord`、Repository permissions 中仅 `Contents: Read-only` 的
fine-grained token；它只进入 Controller 的 GitHub 专用 Client，不能提供给 Worker、
任务容器、模型或 Langfuse。GitHub 发布阶段将另用 GitHub App，不复用这个读取 Token。

确认 Worker 已启动后，在另一个终端加载 Controller 的私有 `.env`，为一条可丢弃的
`pending` 后端缺陷执行：

```bash
set -a
source .env
set +a
.venv/bin/python -m agent.cli run \
  --feedback-id <uuid> \
  --dry-run \
  --provider configured \
  --reproduce
```

若进程或可重试的外部依赖在复现中断，不要重新领取同一 feedback。使用输出或数据库中
已有的 run ID，从持久化 checkpoint 继续：

```bash
.venv/bin/python -m agent.cli run \
  --resume-run-id <run-uuid> \
  --dry-run \
  --provider configured \
  --reproduce
```

若目标失败确认，feedback 与 agent run 停在 `repairing`，保留复现报告供阶段 E 继续；
两轮仍通过或无效则 feedback 为 `cannot_reproduce`、run 为 `completed`；Sandbox Policy
拒绝则二者进入安全终态。`--reproduce` 禁止 Fake Provider，防止人为的固定断言被当作
真实缺陷证据。

## 6. 当前验收结果

- 阶段 D Agent 178 passed（含真实 Docker 两项测试，无 skipped）；后端 44 passed；
- `gate-v2` 真实复测将“仅测试、不需要修复”路由为 `rejected_irrelevant`；
- Prompt Injection 真实复测路由为 `quarantined_security`，`tool_calls=0`；
- Langfuse 每次真实 Gate 包含 root Agent 和 `classify-intent` Generation，且抽查未发现
  完整 Markdown、描述或 contact；
- 阶段 D 真实 Docker 已验证隔离边界和已知表格缺陷的目标失败分类：2 passed；
- Mermaid 真实反馈已验证 GitHub 鉴权、固定 SHA 快照、严格计划 Schema、实际可读路径
  约束和有界测试修正；旧模型接口因 `provider_unavailable` 终结，替换接口的代表性严格
  Schema 预检通过，但 `z-ai/glm-5.2` 在真实 `generate-test` 中两次输出仍不合规并以
  `invalid_response` 终结；`grok-4.5` 的 Gate Schema 一次通过，但代表性 40 KB 测试
  生成在有限重试内均被远端断开；同一 localhost 网关的 `gpt-5.6-luna` 也只通过 Gate，
  35.8 KB 代表性生成最终返回 503。当前 `deepseek-ai/DeepSeek-V4-Flash` 已通过
  35.8 KB 代表性 Schema/Policy 预检；真实反馈 `7990602f-...` 的 run
  `27d1b938-...` 在固定 SHA 上于第二轮生成有效回归测试，新镜像 Sandbox 收集到唯一
  目标测试的预期断言失败，数据库终态为 `repairing/reproduced`。本次真实运行共 5 次
  模型调用、14 次工具调用和 68,094 tokens，阶段 D 端到端验收完成；
- 阶段 D 模型单次请求超时默认 180 秒（可在 30～300 秒内配置）；模型传输错误仍最多
  重试两次，退避为 1 秒和 4 秒；`/models` 返回 200 只代表网关
  在线，不能替代真实 Chat Completions 验收；
- 维护者暂不填写模型单价，因此数据库成本验收仍为延后项。

## 7. Docker 验收

复测时先在 Docker Desktop 的 Settings → Resources → WSL Integration 中启用
当前发行版，然后在仓库根目录执行：

```bash
docker build -f agent/sandbox/Dockerfile \
  -t mdtoword-sandbox:stage-d .

export SANDBOX_IMAGE_DIGEST="$(
  docker image inspect --format '{{.Id}}' mdtoword-sandbox:stage-d
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
docker image inspect mdtoword-sandbox:stage-d \
  --format '{{json .Config.Env}}'
```

结果必须是 `2 passed`，不能是 skipped。测试同时验证已知表格缺陷产生可信目标失败，
以及容器无外网、无业务 Secret、
非 root、只读根文件系统、能力清空、`no-new-privileges`、内存/CPU/PID/超时限制、同一
Job 只执行一次，并确认临时 workspace 已销毁。

Worker 的独立启动入口为：

```bash
.venv/bin/python -m agent.sandbox.worker_http
```

启动前只向 Worker 注入 `.env.example` 中的 Worker-only `SANDBOX_*` 配置，不要加载
Supabase、模型、Langfuse 或 GitHub Secret。开发环境默认绑定 `127.0.0.1:8090`；部署时
只能暴露到 Controller 可访问的内部网络。
