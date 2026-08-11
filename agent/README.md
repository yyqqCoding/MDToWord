# MD To Word Agent

当前已实现阶段 E 修复与独立验证：Gate 接受后可按固定 SHA 复现缺陷，最多生成两轮
受限后端修复，并在全新 Docker 沙箱中重新证明基线失败、修复后目标/全量/DOCX 验证
通过，最终生成带 SHA-256 的 `validated.patch`。

Controller CLI 默认仍只执行 Gate。`--reproduce` 只执行阶段 D，`--repair` 执行阶段
D+E；两者都必须显式使用真实 Provider。当前不会创建分支、提交、PR 或自动合并。详细边界和验收记录见
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
[002_gate_runtime.sql](migrations/002_gate_runtime.sql)、
[003_reproduction_runtime.sql](migrations/003_reproduction_runtime.sql) 和
[004_repair_runtime.sql](migrations/004_repair_runtime.sql)。测试和应用启动都不会自动执行
migration；数据库 owner 应在审查和备份后手工执行。阶段 D 数据库只需追加执行
`004_repair_runtime.sql`，它新增修复摘要列并重建 Agent 可恢复状态索引。

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
- 阶段 E 默认限制 8 次模型、30 次工具、200,000 tokens 和 900 秒 Sandbox；配置名见
  `.env.example`。`BACKEND_BASELINE_SKIPPED` 必须填写当前固定后端基线值，当前为 `0`。

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
真实缺陷证据。Mermaid 第一轮模型编辑为 `invalid_test_edit` 时，第二轮使用 Controller
固定的 drawing 测试模板，不再请求模型；模板仍必须通过 Patch Policy 和真实 Sandbox。

## 6. 阶段 E 修复与独立验证

先由数据库 owner 审查并执行 `agent/migrations/004_repair_runtime.sql`，Worker 继续使用
同一固定 digest 镜像。对新的 `pending` 后端缺陷执行完整 D+E：

```bash
.venv/bin/python -m agent.cli run \
  --feedback-id <uuid> \
  --dry-run \
  --provider configured \
  --repair
```

阶段 D 已确认复现并停在 `repairing` 的旧 run，可直接从原 checkpoint 继续，不重新
领取 feedback，也不重跑 Gate：

```bash
.venv/bin/python -m agent.cli run \
  --resume-run-id <run-uuid> \
  --dry-run \
  --provider configured \
  --repair
```

成功时 feedback 为 `validated`、run 为 `completed`，Artifact 包含 `fix.patch`、
`validated.patch` 和 `validation.json`；当前阶段不会发布这些文件。目标修复两轮仍失败
或最终全量/DOCX 验证失败时不会产生可发布凭据；预算耗尽进入 `budget_exhausted`，之后
不再调用模型或沙箱。若生成的修复需要新增外部可执行程序、Pandoc filter 或部署变更，
本地 Policy 会在 Sandbox 前把 feedback/run 路由为 `needs_human`，不会继续第二轮生成。
已确认复现的 Mermaid drawing 缺陷更早在修复范围 Policy 中确定为需要渲染器与部署
评估，直接输出 `external_dependency_required`，不调用 `generate_fix`。

## 7. 当前验收结果

- 阶段 D Agent 178 passed（含真实 Docker 两项测试，无 skipped）；后端 44 passed；
- 阶段 E 当前实现验收为 Agent 217 passed，其中真实 Docker 4 passed；后端固定镜像
  44 passed；真实 Provider/Supabase/Langfuse/GitHub/Sandbox 运行已得到修正后的
  `needs_human/external_dependency_required` 终态；
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
- 后续真实 run `8d86f6cb-...` 的 JUnit `failure` 未提供 `type` 属性，但 `message` 以
  `AssertionError` 开头；旧分类器因 traceback 中变量名 `FIXTURES` 含有 `fixture` 而误判
  `invalid_test_infrastructure`。当前解析器会从结构化 JUnit message 推断异常类型，并且
  只依据异常类型判断基础设施错误；该 run 保留历史 `cannot_reproduce` 终态，不重新打开；
- 真实 run `aae54eec-...` 已被模型分类为高相关 `bug_report/docx_structure`，但模型把
  完整 Mermaid 源码和明确 Word 导出描述误判为信息不足，因而在 Gate 进入
  `needs_human`。`repair-policy-v2` 仅对该完整组合使用确定性 Mermaid 证据修正
  `sufficient_information=false`；注入、低相关、前端和未知类别优先级保持不变；
- `repair-policy-v2` 的真实 run `4aee5378-...` 已验证上述 Gate 校正生效，并生成正确的
  Mermaid drawing 复现计划；第一轮测试编辑为 `invalid_test_edit`，一次有界修订最终因
  模型严格 Schema 响应不合规而以 `invalid_response` 终结。该失败发生在模型测试生成，
  尚未调用 Sandbox，历史 feedback/run 不重新领取或打开；
- `agent-graph-v4/repair-policy-v3` 为上述 Mermaid `invalid_test_edit` 增加仅第二轮启用的
  受信 drawing 模板；普通反馈仍由模型修订，模板输出仍通过原有 Patch Policy、JUnit 与
  Docker Sandbox，且不会增加模型调用；
- 真实 run `bab5a685-...` 已在第一轮 Sandbox 得到
  `AssertionError/reproduced`，随后首次 `generate_fix` 在 300 秒后超时；失败终结正确
  持久化 4 次模型、8 次工具和 53,862 tokens。`agent-graph-v5/repair-policy-v4` 将已复现
  Mermaid drawing 固定为依赖/部署人工评估，在读取修复源码和调用修复模型之前直接转
  `needs_human/external_dependency_required`；
- 最终真实 run `3a41124d-...` 使用 `agent-graph-v5/repair-policy-v4`，第一轮复现为
  `AssertionError/reproduced`，随后未调用修复模型即完成
  `needs_human/external_dependency_required`。数据库记录 4 次模型、7 次工具、36,216
  tokens，并同时保存 `result.json`、`test.patch` 与 `repair-result.json`；阶段 E 真实
  服务终态验收完成；
- 阶段 D 模型单次请求超时默认 180 秒（可在 30～300 秒内配置）；模型传输错误仍最多
  重试两次，退避为 1 秒和 4 秒；`/models` 返回 200 只代表网关
  在线，不能替代真实 Chat Completions 验收；
- 维护者暂不填写模型单价，因此数据库成本验收仍为延后项。

## 8. Docker 验收

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

结果必须是 `4 passed`，不能是 skipped。测试同时验证已知表格缺陷产生可信目标失败、
Mermaid 受信回退模板在真实 pytest/JUnit 中产生 drawing 断言失败，
以及阶段 E 在三个独立容器中重新证明基线失败、修复后目标/全量/DOCX 通过和最终补丁
哈希一致；同时验证容器无外网、无业务 Secret、
非 root、只读根文件系统、能力清空、`no-new-privileges`、内存/CPU/PID/超时限制、同一
Job 只执行一次，并确认临时 workspace 已销毁。

Worker 的独立启动入口为：

```bash
.venv/bin/python -m agent.sandbox.worker_http
```

启动前只向 Worker 注入 `.env.example` 中的 Worker-only `SANDBOX_*` 配置，不要加载
Supabase、模型、Langfuse 或 GitHub Secret。开发环境默认绑定 `127.0.0.1:8090`；部署时
只能暴露到 Controller 可访问的内部网络。
