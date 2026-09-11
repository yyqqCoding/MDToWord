# 反馈修复代理（Agent）方案设计

本文档面向参与反馈修复 Agent 开发、维护和评审的开发者，说明系统的设计目标、组件边界、
端到端流程以及关键设计取舍。重点回答两个问题：

1. 系统由哪些部分组成，它们如何协作；
2. 为什么采用当前方案，以及这个方案承担了哪些约束和代价。

本文描述的是当前已经实现的系统，不记录开发过程，也不提出未经验证的未来行为。

## 1. 文档定位与边界

本目录属于设计说明层，不是运行契约。各类文档的职责如下：

| 文档层 | 主要回答的问题 | 权威性 |
|---|---|---|
| [AgentRequirements](../AgentRequirements/README.md) | 系统必须做什么、禁止做什么、如何验收 | 唯一权威需求与运行契约 |
| AgentGuide | 为什么这样设计、组件如何协作、有哪些取舍 | 设计解释，不重复定义契约 |
| [AgentProblem](../AgentProblem/InterviewGuide/agent-interview-questions.md) | 如何从问题和场景理解、复述系统 | 面试与知识整理 |
| [deployment-and-operations](../AgentRequirements/deployment-and-operations.md) | 如何部署、运行、排障和恢复 | 运维操作说明 |

当本文与代码、测试或 AgentRequirements 不一致时，以代码和已执行的验收事实为准；
涉及需求、权限、状态、数据安全和发布条件时，以 AgentRequirements 为准。

### 1.1 术语约定

本文第一次引入领域术语时，采用“中文含义（英文代码名）”的写法；后续可以使用中文简称，
但涉及代码对象、状态值或接口字段时保留英文代码名。主要术语如下：

| 术语 | 本文中的含义 |
|---|---|
| 反馈修复代理（Agent） | 处理反馈、复现问题并提出修复候选的自动化组件 |
| 控制器（Controller） | 装配依赖、启动并协调外层业务流程的受信组件 |
| 调度器（Scheduler） | 恢复活动运行、领取待处理反馈并启动 Controller 的后台组件 |
| 流程编排框架（LangGraph） | 承载外层固定业务阶段和状态迁移的流程框架 |
| 反馈分类器（Gate） | 只负责对反馈进行结构化分类、不执行工具动作的模型调用 |
| 本地策略校验器（Policy） | 校验模型结果、工具权限、路径、补丁和状态条件的本地代码 |
| 处理运行 / 运行标识（run / run_id） | 一次处理一条反馈的完整执行实例及其唯一标识 |
| 认领令牌（claim token） | 证明当前运行拥有该反馈条件更新权的随机标识 |
| 认领租约（lease） | 认领的有效时间，防止异常退出后任务永久被占用 |
| 制品存储（Artifact） | 保存源码快照、补丁、JUnit 和验证结果等大对象的存储 |
| 检查点（checkpoint） | 保存流程进度和内层工具状态、用于中断恢复的持久化记录 |
| 会话线程（thread） | 一次修复代理（Repair Agent）工具循环使用的、可持续恢复的内层执行线程 |
| 观察—行动工具循环（ReAct） | 模型根据工具结果继续选择下一项已授权动作的执行方式 |
| 修复代理（Repair Agent） | 在复现和修复阶段运行的内层 ReAct 组件 |
| 隔离执行环境（Sandbox） | 在受限容器中执行固定测试任务的隔离环境 |
| 执行工作进程（Worker） | 接收结构化执行任务（Job）并启动一次性隔离执行环境（Sandbox）容器的受信服务 |
| 受信执行任务（Job） | 由本地代码固定测试类型、版本、补丁和命令的结构化任务 |
| 验证器（Validator） | 汇总独立测试、DOCX（Word 文档格式）结构检查和补丁完整性的受信组件 |
| 发布器（Publisher） | 根据验证结果创建 PR（代码合并请求）或 Issue（问题/需求单）的受信组件 |

常见技术名词只在影响设计理解时补充中文含义；例如，代码仓库主分支（main）指项目的主分支，
代码合并请求（PR / Pull Request）指待审核的代码变更，问题或需求单（Issue）指 GitHub
上的协作条目，Word 文档格式（DOCX）指最终导出的文档格式。

## 2. 背景、目标与非目标

用户通过浏览器扩展提交 Markdown 和问题描述后，系统需要判断反馈是否属于可处理的后端
转换问题。如果可以自动处理，系统应在固定源码版本上复现问题、提出候选修复、独立验证
结果，并把通过验证的改动交给维护者审核。

这个问题同时具有几个工程约束：

