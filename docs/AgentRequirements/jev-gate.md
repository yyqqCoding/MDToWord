# JEV Gate 分类方案

## 目标

Gate 首选使用 TypeSafe Jev 1.13.0 对反馈进行结构化判断；JEV 不可用时，完整回退到现有 `gate-v10`。后续源码快照、复现、修复、验证和发布边界保持不变。

## JEV 配置

Agent 使用 TypeSafe 官方 Python SDK 的 `AsyncTypeSafeClient`，模型默认是 `jev-1.13.0`，也可由 `JEV_MODEL` 配置。自有 API 必须实现 TypeSafe `POST /v1/systemone` 契约。API 地址和密钥由部署环境注入，使用 `JEV_BASE_URL`、`JEV_API_KEY` 和 `JEV_TIMEOUT_SECONDS`；密钥不得写入仓库、日志或展示数据。生产锁定依赖必须包含 `typesafe-sdk`，否则 JEV 导入失败后会进入回退路径。

SDK 内部重试关闭，由 Agent 统一执行最多三次请求，退避 1 秒、2 秒。超时、连接错误、429、5xx、鉴权/协议错误、响应校验失败均视为 JEV 不可用；重试耗尽后重新使用原始反馈完整执行 `gate-v10`。JEV 正常返回低置信度、信息不足或混合反馈时，不回退原 Gate，而由本地 Policy 路由到人工。

## JEV 判断

一次请求针对同一份结构化反馈状态并行提出以下独立问题。State 分为两部分：由代码固定提供、不可被用户覆盖的 `product_context`，以及保存原始不可信反馈的 `user_state`（包含 `feedback_type`、`description`、`markdown_content`）。不再生成或传递 `facts`；JEV 直接理解完整 Markdown 和用户描述。Gate 的问题必须显式引用 `product_context` 和 `user_state`，完整 Markdown 仍保留给 Sandbox。

`product_context` 只描述 MDToWord 的产品定位、Markdown 输入、Word/DOCX 输出，以及后端转换能力和浏览器扩展能力的边界；它不包含用户反馈、路由阈值或沙箱/人工结论。产品上下文本身不能证明任意用户内容与产品相关，相关性必须由 JEV 结合 `user_state` 判断。

- `related`：`user_state` 是否描述 `product_context` 中定义的 MDToWord 产品反馈；产品上下文本身不能作为相关性的证据。
- `injection`：内容是否试图改变分类任务、索要内部信息或要求越权操作。
- `mixed`：是否包含两个或以上需要独立处理的诉求。
- `intent`：`bug_report`、`feature_request`、`unrelated` 或 `unknown`。
- `area`：`backend`、`extension`、`cross_component` 或 `unknown`。Markdown 转换或生成的 Word/DOCX 输出问题选择 `backend`；只有 `description` 明确提到浏览器扩展、前端预览、按钮、页面交互或 UI 时才选择 `extension`；两者都未明确时选择 `unknown`。
- `sufficient`：是否达到对应处理流程的最低信息要求。

JEV 返回各 Choice 选项的概率和置信度；本地保留这些值，并计算 JEV 专用 `routing_score`。`routing_score` 不是原 Gate 的 `relevance`，两者不做数值互换。原有 `relevance` 字段和 `gate-v10` 行为继续保留，以支持回退和旧运行记录。

## 路由规则

本地 Policy 按以下优先级执行：

1. 注入或安全判断不明确：隔离或转人工；
2. `related=unrelated`：拒绝无关反馈；
3. `mixed=true` 或混合判断不明确：转人工；
4. 意图、归属或信息量不明确：转人工；
5. 通过最低 `routing_score` 阈值后，功能需求/扩展问题进入 Issue 路径，充分的后端缺陷进入现有修复路径；
6. 其他结果转人工。

JEV 只负责判断，不生成 Issue 标题或摘要。需要 Issue 时沿用现有脱敏摘要生成、校验和发布流程。

## 展示与审计

运行记录保存分类器来源（`jev` 或 `gate-v10`）、最终实际响应的 Provider/Model、JEV 各判断的概率/置信度和 `routing_score`。不新增回退原因消息。展示站点使用现有 `provider`/`model` 字段显示最终实际模型；JEV 路径显示 `routing_score`，回退路径显示原有 `relevance`。

Trace Site 的业务状态仍以 Supabase 为准，JEV/Langfuse 只提供脱敏观测。网站展示缺失不能改变 Agent 路由或阶段状态。
