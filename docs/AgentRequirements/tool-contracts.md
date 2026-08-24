# 工具与数据契约

## 1. 设计原则

模型只能请求已注册、参数固定、结果结构化的工具。工具不是 Shell 别名；模型不能
提交命令字符串、工作目录、环境变量、网络地址或任意文件路径。每次调用都经过：

```text
Schema 校验 -> 当前节点授权 -> 路径/参数 Policy -> 预算检查 -> 执行 -> 结果脱敏
```

GitHub 发布、数据库更新、Trace 写入和状态转换不作为模型工具。

## 2. 模型可用工具

### 2.1 `search_source`

用途：在只读源码快照中搜索符号或文本。

```json
{
  "query": "normalize_markdown",
  "path_scope": "backend",
  "max_results": 20
}
```

约束：`query` 长度受限；`path_scope` 只能取 Policy 枚举；返回文件、行号和截断片段，
总输出不超过上下文预算。

### 2.2 `read_source_file`

用途：读取允许的源码或测试文件。

```json
{
  "path": "backend/app/normalizer.py",
  "start_line": 1,
  "end_line": 240
}
```

约束：路径必须是仓库相对路径且通过读取白名单；拒绝绝对路径、`..`、符号链接、
隐藏密钥文件和超限范围。

`backend/app/mermaid_renderer.py` 是只读平台能力说明，不在任何写入白名单中。模型不能
把可执行程序、浏览器参数、环境变量或配置路径作为工具参数。

### 2.3 `submit_test_edits`

用途：提交回归测试结构化编辑，由 Workspace 生成 `test.patch`。

输入包含 `edits`、`target_test_selector`、`oracle`、`expected_failure_kind` 和简短说明。
此工具只生成和检查补丁，不执行代码。

### 2.4 `run_reproduction`

用途：在基线应用 `test.patch` 并执行固定目标测试。

```json
{
  "test_patch_ref": "artifact://.../test.patch",
  "target_test_selector": "feedback_ab12cd",
  "expected_failure_kind": "assertion"
}
```

模型不能指定 pytest 参数。Controller 将请求转换为固定 Sandbox Job。

### 2.5 `submit_fix_edits`

用途：提交允许后端源码的结构化编辑，由 Workspace 生成 `fix.patch`。此工具不得
编辑测试补丁、新增测试、配置、依赖或 Agent 文件。

### 2.6 `run_target_validation`

用途：在全新基线中应用 test 与 fix patch，运行固定目标测试并返回结构化摘要。
全量验证由确定性 Graph 节点执行，模型没有直接调用权限。

## 3. 结构化编辑

模型不直接生成 unified diff。支持两种编辑：

```json
{
  "path": "backend/app/normalizer.py",
  "mode": "search_replace",
  "search": "唯一原文片段",
  "replace": "替换内容"
}
```

```json
{
  "path": "backend/app/normalizer.py",
  "mode": "full_file",
  "content": "完整文本内容"
}
```

规则：

- `search_replace.search` 必须恰好命中一次；
- 一个响应不能对同一文件提交相互重叠的编辑；
- 文本必须是 UTF-8，禁止 NUL 和二进制；
- `full_file` 仅允许用于 Policy 标记为小文件的路径；
- Workspace 在临时基线上应用编辑后用 Git 生成确定性 diff；
- 后续检查与发布只使用生成的 patch，不再信任模型原始编辑。

## 4. 生成测试契约

`TestGenerationResult`：

```text
edits: Edit[]
target_test_selector: string
oracle:
  kind: conversion_success | conversion_error | docx_xpath | text_absent | style_present
  parameters:
    validator: registered-validator | null
    minimum: integer | null
    text: string | null
    style: string | null
expected_failure_kind: assertion | unexpected_conversion_error
reason: string
files_needed_for_fix: string[]
extension_sync_required: bool
```

测试统一追加到 `backend/tests/test_feedback_regressions.py`，名称为
`test_feedback_<feedback-id前8位>_<行为>`。不得包含完整 UUID、联系方式或完整问题
描述。测试必须离线、确定且不读取环境 Secret。

模型接口使用严格 Structured Outputs：每个对象属性都进入 `required`，未使用字段以
`null` 表示，动态参数字典禁止进入模型 Schema。结构格式错误最多修正一次，修正提示
只包含 Pydantic 字段路径/错误类型，不包含原始输出；通过 Schema 后若违反固定测试路径
或受信断言 Policy，也只允许一次不回传测试源码的本地规则修正。

`extension_sync_required` 只是审查元数据；当前修复若必须修改扩展才能成立，应在 Gate
阶段以 `requires_extension_change=true` 路由为 `issue_required`，不得生成测试或补丁。

