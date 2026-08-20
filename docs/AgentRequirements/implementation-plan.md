# 实施计划与验收

## 1. 实施原则

- 每个阶段交付一个可独立运行和验证的增量；
- 先建立确定性Policy、状态与Fake，再接真实模型和外部服务；
- 模型节点和沙箱执行分别测试，避免端到端失败时无法定位；
- 所有真实API调用都放在手工集成验收，不进入默认单元测试；
- 不因引入LangGraph或Langfuse删除现有领域Schema和Provider边界；
- 旧实现可按新契约复用，但必须重新通过本计划验收，不沿用旧阶段状态。

## 2. 目标目录

```text
pyproject.toml                  # Agent 包、运行时与测试依赖
agent/
  api.py                       # health、可选管理接口
  scheduler.py                 # Supabase轮询、claim、并发1
  config.py
  state.py                     # AgentState版本化Schema
  graph.py                     # 顶层LangGraph
  domain/
    enums.py errors.py policy.py fingerprints.py
  repositories/
    base.py supabase.py fake.py
  migrations/
    001_agent_foundation.sql
  providers/
    base.py openai_compatible.py fake.py
  prompts/
    gate.md plan_reproduction.md generate_test.md generate_fix.md
  tools/
    source.py edits.py reproduction.py validation.py
  workspace/
    source_repository.py patching.py artifacts.py
  sandbox/
    contracts.py client.py worker.py docker_runner.py
  validators/
    patch_policy.py junit.py docx.py report.py
  publishing/
    github_app.py pr_body.py
  telemetry/
    base.py langfuse.py logging.py
  evals/
    cases/ runner.py
  cli.py
  tests/

backend/tests/
  test_feedback_regressions.py
  docx_assertions.py
  fixtures/feedback/
```

目录表达责任边界，不要求每个文件都包装成类。相同行为只在确实共享同一领域规则时
抽象；避免为框架展示创建空层。

## 3. 阶段A：基线、配置与持久化

### 交付

- 记录当前后端测试基线和最小DOCX转换结果；
- 数据库增加Feedback状态、claim租约和`agent_runs`；
- 实现`FeedbackRepository`、Supabase适配器与Fake；
- 实现配置校验、Artifact目录和内容指纹；
- 实现版本读取：从部署产物路径读取 `extension/dist/manifest.json`，从GitHub读取
  `main` SHA；缺少前者时使用 `unknown`；
- 定义领域状态、错误码和状态转换测试。

### 验收

- 后端全量pytest通过；
- 原子claim并发调用只有一个成功；
- 超时claim可回收，超过最大attempt进入`needs_human`；
- task artifact不含`contact`、数据库Key或Authorization；
- 相同输入指纹稳定；
- Agent服务重启后数据库状态不丢失。

## 4. 阶段B：LangGraph Gate与Langfuse Dry Run

### 交付

- 顶层Graph、持久化checkpointer和Fake Provider；
- Feedback Gate Schema、Prompt、确定性后置Policy；
- Langfuse Telemetry适配器、Trace ID传播和Masking；
- Scheduler每次只领取一条反馈，并发默认1；
- `dry_run`管理入口只运行Gate，不创建源码workspace和沙箱Job。

### 验收

- 表格/公式样例路由为`accepted_backend_bug`；
- 前端和功能建议路由为`out_of_scope`；
- 无关内容路由为`rejected_irrelevant`；
- 注入对抗样例路由为`quarantined_security`且工具调用数为0；
- 低置信度进入`needs_human`；
- Langfuse可看到Gate generation、真实Token、成本与最终route；
- Trace、日志和数据库中没有`contact`和完整Markdown；
- Langfuse不可用时运行仍能完成并写数据库状态。

## 5. 阶段C：受控源码工具与Docker Worker

### 交付

- GitHub SourceRepository按`base_sha`生成源码快照；
- `search_source`、`read_source_file`和结构化编辑工具；
- Patch Policy与机器可读配置；
- Sandbox Job/Result Schema、内部认证Client和Docker Worker；
- 固定镜像，预装后端开发依赖和Pandoc；
- Docker资源、网络、用户、挂载和超时约束。

### 验收

- 合法源码查询只返回白名单内容；
- `..`、绝对路径、符号链接和敏感文件读取被拒绝；
- 修改`extension/`、`.github/`、依赖或`conftest.py`的编辑在执行前被拒绝；
- 模型提交命令字符串或未注册工具被拒绝；
- 沙箱内网络访问失败，环境中无业务Secret；
- CPU、内存、进程数和超时限制生效；
- 同一Job幂等重试不重复执行已完成结果；
- 容器结束后workspace被销毁。

## 6. 阶段D：自动复现

### 交付

- `plan_reproduction`与`generate_test`节点；
- Mermaid 首轮 `invalid_test_edit` 的受信 drawing 测试模板，仅在第二轮启用；
- 测试结构化编辑与固定测试文件策略；
- JUnit解析、目标失败分类和错误摘要；
- 最多两轮的复现子图；
- 受信DOCX断言工具。

### 验收

- 已知表格或公式缺陷产生目标失败；
- 测试直接通过时修订一次，两轮后仍通过则`cannot_reproduce`；
- ImportError、SyntaxError、fixture缺失、超时不算成功复现；
- 测试名不含完整反馈ID、联系方式和用户描述；
- 测试不能加载外部pytest插件或修改测试基础设施；
- 每轮使用全新基线workspace；
- Langfuse展示计划、源码工具、测试生成、沙箱调用和轮次。

阶段完成后系统已可自动把真实反馈转化为可信的复现报告，即使尚不生成修复。

## 7. 阶段E：修复循环与独立验证

### 交付

- `generate_fix`与`revise_fix`节点；
- 修复patch与测试patch互斥检查；
- 目标验证、全量pytest和DOCX分类验证；
- 全新沙箱中的最终独立验证；
- `ValidationResult`和`validated.patch`。

### 验收

- 覆盖一轮成功、第二轮成功和两轮失败；
- fix patch触碰测试时被拒绝；
- 目标测试通过但全量测试回归时整体失败；
- 原有skipped数量增加时整体失败；
- 无效DOCX、缺少XML部件、表格/公式节点不足时失败；
- 最终验证重新证明“基线失败、修复后通过”；
- `validated_patch_sha256`与文件内容一致；
- 预算耗尽后不能继续调用模型或沙箱。

阶段完成后系统可产出经过验证但尚未发布的后端patch。

## 8. 阶段F：GitHub Pull Request

### 交付

- GitHub App认证与限定仓库配置；
- Publisher只接受`ValidationResult.passed=true`的Artifact；
- 发布前`current_main_sha == base_sha`检查；
- 固定分支、commit和PR正文生成；
- 按feedback与patch hash防重复；
- PR写回数据库并链接Langfuse Trace。

建议格式：

```text
branch: agent/feedback-<short-id>-<category>
commit: fix: repair <category> for feedback <short-id>
```

PR正文包含：分类、脱敏问题摘要、基线失败证据、修改文件、目标/全量测试、DOCX
验证、模型与Prompt版本、Token/成本、风险、`extension_sync_required`、Trace URL、
`base_sha`和patch hash。不得包含联系方式或完整用户Markdown。

### 验收

- 验证失败、hash不一致或base过期时不创建PR；
- 合法Artifact自动创建分支和PR；
- 重试不会创建重复PR；
- GitHub App不能修改Actions、Secrets或自动合并；
- PR中包含完整审查证据且无敏感信息；
- 状态进入`pr_opened`并保存PR URL；
- 维护者可正常Review和Merge，Agent没有合并入口。

## 9. 阶段G：评估与自动模式投产

### 交付

- 10至20条离线评估用例；
- Fake Provider端到端场景；
- 真实Provider Dry Run；
- 第一条真实反馈自动修复PR；
- 成本、成功率和错误报告。

### 验收顺序

1. Fake E2E覆盖成功、无关、注入、前端、无法复现、两轮失败、补丁越界、全量回归
   和发布失败；
2. 真实模型只运行Gate，核对Trace、Token、Masking和路由；
3. 选一条Markdown短、问题明确、后端可复现的真实反馈运行全流程；
4. 人工审核PR中的测试、修复范围、反例、Word输出和Trace；
5. 合并后沿用现有Render流程，并人工回放原Markdown确认；
6. 连续运行若干真实反馈，核对无重复PR、无越权、成本和失败原因可理解。

系统从第一条反馈起即按自动模式运行：Gate通过后自动进入后续节点。投产开关只控制
Scheduler是否领取生产反馈，不引入逐条人工批准状态。

## 10. 阶段H：公开反馈入口 IP 限流

### 交付

