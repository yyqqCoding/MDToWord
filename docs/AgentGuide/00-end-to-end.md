# 一条反馈如何走完

## 1. 先说结论

用户提交的 Markdown、期望结果和联系方式进入 Feedback API。Agent 不会直接相信反馈，也
不会让模型直接执行代码。它按下面的顺序把“不可信描述”变成“可审查的后端补丁”：

~~~text
提交
  -> Gate 分类和安全判断
  -> 按路由结束，或固定 main 快照
  -> conversion probe
  -> Repair Agent 工具循环
  -> 独立全量验证
  -> PR / Issue / 人工终态
~~~

## 2. Gate 之后的三条路

| 反馈 | 后续动作 |
|---|---|
| 无关或垃圾 | rejected_irrelevant，结束 |
| 提示词注入、越权或安全风险 | quarantined_security，工具调用为零 |
| 信息不足、无法判断 | needs_human |
| 功能需求、前端/扩展问题 | issue_required，创建脱敏 Issue |
| 相关且可验证的后端转换问题 | accepted_backend_bug，进入修复 |

Gate 是无工具的严格结构化调用；本地 Policy 负责最终路由。模型说“应该创建 PR”不具备
发布权限。

## 3. 后端修复主流程

### 3.1 固定版本

Controller 读取 GitHub main 的 base_sha，生成只读源码快照。反馈正文、快照和 run_id
绑定到本次运行，后续工具不能切换到另一个版本。

### 3.2 先探测转换错误

受信 conversion probe 只回答一个问题：当前 Markdown 在后端转换阶段是否直接抛错。

- 抛错：Controller 固定生成转换回归测试；
- 不抛错：Repair Agent 根据反馈设计语义测试；
- 测试无法稳定证明问题：cannot_reproduce 或 needs_human；
- probe 不代替最终 DOCX 结构验证，也不判断主观视觉偏好。

### 3.3 ReAct 工具循环

内层使用官方 create_agent。模型可以在当前阶段选择下一项已授权动作：

~~~text
search/read 源码
  -> submit_test_edits 或 submit_fix_edits
  -> run_sandbox
  -> 读取结构化结果
  -> 继续编辑，或调用 complete_* / report_blocked
~~~

模型不能选择文件白名单、命令、容器权限、数据库状态或发布动作。只读查询可以并行；
补丁写入和 Sandbox 串行。

### 3.4 独立验证和发布

模型调用 complete_repair 只表示当前目标测试通过。外层 Graph 仍在新容器中检查：

1. 基线确实失败；
2. 修复后目标测试通过；
3. 全量测试没有回归；
4. DOCX 结构和受信断言通过；
5. patch、base_sha 和 Artifact hash 一致。

全部通过才由 Publisher 创建 PR；功能需求和前端问题由 Issue Publisher 创建脱敏 Issue。
维护者负责 Review、Merge、部署和 Word 视觉确认。

## 4. 出错时发生什么

错误会被归因为 kind、code、component、operation、phase、node、attempt 和 handling。
暂时性模型/Sandbox 传输错误最多三次，等待 1 秒、2 秒；认证、越权、永久配置和未知
异常不做盲目重试。工具参数或缺少前置产物会作为 ToolMessage 返回给模型，在同一 run
修正。

如果进程重启，外层数据库状态和内层 repair:<run_id> checkpoint 共同恢复；已完成的
Sandbox、PR、Issue 通过幂等键和 hash 不重复执行。预算耗尽不会自动清零，维护者提高
预算后才能显式续跑。

## 5. 一次运行的可信结果

页面上的阶段和耗时来自 Supabase 业务记录；详细模型调用和工具调用来自脱敏 Langfuse；
大补丁、JUnit 和日志保留在受控 Artifact。Trace 缺失只表示观测未同步，不代表阶段没有
执行。最终以数据库终态、验证 Artifact 和 PR/Issue 实际结果为准。
