# MD To Word Agent

当前已实现阶段 F 发布和阶段 G 评估/投产控制：Gate 接受后可按固定 SHA 复现缺陷，最多生成两轮
受限后端修复，并在全新 Docker 沙箱中重新证明基线失败、修复后目标/全量/DOCX 验证
通过；只有验证凭据和最终补丁哈希一致时，GitHub App Publisher 才能创建固定分支、
单个提交和 PR。生产 Scheduler 默认关闭，离线评估不领取数据库反馈。

Controller CLI 默认仍只执行 Gate。`--reproduce` 只执行阶段 D，`--repair` 执行阶段
D+E，`--publish` 执行完整 D+E+F；三者都必须显式使用真实 Provider。系统没有自动
合并、部署或回滚入口。详细边界和验收记录见
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
[003_reproduction_runtime.sql](migrations/003_reproduction_runtime.sql)、
[004_repair_runtime.sql](migrations/004_repair_runtime.sql) 和
[005_publication_runtime.sql](migrations/005_publication_runtime.sql)。测试和应用启动都不会
自动执行 migration；数据库 owner 应在审查和备份后手工执行。升级到阶段 F/G 只需
追加执行 `005_publication_runtime.sql`，把 `publishing` 纳入可恢复运行索引。

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
`validated.patch` 和 `validation.json`。目标修复两轮仍失败
或最终全量/DOCX 验证失败时不会产生可发布凭据；预算耗尽进入 `budget_exhausted`，之后
不再调用模型或沙箱。若生成的修复需要新增未预装的外部可执行程序、Pandoc filter 或
部署变更，本地 Policy 会在 Sandbox 前把 feedback/run 路由为 `needs_human`，不会继续
第二轮生成。Mermaid CLI、Chromium 与中文字体已由维护者固定版本并同时放入生产和
Sandbox 镜像；已复现的 drawing 缺陷会读取只读受信渲染器 API、调用 `generate_fix` 并
进行完整验证。模型仍不能修改渲染器、依赖清单或 Dockerfile。

## 7. 阶段 F GitHub Pull Request

先手工执行 `agent/migrations/005_publication_runtime.sql`。Publisher 使用独立 GitHub App，
App 只安装到目标仓库，并只授予 `Contents: Read and write`、
`Pull requests: Read and write`；不得授予 Actions、Administration、Secrets 或合并权限。
在 Controller 私有 `.env` 中填写：

```text
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY=
GITHUB_API_URL=https://api.github.com
GITHUB_MAIN_BRANCH=main
LANGFUSE_TRACE_URL_TEMPLATE=https://<实际项目路径>/traces/{trace_id}
```

私钥支持 dotenv 多行 PEM 或使用字面量 `\n`。运行时会签发只限当前仓库、只含
`contents:write` 与 `pull_requests:write` 的短期安装令牌，不保存到 Artifact、数据库、
Trace 或日志。首次发布前可只校验 App 安装和权限，不创建 GitHub 资源：

```bash
.venv/bin/python -m agent.publishing.check
```

对新的、明确可自动修复的 `pending` 反馈执行完整 D+E+F：

```bash
.venv/bin/python -m agent.cli run \
  --feedback-id <uuid> \
  --provider configured \
  --publish
```

`--publish` 是唯一允许真实 GitHub 写入的 CLI 开关，不能与 `--dry-run` 同用。发布前会
重新应用 `validated.patch` 并核对哈希和文件集合，再检查 `current_main_sha == base_sha`。
主分支已变化时不会创建分支或 PR，feedback 自动重排一次；第二次仍过期则转
`needs_human`。GitHub 临时失败后可用原 run ID 重试，只恢复发布节点：

```bash
.venv/bin/python -m agent.cli run \
  --resume-run-id <run-uuid> \
  --provider configured \
  --publish
```

成功后 feedback 为 `pr_opened`，run 为 `completed`，二者保存相同 `pr_url`，Artifact
新增 `publication.json`。固定分支为 `agent/feedback-<short-id>-<category>`；PR 正文只含
结构化验证证据和 Trace URL，不含联系方式、完整 Markdown 或用户描述。Publisher 没有
自动合并接口。

