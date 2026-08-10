# MD To Word 用户反馈自动修复 Agent

本目录是 Agent 项目的唯一权威设计来源。系统读取真实用户反馈，使用自托管
Agent 服务自动判断、复现并修复后端缺陷，在隔离沙箱中完成确定性验证，最后向
GitHub 创建 Pull Request，由维护者人工审核和合并。

## 已确认决策

- Agent 运行在自建服务中，不使用 GitHub Actions 执行任务；
- GitHub 继续作为源码仓库、分支、Pull Request 和人工审核平台；
- 使用 LangGraph 组织可恢复的状态图，在复现和修复阶段使用有限 ReAct 循环；
- 使用 Docker Worker 执行模型生成的测试和修改后代码；
- 使用 Langfuse 记录端到端 Trace、模型用量、工具调用、耗时和结果；
- 反馈通过安全与相关性门禁后自动进入复现和修复，不设置人工批准节点；
- 只自动修改后端，禁止修改 `extension/`；前端问题只分类为超出范围；
- 验证通过后自动创建 PR，但绝不自动合并；
- 用户只提交原 Markdown 和问题描述，不采集 `expected_behavior`；
- 插件版本从 `extension/dist/manifest.json` 读取，不逐条向用户采集版本；
- 每次运行固定一个 `base_sha`，最终产物记录一个 `validated_patch_sha256`；
- MVP 使用一个模型完成门禁、复现规划、测试生成和修复生成。

## 文档结构

| 文档 | 唯一负责的内容 |
|---|---|
| [requirements.md](requirements.md) | 目标、范围、反馈路由、成功标准与非目标 |
| [architecture.md](architecture.md) | 部署组件、依赖方向、状态机与数据所有权 |
| [agent-runtime.md](agent-runtime.md) | LangGraph 状态、节点、有限 ReAct 和恢复语义 |
| [tool-contracts.md](tool-contracts.md) | 模型可用工具、结构化编辑、验证任务与结果契约 |
| [security-and-sandbox.md](security-and-sandbox.md) | 信任边界、权限、Prompt Injection、Docker 与补丁策略 |
| [observability.md](observability.md) | Trace ID、Langfuse、Token/成本、日志与脱敏 |
| [implementation-plan.md](implementation-plan.md) | 实施顺序、每阶段交付物与验收证据 |

策略只维护一份：权限、路径白名单和沙箱约束只在
`security-and-sandbox.md` 定义；状态转换只在 `architecture.md` 定义；工具请求与
结果 Schema 只在 `tool-contracts.md` 定义。其他文档通过引用使用，不复制规则。

## 当前实现状态

截至 2026-08-10：

- 阶段 A、B1 和 B2 已完成；
- 阶段 B3 的真实模型分类、Prompt Injection 隔离、Langfuse Trace、Token 统计和默认
  脱敏已通过手工验收；
- 维护者暂不配置模型单价，因此 `agent_runs.estimated_cost` 当前保持 `0`，阶段 B 的
  数据库成本持久化验收延后；
- 阶段 C 至 G 尚未开始，当前 CLI 只运行 Feedback Gate，不读取源码、不启动沙箱、
  不修改代码，也不创建 PR。

可直接执行的配置和命令见 [agent/README.md](../../agent/README.md)。阶段划分、历史检查点
和验收证据以 [implementation-plan.md](implementation-plan.md) 为准；本文档中的目标
架构不表示对应组件已经实现。

## 目标链路

```text
Supabase feedback
  -> 自托管 Agent Controller
  -> Feedback Gate
  -> LangGraph 复现与修复
  -> Docker Sandbox 确定性验证
  -> GitHub Pull Request
  -> 维护者审核与合并
```

## 设计状态

本目录描述稳定方案。实现进度与验收结果统一记录在
[implementation-plan.md](implementation-plan.md)，不再维护历史版本文档。