- 用户反馈可能不完整，也可能包含提示词注入或越权意图；
- 模型能够探索代码，但模型输出和工具结果都不能直接获得系统权限；
- 运行期间主分支（main）可能继续变化，测试结果必须能够追溯到明确的源码版本；
- 测试和修复代码必须在隔离执行环境（Sandbox）中执行，不能影响控制器（Controller）所在主机；
- 模型调用、Sandbox、外部服务和进程本身都可能失败；
- 创建 PR（Pull Request，代码合并请求）属于自动化边界，但合并、部署和真实 Word 视觉确认仍由维护者完成。

系统的目标是：

1. 将反馈路由到拒绝、隔离、人工处理、Issue（GitHub 问题或需求单）或后端自动修复等明确分支；
2. 让自动修复建立在可重复的基线失败证据之上；
3. 将模型的探索能力限制在受信代码提供的工具和文件边界内；
4. 通过独立验证、补丁哈希和发布前检查，形成可以交给维护者审核的证据链；
5. 在进程重启、临时网络失败和重复请求下保持可恢复、可解释和幂等。

系统明确不负责：

- 自动合并 Pull Request 或自动部署；
- 修改浏览器扩展、依赖、部署策略和受信平台模块；
- 为模型提供通用 Shell、通用文件系统、网络或 GitHub 工具；
- 用模型的自然语言结论替代本地状态、验证结果和发布条件。

## 3. 核心设计原则

### 3.1 将所有外部内容视为不可信输入

用户反馈、模型输出、源码、测试日志、工具结果和外部 API 响应都只能作为数据使用。
它们不能自行改变路由、权限、路径、预算、状态或发布结论。

### 3.2 将决策权放在受信控制面

控制器（Controller）、外层流程编排框架（LangGraph）、本地策略校验器（Policy）、
验证器（Validator）、沙箱执行工作进程（Sandbox Worker）和发布器（Publisher）组成受信
控制面。模型只能选择当前阶段已经注册的工具，并提交候选测试或补丁；本地代码再次
校验工具、路径、补丁、预算和状态。

### 3.3 探索可以自主，业务边界不能自主

外层流程编排框架（LangGraph）固定业务阶段和不可跳过的边界；内层 create_agent 只负责在复现和修复
阶段选择下一项已授权动作。这样既保留了读取源码、分析结果和调整补丁所需的探索能力，
又不会让模型决定流程、命令或权限。

### 3.4 先建立证据，再允许产生副作用

自动修复必须先在基线代码上证明目标问题存在，再允许提交修复补丁。候选修复还必须在
全新的 Sandbox 中完成基线、目标、全量测试和 DOCX 结构检查，验证通过后才允许创建 PR。

### 3.5 用持久化状态和幂等操作承受失败

业务状态保存在 Supabase/PostgreSQL，内层工具循环使用私有检查点（checkpoint），大对象使用
制品存储（Artifact），隔离执行环境（Sandbox）和发布使用稳定的幂等标识。恢复时复用原
处理运行（run）和原会话线程（thread），以及已有证据，
而不是重新猜测或重复执行已经完成的副作用。

## 4. 总体架构

~~~text
浏览器扩展
  -> Render 反馈接口（Feedback API）
       -> Supabase（业务状态存储）：feedback（反馈记录）
            -> 私有调度器（Scheduler）
                 -> 控制器（Controller）/ 外层流程编排框架（LangGraph）
                    |- 反馈分类器（Gate）+ 本地策略校验器（Policy）
                    |- 固定源码快照 / 制品存储（Artifact）
                    |- 修复代理（Repair Agent，内层 create_agent 工具循环）
                    |- 独立验证
                    |- PR / Issue 发布器（Publisher）
                    |- Supabase 运行状态
                    +-- 私有 PostgreSQL 检查点（checkpoint）
                    +-- 脱敏观测数据（Telemetry）/ 运行展示站点（Trace Site）
                         |
                         +-- 沙箱客户端（Sandbox Client）
                              -> 私有 Worker
                                   -> 一次性 Docker 容器
~~~

部署边界与执行边界分别处理：

- Render 只承载公开转换和反馈接口；
- Controller、Worker、Docker Socket 和 Agent Secret 位于私有主机；
- Worker 只接受认证的结构化 Job（受信执行任务），任务容器无网络、非 root，执行后销毁；
- GitHub 负责源码和协作对象，不负责调度或执行；
- Supabase 是业务状态事实来源，Langfuse/Trace Site 只是脱敏观测副本。

## 5. 组件职责与数据所有权

