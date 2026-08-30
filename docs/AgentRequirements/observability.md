# 可观测性与评估

## 1. 目标与边界

一次反馈处理必须能回答：执行了哪些节点和工具、调用了哪个模型、使用多少Token和
成本、每轮为何继续或停止、沙箱执行了什么、最终为何创建或未创建PR/Issue。

Langfuse负责Agent和LLM语义观测；数据库保存权威状态与用量汇总；结构化日志负责
服务故障。Langfuse不可用不能改变业务结果。

## 2. 标识设计

| 标识 | 含义 |
|---|---|
| `feedback_id` | Supabase业务记录，不作为用户身份 |
| `session_id` | 同一反馈的所有Agent尝试，值为反馈ID的稳定哈希 |
| `agent_run_id` | 一次独立尝试，同时作为LangGraph `thread_id` |
| `trace_id` | 一次从claim到终态的端到端Trace |
| `observation_id` | 单个节点、模型或工具调用 |
| `job_id` | 一次Sandbox Job，对应工具Span |
| `operation_id` | 外部副作用的幂等键 |

Controller创建 `agent_run_id` 后生成确定性Langfuse Trace ID，并在Controller、
Sandbox Client、Worker结果和Publisher日志中传播。任务容器没有Telemetry凭证；
外层Worker返回 `job_id`、时间和结果，Controller记录对应子Span。

## 3. Trace结构

每次运行一个Trace：

```text
feedback-repair-run                  root/agent
  claim-feedback                     span
  gate-feedback                      agent
    classify-intent                  generation
  publish-issue                      tool（仅 issue_required）
  prepare-source                     span
  reproduce                          agent
    repair-agent-model               generation（create_agent，phase=reproducing）
    search-source/read-source-file   tool
    submit-test-edits/run-sandbox    tool
    complete-reproduction            tool
  repair                             agent
    repair-agent-model               generation（create_agent，phase=repairing）
    search-source/read-source-file   tool
    submit-fix-edits/run-sandbox     tool
    complete-repair/report-blocked   tool
  validate-final                     span
    reproduce-baseline               tool
    run-target-tests                 tool
    run-full-tests                   tool
    validate-docx                    tool
  publish-pr                         tool（仅 accepted_backend_bug）
  finalize                           span
```

观察名称保持稳定，轮次、模型和动态ID写入metadata，不写进名称。
`create_agent` 的读取、Sandbox 和模型名称会跨复现/修复复用；公开 Trace Site 必须优先使用
脱敏 input projection 中的受信 `phase=reproducing|repairing` 归属阶段，再回退到旧固定名称
映射。否则修复期源码读取会被误算为复现，且工具循环模型耗时不会进入阶段统计。

## 4. LangGraph与Langfuse集成

LangGraph调用统一挂载Langfuse Callback，以捕获标准Graph、LLM和工具事件。当前
`ModelProvider`、Sandbox Client和GitHub Publisher等自定义边界需要显式创建
`generation`、`tool`或`span`，不能假设Callback自动捕获。

同一次调用只能由一个入口记录，避免Callback和手工埋点产生重复Span或重复Token。
Telemetry适配器对领域层只暴露：

```text
start_run / start_span / start_generation / start_tool
record_failure / record_usage / record_score / end_observation / flush
```

具体Langfuse SDK类型不进入Graph State和领域Schema。

B3 的 Gate 模型通过自定义 `ModelProvider` 调用，不属于 LangChain LLM，因此由
`ObservedModelProvider` 显式创建唯一 Generation；Controller 创建 root Agent
observation 并传播确定性 Trace ID、Session ID 和最终 route。后续接入标准 Graph
callback 时不得再次记录同一 Provider 调用。

Gate-only 运行预期包含 root `feedback-repair-run` Agent 和 `classify-intent`
Generation。Feature 也必须调用 Gate；旧版 `feature_feedback_type` 零调用短路只作为历史
记录保留。`issue_required` 运行额外包含一个 `publish-issue` Tool，不含源码或 Sandbox
observation。阶段 D 复现运行还会显式记录 `plan-reproduction`、`read-source-file`、
`generate-test`、`submit-test-edits` 和 `run-reproduction`，工具 metadata 保存轮次。
Telemetry 创建、更新或 flush 失败采用 fail-open，只记录脱敏 warning；Masking 回调兼容
Langfuse v4 的 `mask(data=...)` 调用方式。默认 `TRACE_CONTENT=false`，CLI 会拒绝启用
完整 Trace 内容。生成测试源码、复现假设、反馈原文和 JUnit failure message 不进入
Langfuse，只保留 Schema、路径、大小、Hash、计数和分类摘要。阶段 E 继续显式记录
`generate-fix`、`submit-fix-edits`、目标验证以及最终三个独立 Sandbox Job，也不上传
修复源码、patch 或完整失败输出。

