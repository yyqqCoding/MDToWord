# 10 · 横切设计：权限、隔离执行环境（Sandbox）与可观测性

前面各章按业务阶段说明了反馈如何被接收、分类、复现、修复、验证和发布。本章说明三类不依赖
单个节点、但贯穿整个生命周期的设计：权限控制、隔离执行和运行观测。它们共同回答一个问题：
为什么系统可以在有限范围内使用模型的探索能力，同时仍能控制安全和业务风险。

本文首次引入的术语如下：受信控制面是能够决定权限、状态、执行和发布的本地组件集合；外层
流程图（Graph）承载固定业务阶段；外层结构化状态（Graph State）保存可恢复进度；结构化编辑
（Edit）是模型提交候选改动的输入形式；补丁
策略（Patch Policy）是规定源码读写范围和补丁规模的本地规则；制品存储（Artifact）保存快照、补丁
和验证结果；处理运行记录（AgentRun）保存一次反馈处理实例；Word 文档格式（DOCX）用于表示导出文档；
提示词（Prompt）是发送给模型的结构化输入；模型计量单位（Token）用于统计模型用量；JUnit（测试结果报告格式）
用于保存测试摘要；业务状态存储（Supabase）保存反馈和运行状态；隔离执行环境（Sandbox）是在
受限一次性容器中执行固定任务的环境；执行工作进程（Worker）负责校验并运行任务；运行观测
（Telemetry）记录脱敏后的阶段、工具和用量信息；端到端追踪（Trace）是一次运行的观测树；
Langfuse 是观测后端，Trace Site 是面向维护者的脱敏展示站。

## 1. 权限控制：模型只提出候选，受信组件作决定

系统将用户反馈、模型输出、源码、测试结果、工具结果和外部 API 响应全部视为不可信数据。
能够改变权限、状态或外部副作用的组件组成受信控制面。代码合并请求（PR）和问题或需求单
（Issue）是 Publisher 可能创建的 GitHub 协作对象：

| 组件 | 负责决定 | 明确不能做什么 |
|---|---|---|
| 控制器（Controller） | 装配依赖、启动运行、协调外层 Graph 和恢复 | 不在宿主机执行模型代码，不自动合并 |
| 本地策略（Policy） | 路由、阶段工具、文件路径、补丁规模和状态条件 | 不根据模型解释放宽边界 |
| 验证器（Validator） | 分类 Sandbox 结果、计算最终验证结论 | 不接受模型自报成功 |
| 发布器（Publisher） | 校验发布输入并创建 PR/Issue | 不提供发布工具给模型，不合并或部署 |
| Sandbox Worker | 校验结构化 Job 并启动隔离容器 | 不接收模型命令，不持有模型、数据库或 GitHub 凭据 |

模型只能请求当前阶段已注册的工具、提交候选 Edit（结构化编辑）和提供受限说明。每个候选
动作还要经过工具函数、Policy、Worker 或 Validator 的第二次检查；模型没有直接写数据库、
改变状态或扩大权限的路径。

## 2. 三层权限锁

### 2.1 补丁策略

补丁策略以机器可读的 `patch_policy.json` 保存，并在运行时加载。它同时约束读取范围、
写入阶段、文件类型、改动数量、增删行数和补丁大小。模型不能修改这份策略。

### 2.2 最小工具面

Gate（反馈分类器）不注册任何工具；Repair Agent（修复代理）只获得搜索、读取、提交测试、
提交修复、运行固定 Sandbox 和完成/阻塞等工具。工具按照阶段和子状态动态收窄，未注册工具
没有执行入口；函数内部仍要再次校验，不能把工具可见性当作唯一安全边界。

### 2.3 最小凭据

GitHub App（受限仓库应用身份）令牌只在受信发布进程内短期存在。Sandbox Worker 使用独立的
Bearer（持有者令牌）认证，不接触 Controller、数据库或 GitHub 凭据。生产 Secret（秘密凭据）
只能通过部署环境注入，不进入模型消息、Graph State、checkpoint、Artifact、日志、Trace、
PR、Issue 或任务容器。

## 3. 当前源码读写边界

读取范围和修改范围有意不相等：模型需要读取调用关系，但自动补丁只覆盖可以安全审核的后端
实现和回归测试。

| 用途 | 允许范围 |
|---|---|
| 可读 | `backend/app/**/*.py`、`backend/tests/**/*.py`、`backend/pyproject.toml`、`AGENTS.md`、`README.md` |
| 复现阶段可写 | `backend/tests/test_feedback_regressions.py`、`backend/tests/fixtures/feedback/` 下的文本固件 |
| 修复阶段可写 | `backend/app/normalizer.py`、`backend/app/pandoc_runner.py` |