| 组件 | 主要职责 | 不承担的职责 |
|---|---|---|
| 反馈接口（Feedback API） | 校验反馈大小、格式和限流条件，写入反馈记录 | 不调用模型、不执行源码、不访问 Sandbox |
| 调度器（Scheduler） | 优先恢复活动运行，再领取新的 pending 反馈 | 不决定业务路由，不绕过 Controller |
| 控制器（Controller）/外层流程图（Graph） | 编排阶段、写入业务状态、调用受信适配器 | 不把模型文字当作状态或权限 |
| 反馈分类器（Gate） | 根据最小必要字段返回结构化分类 | 不使用工具，不读取源码，不改变状态 |
| 本地策略（Policy） | 校验路由、路径、补丁、预算、阶段和发布前置条件 | 不根据模型解释放宽边界 |
| 源码工作区（SourceWorkspace）/制品存储（Artifact） | 固定源码版本、保存快照和大对象引用 | 不允许模型直接写宿主机文件 |
| 修复代理（Repair Agent） | 在当前阶段探索源码、提交测试或修复候选 | 不执行 Shell、网络、GitHub、数据库操作 |
| 沙箱执行工作进程（Sandbox Worker） | 校验结构化 Job 并在隔离容器中执行固定命令 | 不解析自然语言，不接受任意命令 |
| 最终验证器（Final Validator） | 在新容器中生成独立验证结果 | 不接受模型自报结果作为通过条件 |
| 发布器（Publisher） | 根据已验证的补丁创建 PR 或脱敏 Issue | 不合并、不部署、不提供发布工具给模型 |

不同数据使用不同事实来源：

| 数据 | 权威来源 | 设计目的 |
|---|---|---|
| 反馈路由、业务状态和终态 | Supabase | 作为调度和业务页面的事实来源 |
| 外层运行摘要 | Supabase agent_runs | 保存阶段、用量、结果和失败摘要 |
| 内层消息与工具状态 | 私有 PostgreSQL checkpoint | 恢复同一条 ReAct 线程 |
| 源码快照、补丁、JUnit 和验证结果 | Artifact | 避免把大对象塞进状态 |
| 模型、工具和阶段观测 | 脱敏 Telemetry / Langfuse | 只用于分析，不恢复业务状态 |

## 6. 端到端流程

~~~text
pending
  -> claimed
  -> gating
      |- rejected_irrelevant
      |- quarantined_security
      |- needs_human / out_of_scope
      |- publishing_issue -> issue_opened
      +-- reproducing
           -> repairing
           -> validating
           -> publishing
           -> pr_opened
~~~

后端自动修复链的关键阶段如下：

| 阶段 | 主要动作 | 通过条件 |
|---|---|---|
| 接收与认领 | 保存反馈、生成 run（一次处理运行），取得 claim token（认领令牌）和 lease（认领租约） | 任务由一个有效运行持有 |
| Gate 与路由 | 结构化分类、重复检查、本地 Policy 校验 | 进入唯一受信分支 |
| 源码准备 | 固定 main（主分支）的 base_sha（基础提交 SHA），建立并校验源码快照 | 所有后续操作绑定同一版本 |
| 复现 | 执行 conversion probe（转换探针），必要时生成语义回归测试 | 基线按预期失败 |
| 修复 | 在有限工具循环中提出并验证最小修复 | 目标测试通过 |
| 独立验证 | 新 Sandbox（隔离执行环境）执行基线、目标、全量测试并检查 DOCX（Word 文档结构） | 四类证据全部成立 |
| 发布 | 校验当前 main（主分支）、补丁哈希和脱敏内容，创建 PR（代码合并请求） | PR 创建成功，交给维护者审核 |

转换探针把后端问题分为两类：

- 当前转换直接抛出转换错误：Controller 生成固定转换回归测试，避免让模型先猜测试；
- 当前转换成功但结果不符合反馈：Repair Agent 根据反馈、源码和产物设计语义测试，
  并先在基线证明测试失败。

## 7. 关键设计决策