- Render/FastAPI 可信客户端 IP 解析边界；
- 单 worker 进程内滑动窗口限流器与 `asyncio.Lock` 并发保护；
- 同 IP 分钟、小时、每日额度和全局小时额度；
- `429`、`Retry-After`、`503 client_ip_unavailable` 响应契约；
- 插件对 `429` 的非重试提示与输入保留；
- Render 上 `CF-Connecting-IP` 的脱敏转发验收。

阶段 H 不新增 Redis、数据库 migration、浏览器指纹、登录、验证码或插件内固定 Secret。

### 验收顺序

1. 使用可控时钟验证分钟、小时、每日、全局窗口和精确 `Retry-After`；
2. 并发提交同一 IP，证明检查与消费原子且只有允许数量写入；
3. 验证 IPv4、IPv4-mapped IPv6、IPv6 `/64`、非法或多值来源；
4. 验证过期清理、10,000 个 IP 容量边界和 Supabase I/O 不持有进程锁；
5. 验证插件收到 `429` 后不自动重试、保留输入并显示等待提示；
6. 部署 Render 后，从 Wi-Fi 与手机流量执行正常/伪造头黑盒请求，以状态码、
   `Retry-After` 和不含 IP 的请求 ID 确认可信边缘覆盖、来源区分及防绕过；不增加临时
   HMAC Secret、IP 日志或诊断接口；
7. 运行后端全量测试、扩展构建以及 Agent 全量测试和 compileall，再按本文记录真实证据。

## 11. 配置清单

主要配置：

```text
SUPABASE_URL
SUPABASE_AGENT_KEY
AGENT_DATABASE_URL / AGENT_CHECKPOINT_SCHEMA=agent_runtime
MODEL_PROVIDER / MODEL_NAME / MODEL_API_KEY / MODEL_BASE_URL
LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
GITHUB_REPOSITORY / GITHUB_READ_TOKEN
GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY
ARTIFACT_ROOT
EXTENSION_MANIFEST_PATH=extension/dist/manifest.json
SANDBOX_WORKER_URL / SANDBOX_WORKER_CREDENTIAL
SANDBOX_IMAGE_DIGEST
POLL_INTERVAL_SECONDS
MAX_* Policy阈值
FEEDBACK_RATE_PER_MINUTE=1
FEEDBACK_RATE_PER_HOUR=5
FEEDBACK_RATE_PER_DAY=10
FEEDBACK_GLOBAL_RATE_PER_HOUR=30
TRACE_CONTENT=false
```

配置启动时校验；错误信息只指出缺少的配置名，不打印值。测试通过Fake和依赖注入
提供配置，不读取生产Secret。

## 12. 验证命令

### 12.1 自动测试与初始化

```bash
uv sync --extra dev
.venv/bin/python -m pytest agent/tests -q
.venv/bin/python -m agent.cli checkpoint setup
```

后端回归从仓库根目录执行：

```bash
# Linux/macOS 后端独立 venv
cd backend && .venv/bin/python -m pytest -v

# 当前 Windows venv + WSL 工作区
backend/.venv/Scripts/python.exe -m pytest backend/tests -v
```

### 12.2 Gate、复现、修复与发布

```bash
# Fake 或真实 Provider 的 Gate-only dry run
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --provider configured

# 阶段 D、D+E 和 D+E+F
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --provider configured --reproduce
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --provider configured --repair
.venv/bin/python -m agent.cli run --feedback-id <uuid> \
  --provider configured --publish
```

除真实 `--publish` 外，CLI 运行必须提供 `--dry-run`；`--publish` 反而禁止与
`--dry-run` 同用。复现、修复、发布和恢复必须显式使用真实 Provider。可恢复错误使用
`--resume-run-id <uuid>` 配合同一个阶段开关，不重新领取 feedback。

### 12.3 评估与生产 Scheduler

```bash
.venv/bin/python -m agent.evals.runner --provider fake
.venv/bin/python -m agent.evals.runner --provider configured
.venv/bin/python -m agent.cli scheduler --once
.venv/bin/python -m agent.cli scheduler --forever
```

生产 Scheduler 默认关闭，只有 `PRODUCTION_SCHEDULER_ENABLED=true` 且 D→E→F 配置预检
全部通过时才领取反馈。Docker Worker 的构建、启动和隔离验收命令见
[agent/README.md](../../agent/README.md)。

## 13. 进度记录

### 阶段 A 实施检查点

**状态：Implemented（2026-08-10）**

**Goal**

建立可独立验证的阶段 A 基础：固定后端转换基线，定义 Agent 的配置、领域状态、
错误、指纹、Artifact、版本读取和反馈持久化边界，为后续 Gate 与 Runtime 提供稳定
契约。

**Acceptance behavior**

- 自动修复范围只包含后端转换报错，以及转换成功但 DOCX 结构或格式断言失败；
- 前端预览正确而后端导出错误时，允许只修改后端；
- 当前修复必须修改扩展才能成立的问题不进入自动修复；
- 原子 claim 在并发时只允许一个调用成功，过期租约可回收，超过最大尝试次数进入
  `needs_human`；
- Task Artifact、Graph State、适配器错误和日志不包含 `contact` 或 Secret；
- 相同规范化反馈产生稳定指纹；
- 插件发布产物缺失时版本记录为 `unknown`；
- 阶段 A 提供固定、离线、可重复的自动化测试入口。

**Out of scope**

- 阶段 B 至 G 的真实模型、LangGraph、Docker Worker、Langfuse 和 GitHub 发布；
- 修改或部署 `extension/`；
- 执行线上 Supabase 迁移、提交或推送代码。

**Assumptions**

- `agent/` 原有内容是无兼容要求的历史原型，已删除并按稳定方案重建；
- `requires_extension_change` 表示当前修复必须修改扩展，命中时路由为
  `out_of_scope`；`extension_sync_required` 仅是后续审查元数据；
- 修改后端 `normalizer.py` 时，必须证明它修复的是导出链路或追平已经正确的前端
  行为；新增共享 Markdown 语义不属于阶段 A。

**Solution boundary**

阶段 A 修改了权威文档、后端固定回归 fixture、Agent Python 包、数据库 migration 和
Agent 单元测试。领域规则不依赖 Supabase、LangGraph 或模型 SDK；Supabase 与 GitHub
只位于适配器边界，并使用 Mock Transport 验证，没有调用真实外部服务。

**实际实现**

- `pyproject.toml` 定义 Agent 包及稳定测试入口；
- `agent/domain/` 定义反馈/运行状态、错误、状态转换、领域对象和内容指纹；
- `agent/config.py` 校验阶段 A 配置并用 `SecretStr` 隐藏 Agent Key；
- `agent/repositories/` 提供 Repository Protocol、并发安全 Fake 和 Supabase 原子 claim
  适配器；
- `agent/migrations/001_agent_foundation.sql` 以附加方式扩展反馈表、创建或兼容
  `agent_runs`、约束 RPC 权限并使用 `FOR UPDATE SKIP LOCKED`；
- `agent/workspace/` 提供原子 Artifact 写入、路径防逃逸、插件版本读取和 GitHub
  `main` SHA 校验；
- `backend/tests/fixtures/table_three_line.md` 替代了会变化且被 Git 忽略的
  `logs/runlog.txt` 测试输入；
- 历史 `agent/context_builder.py`、`agent/schemas/` 和旧分类 Prompt 已按批准删除。

**验证证据**

- `python -m pytest agent/tests -q`：30 passed；
- 后端全量 pytest：42 passed，保留 1 条既有 Starlette/httpx 弃用警告；
- `python -m compileall -q agent`：通过；
- `git diff --check`（将现有 CRLF 视为合法行尾）：通过；
- `pyproject.toml` 使用 Python `tomllib` 解析：通过；
- 维护者已在空 Supabase 数据库手工执行 migration；Schema、RLS、RPC 权限以及
  claim/rollback 功能验收均通过；未调用真实 GitHub，未构建或修改扩展。

### 阶段 A 注释增量

**状态：Implemented（2026-08-10）**

**Goal**

为阶段 A 中不直观的安全、状态、持久化和恢复逻辑增加中文说明性注释，使后续阶段
能够在不改变既有行为的前提下维护这些边界。

**Acceptance behavior**

- 注释覆盖原子 claim、租约回收、状态转换、Supabase 条件更新、Artifact 原子写入、
  指纹规范化、版本读取和 migration 权限；
- 注释解释设计原因、信任边界或失败语义，不逐行复述代码；
- 公共接口、数据库行为和测试预期保持不变。

**Out of scope**

- 重构阶段 A 代码；
- 修改阶段 B 至 G 的实现范围；
- 安装格式化、Lint 或文档生成依赖。

**Assumptions**

- 注释使用中文，代码标识符和稳定错误码保持英文；
- 已经直观的枚举、字段声明和测试不添加重复注释。

