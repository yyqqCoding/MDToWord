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

## 10. 配置清单

主要配置：

```text
SUPABASE_URL
SUPABASE_AGENT_KEY
AGENT_DATABASE_URL / AGENT_CHECKPOINT_SCHEMA=agent_runtime
MODEL_PROVIDER / MODEL_NAME / MODEL_API_KEY / MODEL_BASE_URL
LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY / GITHUB_REPOSITORY
ARTIFACT_ROOT
EXTENSION_MANIFEST_PATH=extension/dist/manifest.json
SANDBOX_WORKER_URL / SANDBOX_WORKER_CREDENTIAL
SANDBOX_IMAGE_DIGEST
POLL_INTERVAL_SECONDS
MAX_* Policy阈值
TRACE_CONTENT=false
```

配置启动时校验；错误信息只指出缺少的配置名，不打印值。测试通过Fake和依赖注入
提供配置，不读取生产Secret。

## 11. 验证命令

### 11.1 当前已实现入口（阶段 B3）

```bash
uv sync --extra dev
.venv/bin/python -m pytest agent/tests -q
.venv/bin/python -m agent.cli checkpoint setup
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --provider configured
```

后端回归从仓库根目录执行：

```bash
# Linux/macOS 后端独立 venv
cd backend && .venv/bin/python -m pytest -v

# 当前 Windows venv + WSL 工作区
backend/.venv/Scripts/python.exe -m pytest backend/tests -v
```

当前 `run` 强制要求 `--dry-run`，并且只执行 Gate。Fake Provider 是默认值；真实模型
必须显式传入 `--provider configured`。

### 11.2 后续阶段目标入口

以下入口要到对应阶段实现后才能使用，不属于阶段 B3：

```bash
python -m agent.evals.runner --provider fake  # 阶段 G
python -m agent.cli run --feedback-id <uuid>  # 阶段 C 至 F 完整链路
```

Docker Worker 另提供不依赖真实模型和 GitHub 的集成测试。文档中的命令必须与实际
CLI 保持一致，接口调整时在同一个变更中更新。

## 12. 进度记录

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

| 阶段 | 状态 | 验收日期 | 证据 |
|---|---|---|---|
| A 基线、配置与持久化 | Implemented | 2026-08-10 | Agent 30 passed；Backend 42 passed；Supabase migration/RLS/RPC/claim 验收通过 |
| B LangGraph Gate与Langfuse | 进行中（B1/B2完成；B3功能与安全通过，成本延后） | - | Agent 88 passed；真实分类/注入隔离/Trace/Token/Masking通过；数据库成本待配置 |
| C 源码工具与Docker Worker | Implemented | 2026-08-11 | Agent 135 passed；Docker 集成 1 passed；Backend 42 passed |
| D 自动复现 | 未开始 | - | - |
| E 修复与独立验证 | 未开始 | - | - |
| F GitHub PR | 未开始 | - | - |
| G 评估与投产 | 未开始 | - | - |

状态只在完成对应验收后更新。已有代码不因存在文件或历史提交自动视为通过。