补丁还必须满足当前策略的规模限制：

| 限制 | 上限 |
|---|---:|
| 单文件 | 80 KiB |
| 单次工具输出 | 20 KiB |
| 改动文件数 | 5 个 |
| 新增行数 | 300 行 |
| 删除行数 | 150 行 |
| 补丁大小 | 200,000 字节 |

编辑应用后还要检查文本编码、二进制、符号链接、权限变化、重命名、子模块、
`git diff --check` 和测试削弱。测试阶段与修复阶段的写入路径互斥；越界补丁直接进入安全拒绝，
不会因为模型解释而放行。

## 4. Sandbox：把不可信执行隔离出主机

### 4.1 Worker 与任务容器的边界

Sandbox Worker 只监听受控主机的 `127.0.0.1:8090` 或内网地址，不公开 Docker Socket。Controller
发送结构化 SandboxJob（隔离任务描述），Worker 先完成 Bearer 认证，再解析有大小上限的请求，
校验源码归档、补丁哈希、任务过期时间和幂等键。

任务容器只接收经校验的源码快照和补丁，执行固定 `argv`（命令参数数组）。Worker 不解析模型
自然语言，也不接受命令字符串、工作目录或环境变量；任务结束后销毁临时容器和工作区。

### 4.2 容器约束

每个 Job 使用新的临时容器，当前约束包括：

| 约束 | 当前设置 | 目的 |
|---|---|---|
| 镜像 | 固定 SHA-256 digest（镜像内容摘要） | 保证执行环境可重复 |
| 网络 | `--network=none` | 阻断外部访问和数据外传 |
| 文件系统 | 根文件系统只读，仅挂载临时工作区和结果目录 | 限制持久化修改 |
| 权限 | 非 root UID/GID、`cap-drop=ALL`、`no-new-privileges` | 不授予容器特权 |
| 资源 | 内存最多 2 GiB、CPU 最多 2、进程最多 256 | 控制资源消耗 |
| 时间 | 单 Job 最长 900 秒 | 防止任务长期占用 Worker |
| 临时空间 | 512 MiB、`noexec`、`nosuid`、`nodev` | 限制可写和可执行区域 |
| 凭据 | 不挂载业务 Secret、Docker Socket 或宿主机敏感路径 | 防止凭据和主机泄露 |
| 生命周期 | `--rm`，执行后销毁 | 不保留运行残留 |

容器执行前后分别计算工作区差异。最终差异必须等于授权补丁产生的差异，否则结果标记为
`SECURITY_REJECTED`，不按普通测试失败处理。

### 4.3 固定 Job 与幂等

允许的任务类型由本地枚举映射到固定命令：

| Job 类型 | 固定用途 |
|---|---|
| `reproduce_target` | 基线加测试补丁，运行目标测试 |
| `validate_target` | 基线加测试和修复补丁，运行目标测试 |
| `validate_full` | 基线加测试和修复补丁，运行全量测试和 Word 文档（DOCX）检查 |
| `compile_patch` | 应用补丁后执行编译和 diff 检查 |

`job_id`（任务标识）由 `run_id`、阶段和轮次确定性生成；相同任务重试时复用任务标识、请求
指纹和幂等键。Worker 对已保存的相同结果直接返回，不重复执行容器；不同请求指纹复用同一
任务标识则作为冲突拒绝。

## 5. 可观测性：记录过程，但不改变事实来源

### 5.1 运行标识和 Trace 树

一次运行使用一组稳定标识：

| 标识 | 用途 |
|---|---|
| `feedback_id` | 用户反馈记录 |
| `session_id` | 同一反馈多次运行的稳定关联 |
| `run_id` | 一次处理实例，也是恢复和对账主键 |
| `trace_id` | 从领取到终态的端到端观测标识 |
| `observation_id` | 单个阶段、模型或工具观测 |
| `job_id` | 一次 Sandbox 任务 |
| `operation_id` | 外部副作用的幂等标识 |

Trace 的稳定结构为：

```text
feedback-repair-run
  -> claim-feedback
  -> classify-intent
  -> prepare-source
  -> repair-agent
       -> 模型调用 / 源码查询 / 补丁提交 / Sandbox / 完成工具
  -> validate-final
       -> reproduce-target / validate-target / validate-full / DOCX
  -> publish-pr 或 publish-issue
  -> finalize
```

观测名称保持稳定，轮次、版本和状态写入 metadata（元数据）；不能把动态 UUID 拼进观测名称。
Repair Agent 跨复现和修复阶段复用模型、源码工具和 Sandbox 工具，因此阶段统计优先读取受信
`phase` 字段，不能仅从观测名称猜测。

### 5.2 脱敏规则

