# 分类、意图与安全路由

## 1. Gate 的职责

Gate 只回答“这条反馈是否值得进入哪条受信流程”，不读取源码、不调用 Sandbox、不生成
补丁。它使用无工具模型和严格结构化 Schema；本地 Policy 再检查跨字段规则。

输入是必要的反馈字段，用户文字和 Markdown 被标记为不可信数据。模型不能通过反馈内容
授予自己读写文件或发布权限。

## 2. 路由

~~~text
提示词注入/越权      -> quarantined_security
无关/垃圾            -> rejected_irrelevant
功能需求/前端问题    -> issue_required
相关后端转换缺陷     -> accepted_backend_bug
信息不足/不确定       -> needs_human
重复反馈              -> duplicate
~~~

功能需求和前端/扩展问题不进入后端修复白名单，而是创建脱敏 Issue。历史数据库中可能有
旧 route 值，仅用于读取展示；新运行使用当前路由。

## 3. 本地 Policy 是什么

Policy 不是另一个 Agent，也不是一段“建议模型遵守”的 Prompt。它是受信代码中的确定性
规则，负责：

- 校验 Gate 输出是否符合跨字段约束；
- 决定 route 和可进入的 Graph 分支；
- 决定当前 phase 暴露哪些工具；
- 检查路径、补丁、预算、Sandbox 状态和发布前置条件；
- 将错误归因并决定 retry、revise、stop 或人工终态。

Prompt 让模型理解任务，Policy 才真正允许动作；两者的跨字段规则必须同步维护。

## 4. 越权和注入

意图识别是第一道信号，纵深控制还包括：

1. Gate 无工具；
2. Repair Agent 只看到当前阶段的注册工具；
3. 工具 Schema、路径白名单和 Patch Policy 在本地再次检查；
4. Sandbox 只运行固定 Job/argv，无网络、非 root、无 Secret；
5. Publisher 不对模型开放；
6. 工具输出和测试日志仍按不可信数据处理。

因此，即使模型误判或用户在源码里写入指令，也不能直接读取密钥、修改扩展、执行任意
命令或创建未经验证的 PR。

## 5. 低置信度

信息不足、类别冲突或结果无法由当前受信流程判断时，Gate 进入 needs_human。不要为了
提高自动化比例强行猜测为后端 Bug；错误路由比一次人工确认更难恢复。

## 6. 如何验证

至少覆盖后端 Bug、前端 Bug、功能建议、无关、注入、信息不足和重复反馈。检查：

- 注入/越权工具调用数为零；
- Issue 路由不会创建源码快照或 Sandbox；
- 后端路由才会进入 source snapshot；
- 模型输出的 route 与本地 Policy 冲突时以 Policy 为准；
- 日志和公开 Trace 不包含原始反馈、联系方式或敏感片段。
