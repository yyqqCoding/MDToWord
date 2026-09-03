# Agent 阅读指南(大白话版)

这份指南把"反馈修复 Agent 是怎么工作的"讲清楚:它是什么、为什么这样设计、一份用户反馈
进来后每一步发生了什么、中间出错了会怎样。面向想理解整体的人(包括你在面试前快速回顾),
不面向调试具体故障——排障细节请看
[docs/AgentRequirements/deployment-and-operations.md](../AgentRequirements/deployment-and-operations.md)。

本目录是"讲给人听的版本":只做解释,不定义契约。规则、权限和安全边界以
[docs/AgentRequirements/](../AgentRequirements/README.md) 为准,两边说法冲突时以 Requirements 为准。

## 一句话:这个系统在干什么

你的产品是"把 AI 生成的 Markdown 网页内容转成 Word"。用户在浏览器里装了扩展,点一下就能
把网页内容转成 Word。但转换偶尔出错。**这个 Agent 系统 = 一台自动修 Bug 的机器**:用户把
"这段 Markdown 转出来坏了"连同出错的那段 Markdown 一起提交上来,机器自动完成——

1. 先当"安检员"判断这条反馈值不值得处理、有没有捣乱成分;
2. 如果值得,就自动"复现"这个 Bug:把代码固定在当时最新的版本,写一条测试,真跑一遍证明
   它确实会坏;
3. 让一个受限的 AI 去读源码、改代码、在隔离容器里反复试,直到改好;
4. 在全新的容器里独立复查一遍(防止 AI"自卖自夸");
5. 全部通过后,自动创建一条 GitHub PR(拉取请求),由人类维护者最后审核、合并。

这套系统里的"不可信输入"原则贯穿始终:用户反馈、AI 模型输出、工具返回、测试日志……全都
被当作可能撒谎、可能带恶意的数据;只有**本地写死的代码**(Policy)能决定权限和最终判断。
AI 只在"当前这一步我能做什么"的范围内自主选择,永远拿不到真正的写文件、执行任意命令或
发 PR 的权力。

## 这篇指南怎么组织

主线是"一份反馈的完整旅程"。每一章解决旅程里的一段,每段都先把里面的名词讲成大白话:

| # | 章节 | 这段旅程发生了什么 |
|---|---|---|
| [01](01-feedback-life.md) | 一份反馈的一生 | 用一段连续的故事把整条流程走一遍,先有个全局印象 |
| [02](02-entry-and-scheduler.md) | 入口与签收 | 反馈怎么进来、怎么存、谁负责"认领"一个任务去处理 |
| [03](03-gate-classification.md) | 安检 Gate:先判断要不要管 | 安检员怎么把反馈分成六类,各类分别去哪 |
| [04](04-code-version-freeze.md) | 把代码"锁死"在某一版 | 为什么要先固定代码版本,再开始复现和修复 |
| [05](05-repair-agent-deep-dive.md) | 复现与修复:核心 AI 干活的一章 | 唯一"AI 自由发挥"的地方:先证明 Bug 存在,再把它修好 |
| [06](06-repair-outcomes.md) | 复现/修复结束后去哪 | 成功、失败、转人工、预算耗尽——各自怎么收场 |
| [07](07-independent-final-validation.md) | 独立终验:不信 AI 的自夸 | 在全新容器里重跑一遍,四种证据齐了才算数 |
| [08](08-publishing.md) | 发布:变成 PR / Issue | 把验证通过的结果交给维护者;main 变了怎么办 |
| [09](09-failures-and-recovery.md) | 出错与恢复 | 失败怎么分类、谁该重试、进程死了怎么接着干 |
| [10](10-cross-cutting.md) | 三件横切的事 | 权限控制 / 隔离沙箱 / 可观测(运行网页、Trace) |

> 章节里的"人名"(Gate、Policy、Scheduler 等)都是代码里的真实组件名,它们的含义在第 3 节
> 术语表里一次讲清,正文里不会反复解释。

## 黑话/术语表(建议通读一次)

