# 阶段 B：Gate、Runtime 与可观测性问题/解决方案

## 问题 1：反馈范围容易被错误扩大到前端

真实用户遇到的主要情况是后端直接报错，或预览正确但导出的 Word 格式错误。不存在“已知
预览错误仍继续导出”这一有价值的自动修复路径。

### 解决方案

Gate 只接受后端转换异常和 DOCX 结构/格式缺陷。明确需要修改扩展、纯前端视觉问题或
功能建议统一为 `out_of_scope`；后端追平已正确前端行为时允许只修改后端。

## 问题 2：只依赖模型分类会产生不稳定路由

“这是一条测试内容，不需要修复”最初被保守地分到 `needs_human`；Prompt Injection 又必须
优先于相关性和信息充分性处理。

### 解决方案

模型只返回严格 Schema 的分类建议，最终路由由本地 Policy 决定。升级 Gate Prompt 后，
无实际问题的测试内容稳定为 `rejected_irrelevant`；“忽略指令并索要系统提示词”稳定为
`quarantined_security`，且 Gate 注册的工具数始终为零。

## 问题 3：Supabase Session Pooler 忽略连接启动参数中的 `search_path`

首次 `checkpoint setup` 虽然执行成功，却把 Checkpointer 表创建到了 `public`，破坏了
`agent_runtime` 私有 Schema 的隔离设计。

### 解决方案

数据库连接建立后显式执行并验证 `search_path=agent_runtime`；如果发现 `public` 中存在
重复 Checkpointer 表则拒绝启动。清理错误表后重新 setup，并确认浏览器角色对私有 Schema
没有 `USAGE` 权限。

## 问题 4：配置错误最初只返回笼统的 `OperationalError` 或 `ValidationError`

CLI 没有指出是 DSN、必填配置还是结构化模型响应导致失败，排障只能反复试运行。

### 解决方案

启动前按阶段校验配置；已知配置、认证、限流、超时和响应格式错误映射为稳定错误码，CLI
不输出 Secret。Checkpoint setup 保持为唯一允许第三方建表的显式命令。

## 问题 5：Langfuse v4 Mask 回调签名不兼容

自定义 `mask_sensitive()` 只接收位置参数，Langfuse 使用 `mask(data=...)` 调用时连续输出
“unexpected keyword argument 'data'”，随后回退到默认 Masking。

### 解决方案

Mask 函数同时兼容位置参数和 `data` 关键字，增加真实调用形态回归测试。Telemetry 保持
fail-open：Langfuse 认证、上传或 flush 失败只记录脱敏 warning，不改变 Gate 业务结果。

## 问题 6：Langfuse Cloud 曾返回 401

Host、Public Key、Secret Key 或项目区域不匹配时，业务流程完成但 Span 导出失败。

### 解决方案

将三项配置作为同一 Cloud 项目的原子配置检查，先用最小真实 Gate 验证 root Trace 和
Generation，再检查 Token 与 Masking。Langfuse 不作为任务状态事实来源，上传失败不回滚
数据库终态。

## 问题 7：不同 OpenAI 兼容模型对严格 Schema 的支持差异很大

部分模型 Gate 可用，但长源码的测试生成会返回非严格 JSON、503、连接中断或超时；
`/models` 返回 200 只能证明网关在线。

### 解决方案

Provider 使用 `response_format=json_schema`、有限一次格式修正、有限传输重试和稳定错误码。
更换模型后必须用代表性 Chat Completions 请求验证严格 Schema，再执行真实阶段任务；模型
名称和 Base URL 通过 `.env` 配置，不写死在业务代码中。

## 问题 8：未配置模型单价导致数据库成本一直为 0

Langfuse 可以估算成本，但 Controller 没有维护者认可的输入/输出单价，不能自行猜测价格。

### 解决方案

真实 Token 仍完整落库，`estimated_cost` 在未配置价格时明确保持 `0`。维护者将成本配置
定义为可选运营数据，不阻塞阶段 B 功能和安全验收。
