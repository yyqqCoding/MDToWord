# 阶段 A：基线、配置与持久化问题/解决方案

## 问题 1：`agent/` 中的历史原型与新方案无兼容价值

旧目录结构和旧 Prompt 容易让后续实现误以为必须兼容，进而把历史假设带入新的安全和
状态模型。

### 解决方案

经维护者确认后删除无关原型，按领域状态、Repository、Workspace 和适配器边界重建
`agent/`。此后 `agent/` 是当前生产实现，不再视为可随意删除的历史目录；这一稳定约定
写入根目录 `AGENTS.md`。

## 问题 2：数据库起初只有 `public.feedback`，不能直接承载可恢复任务

原表只有 `id`、反馈类型、Markdown、描述、联系方式和创建时间，缺少状态、领取租约、
重试、错误、PR 和运行汇总字段。并发进程直接查询 `pending` 会重复领取同一反馈。

### 解决方案

使用增量 migration 扩展 `feedback`，新增 `agent_runs`，并用
`FOR UPDATE SKIP LOCKED` 的 RPC 实现原子 claim、租约回收和最大尝试次数。Migration 只由
数据库 owner 审查后手工执行，应用启动和测试绝不自动修改 Schema。

## 问题 3：真实反馈包含联系方式和完整 Markdown，不能进入所有运行层

如果把数据库对象直接序列化到日志、Artifact、checkpoint 或模型上下文，容易泄漏
`contact`、Secret 和用户原文。

### 解决方案

从数据模型开始做最小化：Task Artifact 结构上排除 `contact`，checkpoint 只保存状态与
Artifact 引用，日志和错误使用稳定代码，Secret 使用受保护配置类型。内容指纹只用于
精确去重，不保存原文副本。

## 问题 4：本地配置与生产连接串容易混淆

`SUPABASE_URL` 是 HTTP API 地址，不是 LangGraph PostgreSQL Checkpointer 所需的 DSN；
把凭据发到聊天、写进命令历史或提交 `.env` 也会扩大泄漏范围。

### 解决方案

区分 `SUPABASE_URL`、`SUPABASE_AGENT_KEY` 与 `AGENT_DATABASE_URL`。后者必须使用
PostgreSQL Direct Connection 或 Session Pooler DSN。仓库只提交 `.env.example`，真实
值放在被 Git 忽略的 `.env` 或部署 Secret 中，所有错误只报告缺少的配置名。

## 问题 5：真实回归样例放在可变或被忽略的日志文件中

依赖 `logs/runlog.txt` 会使自动测试在不同工作区得到不同输入，无法形成稳定基线。

### 解决方案

把经过清理的最小失败输入放入版本控制内的 `backend/tests/fixtures/`，测试固定 fixture，
日志只用于人工诊断。先记录后端全量测试与最小 DOCX 结果，再开始 Agent 改造。

## 问题 6：运行时 Repository 装配参数重复

真实发布运行曾因 Controller 构造 Supabase Repository 时同时传入重复位置参数而触发
`TypeError`，Fake 测试没有覆盖真实装配路径。

### 解决方案

删除重复参数，新增 `agent/tests/test_runtime.py` 覆盖从配置创建真实 Runtime 的装配契约，
确保 Repository、Provider、Telemetry、Sandbox 和 Publisher 使用唯一且明确的配置来源。
