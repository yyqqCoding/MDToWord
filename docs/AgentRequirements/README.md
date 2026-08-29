# MD To Word 用户反馈处理 Agent

本目录是 Agent 项目的唯一权威设计来源。系统读取真实用户反馈，使用自托管
Agent 服务自动判断：后端缺陷在隔离沙箱中复现、修复和确定性验证后创建 Pull Request；
功能需求与前端/扩展缺陷创建脱敏 GitHub Issue，由维护者人工处理。

如果希望先按实际运行顺序理解系统，或准备面试讲解，可以阅读
[AgentGuide](../AgentGuide/README.md)。AgentGuide使用直白语言解释当前实现，但不替代本目录
的接口、安全规则和验收标准。

## 已确认决策

- Agent 运行在自建服务中，不使用 GitHub Actions 执行任务；
- GitHub 继续作为源码仓库、分支、Pull Request 和人工审核平台；
- 使用 LangGraph 组织可恢复的状态图，在复现和修复阶段使用有限 ReAct 循环；
- 使用 Docker Worker 执行模型生成的测试和修改后代码；
- 使用 Langfuse 记录端到端 Trace、模型用量、工具调用、耗时和结果；
- 后端缺陷通过安全与相关性门禁后自动进入复现和修复，不设置人工批准节点；
- 只自动修改后端，禁止修改 `extension/`；前端问题不进入自动修复；
- 相关且信息充分的功能需求，以及前端/扩展缺陷，创建脱敏 GitHub Issue 交由维护者处理；
- `out_of_scope` 只兼容历史运行，新反馈不再产生该路由；
- 验证通过后自动创建 PR，但绝不自动合并；
- 用户只提交原 Markdown 和问题描述，不采集 `expected_behavior`；
- 插件版本从 `extension/dist/manifest.json` 读取，不逐条向用户采集版本；
- 每次运行固定一个 `base_sha`，最终产物记录一个 `validated_patch_sha256`；
- MVP 通常使用一个模型完成门禁、复现规划、测试生成和修复生成；Mermaid 测试生成耗尽
  格式修正，或第一轮测试编辑无效时，Controller 可用固定受信 drawing 模板继续复现。
- 失败处理使用 `FailureCause + RetryPolicy + FailureRecorder`：适配器标准化错误，调用点
  补充阶段和节点，本地纯策略只决定同输入传输调用的 `RETRY/STOP`；格式修正、业务修订、
  受信 fallback 和 `stale_base` 仍由现有 Provider/Graph 所有。短传输调用包含首次在内最多
  三次，按 1 秒、2 秒指数退避，安全、认证、配置、预算和未知错误不重试。当前工作树已完成
  本地实现与自动测试；migration、真实 Docker 验证和生产部署仍待维护者执行。
- 模型 Provider 可选配置一个备用 OpenAI-compatible 接口。它不形成新的供应商领域概念：
  前两次 attempt 使用主接口，第三次使用备用接口，仍共享三次总上限、`transport_retry`
  handling、稳定错误码与统一 `openai_compatible` 观测口径；永久错误不会切换接口。

## 文档结构

| 文档 | 唯一负责的内容 |
|---|---|
| [requirements.md](requirements.md) | 目标、范围、反馈路由、成功标准与非目标 |
| [architecture.md](architecture.md) | 部署组件、依赖方向、状态机与数据所有权 |
| [agent-runtime.md](agent-runtime.md) | LangGraph 状态、节点、有限 ReAct 和恢复语义 |
| [failure-handling-and-retries.md](failure-handling-and-retries.md) | 失败分类、重试策略、最终失败快照与验收契约 |
| [tool-contracts.md](tool-contracts.md) | 模型可用工具、结构化编辑、验证任务与结果契约 |
| [security-and-sandbox.md](security-and-sandbox.md) | 信任边界、权限、Prompt Injection、Docker 与补丁策略 |
| [observability.md](observability.md) | Trace ID、Langfuse、Token/成本、日志与脱敏 |
| [implementation-plan.md](implementation-plan.md) | 实施顺序、每阶段交付物与验收证据 |
| [deployment-and-operations.md](deployment-and-operations.md) | 转换/修复完整链路、Docker 运行位置与启停方式 |

策略只维护一份：权限、路径白名单和沙箱约束只在
`security-and-sandbox.md` 定义；状态转换只在 `architecture.md` 定义；工具请求与
结果 Schema 只在 `tool-contracts.md` 定义；阶段 J 的失败分类与重试决定只在
`failure-handling-and-retries.md` 定义。其他文档通过引用使用，不复制规则；生产行为仍以
已部署 migration、代码版本和 `implementation-plan.md` 的验收状态为准。

## 当前实现状态

截至 2026-08-29：