`files_needed_for_fix` 只接受 Policy `write.fix_exact` 中的路径，即
`backend/app/normalizer.py` 与 `backend/app/pandoc_runner.py`；不确定时填空数组。
`backend/app/mermaid_renderer.py` 可读不可写，是模型最常猜错的一项，拒绝消息因此直接
列出白名单 —— 该消息会进入格式修正提示，只说「invalid」模型无从改起。

新建文件（含 `backend/tests/fixtures/feedback/` 下的固件）必须用 `full_file`。
`backend/tests/test_feedback_regressions.py` 已存在且非空时，Controller 在
`regression_append_context` 中提供最短唯一文件尾部 `append_anchor`；模型必须用
`search_replace`，让 `search` 精确等于该锚点、`replace` 先原样保留锚点再追加新测试。
只有该文件为空时才允许 `full_file`。这些跨字段规则同时写进 `generate_test.md` 并由
本地 Policy 校验：严格 Structured Outputs 要求所有字段都出现，但无法从 Schema 推断
文件是否存在、模式选择和只能追加的约束。

## 5. 生成修复契约

`FixGenerationResult`：

```text
edits: Edit[]
summary: string
behavior_changes: string[]
risk_level: low | medium | high
manual_review_notes: string[]
extension_sync_required: bool
```

风险等级仅用于 PR 展示，不改变 Policy。高风险结果仍须通过相同检查，并在 PR 中
突出显示；命中禁止路径或禁止模式直接 `security_rejected`，不因模型解释而放行。

## 6. Sandbox Job

Controller 只可提交以下 Job 类型：

| Job | 固定行为 |
|---|---|
| `reproduce_target` | 基线 + test patch，运行目标 pytest |
| `validate_target` | 基线 + test + fix patch，运行目标 pytest |
| `validate_full` | 基线 + test + fix patch，运行全量 pytest 与 DOCX 检查 |
| `compile_patch` | 应用 patch 后执行编译和 diff 检查 |

共同输入：

```text
job_id, run_id, job_type, base_sha,
source_snapshot_sha256, test_patch_sha256?, fix_patch_sha256?,
target_test_selector?, limits, expires_at
```

Worker 不从模型请求构造命令。映射命令由受信镜像和 Worker 配置定义，例如：

```text
python -m pytest tests/test_feedback_regressions.py -k <validated-selector>
  -q --junitxml=/result/junit.xml
python -m pytest -q --junitxml=/result/full-junit.xml
```

`target_test_selector` 必须匹配 `^[a-z0-9_]{1,80}$` 后再进入 argv；不得经 `sh -c`。

Controller 与 Worker 的内部 HTTP 请求使用 JSON 封装上述 Job，并携带 Base64 编码的
`source_archive`、`test_patch` 和 `fix_patch`。这些传输字段不是模型工具参数。Worker
必须先认证，再校验 Job Schema、过期时间、Artifact 是否存在、大小和 SHA-256；同一
`job_id` 对应不同请求指纹时返回冲突，不能覆盖已有结果。

## 7. Sandbox Result

```text
job_id, status, exit_code, timed_out
started_at, finished_at, duration_ms
junit_summary
stdout_tail, stderr_tail
docx_summary
workspace_diff_sha256
resource_summary
error_code
```

`junit_summary` 除总测试数、failures、errors 和 skipped 外，还包含
`target_collected`、`target_outcome`、`target_failure_type` 与最大 1 KB 的脱敏
`target_message`。Controller 只依据这些 XML 解析字段判定，不解析 pytest stdout。

DOCX 断言固化在 Sandbox 镜像的只读 `/opt/trusted/docx_assertions.py`，测试通过固定
`PYTHONPATH` 调用。`docx_xpath` 是兼容的 Oracle 类型名，不允许模型传入 XPath；模型
只能从 `valid_zip`、`required_parts_present`、`xml_parseable`、
`minimum_table_count`、`minimum_math_count`、`minimum_drawing_count`、
`paragraph_style_present`、`text_absent` 和 `three_line_table_structure` 中选择已登记断言
及普通数据参数。

stdout/stderr 各自最多保留 4 KB，先清理控制字符和密钥模式。结果内容仍视为不可信
数据，只能作为下一轮模型的带边界输入。

Worker 对已完成 `job_id` 持久化结构化结果；相同请求重试直接返回原结果，不重复启动
容器。运行时 workspace diff 与授权补丁不一致时返回 `security_rejected`，不能把该次
执行结果交给后续验证器。

Controller侧Sandbox Client对连接异常或Worker返回408、429、5xx默认只额外重试一次，
且必须复用相同`job_id`和`Idempotency-Key`。无效200响应、401、400和409不重试，避免把
确定性错误变成重复请求。

## 8. 最终验证结果

`ValidationResult` 是 Publisher 唯一接受的发布凭据：

