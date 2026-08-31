# 修复循环与独立验证

## 1. 修复阶段的目标

修复不是让模型“解释得更像正确答案”，而是让最小后端补丁同时满足：

1. 基线测试仍然证明原问题存在；
2. 修复后目标回归测试通过；
3. 全量测试没有回归；
4. DOCX 结构符合受信断言；
5. 补丁只触碰允许的后端文件。

## 2. Repair Agent 工具

复现确认后开放：

~~~text
search_source
read_source_file
submit_fix_edits
run_sandbox
complete_repair
report_blocked
~~~

模型先读实现和已有测试，再提交结构化 Edit。Workspace 负责应用编辑、生成 patch、
检查白名单和计算 hash；模型永远不直接写工作树。

## 3. 候选修复循环

~~~text
读取相关实现
  -> submit_fix_edits
  -> run_sandbox(validate_target)
  -> 观察 JUnit/转换结果
  -> 必要时继续读取或提交下一候选
  -> 目标通过后 complete_repair
~~~

只读查询可并行；修复写入、Sandbox 和完成工具串行。目标测试失败时模型可以根据结构化
结果继续尝试，但每次候选都受文件、预算和轮次限制。

## 4. 为什么还要独立验证

模型和内层 Sandbox 只证明“当前目标结果符合”。外层 validate_final 会创建新的容器和
workspace，重新应用已校验的 test.patch 与 fix.patch，执行：

- 基线目标测试；
- 修复后目标测试；
- 后端全量 pytest；
- DOCX 部件、文本、样式、公式等受信检查；
- 编译、diff 和 Artifact hash 校验。

只有外层验证通过，Publisher 才能看到可发布 Artifact。模型不能调用、跳过或修改
validate_final。

## 5. 修复范围

自动 PR 只允许 backend/app/normalizer.py、backend/app/pandoc_runner.py。测试文件和
固件属于复现阶段；扩展、配置、依赖、Agent、Dockerfile 和部署文件永远不由 Agent 自动
修改。

如果真实缺陷需要扩展或平台依赖，Agent 只能进入 issue_required 或 needs_human，由维护者
另行设计和审核，不能扩大白名单。

## 6. 修复失败

| 结果 | 处理 |
|---|---|
| 补丁参数可修正 | ToolMessage 返回具体字段和下一步 |
| 目标测试仍失败 | 同一 run 继续候选，直到预算/策略上限 |
| 目标通过但全量或 DOCX 失败 | 返回验证证据，继续修复或人工处理 |
| 补丁越权/削弱测试 | security_rejected |
| 预算耗尽 | budget_exhausted，可在提高预算后显式恢复 |
| 始终无法得到可信修复 | report_blocked，外层结束为 needs_human |

PR 的正文应包含基线失败、目标/全量验证、修改文件、风险和 Trace 链接，但不包含完整
反馈、联系方式、源码或 Secret。