所有发送到 Langfuse 和 Trace Site 的结构化值先经过统一 Masking（脱敏）处理：

- `contact`、Authorization、Cookie、API Key、Token、环境变量等敏感键的值整体替换；
- 邮箱、手机号、Bearer 令牌和疑似 `key=value` 秘密赋值替换；
- 用户 Markdown 和 description 用哈希、字节数和类别摘要表示；
- 源码和补丁只保留路径、增删行数和 SHA-256；
- 标准输出和错误输出只保留限长、脱敏的尾部摘要；
- 单条文本最多 300 个字符；
- 完整 Prompt、模型原文、完整源码、完整补丁、联系方式和完整日志不上传。

脱敏规则要避开 SHA、UUID 和镜像 digest 中的数字片段，避免为了隐藏手机号而破坏版本和补丁
对账信息。用户输入不能触发“临时开启完整内容调试”；如需调试，必须由维护者针对单次运行
显式配置，并在结束后恢复默认。

### 5.3 业务状态与观测分离

| 数据 | 事实来源 | 用途 |
|---|---|---|
| Feedback 路由和终态 | Supabase | 调度、业务页面和状态迁移 |
| AgentRun 汇总 | Supabase `agent_runs` | 阶段、用量、结果和失败摘要 |
| 内层消息与工具状态 | 私有 PostgreSQL checkpoint | 恢复同一条工具线程 |
| 快照、补丁、JUnit、验证结果 | Artifact | 保存大对象和发布凭据 |
| 模型/工具耗时与调用详情 | Langfuse 和结构化日志 | 成本、排障和评估 |

Langfuse 的异步状态、索引延迟或缺失观测不能覆盖 Supabase 的业务结论。Trace Site 只展示
脱敏投影，不展示原始反馈、源码、补丁、Prompt、密钥、完整错误正文或 `safe_details`。

### 5.4 运行结束通知

运行进入终态后，Controller 在通知前显式 flush（刷新待发送观测），然后向 Trace Site 推送
`run_id` 和状态，不推送内容。通知采用 at-most-once（最多一次）语义：丢失时站点可以按需
补抓，不影响业务结果。站点从 Supabase 读取运行摘要，从 Langfuse 异步补抓观测；展示缺失
不代表阶段没有执行。

观测适配器和 FailureRecorder（失败记录器）均为 fail-open（观测失败不阻断业务）设计。Langfuse、
Trace Site 或日志系统不可用时，主流程仍按业务状态完成收场。

## 6. 横切设计的整体取舍

| 选择 | 收益 | 代价 |
|---|---|---|
| Policy/Validator/Publisher 组成受信控制面 | 模型探索不会直接获得权限或发布能力 | 需要在多个边界重复校验 |
| 固定 Job 的一次性容器 | 执行可审计、可重复且隔离主机 | 灵活实验能力降低 |
| 读写白名单分离 | 保留源码理解能力，限制修改范围 | 跨范围问题需要人工处理 |
| Supabase 与观测系统分离 | 观测故障不改变业务事实 | 需要维护摘要、投影和对账 |
| 脱敏后保留哈希和计数 | 兼顾隐私与排障、发布对账 | 不能通过观测直接查看完整现场 |

这些机制不是彼此独立的补丁：权限控制决定谁能做什么，Sandbox 决定不可信代码在哪里执行，
Telemetry 决定人如何审计执行过程；三者共同维护“模型负责探索，受信代码负责授权、执行、
验证和发布”的边界。

## 7. 实现关联

| 能力 | 主要实现 |
|---|---|
| 路径与补丁 Policy | `agent/policies/patch_policy.json`、`agent/workspace/patch_policy.py` |
| 工具阶段和并行控制 | `agent/repair_agent/middleware.py` |
| Sandbox 契约与 Worker | `agent/sandbox/contracts.py`、`agent/sandbox/worker.py`、`agent/sandbox/docker_runner.py` |
| 观测与脱敏 | `agent/telemetry/` |
| 运行通知与公开投影 | `agent/controller.py`、Trace Site 相关适配器 |

## 8. 全链路结论

```text
反馈入口
  -> 认领与租约：确定当前运行所有权
  -> Gate + Policy：确定是否允许进入哪条业务分支
  -> base_sha + Patch Policy：确定代码版本和读写范围
  -> Repair Agent：在工具循环中提出候选
  -> Sandbox Worker：在固定容器中执行
  -> Validator：独立计算证据
  -> Publisher：有限写入 GitHub
  -> 维护者：审核、合并、部署和确认真实 Word 效果
```

自动化到创建可审核的 PR 或 Issue 为止。最终合并、部署以及主观 Word 视觉效果确认仍属于
维护者职责。