| 决策 | 要解决的问题 | 选择 | 主要代价 |
|---|---|---|---|
| 外层 Graph + 内层 ReAct | 既需要固定阶段，又需要源码探索 | 外层控制业务流程，内层只选择已授权工具 | 状态和恢复逻辑分为两层，需要明确边界 |
| Gate 无工具 + 本地 Policy | 模型可能误判、被注入或声称越权 | Gate 只分类，Policy 决定是否放行 | 需要维护结构化 Schema 和本地校验 |
| 固定 base_sha | main 变化会破坏复现和验证的可比性 | 每个 run 使用不可变源码快照 | 快照需要磁盘、哈希和清理策略 |
| 固定 Sandbox Job | 测试代码可能执行危险操作 | Worker 只接受 Job 类型、固定 argv 和资源限制 | 可验证的场景必须预先建模，灵活性降低 |
| 独立终验 | Agent 可能只让目标测试通过或错误地宣布完成 | 新容器重复基线、目标、全量和 DOCX 检查 | 每次候选修复需要额外执行时间 |
| 业务状态与观测分离 | 观测系统不应改变业务结论 | Supabase 保存业务事实，Telemetry 保存脱敏副本 | 需要维护多套数据投影 |
| PR 不自动合并 | 自动修改仍需代码审查和真实 Word 验收 | Publisher 只创建 PR/Issue | 自动化链路不能直接完成上线 |

这些决策的具体参数和契约分别见 [AgentRequirements](../AgentRequirements/README.md) 中的
架构、运行时、工具、安全、失败处理和可观测文档；本文只解释设计关系，不复制同一份
白名单、状态转换表或重试表。

## 8. 阅读地图

建议先阅读本文和 [01 · 端到端流程与核心设计](01-feedback-life.md)，再按关注点深入：

| 文档 | 设计主题 |
|---|---|
| [02 · 任务接收与调度](02-entry-and-scheduler.md) | 反馈入口、认领（claim）、租约（lease）、运行（run）和单并发 |
| [03 · Gate 与反馈路由](03-gate-classification.md) | 分类、重复检测、本地策略（Policy）和 Issue 分支 |
| [04 · 源码快照与补丁边界](04-code-version-freeze.md) | 基础提交（base_sha）、制品（Artifact）、白名单和补丁来源 |
| [05 · Repair Agent 工具循环](05-repair-agent-deep-dive.md) | ReAct 工具循环、阶段工具、Sandbox 和上下文管理 |
| [06 · 复现与修复的状态收场](06-repair-outcomes.md) | 阶段转换、阻塞、失败和预算终态 |
| [07 · 独立验证与证据链](07-independent-final-validation.md) | 验证矩阵、DOCX 检查和通过条件 |
| [08 · 发布边界](08-publishing.md) | PR/Issue、幂等发布和 stale_base（基础版本过期） |
| [09 · 失败处理与恢复](09-failures-and-recovery.md) | 失败分类、重试、检查点（checkpoint）和续跑 |
| [10 · 横切设计](10-cross-cutting.md) | 权限、Sandbox、脱敏观测和运行展示 |

## 9. 实现索引

| 能力 | 主要实现 | 相关测试 |
|---|---|---|
| 外层 Graph 与状态迁移 | agent/graph.py、agent/state.py | agent/tests/test_graph_runtime.py、test_state.py |
| 入口、认领与恢复 | agent/controller.py、agent/scheduler.py | agent/tests/test_scheduler.py、test_repository.py |
| Gate（反馈分类器）与分类 | agent/gate.py、agent/domain/gate.py | agent/tests/test_gate.py |
| Repair Agent（修复代理）运行时 | agent/repair_agent/runtime.py、middleware.py、tools.py | agent/tests/test_repair_agent_runtime.py、test_repair_agent_contract.py |
| 源码快照与 Artifact（制品存储） | agent/workspace/preparation.py、artifacts.py | agent/tests/test_source_workspace.py、test_artifacts.py |
| 补丁和路径 Policy（本地策略） | agent/workspace/validation.py、patch_policy.py、paths.py | agent/tests/test_patch_policy.py、test_validated_patch.py |
| Sandbox（隔离执行环境） | agent/sandbox/client.py、worker.py、docker_runner.py | agent/tests/test_sandbox_worker.py、test_docker_runner.py |
| PR / Issue 发布 | agent/publishing/github.py、check.py | agent/tests/test_github_publisher.py、test_github_issue_publisher.py |
| 脱敏与观测 | agent/telemetry/masking.py、langfuse.py | agent/tests/test_telemetry.py |

## 10. 维护规则

本文档只描述已确认的设计。修改外层 LangGraph、提示词（Prompt）、Policy、工具契约、
State Schema（状态结构）、Sandbox
镜像或发布边界时，应先更新对应的 AgentRequirements 和验收证据，再同步本文的设计解释。
如果某条规则已经在 AgentRequirements 中定义，本文只保留设计原因和链接，不再复制完整规则。

后续 AgentGuide 文档首次引入领域术语时，也必须在当前文档中给出中文含义和职责，不能只依赖
本 README 的术语表。代码状态、字段和工具名称继续保留英文原名，并在首次出现处解释其业务含义。
