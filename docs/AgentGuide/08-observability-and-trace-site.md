# 可观测性与 Trace Site

## 1. 三类数据各自负责什么

| 数据源 | 负责 | 不负责 |
|---|---|---|
| Supabase/PostgreSQL | 反馈、run 状态、阶段、终态、用量和发布 URL | 大日志和完整源码 |
| checkpoint | Repair Agent 消息、工具循环和恢复位置 | 公开展示和业务统计 |
| Artifact | 源码快照、patch、JUnit、DOCX 结果和完整受控日志 | 模型权限和业务状态 |
| Langfuse | 脱敏 generation/tool observation | 业务状态、发布授权 |
| Trace Site | 从 Supabase 和脱敏快照展示运行摘要 | 重新决定运行是否成功 |

Trace Site 缺少 Langfuse observation 不等于 Agent 没有执行；以 Supabase 状态和 Artifact
验证结果为准。

## 2. 每个运行要能回答什么

运行摘要至少能定位：

~~~text
run_id / feedback_id（公开页面只使用脱敏引用）
route、phase、node、status
base_sha、prompt/model/policy 版本
model_calls、tool_calls、token/cache、耗时
FailureSnapshot：kind、code、component、operation、attempt、handling、safe_details
reproduction、repair、validation 和 publication 结果
~~~

错误位置由 Controller 和 Middleware 注入，不能依赖模型自报。未知异常也要有安全异常
类型、阶段和节点。

## 3. 脱敏规则

默认不上传：

- 原始反馈全文、联系方式和邮箱/电话；
- 完整源码、完整 patch、命令、环境变量和测试日志；
- API Key、GitHub Token、数据库 URL、Webhook Secret；
- 能直接关联用户身份的路径和请求头。

允许上传字段级错误路径、状态、计数、耗时、有限日志尾部和 hash。invalid_response 的
safe_details 只保留 schema error path，不把含校验器文案或用户正文的 hint 送入 Trace。

## 4. Trace 树和异步索引

一次 run 使用稳定命名的 feedback-repair-run 作为根，Gate、Repair Agent 模型轮次、工具
和验证作为子 observation。发送前尽量 flush，但 Langfuse 的索引可能晚于请求返回。

Trace Site 只有找到稳定根和必要子节点才固化快照；孤立调用、半棵树和真正零调用运行
要区分处理。完成回调只发送 run_id 和 status，回调失败不能改变 Agent 终态。

## 5. 调试路径

用户给出 run URL 或 UUID 后，维护者按顺序查看：

1. 页面阶段、终态、error_code；
2. Supabase 中 agent_runs 的 failure 和用量；
3. Controller/Scheduler/Worker 日志；
4. checkpoint 中最后的工具调用和 required_action；
5. Langfuse 对应 generation/tool observation；
6. Artifact 中 JUnit、patch hash 和 DOCX 摘要。

不要把完整日志直接粘到公开 Issue 或模型提示词中；先用 FailureSnapshot 的安全字段
缩小范围。

## 6. 指标和评估

持续观察：

- 各 route 和终态数量；
- 复现成功率、修复成功率、最终验证通过率；
- provider、tool、Sandbox 和 publication 错误分布；
- 重试次数、恢复成功率、stale_base 次数；
- 模型/工具调用、输入输出/cache token、耗时和估算成本；
- 重复 PR/Issue、越权拒绝和脱敏扫描结果。

这些指标用于发现规则和 Prompt 退化，不直接让模型自行修改 Prompt 或 Policy。改变前
先离线评估，再由维护者审查。
