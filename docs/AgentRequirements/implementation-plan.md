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

实现后至少提供以下稳定入口：

```bash
python -m pytest agent/tests -q
cd backend && .venv/bin/python -m pytest -q
python -m agent.evals.runner --provider fake
python -m agent.cli run --feedback-id <uuid> --dry-run
python -m agent.cli run --feedback-id <uuid>
```

Docker Worker另提供不依赖真实模型和GitHub的集成测试。文档中的命令必须与实际CLI
保持一致，接口调整时在同一个变更中更新。

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

| 阶段 | 状态 | 验收日期 | 证据 |
|---|---|---|---|
| A 基线、配置与持久化 | Implemented | 2026-08-10 | Agent 30 passed；Backend 42 passed；Supabase migration/RLS/RPC/claim 验收通过 |
| B LangGraph Gate与Langfuse | 未开始 | - | - |
| C 源码工具与Docker Worker | 未开始 | - | - |
| D 自动复现 | 未开始 | - | - |
| E 修复与独立验证 | 未开始 | - | - |
| F GitHub PR | 未开始 | - | - |
| G 评估与投产 | 未开始 | - | - |

状态只在完成对应验收后更新。已有代码不因存在文件或历史提交自动视为通过。
