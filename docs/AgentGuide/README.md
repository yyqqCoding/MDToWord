# Agent 阅读指南

这组文档解释当前已经运行的反馈修复 Agent：它是什么、为什么这样设计、一次任务如何
流动、出错后怎样恢复，以及维护者怎样部署和排障。需求、状态和安全边界以
docs/AgentRequirements/ 为准，本目录不重新定义契约。

## 推荐阅读顺序

| 顺序 | 文档 | 你会知道什么 |
|---|---|---|
| 1 | [00-end-to-end.md](00-end-to-end.md) | 一条反馈从提交到终态的完整旅程 |
| 2 | [01-feedback-submission.md](01-feedback-submission.md) | 反馈入口、限流和脱敏 |
| 3 | [02-scheduling-and-claim.md](02-scheduling-and-claim.md) | Scheduler 如何领取和恢复任务 |
| 4 | [03-langgraph-state-and-recovery.md](03-langgraph-state-and-recovery.md) | 外层 Graph、checkpoint 和恢复 |
| 5 | [04-classification-and-safety.md](04-classification-and-safety.md) | Gate、路由和安全隔离 |
| 6 | [05-reproduction.md](05-reproduction.md) | conversion probe、基线复现和测试工具 |
| 7 | [06-repair-and-validation.md](06-repair-and-validation.md) | 修复循环与独立验证 |
| 8 | [07-publishing.md](07-publishing.md) | PR/Issue 发布和人工边界 |
| 9 | [08-observability-and-trace-site.md](08-observability-and-trace-site.md) | 日志、Trace、数据库和公开展示 |
| 10 | [09-permissions.md](09-permissions.md) | Policy、白名单和能力控制 |
| 11 | [10-sandbox.md](10-sandbox.md) | Worker 和隔离容器 |
| 12 | [11-prompts-and-tools.md](11-prompts-and-tools.md) | Prompt、工具循环和上下文总结 |
| 13 | [12-failures-retries-and-stability.md](12-failures-retries-and-stability.md) | 失败归因、重试和恢复 |
| 14 | [15-feedback-rate-limiting.md](15-feedback-rate-limiting.md) | 公网反馈入口限流实现 |
| 15 | [16-issue-routing-and-public-display.md](16-issue-routing-and-public-display.md) | 功能需求 Issue 和 Trace 展示 |

面试准备请阅读 [AgentProblem/InterviewGuide/agent-interview-questions.md](../AgentProblem/InterviewGuide/agent-interview-questions.md)，
不要把历史故障流水账当作当前实现。

## 按问题查找

- 想知道“模型能做什么”：09、11；
- 想知道“为什么不直接让模型改代码”：05、06、09、10；
- 想知道“超时、越权、预算耗尽怎么办”：12；
- 想知道“服务器怎么更新”：AgentRequirements/deployment-and-operations.md；
- 想知道“当前做了哪些阶段”：AgentRequirements/implementation-plan.md。