下面按"一句话人话 → 它是代码里的哪个东西"来写,按出现顺序排。

| 术语 | 一句话人话 | 对应代码/位置 | 会拦在哪一章细讲 |
|---|---|---|---|
| **外层流程 / LangGraph** | 一条**流水线**,由一段段写死的代码按顺序执行,决定"现在该进行到哪个环节" | `agent/graph.py`(`StateGraph`),本地代码在 langgraph 库里按我们注册的顺序跑 | 全程的主线 |
| **节点 (node)** | 流水线上的**一个环节**;每个环节是一小段写死的 Python 函数 | `graph.py` 里的 `start_gate`、`classify_gate`、`route_feedback`、`prepare_source`、`repair_agent`、`validate_final`、`publish_pull_request`…… | 每章按节点逐个讲 |
| **内层循环 / ReAct / create_agent** | 流水线里**唯一让 AI 自由发挥**的一环:AI 反复"看结果→想→调工具",直到干完或放弃 | `agent/repair_agent/`,由官方 `create_agent` 生成 | [05](05-repair-agent-deep-dive.md) |
| **Gate(安检员)** | 一个**没有工具**的模型调用,专门判断"这条反馈该不该管、该走哪条路" | `agent/gate.py` | [03](03-gate-classification.md) |
| **Policy(本地守则)** | 一段**写死的 Python 代码**,是真正的"谁说了算":检查 AI 的输出是否合规、放不放行 | `agent/domain/policy.py` 及各校验器 | [03](03-gate-classification.md) |
| **Controller(指挥)** | 流水线入口的"总指挥":把一次反馈变成一次"运行",组装好所有依赖再启动流水线 | `agent/controller.py`(`GateController`) | [02](02-entry-and-scheduler.md) |
| **Scheduler(轮询员)** | 一个后台循环,**一次只领一个任务**,领了就跑流水线;跑完再领下一个 | `agent/scheduler.py` | [02](02-entry-and-scheduler.md) |
| **claim / claim token(认领 + 认领牌)** | 数据库里"**这个任务现在归谁**"的记号。认领时系统生成一个随机"认领牌",之后每次改这条记录都**必须出示同一张牌**,防止两个进程抢同一任务 | `feedback.claim_token` 字段;认领动作调 `agent/migrations/001_agent_foundation.sql` 里的 `claim_next_agent_feedback` | [02](02-entry-and-scheduler.md) |
| **lease(认领的"保质期")** | 认领不是永久的:有个**时间上限(默认半天量级)**。到期后任务自动释放回队列,别的进程可以再认领——防止"认领后进程死了,任务永远没人管" | 由 SQL 函数按 `claimed_at` 过期判断 | [02](02-entry-and-scheduler.md) |
| **run / run_id(一次运行)** | **一次"处理这份反馈"的完整过程**,有全局唯一编号。一次反馈被认领后生成一次 run | `agent_runs` 表 | [02](02-entry-and-scheduler.md) |
| **快照 / 固定版本 (snapshot / base_sha)** | 开始干活前,把**当时 GitHub main 分支那份源码整个复制一份只读副本**,并记下它的提交号 `base_sha`。之后所有测试、修复都在这个副本上做,谁也别想偷偷换版本 | `SourceWorkspace.prepare()` | [04](04-code-version-freeze.md) |
| **conversion probe(转换探针)** | 一个**不花模型钱**的确定性小测试:先把用户那份 Markdown 原样跑一遍转换,看它是不是**直接崩溃**。结果决定复现走哪条路 | `agent/repair_agent/tools.py` 的 `run_conversion_probe` | [05](05-repair-agent-deep-dive.md) |
| **patch(补丁)** | 一次对源码的**改动清单**(改哪个文件、删哪几行、加哪几行)。AI 只能"提议补丁",能不能落地由本地守则审查 | `patch` 系列 Artifact | [04](04-code-version-freeze.md) |
| **Artifact(大件仓库)** | 存**大文件**的地方:源码副本、补丁、测试结果 JUnit、日志。运行状态里只存它们的**引用(路径)**,不把大文件塞进状态 | `agent/workspace/artifacts.py` | [04](04-code-version-freeze.md) |
| **checkpoint(断点存档)** | 流水线每走一步就把当前进度**存一份档**(存到数据库)。进程崩了可以从**上一个存档点**接着跑,而不是从头来 | 基于 PostgreSQL 的 LangGraph checkpointer | [09](09-failures-and-recovery.md) |
| **状态 (AgentState)** | 这次运行到目前为止的**一张小卡片**:现在到哪一步了、有了哪些文件引用、用了多少预算 | `agent/state.py` | 各章 |
| **沙箱 / Worker(隔离车间)** | 一个**和主程序完全隔离的容器**,专门用来"真跑一遍测试/代码"。容器没网、不是管理员、跑完就销毁——AI 的代码即使有毒也伤不到主机 | `agent/sandbox/` | [05](05-repair-agent-deep-dive.md)、[10](10-cross-cutting.md) |
| **Job(车间工单)** | 发给车间的一张**写死的工单**:跑哪个测试、用什么版本、带哪两个补丁。AI 只能挑"我要验证",不能挑工单内容 | `SandboxJob` | [05](05-repair-agent-deep-dive.md) |
| **复现 (reproduce)** | **在没修过的代码上,把用户说的问题"真跑出来一次"**:写一条会失败的回归测试,证明基线代码确实坏 | 内层 `reproducing` 阶段 | [05](05-repair-agent-deep-dive.md) |
| **修复 (repair)** | 在复现成功后,让 AI 去**改代码**,直到那条测试通过 | 内层 `repairing` 阶段 | [05](05-repair-agent-deep-dive.md) |
| **终验 (validate_final)** | 在**全新的容器**里,把"测试补丁 + 修复补丁"重跑三遍(基线/目标/全量),再查 Word 结构和文件哈希——AI 自己说的"我修好了"不算数 | `validate_final` 节点 | [07](07-independent-final-validation.md) |
| **Publisher(发布器)** | 外层里**唯一能碰 GitHub** 的环节:创建 PR 或 Issue。模型和 AI 都没有 GitHub 权限 | `agent/publishing/github.py` | [08](08-publishing.md) |
| **stale_base(main 变了)** | 验证做完了,准备发 PR 时发现 **main 分支已经前进、代码不是我们测的那版了**,这个补丁不能直接发 | —— | [08](08-publishing.md) |
| **Trace / 可观测(运行记录)** | 每一次运行的"**体检报告**":花了多少模型钱、每一步多久、在哪失败。脱敏后展示在 Trace Site 网页上 | Langfuse + `trace-site/` | [10](10-cross-cutting.md) |