```text
passed: bool
base_sha: string
source_snapshot_sha256: string
test_patch_sha256: string
fix_patch_sha256: string
target_test_selector: string
baseline_reproduction:
  executed: bool
  expected_failure_observed: bool
target_validation:
  passed: bool
full_validation:
  passed: bool
  tests: int
  failures: int
  skipped: int
  baseline_skipped: int
docx_validation:
  passed: bool
  checks: object
changed_files: string[]
validated_patch_ref: string
validated_patch_sha256: string
failure_code: string?
failure_summary: string?
```

`passed=true` 必须由 Controller 根据所有子结果计算，不能由模型或 Worker 直接提供。
执行完成后生成的 workspace diff 必须与 test/fix patch 的授权组合一致，否则结果为
`security_rejected`。

## 8.1 发布契约

Publisher 输入由 Controller 从 `ValidationResult`、`validated.patch` 和固定源码快照
重新构造，结构上不包含原始 Markdown、description 或 contact：

```text
PublicationRequest
  feedback_id
  validation
  validated_patch
  files(path, content|null)
  evidence(category, risk, versions, usage, trace_url)
```

进入 GitHub 前必须同时满足：`validation.passed=true`、补丁内容 SHA-256 与
`validated_patch_sha256` 一致、重新应用后文件集合与 `changed_files` 一致、所有路径仍
通过 Patch Policy。输出仅有 `pr_opened` 或 `stale_base`；前者必须含 branch、commit SHA、
PR number/URL，后者不得含任何 GitHub 写入结果。Publisher 不暴露 merge 方法。

## 8.2 Issue 发布契约

Issue 发布与 PR 发布是两个独立契约。它不接受 `ValidationResult`、源码、patch 或文件
集合，也不能复用 `PublicationRequest` 的可选字段拼出“万能 Publisher”。Gate 的模型输出
先经过本地 Policy 形成：

```text
IssueDraft
  title: 单行，1..80 字符
  summary: 1..600 字符
  intent: bug_report | feature_request
  area: backend | extension | cross_component
  category
```

`IssueDraft` 只复述用户明确表达的需求、现象和归属，不生成实现方案或用户未提出的验收
条件。它仍是不可信模型输出；Controller 构造受信请求时必须再次做长度、控制字符、邮箱、
电话、Authorization、Cookie、Secret/Token 赋值和提示注入片段扫描。

```text
IssuePublicationRequest
  feedback_id
  content_fingerprint
  run_ref
  draft
  evidence(graph_version, policy_version, prompt_versions,
           provider, model, usage, trace_url)

IssuePublicationResult
  disposition: issue_opened
  issue_number
  issue_url
  reused
```

请求结构中不得出现原始 `description`、`markdown_content` 或 `contact`。Publisher 根据
`draft.intent` 确定标签：前端/扩展 Bug 使用仓库已有 `bug`，功能、展示、视觉、交互和
布局需求使用仓库已有 `enhancement`；调用方和模型都不能提供任意标签名。

Publisher 只允许固定仓库的 `/issues` API，不提供创建标签、关闭 Issue、编辑 Issue、
分配人员或修改项目面板的方法。正文包含受信模板、脱敏摘要、area/category、run_ref、
Trace URL 与固定隐藏 marker：

```text
<!-- mdtoword-agent-issue run-ref=<12-char-ref> fingerprint=<sha256> -->
```

创建前按 marker 查询开放和关闭的 Issue；命中即返回 `reused=true`。POST 成功但响应丢失时
也按同一 marker 恢复，保证同一反馈和内容指纹最多对应一个 Issue。

## 9. 复现判定

Validator 解析 JUnit，不用正则猜测 pytest 文本：

- 指定测试必须实际被收集和执行；
- `<failure>` 且断言与 Oracle 方向一致才是目标失败；
- ImportError、SyntaxError、fixture 缺失、pytest内部错误和超时是 `invalid_test`；
- 测试直接通过是 `not_reproduced`；
- 非目标测试先失败是 `baseline_regression`；
- 模型生成的测试不得通过自定义插件、hook 或配置改变报告行为。

`expected_failure_kind=unexpected_conversion_error` 且首轮结果为 `invalid_test` 时，第二轮
不再请求模型重写测试。Controller 确定性生成固定测试与 Markdown fixture：测试只调用
`convert_markdown_to_docx`，成功后调用计划中已登记的受信 DOCX 断言；在缺陷基线上必须由
目标测试产生 `ConversionError`。该模板仍通过相同的路径、AST、selector、补丁规模和
Sandbox Policy，不能扩大修复白名单。

## 10. DOCX Validator

复用 `convert_markdown_to_docx` 和受信断言库，至少支持：

```text
valid_zip
required_parts_present
xml_parseable
minimum_table_count
minimum_math_count
minimum_drawing_count
paragraph_style_present
text_absent
three_line_table_structure
```

分类决定允许使用的断言集合，模型只提供参数，不能提供 XPath 代码、Python回调或
任意脚本。视觉偏好不能通过这些工具自动证明，必须转人工。
