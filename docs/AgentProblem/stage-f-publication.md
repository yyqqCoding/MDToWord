# 阶段 F：GitHub PR 发布问题/解决方案

## 问题 1：普通 GitHub HTTPS 密码推送返回 403

GitHub 不接受账户密码作为 Git 操作凭据；账号、仓库权限或 Token 权限不正确时，即使
用户名正确也会得到 permission denied。

### 解决方案

人工开发推送使用正确授权的 PAT/凭据管理器。Agent 发布不复用个人密码或源码读取 Token，
而使用只安装到目标仓库的 GitHub App，并只授予 Contents 与 Pull Requests 读写权限。

## 问题 2：GitHub App 配置正确性难以在不写仓库的情况下确认

私钥换行、App ID、安装仓库或权限任一错误都会在完整运行最后才暴露。

### 解决方案

提供 `python -m agent.publishing.check` 只执行 JWT、安装和最小权限预检，不创建分支、
commit 或 PR。只有返回 `github_app_ready` 后才执行真实 `--publish`。

## 问题 3：Langfuse Trace URL 中的 `{trace_id}` 不清楚如何配置

Cloud 控制台复制出的链接通常是某条具体 Trace 地址，直接填写会让所有 PR 指向同一条
记录。

### 解决方案

配置 `LANGFUSE_TRACE_URL_TEMPLATE`，保留字面量 `{trace_id}` 占位符，由 Publisher 在
生成 PR 正文时替换为本次运行的稳定 Trace ID；模板和最终 URL 都不包含 Secret。

## 问题 4：CLI 返回 `completed=true` 但 `pr_url=null`

Graph 到达终态不等于发布成功。早期输出只显示 completed，发布失败时容易被误认为已创建
PR。

### 解决方案

CLI 增加 `status`、`error_code`、`published` 和 `pr_url`。真实发布成功必须同时满足
`published=true`、`error_code=null`、`pr_url` 非空；Graph 完成只表示本次状态机不再继续。

## 问题 5：隐私扫描把补丁哈希误判为电话号码

真实 validated patch 的 SHA 以连续数字开头，被通用电话正则命中，导致分支已创建但 PR
正文发送前返回 `publication_failed`。

### 解决方案

PR 正文只使用不含 contact、description 和 Markdown 的结构化机器元数据，因此对该字段
关闭电话模式，同时继续检测邮箱、Bearer 和 Secret/Token 赋值。新增哈希回归测试，避免
为了通过发布而整体关闭脱敏。

## 问题 6：网络失败或进程中断可能重复创建 PR

发布已经在 GitHub 成功但响应丢失时，简单重跑会创建重复分支或 PR；重新运行 D/E 又会
浪费模型和 Sandbox 成本。

### 解决方案

分支、commit 标题和 PR marker 由 feedback/category/patch hash 确定。Publisher 先查找
已有 marker，再执行写入；`publication_*` 错误允许用同一 `run_id` 只恢复发布 checkpoint，
不重跑 Gate、模型或五个 Sandbox Job。

## 问题 7：发布时主分支可能已经变化

validated patch 基于旧 `base_sha`，直接发布可能把过期修复应用到新主分支。

### 解决方案

发布前检查 `current_main_sha == base_sha`。不一致时零 GitHub 写入并把反馈重排一次；第二次
仍过期转 `needs_human`。Agent 没有自动 rebase、merge 或部署入口。

## 问题 8：首次真实发布暴露 Runtime 装配 `TypeError`

Fake Publisher 路径没有触发真实 Repository 构造，直到 `--publish` 才发现参数重复。

### 解决方案

修复 Runtime wiring，并新增真实配置装配测试；发布前分别执行配置检查、GitHub App 预检、
Agent 全量测试和后端回归，确保错误在 GitHub 写入前暴露。