## 角色表(人类 & 程序)

| 称呼 | 是谁 | 能做什么 |
|---|---|---|
| **维护者(你/面试官)** | 真人 | 审核并合并 PR、改配置、部署、在真实 Word 里肉眼确认效果。**是唯一能真正上线修改的人** |
| **用户** | 真人 | 在扩展里提交反馈和 Markdown |
| **模型 / AI** | 大模型 | 只在被允许的环节里被调用:安检分类、出测试/修复补丁、总结上下文 |
| **本地写死的代码** | 程序 | 决定"谁能做什么":白名单、权限、预算、发布。模型说"我通过了"不算数 |

## 阅读提示

- **先读 [01](01-feedback-life.md)**。它不展开名词,只把整段旅程讲成一个故事,好让你有全局。
- 之后按 02→10 顺序读。每章开头都会用一句话"回顾我们讲到哪了"。
- 面试想找"怎么问 / 怎么答",请看
  [docs/AgentProblem/InterviewGuide/](../AgentProblem/InterviewGuide/agent-interview-questions.md)。

## 现状与旧文档

旧版 AgentGuide 把复现/修复描述成一串固定节点,与当前"一个 AI 节点内层自由循环"的实现不符,
也已不维护。当前唯一的现实只有:**外层流水线固定节点** + **内层 repair_agent 的 create_agent
循环**。新读者请只依据本文。
