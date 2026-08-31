# Issue 路由与公开展示

## 1. 为什么单独走 Issue

功能需求、前端/扩展缺陷和需要人工产品判断的问题，不能进入只允许修改后端转换代码
的自动修复链。Gate 将它们路由到 issue_required，由受信 Issue Publisher 创建脱敏 Issue。

这条路由不会创建源码快照、测试补丁、Sandbox、修复 PR 或后端代码。

## 2. Issue 内容

Publisher 使用固定仓库、受限标签和幂等 marker。公开标题和正文只包含：

- 脱敏后的简短摘要；
- area/category；
- run 的短引用；
- 必要的模型用量和 Trace URL。

不包含原始 Markdown、联系方式、邮箱/电话、完整源码、命令、Token 或测试日志。标题、
摘要、标签和 marker 先经过结构化 Schema 与敏感模式检查。

## 3. 幂等和恢复

创建 Issue 前先按 marker 查询已有对象。请求超时或进程重启后，使用同一个 run 和 marker
恢复；已创建则复用，不重复创建。Issue Publisher 没有关闭、编辑、分配或项目面板权限。

历史 out_of_scope 只用于读取和展示，不批量转换为 Issue。新运行不使用旧路由名。

## 4. Trace Site 展示

运行列表和详情的业务状态来自 Supabase 公开脱敏视图。Langfuse 只提供异步 observation；
公开站不能根据模型文字重新判断成功或失败。

展示至少正确映射：

~~~text
route、area、category
completed、failed、needs_human、security_rejected 等终态
模型/工具调用、Token、耗时、PR/Issue 唯一计数
~~~

完成回调只发送 run_id 和 status；回调失败不影响 Agent，页面访问时按需补抓。稳定命名的
feedback-repair-run 根尚未索引时，不把孤立子调用伪装成完整 Trace。

## 5. 验收

验证后端 Bug 仍走复现、修复和 PR；功能/前端反馈只走 Issue；注入反馈不产生任何工具
调用。检查 Issue/Trace 不泄露敏感输入、重复恢复只产生一个外部对象、Supabase 统计和
页面展示一致。