**Solution boundary**

本增量只修改了阶段 A Python、SQL 和本实施记录，没有改变公共接口、数据库行为或
测试预期。注释集中在 `agent/config.py`、`agent/state.py`、`agent/domain/`、
`agent/repositories/`、`agent/workspace/` 和阶段 A migration。

**验证证据**

- `python -m pytest agent/tests -q`：30 passed；
- 后端全量 pytest：42 passed，保留 1 条既有 Starlette/httpx 弃用警告；
- `python -m compileall -q agent`：通过；
- diff-check 与注释后 Python 行长检查：通过。

### 阶段 B1 实施检查点

**状态：Implemented（2026-08-10）**

**Goal**

在不连接真实模型、LangGraph、Langfuse 或数据库的前提下，建立可独立测试的 Feedback
Gate 核心，使模型只负责严格分类，最终路由完全由本地 Policy 决定。

**Acceptance behavior**

- 表格和公式后端缺陷路由为 `accepted_backend_bug`；
- 前端、纯视觉和功能建议路由为 `out_of_scope`；
- 无关或垃圾内容路由为 `rejected_irrelevant`；
- 疑似注入优先路由为 `quarantined_security`，Gate 不注册或执行任何工具；
- 低置信度、信息不足、未知意图或未知类别进入 `needs_human`；
- 功能反馈、重复反馈和不合法输入在模型调用前确定性终止；
- Gate 持久化结果不包含联系方式、用户描述或完整 Markdown。

**Out of scope**

- 真实模型 Provider、格式修正重试和真实 Token/成本统计；
- LangGraph、PostgreSQL Checkpointer、Scheduler 与 `dry_run` CLI；
- Langfuse、日志 Masking 和外部服务集成；
- 源码 workspace、沙箱任务以及阶段 C 之后的自动修复能力。

**Assumptions**

- `MIN_GATE_CONFIDENCE` 默认值为 `0.80`，允许通过 Controller 配置覆盖；
- 描述上限沿用现有反馈 API 的 1000 字符，Bug Markdown 按 UTF-8 字节限制为
  50 KiB；
- `duplicate_found` 由后续 Controller 使用阶段 A Repository 查询后传入，领域 Policy
  不自行访问数据库；
- 当前工作区中的后端、扩展及其他既有未提交修改保持不变，不属于本增量。

**Solution boundary**

- `agent/domain/gate.py` 定义严格分类和可持久化 Gate 结果；
- `agent/domain/policy.py` 拥有输入校验、后端类别白名单、优先级和最终路由；
- `agent/providers/` 定义模型端口与可检查请求的 Fake Provider；
- `agent/prompts/gate.md` 将反馈标记为不可信数据，并明确后端/前端边界；
- `agent/gate.py` 只组合上述边界，始终以空工具集合调用模型；
- `agent/tests/test_gate.py` 固定主要路由、安全优先级、严格 Schema 和数据最小化。

**验证证据**

- `python -m pytest agent/tests -q`：52 passed；
- `python -m compileall -q agent`：通过；
- 后端全量 pytest：42 passed，保留 1 条既有 Starlette/httpx 弃用警告；
- `git diff --check`（B1 范围）：通过；
- 未调用真实模型、数据库、Langfuse、GitHub 或沙箱。

### 阶段 B2 实施检查点

**状态：Implemented（2026-08-10）**

**Goal**

在 B1 Gate 之上建立可恢复的 Gate-only LangGraph、单并发 Scheduler、运行摘要持久化
和 `dry_run` 管理入口；生产 checkpoint 使用同一 Supabase PostgreSQL 的私有
`agent_runtime` Schema。

**Acceptance behavior**

- Graph 显式执行 `start_gate -> classify_gate -> route_feedback`，`thread_id` 等于
  `agent_run_id`；
- checkpoint 只保存运行元数据和 Artifact 引用，不保存联系方式、描述或 Markdown；
- 在分类节点完成后中断可由新 Controller 恢复，且不重复调用模型；
- Graph 节点在数据库写入后重试时按 claim token 和目标状态保持幂等；
- Scheduler 每次优先恢复旧运行，再领取一条新反馈，进程内最大并发固定为 1；
- `run --feedback-id <uuid> --dry-run` 只执行 Gate，不创建源码 workspace 或沙箱 Job；
- `checkpoint setup` 是唯一允许调用第三方建表逻辑的入口，服务启动不自动迁移。

**Out of scope**

- 真实模型 Provider、格式修正重试、真实 Token/成本和 Langfuse；
- 源码 workspace、Docker Sandbox、复现、修复或发布；
- 自动执行 Supabase migration 或 checkpoint setup；
- 多 Controller 分布式并发调度。

**Assumptions**

- 单个自托管 Controller 运行 Scheduler，并发固定为 1；
- `AGENT_DATABASE_URL` 使用可访问同一 Supabase PostgreSQL 的 Direct Connection 或
  Session Pooler 服务连接，不向浏览器或后端转换服务暴露；
- migration 由数据库 owner 手工执行，运行账号拥有 `agent_runtime` Schema 的
  `USAGE`、`CREATE` 和表读写权限；
- B2 CLI 使用 Fake Provider，默认路由 `needs_human`；接受后端缺陷路径必须显式传入
  `--fake-route accepted_backend_bug`。

**Solution boundary**

- `agent/graph.py` 与 `agent/controller.py` 实现可恢复 Gate-only Graph 和幂等副作用；
- `agent/checkpoint.py` 在实际数据库会话中显式设置并验证私有 Schema，且发现
  `public` Checkpointer 表时拒绝启动；
- `agent/repositories/` 增加 `AgentRunRepository` 和定向 claim；
- `agent/scheduler.py` 提供恢复优先的单并发轮询；
- `agent/cli.py` 提供 checkpoint setup 与指定反馈 dry run；
- `agent/migrations/002_gate_runtime.sql` 增加恢复字段、定向 claim RPC 和私有 Schema；
- `uv.lock` 固定本次解析的 LangGraph 与 Psycopg 依赖图。

**自动验证证据**

- `python -m pytest agent/tests -q`：71 passed；
- 中断恢复测试证明 Gate 模型只调用一次；
- Scheduler 并发测试证明最大活动运行数为 1；
- checkpoint State 数据最小化、定向 claim、私有 Schema 和 CLI 脱敏测试通过；
- 未连接真实 Supabase、模型、Langfuse、GitHub 或沙箱。

**数据库手工验收**

1. 由数据库 owner 审查并执行 `agent/migrations/002_gate_runtime.sql`；
2. 设置 `AGENT_DATABASE_URL` 后执行
   `.venv/bin/python -m agent.cli checkpoint setup`；
3. 确认 `agent_runtime` 中存在 `checkpoint_migrations`、`checkpoints`、
   `checkpoint_blobs` 和 `checkpoint_writes`；
4. 确认 `anon`、`authenticated` 对 `agent_runtime` 无 `USAGE` 权限；
5. 使用可丢弃的测试反馈执行 Fake dry run，确认 `feedback`、`agent_runs` 与 checkpoint
   的 `thread_id` 状态一致且不存在联系方式或完整 Markdown；接受路径会停在
   `reproducing`，不要用于生产反馈。

**实际数据库证据**

- 维护者已执行 B2 migration 和 `checkpoint setup`；
- Supabase Session Pooler 会忽略连接启动参数中的 `search_path`，首次 setup 曾将空的
  Checkpointer 表建到 `public`；重复表已清理，代码改为连接后显式设置并验证 Schema，
  且发现 `public` 重复表时拒绝启动；
