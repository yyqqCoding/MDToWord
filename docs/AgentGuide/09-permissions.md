# Policy、工具和权限

## 1. Policy 到底是什么

Policy 是 Controller 中的确定性授权层，不是 Prompt，也不是一个需要模型调用的 Agent。
它把当前阶段、状态、路径、补丁和预算转换为“允许/拒绝/恢复”的决定。

Prompt 负责告诉模型目标和可用动作；Policy 负责真正放行动作。模型看不到 Policy 文件，
所以所有跨字段规则必须同时写进提示词和本地校验。

## 2. 阶段能力

~~~text
Gate：无工具
reproducing：search/read、submit_test_edits、run_sandbox、complete_reproduction
repairing：search/read、submit_fix_edits、run_sandbox、complete_repair
任意阶段：report_blocked、write_todos
外层：源码快照、final validation、PR/Issue Publisher
~~~

工具未注册就没有执行入口；工具函数仍要检查当前阶段、checkpoint、前置产物、路径、
预算和幂等关系。

## 3. 读写白名单

读取白名单允许诊断 backend/app 和 backend/tests 的必要 Python 文件，以及项目摘要和
Agent 规则。写入白名单只允许后端转换实现和反馈回归测试。读取和写入是两份独立权限，
“能读”不代表“能改”。

扩展、依赖、配置、Agent、Dockerfile、部署文件、密钥和测试基础设施不在自动写入范围。
真实缺陷超出范围时路由 Issue 或人工处理，不能让模型扩大白名单。

## 4. 参数和补丁校验

所有路径必须是仓库相对路径，拒绝绝对路径、..、仓库外符号链接和敏感文件。结构化 Edit
必须生成可审查 patch，search_replace 恰好命中一次，禁止二进制、权限变化、重命名、
重叠编辑、删除或弱化测试。

Sandbox 命令、Job ID、base_sha、测试选择器、超时和容器参数由 Controller 生成；模型
只能传简短 reason。Publisher 只接受 final validation 通过且 hash 匹配的 Artifact。

## 5. 并行和重试也是权限

模型一次响应可以返回多个工具调用，但本地只允许多个无副作用的 search/read 并行。写入、
Sandbox、完成工具和发布动作必须单独执行。Worker 本身串行。

暂时性模型/Sandbox 传输错误最多三次，等待 1 秒、2 秒；认证、越权、Schema/Policy、
配置和未知错误不做盲目重试。预算和 recursion limit 也由本地计数器控制。

## 6. 如何检查权限没有失效

测试应覆盖：

- 未注册、跨阶段、缺少前置产物和重复副作用；
- 路径穿越、黑名单文件、越界补丁和测试削弱；
- 多个只读调用并行、写入与 Sandbox 冲突拒绝；
- retry 只对明确 transient 生效；
- 注入反馈工具调用数为零；
- Publisher 拒绝未验证或过期 Artifact。

任何安全拒绝都要保留阶段、节点、组件、错误码和安全原因，但不公开敏感输入。