## 8. 阶段 G 评估与生产 Scheduler

Fake 离线评估不会访问模型、数据库、GitHub 或 Sandbox：

```bash
.venv/bin/python -m agent.evals.runner --provider fake
```

当前评估集包含 12 条表格、公式、标题、崩溃、后端规范化、前端、功能建议、无关、
信息不足、Prompt Injection 和缺失输入用例。报告 Gate accuracy、automatable precision、
Schema compliance、注入隔离召回/误报、Token、成本、延迟和 Oracle 覆盖率。真实模型
Gate-only Dry Run 会产生模型费用并写 Langfuse，需显式执行：

```bash
.venv/bin/python -m agent.evals.runner --provider configured
```

生产 Scheduler 默认由 `PRODUCTION_SCHEDULER_ENABLED=false` 硬关闭。只有维护者完成真实
PR 审核后，才把私有部署 Secret 改为 `true` 并使用：

```bash
.venv/bin/python -m agent.cli scheduler --once
.venv/bin/python -m agent.cli scheduler --forever
```

Scheduler 每次优先恢复 checkpoint，再领取一条反馈，进程内并发固定为 1。开关只控制
是否领取生产反馈；领取后仍自动执行 D→E→F，不增加逐条人工批准，也不自动合并或部署。

## 9. 当前验收结果

- 阶段 D Agent 178 passed（含真实 Docker 两项测试，无 skipped）；后端 44 passed；
- 阶段 E 当前实现验收为 Agent 217 passed，其中真实 Docker 4 passed；后端固定镜像
  44 passed；真实 Provider/Supabase/Langfuse/GitHub/Sandbox 运行已得到修正后的
  `needs_human/external_dependency_required` 终态；
- 阶段 F/G 本地实现覆盖验证失败/哈希不符/过期基线拒绝、合法 PR、幂等复用、最小 App
  权限、发布失败保留 Artifact、同 run 发布重试、12 条 Fake 评估和默认关闭的生产
  Scheduler；当前 Agent 全量（含 4 项 Docker 集成）249 passed，后端只读固定镜像
  52 passed；
  `deepseek-ai/DeepSeek-V4-Flash` 使用 `gate-v6/publication-policy-v3` 的 12 条真实评估达到
  Gate accuracy、automatable precision、Schema compliance、注入召回均 100%，注入误报
  0%；GitHub App 真实 JWT、单仓库安装和最小权限令牌预检已通过，真实 PR 和生产连续运行
  仍需手工验收；
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
- 以上 Mermaid 转人工记录是依赖尚未获批时的历史证据。2026-08-12 维护者确认真实问题
  可以引入审核后的依赖；当前 `publication-policy-v4/patch-policy-v2/fix-generation-v2`
  已预装固定 Mermaid CLI + Chromium + 中文字体，删除 Mermaid 提前终止，并在无网络、
  非 root、只读 Sandbox 中用中文流程图验证“旧基线 drawing 失败、接入后通过”。历史 run
  不重开；平台变更合并部署后需提交新 feedback 执行真实 PR 验收；
- 阶段 D 模型单次请求超时默认 180 秒（可在 30～300 秒内配置）；模型传输错误仍最多
  重试两次，退避为 1 秒和 4 秒；`/models` 返回 200 只代表网关
  在线，不能替代真实 Chat Completions 验收；
- 维护者暂不填写模型单价，因此数据库成本验收仍为延后项。

## 10. Docker 验收

复测时先在 Docker Desktop 的 Settings → Resources → WSL Integration 中启用
当前发行版，然后在仓库根目录执行：

```bash
docker build -f agent/sandbox/Dockerfile \
  -t mdtoword-sandbox:mermaid .

export SANDBOX_IMAGE_DIGEST="$(
  docker image inspect --format '{{.Id}}' mdtoword-sandbox:mermaid
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
docker image inspect mdtoword-sandbox:mermaid \
  --format '{{json .Config.Env}}'
```

结果必须是 `4 passed`，不能是 skipped。测试同时验证已知表格缺陷产生可信目标失败、
Mermaid 受信回退模板先产生 drawing 断言失败，再用预装渲染器验证最小接入后通过，
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