- `checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 目前
  仅存在于 `agent_runtime`，当前连接 `search_path=agent_runtime`；
- `anon`、`authenticated` 对 `agent_runtime` 的 `USAGE` 均为 `false`；
- Checkpointer migration 共 10 条，B2 `agent_runs` 字段和定向 claim RPC 验收通过。

### 阶段 B3 实施检查点

**状态：Implemented；功能与安全已验收，数据库成本延后（2026-08-10）**

**Goal**

在 B2 Gate-only Runtime 上接入可配置的 OpenAI 兼容 Chat Completions 接口和 Langfuse
Cloud，使真实 Gate 调用具备严格结构化输出、有限重试、真实 usage 汇总、Trace 关联和
默认脱敏，同时保证 Telemetry 故障不改变业务结果。

**Acceptance behavior**

- `--provider configured` 才启用真实模型，Fake 仍是 CLI 安全默认值；
- 请求使用 `response_format=json_schema` 和 `strict=true`，Gate 始终传空工具集合；
- 非法结构最多进行一次不带原始错误输出的格式修正，之后返回 `invalid_response`；
- 认证、限流、超时、上下文过大、服务不可用和安全拒绝使用稳定错误码；
- Provider 的输入、输出、缓存、推理和总 Token 被归一化并写入 `agent_runs`；
- Langfuse Trace ID 从 `agent_run_id` 稳定推导，Generation 记录模型、请求 ID、重试、
  互斥 Token bucket、成本和最终 route；
- Trace 不包含 `contact`、完整 Markdown、完整 Prompt、模型 reason 原文或密钥；
- Langfuse 创建、更新或 flush 失败只记录脱敏 warning，不回滚 Gate；
- 耗尽重试的 Provider 失败将运行和反馈终结为 `failed`，不被 Scheduler 无限恢复。

**Out of scope**

- 源码读取、Docker Sandbox、复现、修复、验证和 GitHub 发布；
- 非 Chat Completions 协议、非 JSON Schema 兼容的模型接口；
- 临时启用完整 Trace 内容；B3 CLI 明确拒绝 `TRACE_CONTENT=true`；
- 自动读取 Langfuse 结果作为状态或预算事实来源。

**Solution boundary**

- `agent/providers/openai_compatible.py` 拥有厂商协议、有限重试、错误和 usage 归一化；
- `agent/providers/observed.py` 在 Provider 端口外记录唯一 Generation，避免重复埋点；
- `agent/telemetry/` 定义领域无关端口、统一 Masking 和 Langfuse v4 适配器；
- `agent/controller.py` 传播确定性 Trace ID、Session ID、最终 route 和失败终态；
- `agent/config.py` 与 `.env.example` 定义真实模型、成本和 Langfuse Cloud 配置；
- `agent/cli.py` 保持 Fake 默认，并通过 `--provider configured` 显式启用真实调用。

**自动验证证据**

- `python -m pytest agent/tests -q`：88 passed；
- OpenAI 兼容请求结构、一次格式修正、usage/cost 和稳定错误的 MockTransport 测试；
- Masking、互斥 Token bucket、Generation/route 摘要及 Langfuse fail-open 测试；
- Graph 验证 usage 写库、32 位 Trace ID 和 Provider 失败终态；
- 自动测试不调用真实模型或 Langfuse Cloud；真实服务证据见下节。

**真实服务证据**

- Langfuse Japan 项目认证通过；真实 Gate 写入 root Agent 与 `classify-intent`
  Generation，共 2 个 observation；
- 一次真实调用写入 613 input、70 output、683 total tokens，Langfuse 推算成本约
  `$0.0002066`；数据库成本因维护者暂不配置价格而保持 `0`；
- Trace 内存扫描确认不包含该反馈的完整 Markdown、描述或 contact；
- Langfuse v4 使用 `mask(data=...)` 的兼容问题已修复并增加关键字调用回归测试；
- “只用于测试、不需要修复”的无实际问题反馈首次被保守路由为 `needs_human`；Gate
  Prompt 升级至 `gate-v2` 后，真实复测正确路由为 `rejected_irrelevant`；
- “忽略一切指令并索要系统提示词”的真实注入复测路由为
  `quarantined_security`，`tool_calls=0`；
- 两次 `gate-v2` 运行均为 `completed`，Langfuse 各包含 root Agent 与
  `classify-intent` Generation，且完整 Markdown、描述和 contact 扫描结果均为不存在；
- 真实 Gate 分类、注入隔离、Trace、Token 和 Masking 已通过；维护者决定暂不配置模型
  单价，因此数据库成本仍为 `0`，阶段 B 的成本持久化验收继续保留为待办。

### 阶段 C 实施检查点

**状态：Implemented；自动测试与真实 Docker 隔离验收全部通过（2026-08-11）**

**Goal**

建立阶段 D 可复用的源码与执行安全边界：按固定 `base_sha` 获取 GitHub 快照，只允许
白名单读取和结构化编辑，在补丁进入执行前应用本地 Policy，并通过独立认证 Worker
使用固定 Docker Job 执行不可信代码。

**Acceptance behavior**

- GitHub archive 按完整 commit SHA 下载、流式限长、计算 SHA-256，并拒绝路径逃逸、
  符号链接、设备文件、多根目录和解压大小超限；
- `search_source` 使用字面量搜索，`read_source_file` 只读取白名单 UTF-8 文件；绝对
  路径、`..`、Windows 路径、敏感路径和符号链接均在读取前拒绝；
- 模型只能提交 `search_replace` 或受限 `full_file` 编辑，不能提交 Shell、环境变量或
  任意工具名；Gate 仍没有任何工具权限；
- 测试编辑只允许固定回归测试与 fixture，修复编辑只允许
  `normalizer.py`、`pandoc_runner.py`；文件数、行数、大小、二进制和 Git 元数据变更由
  `patch-policy-v1` 在执行前拒绝；
- Sandbox Job 使用严格 Schema 和固定资源上限，Worker 先认证再解析，校验 Artifact
  Hash、过期时间和幂等键；同一 Job 不重复执行，不同请求不能复用 Job ID；
- Docker Runner 只生成固定 argv，不使用 `sh -c`，启用无网络、只读根、能力清空、
  `no-new-privileges`、非 root、内存/CPU/PID/超时限制和独立 tmpfs；
- Worker 在容器启动前确定性规范源码快照和补丁新增文件的读取权限，不继承宿主
  systemd `UMask`，固定非 root UID 能完成 pytest 启动与收集；
- 任务容器看不到 Worker Git 元数据或业务 Secret；执行后 workspace 偏离授权补丁时
  返回 `security_rejected`，临时 workspace 在任何终态后销毁。

**Solution boundary**

- `agent/workspace/source_repository.py` 与 `agent/tools/source.py` 负责安全快照和只读工具；
- `agent/policies/patch_policy.json` 是安全文档的机器可读镜像；
- `agent/workspace/edits.py` 与 `agent/tools/edits.py` 生成确定性 Git patch Artifact；
- `agent/tools/authorization.py` 固定每个节点可见的工具集合；
- `agent/sandbox/contracts.py`、`client.py`、`worker.py` 和 `worker_http.py` 定义传输、认证
  与跨重启幂等边界；
- `agent/sandbox/docker_runner.py` 和 `agent/sandbox/Dockerfile` 定义固定执行命令与容器
  约束；阶段 D 才把这些能力接入复现 Graph。

**验证证据**

- 设置 `SANDBOX_IMAGE_DIGEST` 后执行 `python -m pytest agent/tests -q`：135 passed，
  无 skipped；
- `agent/tests/test_docker_integration.py -v -m docker`：1 passed；
- 源码/补丁 C1 聚焦测试：31 passed；Sandbox C2 聚焦测试：12 passed；
- 后端全量 pytest：42 passed，保留 1 条既有 Starlette/httpx 弃用警告；
- `python -m compileall -q agent`、`uv lock --check` 和阶段 C diff-check：通过；
- 真实容器已验证无外网、无业务 Secret、非 root、只读根文件系统、能力清空、
  `no-new-privileges`、2 GiB/2 CPU/256 PID 限制、幂等执行、超时终止与 workspace 销毁；
- 最终镜像环境未包含构建使用的代理变量，临时代理桥已在验收后删除。

**真实 Docker 验收**

1. 在 Docker Desktop 中启用当前 WSL 发行版的 Integration；
2. 从仓库根目录构建 `agent/sandbox/Dockerfile`；
3. 读取本地镜像的不可变 `sha256` ID 到 `SANDBOX_IMAGE_DIGEST`；
4. 运行 `agent/tests/test_docker_integration.py`；
5. 只有测试实际执行为 passed，才能把阶段 C 更新为完成。

### 阶段 D 实施检查点

**状态：Implemented（2026-08-11）**

**已实现**

- Gate 的 `accepted_backend_bug` 路由已接入 `prepare_source -> plan_reproduction ->
  generate_test_edit -> run_reproduction_in_sandbox -> classify_reproduction` 子图；其他 Gate
  路由保持 Gate-only 行为；
- 源码快照按运行和完整 `base_sha` 固定，Controller 重启时复用已校验 archive；每轮
  Sandbox Job 都重新从该 archive 物化 workspace，不叠加上一轮修改；
- `ReproductionPlan`、`TestGenerationResult`、Oracle 与目标测试名使用严格 Schema；完整
  UUID、contact 和完整描述不进入测试名，contact 从 Task Artifact 结构上排除；
- 严格 Schema 递归要求全部对象字段并以 `null` 表达未使用值；格式修正只回传字段路径
  与规则。计划只可选择固定快照中实际存在的读取白名单，不能猜测仓库路径；
- 每个计划源码读取范围至少覆盖 20 行，避免只读取文件首行后凭空猜测调用接口；
- 测试补丁只能新增一个计划 selector 到固定回归测试文件；显式 pytest plugin、pytest
  hook、直接 ZIP/XML 解析、网络、Shell、Secret、测试基础设施和额外目标测试在执行前
  拒绝；
- JUnit 由 XML 解析器读取目标 testcase；AssertionError 或明确的 `ConversionError`
  才能按计划确认复现。ImportError、SyntaxError、fixture 缺失、skip、timeout、目标未
  收集和非目标失败均不算成功；
- 首轮测试通过或无效时只修订一次；第二轮仍未产生目标失败进入
  `cannot_reproduce`。Policy 安全拒绝直接进入 `security_rejected`；
- 受信 DOCX 断言固化在 Sandbox 镜像只读层，覆盖 ZIP、必需部件、XML、表格、公式、
  图形数量、段落样式、文本缺失和三线表边框结构；模型只能选择登记 validator 和数据
  参数；Mermaid 计划必须使用图形数量 Oracle，不能用通用 DOCX 完整性代替；
- Langfuse 显式展示复现计划、源码读取、测试生成、结构化编辑、Sandbox 调用和轮次，
  但不上传反馈原文、源码正文、测试源码、复现假设或 JUnit failure message；
- `003_reproduction_runtime.sql` 扩展可恢复索引；CLI 只有显式提供 `--reproduce
  --provider configured` 才启用阶段 D，默认 Fake/Gate 流程不启动源码或 Docker；
- GitHub 源码读取使用独立的 Controller-only、指定仓库 `Contents: Read-only` Token；
  tarball 跟随 GitHub 受信重定向，空的中断目录可幂等清理；`--resume-run-id` 从
  checkpoint 继续已有运行，不重新领取 feedback；
- 生成测试通过 Schema 后若违反固定测试路径或受信断言，只进行一次不含测试源码的
  本地 Policy 修正；Mermaid 测试生成耗尽格式修正时仅在受信 drawing Oracle 下改用
  Controller 固定模板；确定性的源码访问拒绝会终结 run，避免 Scheduler 无限恢复。
- 阶段 D 长源码模型请求默认超时 180 秒，可通过环境变量在 30～300 秒内调整；模型
  5xx/传输错误最多重试两次，使用 1 秒、4 秒的有限退避；测试编辑对不存在目标
  使用 `search_replace` 时归为可修订的 `invalid_test_edit`，不误判为源码读取拒绝。

**自动验收证据**

- 设置阶段 D 镜像 digest 后执行 Agent 全量测试：178 passed，无 skipped；
- 已知“表格导出为普通文本”的 DOCX 缺陷在真实 Docker 中产生目标
  `AssertionError`，JUnit 分类为 `reproduced`；
- 真实 Docker 隔离与已知缺陷复现：2 passed；
- Graph 覆盖一轮复现、两轮直接通过、无效语法修订、两轮无效和 pytest plugin 安全
  拒绝；每轮使用相同原始 archive 且只启动必要次数的 Sandbox；
- 后端全量测试：44 passed，保留 1 条既有 Starlette/httpx 弃用警告；
- Mermaid 真实反馈已走通 Supabase、Langfuse、GitHub 固定快照与计划安全边界；旧模型
  接口曾因 `provider_unavailable` 终结，替换接口的代表性严格 Schema 预检通过，但
  `z-ai/glm-5.2` 在真实 `generate-test` 中耗尽一次格式修正后仍为 `invalid_response`。
  `grok-4.5` 的 Gate Schema 一次通过，但代表性 40 KB 测试生成在三次有限传输尝试中
  均被远端断开；同一 localhost 网关的 `gpt-5.6-luna` 也只通过 Gate，35.8 KB 代表性
  生成最终返回 503。当前 `deepseek-ai/DeepSeek-V4-Flash` 已通过 35.8 KB 代表性
  Schema/Policy 预检；真实反馈 `7990602f-...` 的 run `27d1b938-...` 使用固定
  `base_sha=89c9943f...`，在第二轮生成受控测试补丁。新镜像 Sandbox 只收集一个目标
  测试并得到预期 `AssertionError`，无 error、skip 或 timeout；数据库反馈/run 终态均为
  `repairing`，复现 disposition 为 `reproduced`。本次真实运行记录 5 次模型调用、14 次
  工具调用、68,094 tokens，完成 Supabase、Langfuse、GitHub、模型与 Sandbox 端到端验收。

### 阶段 E 实施检查点

**状态：Implemented（2026-08-11）**

**已实现**

- `generate_fix_edit -> run_target_validation -> classify_target` 修复子图最多执行两轮；
  每轮都读取同一固定快照并把 test/fix patch 发送到新的 Sandbox Job，不继承上一轮
  workspace；
- `FixGenerationResult` 使用严格 Schema，模型无工具权限；修复只允许结构化修改
  `backend/app/normalizer.py` 或 `backend/app/pandoc_runner.py`，测试、fixture、依赖、配置、
  extension、Agent 和部署文件均在执行前拒绝；
- 修复源码若新增 `shutil.which` 外部程序探测或 Pandoc `--filter/--lua-filter`，本地 Policy
  判定为依赖/部署变更并转 `needs_human`；该口径区别于危险能力的
  `security_rejected`，且不会启动目标 Sandbox 或继续第二轮模型请求；
- test/fix patch 在原始快照上重新应用并显式检查文件互斥；组合后的确定性 diff 写入
  `validated.patch`，其内容 SHA-256 写入 `ValidationResult`；
- 目标修复通过后，`validate_final` 使用三个确定性 Job 和三个全新容器，依次重新证明
  仅 test patch 时基线目标失败、test+fix 时目标通过、后端全量 pytest 与同一受信 DOCX
  Oracle 通过；workspace diff 必须与授权 test patch 或组合 patch 的 Hash 一致；
- 全量验证要求无 failure/error，目标测试实际收集并通过，且 skipped 不超过配置的固定
  基线；`passed` 完全由 Controller 计算，模型和 Worker 都不能直接提供；
- 默认运行预算为 8 次模型、30 次工具、200,000 tokens 和 900 秒 Sandbox。每个后续
  模型/沙箱节点执行前检查预算；耗尽后反馈进入 `failed`、run 进入
  `budget_exhausted`，不再产生外部调用；
- `004_repair_runtime.sql` 新增 repair 摘要并把 `repairing/validating` 纳入恢复索引；
  `--repair --provider configured` 可从新反馈执行完整 D+E，也可把阶段 D 已到 END 的
  `repairing` checkpoint 从 `finish_reproduction` 继续，不重新领取反馈或重跑 Gate；
- 当前阶段只产出已验证 Artifact，不创建分支、提交或 PR；发布仍属于阶段 F。
- 模型、结构化响应或源码访问失败终结 run 时，Controller 会合并数据库摘要与最新
  checkpoint 的单调用量，避免修复节点已完成但尚未到数据库汇总节点时丢失计量。

**自动验收证据**

- Agent 全量测试：217 passed，无 skipped；覆盖首轮成功、第二轮成功、两轮失败、旧
  checkpoint 续跑、fix 触碰测试拒绝、目标通过但全量回归、skipped 增加、DOCX Oracle
  失败、组合 patch Hash、预算停止、外部依赖转人工及失败 checkpoint 用量落库；
- 真实 Docker 集成：4 passed；Mermaid 受信模板在固定镜像中只收集唯一目标并得到
  `AssertionError/reproduced`；阶段 E 另用三个独立容器得到基线 AssertionError、修复后
  目标通过和全量通过，修复后 workspace diff 与 `validated.patch` Hash 一致；
- 后端全量测试：固定 Sandbox 镜像只读挂载当前工作区后 44 passed，保留 1 条既有
  Starlette/httpx 弃用警告；
- 真实 run `27d1b938-...` 从阶段 D checkpoint 进入阶段 E。第一轮模型生成了调用
  `pandoc-mermaid` 的修复，目标 Sandbox 正确失败；第二轮模型请求在 300 秒后以
  `timeout` 终结。该 run 的数据库摘要仍停留在阶段 D 的 5 次模型、14 次工具和 68,094
  tokens，证明当时存在失败前 checkpoint 用量未汇总的问题；上述两项缺口现已由本地
  Policy 与失败终结逻辑修正。历史失败 run 不重新打开；当时待新 feedback/run 复验的
  `external_dependency_required -> needs_human` 终态，现已由下述最终真实 run 覆盖；
- 新 run `8d86f6cb-...` 在第二轮生成了正确的 Mermaid drawing Oracle，Sandbox 得到明确
  `AssertionError: expected at least 1 drawing(s), got 0`，但 pytest JUnit 未写 `type`
  属性。旧逻辑扫描完整 traceback 时把变量名 `FIXTURES` 误命中为 fixture 基础设施错误，
  因而历史 run 终结为 `cannot_reproduce/invalid_test_infrastructure`。解析器现从 JUnit
  `message` 开头推断异常类型，基础设施判定只检查类型、不扫描测试源码 traceback；该
  回归由真实报告形态测试覆盖，后续 Stage E 路由已由下述最终真实 run 验证。
- 真实 run `aae54eec-...` 的模型分类为 `bug_report/docx_structure`、相关度 `0.95` 且无
  注入/前端依赖，但把完整 Mermaid 源码与明确 Word 导出故障误判为信息不足，Gate 因此
  终结为 `needs_human`。`repair-policy-v2` 增加窄范围确定性校正：仅在上述后端分类与
  Mermaid 内容证据同时成立时忽略单一 `sufficient_information=false`；其他安全与范围
  路由不变。该历史 run 不重新打开，后续 Stage E 路由已由下述最终真实 run 验证。
- 2026-08-16 真实 feedback `4b42428e-...` / run `4a3fd6c9-...` 的模型同时输出
  `bug_report/conversion_crash`、信息充足、不依赖扩展和 `relevance=0.0`，`reason` 又明确
  判定为后端 Pandoc 转换缺陷；旧 Policy 因 `confidence_below_threshold` 在 Gate 终结为
  `needs_human`，没有生成复现计划或调用 Sandbox。`gate-v7` 明确相关度字段的跨字段口径，
  `publication-policy-v6` 在注入、无关和前端规则之后识别窄范围 Pandoc 失败签名，即使
  模型类别或相关度不稳定也进入 `conversion_crash` 有界复现；缺少明确错误证据的低相关
  反馈仍转人工。历史 run 不重新打开，部署后必须使用新 feedback 验证完整复现/修复/发布
  链路。
- 后续真实 feedback `5180ba17-...` / run `1ebfb33c-...` 已使用 `gate-v7` 与
  `publication-policy-v6` 正常进入 Stage D，但第二轮测试生成把已有
  `backend/tests/test_feedback_regressions.py` 作为 `full_file` 提交，Controller 在提交
  Patch 时以 `test_edit_security_rejected` 终结，Sandbox 实际没有启动。根因是
  `test-generation-v3` 既没有提供现有文件的可追加锚点，又错误允许对该文件提交完整内容。
  `test-generation-v4` 改为由 Controller 提供最短唯一尾部 `append_anchor`，已有文件只
  接受精确锚点的 `search_replace`，并在进入 Patch Policy 前给予一次不回传源码的本地
  格式修正；安全白名单与“不得改写既有回归”规则不放宽。历史 run 不重新打开，部署后用
  新 feedback 验证复现、修复与发布链路。变更后 Agent 套件按文件分组完整执行：301
  passed、4 个 Docker 条件测试 skipped，`compileall` 与 `git diff --check` 通过。
- 部署上述修复后的真实 feedback `064f8e30-...` / run `a00cc2f7-...` 已使用
  `test-generation-v4`，两轮均成功提交包含 fixture 与追加回归测试的合规 Patch，证明
  Problem 13 已解决；但两个 `reproduce_target` Sandbox Job 都在约 2 秒内以
  `status=completed/exit_code=1/junit=null` 返回，最终为 `invalid_test/missing_junit`，没有
  进入修复阶段。`agent-graph-v8` 对 `unexpected_conversion_error` 增加受信转换测试回退：
  首轮测试无效或模型格式修正耗尽时，Controller 固定生成 fixture、转换调用与登记 Oracle，
  第二轮不再调用测试模型，仍经过原 Patch Policy 与全新 Sandbox。领域与 Graph 聚焦测试
  证明缺少 JUnit 后只保留一次模型调用，第二轮目标 `ConversionError` 可进入
  `repairing/reproduced`；真实 Docker 测试已加入，当前环境缺少 Docker 条件时必须报告
  skipped，部署后应在 ECS 上执行该项并用新 feedback 验证。变更后 Agent 套件按文件
  分组完整执行：303 passed、5 个 Docker 条件测试 skipped，`compileall` 与
  `git diff --check` 通过。
- 部署 `agent-graph-v8` 后的真实 feedback `14f0f023-...` / run
  `677c3be4-...` 已进入受信转换测试回退，但第二轮 Job `5521fdb2-...` 仍以
  `exit_code=1/junit=null` 返回。Worker 持久化 stderr 证明 pytest 因
  `/workspace/backend/pytest.toml` 的 `PermissionError` 在收集前退出：systemd
  `UMask=0077` 使快照子目录成为 `0700`，补丁新增文件也可能成为 `0600`。快照物化与
  Docker Runner 现显式规范非 root 容器的读取权限，不依赖宿主 umask。回归在
  `umask 0077` 下覆盖基线目录和新增 fixture；Agent 全量为 305 passed、5 个 Docker
  条件测试 skipped，`compileall` 与 `git diff --check` 通过；使用当前 Dockerfile 新建的
  固定镜像运行完整 Docker 集成为 5 passed。旧 `.env` 镜像因不含 `mmdc` 得到的 Mermaid
  失败不计为通过，更新镜像后已复测全部通过。
- 权限修复部署后的真实 feedback `41d6c497-6c9e-4647-b54c-cfd11d9fff6c` / run
  `d771e2a9-6ce3-4b08-bbfb-15182ec72514` 基于 `26b84f7...` 完成生产全链路。
  `agent-graph-v8` 第二轮受信回退以 `target_conversion_error` 复现
  `test_feedback_41d6c497_aligned_notag`，第一轮修复即令目标测试通过；独立验证确认基线
  失败、目标通过、DOCX `minimum_math_count` 通过及后端全量 55 passed/0 failures/0 skipped。
  最终 `validation.passed=true`，变更仅含 `backend/app/normalizer.py`、固定回归测试和 fixture，
  validated patch SHA-256 为 `545904a6...`。GitHub App 自动创建
  [PR #2](https://github.com/yyqqCoding/MDToWord/pull/2)，feedback=`pr_opened`、
  run=`completed`、两者错误码均为空；共 5 次模型调用、21 次工具调用和 48,146 tokens。
  维护者已确认本次 Agent 运行与 PR 内容正确；PR 是否合并及后端是否部署仍按独立人工
  流程验收，不由该 run 推断。
- `repair-policy-v2` 的真实 run `4aee5378-...` 已通过 Gate 并生成合规 Mermaid drawing
  复现计划，证明上述校正真实生效；第一轮结构化测试编辑未通过本地文本/Python 校验，
  有界第二轮生成又在 Provider 格式修正后仍不符合严格 Schema，run 以
  `invalid_response` 终结（3 次已完成模型调用、7 次工具、24,015 tokens）。该路径没有
  启动 Sandbox，属于当前模型测试生成稳定性，终态 run 不恢复或重复领取；
- `agent-graph-v4/repair-policy-v3` 针对上述稳定性问题增加受信回退：仅当完整 Mermaid
  drawing 计划的第一轮模型编辑为 `invalid_test_edit` 时，第二轮确定性生成固定测试文件
  与 Markdown fixture，不再发起模型调用。模板保留基线已有回归、只调用登记的
  `assert_minimum_drawing_count`，并继续经过路径/AST/规模 Policy 和全新 Sandbox；领域与
  Graph 回归证明模型只调用一次且目标失败进入 `repairing/reproduced`；
- 真实 run `bab5a685-...` 在第一轮生成合法 drawing 测试，Sandbox 只收集唯一目标并以
  `AssertionError/reproduced` 进入 Stage E；首次 `generate_fix` 请求在 300 秒后超时，
  run 正确终结为 `failed/timeout`，并从 checkpoint 汇总 4 次模型、8 次工具和 53,862
  tokens，证明失败用量修正确实生效。由于当时固定镜像无 Mermaid 渲染器，该场景的
  正确修复必然要求依赖/部署评估；`agent-graph-v5/repair-policy-v4` 因此在复现确认后的
  repair scope 节点直接输出 `external_dependency_required/needs_human`，不读取修复源码、
  不调用 `generate_fix`。非 Mermaid 修复循环保持不变；
- 最终真实 feedback `c9c53e99-...` / run `3a41124d-...` 使用
  `agent-graph-v5/repair-policy-v4` 完成全链路：Gate 接受后第一轮 Sandbox 产生
  `AssertionError/reproduced`，repair scope 随即写入
  `needs_human/external_dependency_required`，run 以 `completed` 终结。数据库中的
  feedback、run、reproduction、repair disposition 与错误码一致；记录 4 次模型、7 次
  工具、18,404 input、17,812 output、36,216 total tokens，Artifact 包含
  `result.json/test.patch/repair-result.json`，且没有 validation 或 `validated.patch`，符合
  依赖/部署问题不得伪造已验证补丁的边界。真实阶段 E 终态验收完成。
- 2026-08-12 维护者修正依赖口径：真实、可修复的问题允许引入经人工审核的平台依赖，
  但模型不能自行修改依赖或部署。平台现固定 `@mermaid-js/mermaid-cli 11.16.0`、
  `puppeteer 24.43.1`、系统 Chromium 和中文字体，并用同一锁文件构建生产/Sandbox。
  `app.mermaid_renderer` 只接受源码与工作目录，使用固定 argv/配置和去 Secret 环境，限制
  5 图、单图 20,000 UTF-8 字节及 120 秒，拒绝外链、HTML、click 和 init 配置；Sandbox
  仍无网络、非 root、只读根文件系统。旧 run 继续保留历史终态，不回写数据库。
- `publication-policy-v4/patch-policy-v2/fix-generation-v2` 删除 Mermaid 的确定性人工终止，
  改为向修复模型额外提供只读受信 API；渲染器、依赖清单和 Dockerfile 仍不可编辑，
  未预装的新依赖仍按 `external_dependency_required` 转人工。真实 Docker 已用中文流程图
  证明旧基线 drawing 断言失败、最小 `pandoc_runner.py` 接入后同一断言通过；第一条真实
  Agent PR 仍需在平台变更合并部署后用新 feedback 执行，不能复用历史终态 run。
- 平台合并后的真实 run `878a75c3-...` 已证明 Gate 与 drawing 复现有效，但模型只取得
  `pandoc_runner.py` 的 1～50 行片段，两轮结构化 Edit 均未通过本地应用，终态为
  `failed/invalid_fix_edit` 且没有 PR；旧 Artifact 未保存 Edit，不能进一步断定具体原因。
  `agent-graph-v7/publication-policy-v5/fix-generation-v3` 改为读取完整可编辑固定快照，
  允许同一修复文件按序执行多个 `search_replace`，并仅将受信校验器的稳定失败原因传给
  第二轮；不回显模型 Edit、用户内容或源码到 CLI/日志。
- 新 run `f11032d7-...` 已产生 `validation.passed=true` 的 Mermaid 补丁；首次发布成功创建
  固定分支，但通用电话脱敏规则把合法补丁 SHA 的数字前缀误判为联系方式，在 PR 请求前
  以 `publication_failed` 终止。PR 正文输入契约本身不含 contact、description 或 Markdown，
  因此 Publisher 对该结构化机器元数据关闭电话匹配，继续拦截邮箱、Bearer 与 Secret
  赋值，并保留同 run 幂等恢复。修复后同 run 已创建
  [PR #1](https://github.com/yyqqCoding/MDToWord/pull/1)；feedback=`pr_opened`、
  run=`completed`、`error_code=null`，数据库、GitHub 分支和 `publication.json` 一致。
  该电话规则的根因已在后续修复：匹配边界改为排除十六进制字符与连字符，SHA-256、git
  SHA、镜像 digest 与 UUID 不再被截断。Publisher 侧关闭电话匹配的处置保持不变。

### 阶段 F 实施检查点

**状态：实现与真实 GitHub App/PR 验收完成（2026-08-12）**

- `agent-graph-v7/publication-policy-v5` 只在 `ValidationResult.passed=true` 后进入
  `publishing`；Publisher 重新应用 `validated.patch` 并核对内容哈希、变更文件集合和
  Patch Policy，不接受模型提供的发布凭据；
- GitHub App 使用 `PyJWT[crypto]` 在进程内签发 App JWT，再申请只限当前仓库、只含
  `contents:write` 与 `pull_requests:write` 的短期安装令牌。源码读取 Token、App 私钥和
  安装令牌使用隔离 Client，均不进入 Graph State、Artifact、模型、Worker 或 Trace；
  2026-08-12 真实 App JWT、单仓库安装和最小权限令牌预检已返回
  `github_app_ready`，未创建分支、提交或 PR；
- 固定分支、commit 标题和 PR marker 由 feedback/category/patch hash 确定；Publisher
  先按 marker 查找已有 PR，网络响应丢失或显式重试不会重复创建 PR。代码不提供 merge
  endpoint，也不请求 Actions、Administration 或 Secrets 权限；
- 发布前读取当前 main SHA。过期时零 GitHub 写入并自动重排 feedback 一次；第二次过期
  转 `needs_human`。发布错误保留 validation/validated.patch 并终结为 `failed`，只有
  `publication_*` 错误允许同 run 显式恢复发布 checkpoint，不重跑模型或 Sandbox；
- 成功后 feedback=`pr_opened`、run=`completed` 并保存同一 `pr_url`，Artifact 新增
  `publication.json`；PR 正文仅由结构化验证与运行摘要生成，发布前再次拒绝邮箱、电话、
  Bearer 和 Secret/Token 赋值模式，不拼接 description、Markdown 或 contact；
- `005_publication_runtime.sql` 仅重建可恢复索引并加入 `publishing`，不自动执行数据库
  迁移。CLI 只有显式 `--publish --provider configured` 才允许真实 GitHub 写入，且禁止
  与 `--dry-run` 同用；输出中的 `completed` 只表示 Graph 终结，必须以
  `published=true/error_code=null/pr_url!=null` 判断 PR 发布成功。

### 阶段 G 实施检查点

**状态：开发、独立主机部署与生产小流量验收完成（2026-08-13）**

- `agent/evals/cases.json` 保存 12 条脱敏/构造用例，覆盖表格、公式、标题、崩溃、后端
  规范化、前端、功能建议、无关、信息不足、Prompt Injection 和缺失输入；数据模型没有
  contact 字段；
- `python -m agent.evals.runner --provider fake` 不访问外部服务，确定性报告 Gate accuracy、
  automatable precision、Schema compliance、注入隔离 recall/FPR、Token、成本、延迟和
  Oracle 覆盖；Sandbox/修复指标在 Gate-only 报告中显式为 null，不伪造成功数据；
- `--provider configured` 只执行 Gate 并写 Langfuse，供模型/Prompt/Policy/Graph 变更前
  对比同一评估集；每条输出只含稳定 case ID、分类结果和用量，不回显 Markdown/描述；
- `PRODUCTION_SCHEDULER_ENABLED=false` 是默认硬开关。`agent.cli scheduler --once/--forever`
  只有开关为 true 且所有 D→E→F 配置在领取前通过校验才运行；Scheduler 恢复优先、单
  并发，并复用 CLI 的同一 `ConfiguredRuntime`；
- Fake E2E 已覆盖发布成功、发布失败保留验证 Artifact、同 run 发布重试不重跑五个
  Sandbox Job、stale base 重排及现有无关/注入/前端/无法复现/两轮失败/补丁越界/全量
  回归路径；`deepseek-ai/DeepSeek-V4-Flash` 使用 `gate-v6/publication-policy-v3` 完成 12 条
  真实 Gate 评估，Gate accuracy、automatable precision、Schema compliance、注入隔离
  recall 均为 1.0，注入误报率为 0，且无超时；
- 第一条真实自动修复 run `f11032d7-...` 创建 PR #1，维护者已人工 Review、合并并完成
  Render 部署；原 Mermaid 反馈在插件中重新导出成功，生产转换闭环验收完成；
- 合并后首次在常驻 Agent 目标服务器运行 Docker 回归时，Mermaid 用例仍把当前已修复
  源码当作故障基线，因目标直接通过而缺少 `AssertionError`。测试现从当前实现确定性
  构造临时旧基线，再验证“drawing 失败 -> 应用当前实现后通过”；复测为非 Docker
  252 passed、真实 Docker 4 passed，生产代码未改变；
- Controller、Worker 与 Docker Engine 已部署到独立 Linux ECS；Worker 与 Scheduler 由
  systemd 管理并保持 `active/enabled`，Worker 仅监听 `127.0.0.1:8090`。安装脚本每次
  更新后仍默认关闭 Scheduler，只有 `mdtoword-agentctl audit` 通过并显式确认才恢复领取；
  日常更新新增 `deploy/agent/deploy.sh` 编排入口，服务器只需执行一次 fast-forward pull
  和一次部署命令。入口内部仍固定停止领取、安装后显式重启 Worker、审计、交互式
  `ENABLE` 与状态输出，失败时保持 Scheduler 关闭；三个生产 Shell 脚本的 `bash -n`
  检查通过；
- 生产小流量验证覆盖两条不会产生 GitHub 写入的路径：无关内容自动进入
  `rejected_irrelevant`；已修复 Mermaid 反馈在 `generate-test` 的复杂严格 Schema 失败后
  由受信 drawing 模板接管，Docker 无法复现旧缺陷并进入 `cannot_reproduce`，没有补丁或
  PR。`/models=200` 只作为连通/认证证据，Provider 故障继续按 Langfuse generation 节点与
  `provider_unavailable`/`invalid_response` 分开诊断；权限修复部署后又以真实 aligned/notag
  公式反馈验证可修复路径，完整通过复现、修复、独立验证并自动创建 PR #2。
- 2026-08-17 完成公开展示站 Trace 快照对账：49 条运行中识别出 7 条“调用计数大于零但
  observation 明细为空”的异步索引坏快照，均从 Langfuse 命名根安全回填；另 1 条无快照
  Trace 经 API 确认 404 后保持缺失。投影、旧快照自愈与真实零调用边界新增 6 条聚焦回归，
  本地全部通过。修复合并为 `d29a412` 并完成 Vercel 部署；维护者复核最新 PR 运行与多条
  `cannot_reproduce` 页面，工具调用和 Sandbox observation 均已恢复，页面展示正常。

### 阶段 H 实施检查点

**状态：实现、自动验证与 Render 生产黑盒验收完成（2026-08-18）**

- 维护者已接受进程内滑动窗口方案和默认额度：同 IP 每 60 秒 1 次、每小时 5 次、每天
  10 次，全局每小时 30 次；
- 已接受 IPv6 `/64` 聚合、无可信 IP 时 `503` 失败关闭、Supabase 写入失败不返还额度，
  以及 Render 重启或重新部署后计数清空；
- `backend/app/feedback_rate_limit.py` 已实现公网 IP 规范化、IPv4-mapped IPv6、IPv6
  `/64`、分钟/小时/每日/全局滑动窗口、容量清理和单进程 `asyncio.Lock`；锁内不执行
  Supabase I/O，数据库失败不回滚已消费额度；
- `/feedback` 已接入 `CF-Connecting-IP` 失败关闭、`429/Retry-After`、`503` 和脱敏 `502`；
  插件等待成功后才清空表单，`429` 不自动重试并在弹窗内保留输入、显示等待提示；
- 新增限流/API 聚焦测试 17 条，与现有 API 合跑为 22 passed；Agent 为 305 passed、5
  skipped，compileall 通过；Windows Node 下扩展 `tsc + vite build` 成功，Node 20.17.0
  低于 Vite 推荐的 20.19+，仅产生版本警告；
- 开发机后端全量曾因未安装 `mmdc` 得到 70 passed、1 failed；随后使用包含 Mermaid
  CLI、Chromium 和 Pandoc 的后端生产 Docker 镜像复验，后端全量为 71 passed、1 个
  Starlette/httpx 弃用警告，无 failure 或 error；
- Render 生产黑盒验收中，正常 Wi-Fi 请求返回 `200`；调用方伪造
  `CF-Connecting-IP` 时由 Cloudflare 边缘返回 `403`，请求未到达 Render 应用；同一
  Wi-Fi 在允许请求 9 秒后仅伪造 `X-Forwarded-For`，应用返回 `429`、
  `Retry-After: 51` 和 `Cache-Control: no-store`，证明该头不能绕过限流；
- 绕过本地 HTTP 代理后，手机热点在 15:40:26 UTC 返回 `200`，切换 Wi-Fi 后在同一
  60 秒窗口内于 15:40:53 UTC 返回 `200`，证明两种网络使用不同限流身份；Wi-Fi 于
  15:41:03 UTC 立即重试返回 `429` 和 `Retry-After: 50`，证明同一身份的分钟窗口生效；
- 维护者接受上述不记录 IP 的黑盒证据替代临时 HMAC 诊断，避免为一次性验收新增 Secret、
  IP 派生日志和再次部署；该决定不放宽解析器对单个可路由 IP 的强制校验；
- 2026-08-19 维护者确认生产插件人工验收全部通过：首次反馈提交成功，立即重试显示限流
  提示且不自动重试，失败后输入仍保留；测试数据清理后生产 Scheduler 已恢复运行；
- Edge 商店补丁版本从 `0.3.2` 升至 `0.3.3`，版本源码位于
  `extension/public/manifest.json`，`extension/dist/manifest.json` 由构建生成；Windows
  Node 20.17.0 下 `tsc + vite build` 成功，两个 manifest 均为 `0.3.3`。发布构建已准备，
  不在商店审核完成前声称已发布；
- 实现未增加依赖、数据库 migration、Redis、验证码或浏览器指纹。

### 2026-08-20 小范围健壮性维护

**状态：实现、Agent全量回归与真实Docker隔离验证完成**

- 四份提示词补充“只使用输入事实、区分普通技术文字与注入、测试必须因用户可观察行为
  失败、修复不得针对测试名/反馈ID/完整样例硬编码”等规则，版本更新为`gate-v8`、
  `reproduction-plan-v4`、`test-generation-v5`和`fix-generation-v4`；没有增加工具或可写
  范围；
- 模型Provider在原有1秒/4秒有界退避上支持数值`Retry-After`，仅用于429且最多等待10秒；
  非法、负数和超限值不能让单并发Scheduler无限等待；
- Sandbox Worker HTTP入口改为在读取和解析最多71 MB请求体前校验Bearer认证；
  Sandbox Client对连接异常和408/429/5xx默认额外重试一次，始终复用同一`job_id`与
  `Idempotency-Key`，无效200、认证、非法请求和冲突不重试；
- 新增Provider `Retry-After`、Sandbox同ID重试、无效成功响应不重试和Worker认证早于JSON
  解析的聚焦测试。Provider/Client/Worker/HTTP为22 passed，分类、复现、修复与补丁Policy
  回归为95 passed，本次相关聚焦验证合计117 passed，Agent compileall通过；
- 根`.venv`已从Windows解释器恢复为WSL/Linux CPython 3.11.15；实际虚拟环境放在WSL
  文件系统，项目内`.venv`保持为入口，依赖按`uv.lock`和`dev`可选组安装。此前受Windows
  权限语义影响的严格umask、目录/文件权限和符号链接测试均已通过；
- 使用当前`agent/sandbox/Dockerfile`构建本地镜像，并以不可变`sha256`镜像ID运行真实
  Docker集成测试，5 passed；随后携带同一镜像ID执行Agent完整测试，结果为314 passed，
  无failure或skipped，`python -m compileall -q agent`通过。
- 首次部署该安全增强时，Worker已正常启动并对无凭据探针返回`401`，旧部署审计仍只接受
  认证前的`400`，因此按fail-safe保持Scheduler关闭。就绪契约已改为只接受`401`，既验证
  HTTP监听与认证边界，又不在curl参数或日志中携带Worker Secret；新增脚本级回归测试锁定
  `401`通过、旧`400`失败。

| 阶段 | 状态 | 验收日期 | 证据 |
|---|---|---|---|
| A 基线、配置与持久化 | Implemented | 2026-08-10 | Agent 30 passed；Backend 42 passed；Supabase migration/RLS/RPC/claim 验收通过 |
| B LangGraph Gate与Langfuse | Completed（成本字段按维护者选择保持 0） | 2026-08-10 | Agent 88 passed；真实分类/注入隔离/Trace/Token/Masking通过；未配置模型单价不阻塞功能验收 |
| C 源码工具与Docker Worker | Implemented | 2026-08-11 | Agent 135 passed；Docker 集成 1 passed；Backend 42 passed |
| D 自动复现 | Implemented | 2026-08-11 | Agent 178 passed；Docker 2 passed；Backend 44 passed；真实 Mermaid 反馈在固定 SHA 上产生目标断言失败并进入 `repairing/reproduced` |
| E 修复与独立验证 | Completed | 2026-08-11；Mermaid 平台能力更新 2026-08-12 | 历史 Agent 217 passed/Docker 4 passed/Backend 44 passed；固定渲染器完成中文流程图“基线失败 -> 修复通过”，真实 run 最终生成 validated patch |
| F GitHub PR | Completed | 2026-08-12 | Agent 全量回归通过；真实 App 最小权限预检通过；run `f11032d7-...` 幂等创建 PR #1，数据库与 Artifact 一致，维护者已人工合并 |
| G 评估与投产 | Completed | 2026-08-13；公式闭环复验 2026-08-16 | 12 条真实 Gate 评估、Fake 发布 E2E、PR/Render/插件回放通过；独立 ECS Worker/Scheduler 常驻；生产 `rejected_irrelevant`、Mermaid `cannot_reproduce` 与公式自动修复 PR #2 验收通过 |
| H 公开反馈入口 IP 限流 | Completed | 2026-08-18；插件人工验收 2026-08-19 | 限流/API/插件与自动测试完成；生产 Docker 后端全量 71 passed；Render 黑盒验证伪造头不能绕过、Wi-Fi/手机身份不同、分钟窗口与 `Retry-After` 正确；插件限流提示与输入保留人工验收通过，`0.3.3` 发布构建已准备 |

状态只在完成对应验收后更新。已有代码不因存在文件或历史提交自动视为通过。