## 5. Generation字段

每次模型调用记录：

```text
provider, model, provider_request_id
operation: gate | plan_reproduction | generate_test | generate_fix
prompt_version, graph_version, policy_version
round, latency_ms, status, retry_count
input_tokens, output_tokens, cached_input_tokens?, reasoning_tokens?, total_tokens
input_cost, output_cost, total_cost
```

Token优先采用Provider响应的真实usage。Provider适配器将包含缓存或推理Token的计数
归一化成互不重叠的bucket，避免重复计费。Provider不返回成本时，Controller 只使用
显式配置的模型单价估算；未配置单价时数据库成本保持 `0`。Langfuse 可以按自身模型
价目显示推算成本，但该分析值不回写数据库，也不参与运行预算。

Controller同步累计每次调用，写入 `agent_runs`。预算判定使用Controller累计值，不
查询Langfuse实时结果。

调用失败时Generation记 `level=ERROR`、`status_message=error_code`，输出为
`{error_code, error_type}`。其中 `invalid_response` 额外记 `schema_errors`：

```text
schema_errors: "字段路径:Pydantic规则名" 逗号分隔，最多8项，每段路径截断40字符
               例：relevance:less_than_equal,reason:value_error
```

该摘要**只含字段路径与规则名**，不含校验器文案、模型原文或用户内容 —— `extra=forbid`
下路径可能是模型自己编造的字段名，因此逐段截断。它同时以 WARNING 写入进程日志，
两次格式尝试都记，便于判断修正提示是否生效。

进程日志额外带 `detail=`，即回传给模型的那份修正摘要（含 Pydantic 校验器文案）。
同一个 validator 可能抛出多条不同 ValueError，只看 `字段:value_error` 分不出是哪条，
必须有文案才能定位。该字段**只进本机日志**，不上 Langfuse 也不上展示站。

之所以必须在Provider层留痕：该异常用 `from None` 切断链路，Controller 只持久化异常
类名，CLI 只输出 `error_code`，没有这一项就无法判断 `invalid_response` 卡在哪个字段。
回传给模型的修正提示是另一份更宽的摘要（含校验器文案），只进入受限本机日志，不进入
Langfuse、数据库或公开Trace。

阶段 J 的当前契约由
[failure-handling-and-retries.md](failure-handling-and-retries.md) 负责：`schema_errors` 可进入
Langfuse 与私有最终 `safe_details`，但公开 Trace Site 不投影 `safe_details`。本地实现与
自动测试已经完成；migration 和生产部署状态以实施计划为准。

## 6. Tool字段

每次工具调用记录：

```text
tool_name, node, round, call_id
authorized, denial_reason?
input_summary, output_summary
duration_ms, status, error_code
job_id?, exit_code?, timed_out?
```

`input_summary`只保留路径、选择器、大小和哈希；不记录完整源码、patch或用户原文。
Sandbox工具补充CPU/内存限制和容器镜像digest，不上传完整stdout/stderr。

## 7. Trace metadata与结果

Root observation至少包含：

```text
run_id, feedback_hash, intent, area, category, route
base_sha, extension_version
provider, model
graph_version, policy_version, sandbox_image_digest
reproduction_rounds, repair_rounds
changed_files, validated_patch_sha256
final_status, error_code, pr_url, issue_url
```

PR正文包含Trace URL，便于维护者从代码审查跳转到执行证据。Trace不包含完整反馈，
因此不能替代受控Artifact中的复现输入。

阶段 F 的 `publish-pr` Tool observation 只记录 feedback ID 前缀、`base_sha`、patch hash、
分支、PR number 和是否复用；不记录 App JWT、安装令牌、PR 正文或文件内容。阶段 G
离线评估的逐条输出只包含稳定 case ID、分类、用量和错误码，原始用例内容不写报告。

阶段 I 的 `publish-issue` Tool observation 只记录 `run_ref`、内容指纹前缀、area、
固定标签、Issue number 和是否复用；不记录 Issue 标题、摘要、正文、marker 全文、App JWT
或安装令牌。Gate Trace 只记录 `issue_draft_present=true/false` 与字符数，不上传 draft
内容。

## 8. 数据最小化与脱敏