- 阶段 A、B1 和 B2 已完成；
- 阶段 B3 的真实模型分类、Prompt Injection 隔离、Langfuse Trace、Token 统计和默认
  脱敏已通过手工验收；
- 维护者暂不配置模型单价，因此 `agent_runs.estimated_cost` 当前保持 `0`；真实 Token
  已完整记录，该可选运营字段不阻塞阶段 B 完成；
- 阶段 C 的源码快照、受控工具、补丁 Policy、Sandbox 契约、认证 Client、幂等 Worker
  和 Docker Runner 已实现；自动测试与真实 Docker 容器隔离验收均已通过；
- 阶段 D 自动复现已实现，并通过自动测试、真实 Docker 隔离以及
  Supabase/模型/Langfuse/GitHub/Sandbox 端到端验收；Controller 只有在显式提供
  `--reproduce --provider configured` 时才读取固定 GitHub 快照并启动沙箱；
- 阶段 E 修复循环、预算、独立验证和 `validated.patch` 已实现并通过自动测试与真实
  Docker 验收；`--repair --provider configured` 可执行完整 D+E，也可续跑阶段 D 的
  `repairing` checkpoint。历史真实运行已确认 Mermaid 缺陷复现和当时无渲染器时的
  `needs_human/external_dependency_required` 安全终态；2026-08-12 经维护者确认后，
  已新增生产/Sandbox 同版本的本地 Mermaid CLI + Chromium 平台能力，Mermaid 可继续
  进入自动修复和 drawing 验证，模型仍不能修改依赖或部署文件；
- 阶段 F GitHub App Publisher、固定分支/提交/PR、基线过期与幂等恢复已实现；真实
  run 已创建 PR #1 并由维护者审核合并，公式转换崩溃反馈又自动创建 PR #2；
- 阶段 G 的 12 条离线评估、Fake E2E 发布场景和默认关闭的生产 Scheduler 已实现；真实
  Provider 的 Gate/自动化精确率/Schema/注入召回均为 100%，注入误报为 0；合并后的
  Render 部署与原 Mermaid 反馈回放已成功完成；独立 Linux ECS 上的 Worker 与 Scheduler
  已通过一键安装、审计和 systemd 常驻验收。生产反馈已验证无关内容进入
  `rejected_irrelevant`，已修复的 Mermaid 问题经受信回退和 Docker 复现后进入
  `cannot_reproduce`，未创建无效 PR；真实公式反馈已完成“复现、修复、独立验证、创建
  PR”全链路。阶段 A～G 的开发、生产部署与小流量验收完成。
- 阶段 H 的公开反馈入口 IP 限流已于 2026-08-18 完成实现、自动验证和 Render 生产黑盒
  验收，采用单 worker 进程内滑动窗口，不引入 Redis、数据库 migration、验证码、浏览器
  指纹或临时 IP 诊断日志；分钟/小时/每日/全局窗口、并发、失败关闭、插件 `429` 行为、
  伪造头防绕过以及 Wi-Fi/手机流量身份区分均已验证。维护者又于 2026-08-19 完成生产
  插件人工验收；Edge 商店 `0.3.3` 发布构建已准备，尚不记为商店已发布。
- 阶段 I 的功能需求/前端缺陷 Issue 路由、细分类别和 Trace Site 统计正确性已于
  2026-08-24 完成本地实现与自动测试；migration、GitHub App Issues 权限、真实 Issue
  验收和生产部署仍待维护者执行。
- 阶段 J 的统一失败分类、Provider/Sandbox 三次总 attempt、失败位置补全、最终
  FailureSnapshot、Scheduler 守护和 Trace Site 白名单已完成本地实现；Agent 自动测试与
  Trace Site 测试/类型检查通过；Provider 已在同一三次总 attempt 内支持可选第三次备用
  OpenAI-compatible 接口，Gate 30～120 秒短超时可配置。当前增量尚未生产部署。

可直接执行的配置和命令见 [agent/README.md](../../agent/README.md)。阶段划分、历史检查点
和验收证据以 [implementation-plan.md](implementation-plan.md) 为准；本文档中的目标
架构不表示对应组件已经实现。

运行位置、本地 Docker 是否需要开启以及常驻部署建议见
[deployment-and-operations.md](deployment-and-operations.md)。开发期间遇到的问题与解决
方案按阶段归档在 [AgentProblem](../AgentProblem/README.md)。

## 目标链路

```text
Supabase feedback
  -> 自托管 Agent Controller
  -> Feedback Gate
      |- 后端缺陷 -> LangGraph 复现与修复 -> Docker Sandbox -> GitHub Pull Request
      `- 功能需求/前端缺陷 -> 脱敏 GitHub Issue
  -> 维护者人工处理
```

## 设计状态

本目录描述稳定方案。实现进度与验收结果统一记录在
[implementation-plan.md](implementation-plan.md)，不再维护历史版本文档。
