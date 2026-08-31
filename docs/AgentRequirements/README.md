# MD To Word 反馈修复 Agent

本目录是反馈修复 Agent 的唯一权威需求与运行契约。它描述当前已经实现的系统，不记录
开发过程，也不保留旧实现的兼容路径。

## 系统要解决什么问题

用户通过浏览器扩展提交 Markdown 和问题描述后，Agent 自动完成以下工作：

1. 过滤无关、危险或信息不足的反馈；
2. 将可自动处理的后端转换问题固定到源码版本并在 Sandbox 中复现；
3. 使用受限的 ReAct 工具循环读取源码、提交测试、观察结果并生成修复；
4. 在全新的 Sandbox 中独立验证测试、全量回归和 DOCX 结构；
5. 验证通过后创建 GitHub Pull Request，交给维护者审核和合并；
6. 将功能需求和前端/扩展问题整理成脱敏 GitHub Issue。

Agent 不自动合并、部署或修改扩展，也不把模型的文字结论当成成功证据。

## 当前已经确认的设计

- 外层使用 LangGraph 保存业务状态、阶段边界、恢复点和发布条件；
- 后端复现与修复阶段使用 LangChain `create_agent` 驱动的有限 ReAct 工具循环；
- Gate 是无工具的严格结构化分类；Gate 之后先执行受信 conversion probe；
- 转换直接抛错时由 Controller 固定转换回归测试；转换成功时由 Agent 根据反馈设计语义测试；
- 模型只能调用注册工具，工具权限、路径、补丁、预算和状态由本地代码再次校验；
- 只读源码查询可并行，补丁写入和 Sandbox 始终串行；生产 Worker 维持单并发；
- 模型临时传输失败和幂等 Sandbox 请求最多三次，退避为 1 秒、2 秒；永久错误不重试；
- 主模型前两次失败后，第三次可使用备用 OpenAI-compatible API，仍属于统一 Provider；
- 上下文按主备模型较小窗口的 65%/85% 比例总结或停止，不绑定具体模型的固定 Token 上限；
- 任何候选修复都必须经过全新容器的基线、目标、全量和 DOCX 独立验证；
- GitHub App 只创建 PR/Issue，不提供合并、部署、关闭或修改项目的权限；
- Supabase/PostgreSQL 保存业务状态，私有 checkpoint 保存工具循环，Artifact 保存大对象，
  Langfuse 只保存脱敏观测副本；
- 当前不引入多 Agent、通用 Shell、通用 Filesystem、Skill 或自有沙箱运行时。

## 文档地图

| 文档 | 负责内容 |
|---|---|
| [requirements.md](requirements.md) | 目标、范围、路由、成功标准和非目标 |
| [architecture.md](architecture.md) | 组件、数据流、状态机和所有权 |
| [agent-runtime.md](agent-runtime.md) | 外层 LangGraph、内层 ReAct、恢复和完成判定 |
| [repair-agent-loop.md](repair-agent-loop.md) | 工具、Middleware、并行、Summary 和 ReAct 循环 |
| [tool-contracts.md](tool-contracts.md) | 工具参数、编辑、Sandbox 和验证契约 |
| [security-and-sandbox.md](security-and-sandbox.md) | 不可信输入、权限、路径、容器和补丁边界 |
| [failure-handling-and-retries.md](failure-handling-and-retries.md) | 错误归因、重试、预算和恢复 |
| [observability.md](observability.md) | Trace、日志、指标、脱敏和展示数据 |
| [deployment-and-operations.md](deployment-and-operations.md) | 部署、配置、运行、验收和常见排障 |
| [implementation-plan.md](implementation-plan.md) | 当前阶段状态与关键验收证据 |

每条规则只在一个文档中定义。其他文档只引用，不复制同一份白名单、状态转换或重试表。
代码和已执行的验收是事实来源；文档只描述已经确认的行为。

## 当前状态（2026-08-31）

- 阶段 A～H：已完成开发、自动验证和相应生产验收；
- 阶段 I：Issue 路由和公开展示的生产核心已验收，扩展商店发布不属于 Agent 自动发布；
- 阶段 J：统一失败分类、三次短传输重试、失败位置和最终快照已完成生产验收；
- 阶段 K：`create_agent` ReAct 工具循环、主备模型、工具权限、并行只读、Summary 和预算
  已完成生产验收；真实运行 `7a0acabc-217a-4be1-a1a3-0926282866e1` 完成基线复现、修复、
  独立验证并创建 PR，PR #5 已合并到 `main`（合并提交 `d0ef01f`）；
- 当前系统仍由维护者决定 PR 合并、部署和真实 Word 视觉验收。

## 版本与变更

Graph、Prompt、Policy、工具契约和 Sandbox 镜像版本会写入运行记录。改变其中任意一项都
必须先修改对应权威文档，再更新实现和验收证据。旧 ReAct 之前的固定计划路径不再作为
生产行为或文档参考。