默认 `TRACE_CONTENT=false`。发送Langfuse前执行统一Masking：

- 永不发送`contact`；
- 用户Markdown替换为哈希、字节数和分类摘要；
- 源码和patch替换为路径、增删行数和SHA-256；
- 模型输入输出只保留结构化结果摘要；
- Issue draft 替换为 area/category、字符数和哈希，不上传标题或摘要正文；
- stdout/stderr只保留脱敏、截断后的错误摘要；
- 删除Authorization、Cookie、API Key、邮箱、电话和已知Secret模式；
- 电话匹配的前后边界排除十六进制字符与连字符。SHA-256、git SHA、镜像 digest 和 UUID
  普遍含 9 位以上连续数字段，边界若只排除数字，会把这些可核对标识符从中间截断 ——
  性质是过度脱敏而非泄露，但 Trace 里的指纹、`job_id` 和补丁 SHA 会无法对账；
- development、staging、production使用不同environment标签。

如需临时调试完整内容，必须由维护者显式启用，限定单次run，并在完成后恢复默认；
该能力不得由模型或反馈内容触发。

## 9. 结构化日志

Controller和Worker使用JSON日志，每条包含：

```text
timestamp, level, service, run_id, trace_id,
node/tool/job_id, status, duration_ms, error_code
```

禁止日志：完整Markdown、联系方式、完整prompt、完整patch、环境变量和密钥。异常只
记录类型与脱敏摘要。运行日志与Langfuse通过 `trace_id` 关联。

## 10. 运行指标

MVP从数据库和Langfuse统计：

- Gate分类分布与疑似注入数量；
- 首轮/两轮复现成功率；
- `cannot_reproduce`比例；
- 一轮/两轮修复成功率；
- Patch Policy拒绝率；
- PR创建率与人工接受率；
- Issue创建率、复用率、发布失败数，以及按 backend/extension/cross_component 的分布；
- 每阶段耗时；
- 每次运行及每个成功PR的Token和成本；
- Provider错误、Sandbox超时和GitHub发布失败数量。

不要求MVP部署Prometheus/Grafana。Controller提供基础health/readiness；主机与容器
监控可在真实运行量证明需要后增加。

## 11. 离线评估

维护10至20条脱敏或构造用例：表格、公式、标题、崩溃、前端 Bug、后端/前端/跨端功能
建议、无关内容、
信息不足和Prompt Injection。每条定义期望路由、分类、是否允许工具、Oracle类型和
可选修改路径。

评估至少报告：

```text
gate accuracy
automatable precision
schema compliance
injection quarantine recall / false-positive rate
issue routing precision / issue draft schema compliance
reproduction success
patch policy pass rate
validated repair rate
average token/cost/latency
```

更换模型、Prompt、Policy、Graph或沙箱镜像前运行同一评估集，并将版本与结果作为
发布证据。模型自评不能替代这些确定性结果。

阶段 B3 已完成两条真实对抗复测：`gate-v2` 将“仅测试、不需要修复”路由为
`rejected_irrelevant`，将索取系统提示词的注入路由为 `quarantined_security`，后者
`tool_calls=0`。两次 Trace 均包含预期的两个 observation，抽查未发现完整 Markdown、
描述或 contact。数据库成本因维护者暂不配置单价而保持待验收。

阶段 G 于 2026-08-12 使用 `deepseek-ai/DeepSeek-V4-Flash`、`gate-v6` 和
`publication-policy-v3` 完成 12 条真实 Gate 评估：Gate accuracy、automatable
precision、Schema compliance 和 injection quarantine recall 均为 1.0，injection
false-positive rate 为 0。由于模型单价仍配置为 0，本轮 `estimated_cost` 只表示未配置
估算单价，不表示上游 API 免费；Gate-only 报告的复现、补丁和验证指标保持 null。

## 12. 故障与保留

- Telemetry采用异步发送；Langfuse失败只记warning，不回滚业务状态；
- 长期Controller定期flush，进程优雅退出时显式flush；配置了展示站点完成回调时，
  每次运行落终态、推送之前还会额外 flush 一次 —— 根节点是最后关闭的，
  不 flush 就通知，站点会拿到不完整的树；
- Trace与Artifact默认保留14天，数据库保留脱敏汇总；
- Langfuse Cloud或自托管通过配置选择，领域代码不分支；
- 无论Trace是否完整，最终Token、成本、状态、patch hash、PR URL和Issue URL必须写入数据库；
- 定期抽样对账Provider usage、数据库汇总和Langfuse totals。
