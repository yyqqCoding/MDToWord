# Agent 修复系统说明

这套文档解释 MD To Word 如何把一条用户反馈安全分类，并把后端缺陷变成经过验证的
GitHub Pull Request、把功能需求和前端/扩展缺陷变成脱敏 GitHub Issue。
它面向两类读者：

- 项目维护者：需要知道数据在哪里、每一步执行什么、失败后如何恢复；
- 面试准备：需要能够用真实项目回答 Agent 状态、工具、安全、沙箱和稳定性问题。

这里不记录开发过程。当前需求和接口仍以
[AgentRequirements](../AgentRequirements/README.md) 为准，历史故障仍放在
[AgentProblem](../AgentProblem/README.md)。如果本目录与权威需求或代码不一致，应修改
本目录，不能反过来用本目录改变系统约束。

流程文档末尾都提供“结合源码看”小节。代码块只摘录决定流程、安全或恢复行为的关键行，
完整上下文以链接到的当前源码为准。

## 建议阅读顺序

| 顺序 | 文档 | 读完后能回答的问题 |
|---|---|---|
| 1 | [完整流程](00-end-to-end.md) | 一条反馈怎样分流到 PR、Issue 或安全终态？ |
| 2 | [反馈提交与保存](01-feedback-submission.md) | 用户数据怎样进入 Supabase？ |
| 3 | [发现和领取反馈](02-scheduling-and-claim.md) | Agent 怎样知道有新反馈？怎样避免重复领取？ |
| 4 | [LangGraph State与恢复](03-langgraph-state-and-recovery.md) | State有哪些字段？宕机后从哪里继续？ |
| 5 | [分类与安全检查](04-classification-and-safety.md) | 哪些反馈允许自动修复？模型说了算吗？ |
| 6 | [复现问题](05-reproduction.md) | 怎样证明问题真实存在？ |
| 7 | [生成修复与最终验证](06-repair-and-validation.md) | 怎样避免“模型说修好了”却实际没修好？ |
| 8 | [发布PR或Issue](07-publishing.md) | 两种发布路线怎样隔离权限并保证幂等？ |
| 9 | [Langfuse与追踪网站](08-observability-and-trace-site.md) | 网站如何近实时获得运行数据？ |
| 10 | [权限控制](09-permissions.md) | 每个组件能做什么、不能做什么？ |
| 11 | [Docker沙箱](10-sandbox.md) | 不可信测试和代码在哪里执行？ |
| 12 | [提示词与工具选择](11-prompts-and-tools.md) | 提示词怎样写？如何避免选错工具？ |
| 13 | [失败、重试与稳定运行](12-failures-retries-and-stability.md) | 模型、工具、数据库或外部服务失败怎么办？ |
| 14 | [面试问答](13-interview-guide.md) | 如何简洁、准确地介绍这个项目？ |
| 15 | [Agent开发88个真实问题](14-agent-questions-88.md) | 如何结合当前代码回答常见Agent实战问题？ |
| 16 | [反馈入口限流](15-feedback-rate-limiting.md) | 滑动窗口怎样实现？为什么当前不使用令牌桶或Redis？ |
| 17 | [Issue分流与公开展示](16-issue-routing-and-public-display.md) | 功能、前端缺陷、无关和注入怎样分类、发布与展示？ |

## 本文档使用的几个词

- **Agent主进程**：运行Scheduler和LangGraph的Python程序。代码中的类名仍可能包含
  `Controller`，本文不单独使用这个抽象词。
- **Agent本地运行文件**：某次修复保存在Agent服务器磁盘上的JSON、补丁和结果文件。
  代码中称为`Artifact`。
- **State快照**：LangGraph执行完一个节点后保存的State，用于服务器重启后继续执行。
  代码和LangGraph文档中称为`checkpoint`。
- **Sandbox Job**：Agent主进程提交给Worker的一份固定格式测试任务，不包含任意Shell
  命令。
- **Trace**：一次运行中的模型调用、工具调用、耗时和结果记录。

后续文档第一次使用这些词时仍会说明具体数据和动作。
