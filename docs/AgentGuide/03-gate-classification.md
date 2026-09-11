# 03 · 反馈分类与路由设计（Gate）

本章说明认领后的第一道业务关卡如何把反馈转换为稳定路由。反馈分类器（Gate）负责理解自然
语言并返回结构化事实；本地策略（Policy）负责校验这些事实并决定业务出口。两者职责分离，
是本系统防止模型误判扩大为执行权限的关键设计。

本文首次出现的几个术语含义如下：提示词注入（Prompt Injection）是把用户内容伪装成模型
指令、试图改变系统行为的输入；路由（route）是反馈下一步进入的固定分支；内容指纹（content
fingerprint）是反馈正文的哈希摘要，用于识别重复提交；结构化 Schema（字段结构约束）用于
限制分类结果的形状；认领令牌（claim token）用于确认当前运行拥有反馈更新权；隔离执行环境
（Sandbox）用于执行受信测试；代码合并请求（PR）和问题或需求单（Issue）是 GitHub 协作对象。

## 1. Gate 要解决的问题

入口只负责保存反馈，自动修复链不能接收所有内容。系统需要在进入源码和 Sandbox 之前判断：

| 判断问题 | 可能结果 | 后续风险 |
|---|---|---|
| 是否属于产品问题 | 后端缺陷、扩展问题、功能需求、无关内容 | 进入错误的处理链 |
| 是否存在越权意图 | 正常描述或提示词注入 | 将不可信内容当作指令执行 |
| 是否具备最小证据 | 信息充分或无法复现 | 无证据修改代码 |
| 是否已有相同反馈 | 新问题或重复问题 | 重复调用模型和创建 PR |

Gate 的输出只能作为分类信号，不能直接获得文件、执行或发布权限。安全、状态和路由的最终
决定必须由本地代码完成。

## 2. 两个角色：分类模型与本地 Policy

### 2.1 Gate 模型只填写分类表

Gate 模型接收反馈类型、问题描述和 Markdown 的最小必要字段，返回严格结构化的
`GateClassification`（分类结果结构）。分类维度包括：

| 字段 | 含义 |
|---|---|
| `intent` | Bug 报告、功能需求、无关、垃圾或未知意图 |
| `area` | 后端、浏览器扩展、跨组件、无关或未知区域 |
| `category` | 后端转换崩溃、公式/表格/标题/列表解析、Word 文档（DOCX）结构、后端归一化等类别 |
| `relevance` | 与本产品相关性的 0～1 分数 |
| `sufficient_information` | 是否达到继续处理所需的最低信息量 |
| `injection_suspected` | 是否怀疑包含提示词注入或越权意图 |
| `requires_extension_change` | 是否需要修改扩展 |
| `reason` | 受限长度的分类理由 |
| `issue_title`、`issue_summary` | Issue 分支使用的脱敏标题和摘要 |

调用接口显式传入空工具集合 `tools=()`，因此模型不能读取源码、运行代码、修改数据库或
创建 GitHub 对象。模型可以判断语义，但不能通过输出额外字段扩大分类契约。

### 2.2 Policy 按固定优先级拍板

本地 Policy 是受信代码，将分类结果归一化后映射到有限的 `GateRoute`（Gate 路由）集合。
它不会根据模型解释放宽条件，也不会把模型返回的“允许执行”当作授权。

拆分的理由是：自然语言理解需要模型的泛化能力，而安全边界和业务状态需要确定性。模型即使
被输入影响，也只能改变分类建议，不能直接改变最终路由。

## 3. Gate 的执行顺序

外层 LangGraph（业务流程编排框架）将 Gate 拆成三个固定节点：

```text
start_gate
  -> 校验反馈所有权和 claim token
classify_gate
  -> 先执行确定性检查；必要时调用无工具 Gate 模型
  -> 使用本地 Policy 生成最终路由
route_feedback
  -> 持久化分类摘要，按路由进入终态或后续阶段
```

### 3.1 `start_gate`：先确认所有权

上一章取得的 claim token 可能已经因租约过期而失效。`start_gate` 会重新读取反馈并确认当前
运行仍拥有有效 token，只有确认成功才将反馈从 `claimed` 推进到 `gating`（分类中）。如果
条件更新失败，当前运行停止，不能继续消耗模型预算。

### 3.2 `classify_gate`：先执行确定性检查

无需模型即可判断的情况优先处理：

| 检查 | 条件 | 结果 |
|---|---|---|
| 描述有效性 | `description` 为空或超过 1000 个字符 | `needs_human` |
| Bug 输入完整性 | `feedback_type=bug` 且 Markdown 为空或超过 50 KiB | `needs_human` |
| 重复反馈 | 内容指纹命中另一条开放处理记录 | `duplicate` |

重复查询覆盖仍在处理或已经产生结果的开放状态，并排除当前反馈自身。重复判断位于模型调用
之前，避免为同一问题再次启动处理；它也使已有反馈记录成为重复关系的事实来源。

确定性检查没有命中时，才调用无工具 Gate 模型，并将输出交给 Policy。

## 4. Policy 的固定路由优先级

Policy 按以下顺序执行，命中一项后立即停止：

```text
1. injection_suspected=true
      -> quarantined_security
2. intent=unrelated 或 spam
      -> rejected_irrelevant
3. 功能需求或扩展缺陷
      -> 信息、相关性、区域或 Issue 脱敏草稿不足：needs_human
      -> 条件满足：issue_required
4. 明确的转换报错证据
      -> accepted_backend_bug / conversion_crash
5. 明确的公式输出失败证据
      -> accepted_backend_bug / formula_parsing
6. 其他后端 Bug
      -> 相关性、信息量、意图和类别均满足：accepted_backend_bug
      -> 任一条件不满足：needs_human
```

### 4.1 安全和无关内容优先

提示词注入或越权意图的优先级高于其他分类。即使同一反馈同时看起来像真实 Bug，只要
`injection_suspected` 为真，就进入 `quarantined_security`（安全隔离），不能进入模型修复、
Sandbox 或发布。

无关、垃圾和普通问答进入 `rejected_irrelevant`（无关拒绝）。这两个分支只保留脱敏分类摘要，
不把用户全文传播到后续执行面。

### 4.2 Issue 分支需要最小公开信息

功能需求或扩展缺陷不进入后端自动修复，而是可能进入 Issue 发布分支。Policy 要求同时满足：

- 相关性达到最低阈值，当前默认值为 `0.80`；
- `sufficient_information=true`；
- 区域明确为后端、扩展或跨组件；
- `issue_title` 和 `issue_summary` 均存在且通过字段校验。

如果条件不满足，进入 `needs_human`，而不是让模型补全一个可能泄露用户内容的公开条目。
通过条件时只使用脱敏草稿，原始描述和联系方式不进入 Issue；发布细节见 [08 · 发布边界](08-publishing.md)。

### 4.3 后端自动修复的类别白名单

普通后端 Bug 必须满足相关性、信息量和意图要求，且类别属于以下白名单：

```text
conversion_crash
formula_parsing
table_parsing
heading_parsing
list_parsing
docx_structure
backend_normalization
```

白名单之外的类别进入 `needs_human`。白名单使自动修复范围可审查，也防止模型通过自定义类别
将扩展、部署或其他受信模块纳入修改范围。

### 4.4 明确转换错误和公式输出错误的特殊处理

如果反馈类型为 Bug，且描述中包含明确的转换报错证据，Policy 可以将其归一化为
`conversion_crash` 并进入后端修复链；它仍必须具备非空 Markdown，最终能否复现由 Sandbox
决定。

如果描述明确指出 Word/DOCX 中公式文本化或丢失，Policy 可以归一化为 `formula_parsing`。
这两个规则减少相邻类别之间的模型波动，但不跳过后续的固定源码快照、基线复现和独立验证。

## 5. 输出与业务状态

`route_feedback` 将 Policy 结果写入运行制品（Artifact，即保存大对象和结果引用的存储），
再按照稳定路由推进反馈：

| `GateRoute` | 反馈下一状态 | 后续动作 |
|---|---|---|
| `accepted_backend_bug` | `reproducing` | 固定源码版本，进入复现与修复链 |
| `issue_required` | `publishing_issue` | 使用脱敏草稿创建 Issue |
| `duplicate` | `duplicate` | 结束，复用已有处理事实 |
| `rejected_irrelevant` | `rejected_irrelevant` | 结束 |
| `quarantined_security` | `quarantined_security` | 结束并保留安全摘要 |
| `needs_human` | `needs_human` | 结束自动流程，交维护者判断 |

路由值、反馈状态和运行状态不是同一字段：`GateRoute` 描述分类出口，反馈状态描述业务生命
周期，运行状态枚举（`AgentRunStatus`）描述本次运行的执行阶段。状态转换由领域层统一校验，模型不能自行跳转。

## 6. 设计取舍

| 设计 | 解决的问题 | 代价 |
|---|---|---|
| 模型只返回结构化分类 | 获得自然语言理解能力，限制输出形状 | 需要维护分类 Schema 和格式修正 |
| Gate 无工具 | 分类阶段不会产生文件、执行和发布副作用 | 模型无法在分类时自行查证源码 |
| Policy 固定优先级 | 防止模型用解释改变安全和路由 | 新类别必须修改本地规则和验收 |
| 明确错误证据可快速放行 | 让硬证据直接进入有界复现 | 仍需要后续 Sandbox 验证，不能直接视为成功 |
| 信息不足转人工 | 避免模型猜测测试断言和公开内容 | 一部分反馈不能自动处理 |

## 7. 实现关联

| 能力 | 主要实现 |
|---|---|
| Gate 模型调用 | `agent/gate.py`、`agent/prompts/gate.md` |
| 分类 Schema | `agent/domain/gate.py`、`agent/domain/enums.py` |
| 确定性检查与路由 Policy | `agent/domain/policy.py` |
| 外层节点与状态迁移 | `agent/graph.py`、`agent/domain/transitions.py` |
| 内容指纹查重 | `agent/repositories/supabase.py` |

通过 Gate 的后端反馈还不能立即修改代码。下一章说明如何固定 `main` 的版本、保存源码快照，
并建立模型可以读取和修改的边界。
