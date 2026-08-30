# 失败处理与重试契约

## 1. 状态与范围

本文是阶段 J 中 Agent 失败捕获、分类、短传输重试和最终失败快照的唯一权威
来源。

**实现状态：本地实现与自动测试完成，尚未部署。** `008_failure_handling.sql` 尚未执行，
真实 Docker 边界和生产服务尚未验收；生产行为仍以当前已部署版本为准。实施与验收证据
只在 [implementation-plan.md](implementation-plan.md) 的阶段 J 更新，不得把本地实现描述
为已经上线。

本文解决三个问题：

- 在一次运行边界内捕获预期失败和未知普通异常，避免单个任务直接终止常驻 Scheduler；
- 将 Provider 与 Sandbox 的相同输入短传输重试收敛成可独立测试的本地策略；
- 让最终失败明确回答发生阶段、节点、组件、稳定错误码、尝试次数与最终处理方式。

本文不接管 Schema 格式修正、复现、修复、预算、状态转换、受信 fallback、幂等发布或
`stale_base` 重排。权限与路径规则仍由
[security-and-sandbox.md](security-and-sandbox.md) 负责，LangGraph 状态与恢复仍由
[agent-runtime.md](agent-runtime.md) 负责，日志、Trace 与公开字段仍由
[observability.md](observability.md) 负责。

## 2. 设计依据

当前实现已经具有有界重试和恢复行为，但所有权分散：

- OpenAI 兼容 Provider 自行判断 timeout、429、传输错误和 5xx；
- Sandbox Client 自行判断连接异常和 408/429/5xx，并复用同一 `job_id`；
- 模型 Schema 修正、复现修订与修复修订分别由 Provider 和 Graph 管理；
- 特定转换错误和 Mermaid 测试在模型格式修正耗尽后可由 Graph 的受信模板接管；
- 发布失败由 Controller 使用原 checkpoint 幂等恢复，`stale_base` 由 Graph 最多重排一次；
- Controller 只捕获部分异常，其他异常可能穿透 Scheduler 并使进程退出；
- 最终数据库错误通常只有 `error_code` 与异常类名，不能稳定指认失败位置。

真实压力是“失败事实缺少统一表达、捕获和定位”，不是所有恢复行为都应由一个策略决定。
目标设计因此只使用三个小模式：

```text
Adapter   外部异常 -> FailureCause
Strategy  FailureCause + RetryContext -> RETRY / STOP
Observer  旁路记录失败 attempt 与实际处理方式
```

LangGraph 继续作为唯一工作流状态机；Retry Policy 只处理相同输入的短传输重试，不成为
第二套路由器。

## 3. 责任边界

```text
Provider / Sandbox Adapter
          |
          v
     FailureCause
          |
          +---- 调用点补 phase / node ----> LocatedFailure
          |                                  |
          v                                  v
     RetryPolicy                       FailureRecorder
     RETRY / STOP                            ^
                                             |
Provider 格式修正 / Graph 修订 / 受信 fallback / stale_base 重排
                继续由既有所有者决定并记录实际 handling
```

各边界职责固定为：

- Adapter 只把厂商协议、HTTP 状态和本地异常转换为稳定 `FailureCause`；
- 调用 Provider、Sandbox、Policy、Publisher 或 Repository 的受信边界补充 `phase/node`；
- Retry Policy 是无 I/O 的纯策略，只对幂等短传输操作返回 `RETRY/STOP`；
- Provider 继续拥有一次格式修正，Graph 继续拥有业务条件边、受信 fallback 与
  `stale_base` 重排；
- Runtime 执行等待、预算检查、Graph 条件边和 checkpoint 恢复；
- Failure Recorder 旁路记录 attempt 与实际 handling，记录故障必须 fail-open；
- Failure Finalizer 负责最终数据库状态，不属于 fail-open Observer；
- Repository 只持久化最终权威摘要，不参与错误分类。

不得为了“统一”删除现有 Graph 条件边、受信 fallback 或发布恢复逻辑。

## 4. Failure 契约与位置补全

### 4.1 FailureCause

Adapter 返回的 `FailureCause` 只描述它能够证明的边界事实：

```text
FailureCause
  code
  kind
  component
  operation
  safe_details
```

| 字段 | 含义 |
|---|---|
| `code` | 对外稳定错误码，如 `timeout`、`sandbox_invalid_response` |
| `kind` | 用于短重试和终结映射的有限失败类别 |
| `component` | `provider`、`sandbox`、`policy`、`publisher`、`repository` 或 `runtime` |
| `operation` | 稳定操作名，不包含动态 ID |
| `safe_details` | 受信代码生成的有限脱敏标量字段 |

`FailureCause` 是诊断值，不是端口的另一种成功返回值。Provider、Sandbox 等现有端口继续
“成功返回原结果、失败抛出领域 `AgentError`”，不改成 Result Monad。Adapter 或调用边界用
表驱动的受信映射从“异常类型 + HTTP状态 + 静态operation”生成 FailureCause；需要向上抛出
时，领域异常携带或可稳定重建该 Cause，禁止下游解析异常文案。

`kind` 只允许：

```text
transient   临时传输或上游可用性失败
invalid     已收到结果，但格式或普通本地规则不接受
business    请求成功执行，但业务目标没有达成
security    权限、路径、补丁或运行时完整性被拒绝
permanent   认证、配置、上下文或外部依赖等重复相同操作不能恢复的失败
```

预算不是 `kind`。预算和 deadline 属于当前执行上下文；任一自动处理在预算或 deadline
不足时都必须停止。

### 4.2 LocatedFailure

Provider 和 Sandbox Adapter 不知道自己位于哪个 Graph 节点，不得向它们注入 Graph 知识。
调用点捕获 `FailureCause` 后，用受信的静态上下文生成：

```text
LocatedFailure
  cause
  phase
  node
```

- `phase` 使用当前运行阶段，如 `gating`、`reproducing`、`repairing`、`validating`、
  `publishing`；
- `node` 使用真实注册的 Graph 节点名，如 `generate_test_edit`；
- phase/node 来自受信调用点，不从异常文案、模型响应或外部 HTTP 内容推导；
- Graph 外部失败使用固定运行时边界名，如 `controller_start`、`controller_finalize` 或
  `scheduler_run_once`；若 checkpoint 可证明更具体的当前节点，优先使用该节点；
- 最终 FailureSnapshot 的 phase/node 必须非空；调用点遗漏属于本地契约错误。

### 4.3 safe_details

第一版不为每个组件建立独立详情类型。`safe_details` 只接受
`string | integer | boolean | null`，禁止嵌套对象和数组，并满足：

- key 由受信代码按 `code` 固定，不接受模型或外部响应提供的任意 key；
- key 数量、字符串长度和整数范围有本地上限；
- 禁止用户 Markdown、description、contact、完整 Prompt、模型原文、patch、源码、
  stdout/stderr、响应正文、URL 查询串、Header、Cookie、环境变量与 Secret；
- 未知第三方异常只允许记录异常类名，不记录 `str(exc)`；
- 每个允许的 `code` 用测试锁定可出现的详情字段。

`invalid_response` 必须使用 Provider 已生成的 `_schema_error_paths` 结果：

```text
safe_details.schema_errors = "字段路径:Pydantic规则名"
```

该值最多 8 项，每段路径最多 40 字符，不含校验器文案或模型原文。包含校验器文案的
`_validation_error_hint` 只允许回传给模型并写入受限本机日志，禁止进入 `safe_details`、
Langfuse、数据库和公开 Trace Site。

以下是不同 code 各自允许的独立示例，不表示一个 Failure 可以混合三组 key：

```json
{"timeout_type": "read"}
{"http_status": 503}
{"schema_errors": "edits.0.content:string_too_long"}
```

## 5. RetryContext、RetryDecision 与 handling

### 5.1 Retry Policy

```text
RetryContext
  attempt
  max_attempts
  budget_remaining
  deadline_remaining_seconds
  operation_id
  idempotent
```

`RetryDecision` 只允许：

```text
RETRY  使用完全相同的受信操作参数做短传输重试
STOP   不再做相同输入传输重试
```

通用规则：

```text
kind == transient
and operation is idempotent
and attempt < max_attempts
and budget_remaining
and deadline can accommodate delay and next request
  -> RETRY

otherwise
  -> STOP
```

未知错误默认转换为 `unexpected_error/permanent` 并 STOP，不能用通用重试掩盖编程错误。

### 5.2 记录实际 handling

Recorder 为统一诊断记录实际发生的处理方式，但不据此控制业务：

```text
transport_retry   Retry Policy 允许同输入重试
format_revise     Provider 执行一次格式修正
graph_revise      Graph 使用既有条件边进入下一业务轮
trusted_fallback  Graph 使用已验收的受信确定性模板
stale_requeue     发布 stale_base 按既有规则重排一次
stop              当前自动处理终止
```

这些值是观测结果，不是一个新的统一状态机。Checkpoint 与发布恢复继续由 LangGraph 与
Controller 使用原 `run_id`、operation ID 和幂等查询完成。

### 5.3 可选备用模型接口

OpenAI-compatible Provider 可选配置一个备用接口，但它仍是同一个 Provider 边界，不增加
供应商路由、供应商状态或新的 handling：

```text
attempt 1  主接口
attempt 2  主接口
attempt 3  备用接口（仅启用且前两次均为 transient 时）
```

- 总 attempt 仍包含首次最多三次，等待仍为 1 秒、2 秒；不得变成两个接口各三次；
- `timeout`、连接异常、408、429 和 5xx 可按 Retry Policy 进入下一 attempt；
- 认证、权限、配置、上下文、安全、无效响应、预算和未知错误立即 STOP，不得借备用接口
  绕过；
- 第三次切换仍沿用 `transport_retry`，成功与失败统一使用
  `provider=openai_compatible`；
- 不记录接口名称、Base URL、凭据或主/备用身份，不新增 `provider_failover` 事件；
- 两套凭据只由受信配置和逐请求 Header 使用，备用配置不进入 Graph State、Artifact、
  数据库或 Trace；
- Schema 格式修正仍是成功传输后的既有独立轮次，不纳入这三个传输 attempt。

## 6. 捕获与转换边界

### 6.1 Adapter 边界

Provider、Sandbox、Policy、Publisher 和 Repository 应把已知边界异常转换为稳定
`FailureCause`。Adapter 不捕获进程控制信号，也不填写 Graph phase/node。

### 6.2 Graph 调用点边界

每个调用外部端口或本地 Policy 的 Graph 节点负责：

1. 为已知 `FailureCause` 补充静态 phase/node；
2. 将格式修正、业务修订和受信 fallback 的实际 handling 交给 Recorder；
3. 未被本节点恢复的异常继续抛给单次运行边界；
4. 不在节点中复制最终数据库终结逻辑。

### 6.3 Controller 单次运行边界

围绕一次 `graph.ainvoke` 的 Controller 边界必须捕获：

- 所有预期 `AgentError`；
- 未列入错误映射的普通 `Exception`，安全转换为
  `unexpected_error/permanent/runtime`，只记录异常类名。

该边界先尝试读取 checkpoint 补全当前 phase/node 和可信 usage，再调用 Failure Finalizer。
不得捕获或转换 `CancelledError`、`KeyboardInterrupt`、`SystemExit` 等取消与进程控制信号。

### 6.4 Scheduler 守护边界

Scheduler 的常驻循环必须隔离一次 run 的失败，避免单条反馈终止进程：

- Controller 已成功终结的失败不得再次抛出并终止 Scheduler；
- Failure Finalizer 因 Repository 暂时不可用而失败时，Scheduler 写脱敏结构化日志，等待
  既有轮询间隔后继续，不伪造数据库已落库；
- 未完成终结的 run 保持可恢复，由 Repository 恢复后通过原 checkpoint 再处理；
- 守护边界不吞掉取消与进程控制信号。

当 Repository 本身不可用时，不可能保证向同一个数据库写入 FailureSnapshot；该限制必须
在验收和告警中明确，不能用“Recorder fail-open”掩盖。

## 7. 短传输重试与现有恢复

### 7.1 统一短传输重试

已确认配置：

```text
MAX_TRANSPORT_ATTEMPTS=3
RETRY_BASE_DELAY_SECONDS=1
RETRY_MAX_DELAY_SECONDS=10
SANDBOX_RECONCILIATION_GRACE_SECONDS=60
```

“最多三次”包含首次调用，即首次调用加最多两次重试。它是上限，不是必须执行满三次；
预算、幂等条件或总 deadline 不足时提前 STOP。

指数退避公式：

```text
delay = min(RETRY_BASE_DELAY_SECONDS * 2^(attempt - 1),
            RETRY_MAX_DELAY_SECONDS)
```

三次总 attempt 的等待序列：

```text
attempt 1 失败 -> 等待 1 秒
attempt 2 失败 -> 等待 2 秒
attempt 3 失败 -> STOP，不再等待
```

当前单并发、低流量场景不增加随机抖动。429 只接受合法、非负、有限的秒数形式
`Retry-After`：等待本地指数退避与该值中的较大值，但仍截断到 10 秒；非法值退回本地
退避。等待和下一次请求不能放进剩余 deadline 时直接 STOP。

统一短重试覆盖：

- Model Provider 的 timeout、429、普通传输异常和 5xx；
- Sandbox Client 的连接异常与 408/429/5xx；
- GitHub 源码版本和快照下载这两个只读 GET 的连接异常、408、限流与 5xx。

通用短重试不覆盖：

- Supabase 状态写入；响应丢失必须按条件更新和现有状态对账；
- GitHub PR/Issue 等写操作；结果未知时先查询幂等 marker、分支、patch hash 或已有结果；
- Artifact 写入；继续使用原子临时文件加 rename；
- Langfuse 与 Trace Site 通知；继续 fail-open 和既有按需自愈。

### 7.2 Provider 格式修正与 Graph fallback

Provider 的结构化响应按以下固定顺序处理：

```text
Schema 非法
  -> Provider 最多 1 次 format_revise
  -> 仍非法并抛出 InvalidModelResponseError
       -> 当前 Graph 节点有已验收受信模板：trusted_fallback
       -> 无受信模板：stop
```

转换错误测试模板和 Mermaid 测试模板属于阶段 D 已验收行为。Retry Policy 不覆盖该分支，
实施不得删除、改为额外模型请求或绕过原 Patch Policy。受信 fallback 成功后继续运行，最终
数据库 `failure` 保持 null；失败过程只在结构化日志和 Langfuse 中可见。

### 7.3 Graph 业务修订

以下行为继续由现有 Graph 条件边决定，不调用 Retry Policy，也不使用指数等待：

```text
复现：最多 2 轮
修复：最多 2 轮
```

测试未按预期失败、目标验证未通过、普通编辑不合法等 `business/invalid` 结论，由
`route_after_test_edit`、`route_after_fix_edit` 和对应分类节点继续拥有。Recorder 只记录
`graph_revise` 或 `stop`。

### 7.4 stale_base

`stale_base` 是既有发布一致性结果，不是本文定义的短传输 RETRY。它继续使用原
checkpoint，把 feedback 最多重排一次；连续漂移进入 `needs_human`。本文不增加第二种
重排、`next_retry_at` 或 waiting 状态。

### 7.5 永不短重试的类别

以下是规范性类别，不是可能遗漏的错误码穷举：

- `kind in {security, permanent}`；
- 预算或总 deadline 已耗尽；
- operation 不幂等；
- 未知普通异常转换得到的 `unexpected_error`。

易错示例包括 `auth_error`、`configuration_error`、`context_too_large`、
`safety_refusal`、`tool_not_authorized`、`source_access_denied`、安全型编辑拒绝、
`external_dependency_required`、`workspace_modified`、
`sandbox_auth_error`、`sandbox_job_conflict` 和 `budget_exhausted`。最终判定以 kind、
幂等属性、预算和 deadline 为准。

## 8. 边界错误映射

### 8.1 Model Provider

| 条件 | code/kind | Retry Policy | 既有所有者后续处理 |
|---|---|---|---|
| timeout 或 HTTP 408 | `timeout/transient` | 条件允许时 RETRY | attempt耗尽后终结 |
| HTTP 429 | `rate_limit/transient` | 有界 Retry-After + RETRY | attempt耗尽后终结 |
| 传输异常或 HTTP 5xx | `provider_unavailable/transient` | RETRY | attempt耗尽后终结 |
| HTTP 401/403 | `auth_error/permanent` | STOP | Failure Finalizer |
| 上下文过大 | `context_too_large/permanent` | STOP | Failure Finalizer |
| 安全拒绝 | `safety_refusal/permanent` | STOP | Failure Finalizer |
| 单次响应 Schema 非法 | `invalid_response/invalid` | 不参与 | Provider format_revise |
| 格式修正耗尽 | `invalid_response/invalid` | 不参与 | Graph fallback 或终结 |

timeout 后请求可能已经在上游执行，因此只确认实际收到的 usage；不得猜测丢失响应的 Token
或成本。每次请求 attempt 在 Trace 中单独计数，最终数据库 Token 仍只累计 Provider 返回的
可信 usage。

### 8.2 Sandbox

| 条件 | code/kind | Retry Policy | 后续处理 |
|---|---|---|---|
| Client连接异常、408/429/5xx | `sandbox_unavailable/transient` | 相同 job RETRY | attempt/deadline耗尽后终结 |
| Worker认证失败 | `sandbox_auth_error/permanent` | STOP | Failure Finalizer |
| job ID 与请求指纹冲突 | `sandbox_job_conflict/permanent` | STOP | Failure Finalizer |
| HTTP 400 等确定性请求拒绝 | `sandbox_request_rejected/permanent` | STOP | Failure Finalizer |
| 200响应 Schema 非法或job ID不符 | `sandbox_invalid_response/permanent` | STOP | Failure Finalizer |
| 任务执行超时结果 | `sandbox_timeout/business` | 不参与 | 当前 Graph 分类 |
| workspace出现未授权变化 | `workspace_modified/security` | STOP | 安全终态 |

`code -> kind` 在目标错误注册表中必须一对一；新增或改变稳定 code 时同步兼容性测试和公开
投影。不能继续使用 `sandbox_unavailable` 同时表达临时传输失败和确定性无效成功响应。

Sandbox 的三次 attempt 必须复用相同 `job_id`、`Idempotency-Key` 和请求指纹，并受一个
submit 总 deadline 约束：

```text
submit_deadline = monotonic_start
                  + wall_timeout_seconds
                  + SANDBOX_RECONCILIATION_GRACE_SECONDS
```

每次 HTTP timeout 取“单次上限”和“剩余 deadline”中的较小值。默认 900 秒 Worker wall
timeout 下，单次 submit 最长约 960 秒，而不是三个完整 930 秒相加。

Worker 必须先在锁内按 `job_id + fingerprint` 查询已保存结果：

- 有匹配结果时即使原 Job 已过期也直接返回，不重复执行；
- 有相同 job ID 但 fingerprint 不同仍返回冲突；
- 只有不存在已保存结果、准备启动新执行时才检查 `expires_at`；
- 已过期且无已保存结果不得重新启动容器。

这样 response 丢失后的重试可以在 reconciliation grace 内取回结果，同时保持过期 Job 不会
触发新副作用。

### 8.3 Patch Policy

当前底层 `PatchPolicyError.error_code=patch_policy_rejected` 只是 Adapter 输入，目标实现不得
把这个通用 code 同时持久化为 `invalid` 和 `security`。Adapter 使用稳定 `rule_id` 和调用点
生成既有、更具体的 code，禁止解析异常文案：

| 条件 | code/kind | 所有者 |
|---|---|---|
| 测试编辑路径越界、测试削弱等安全规则 | `test_edit_security_rejected/security` | Graph直接终结 |
| 修复编辑路径越界、依赖/部署/Shell/网络等安全规则 | `fix_edit_security_rejected/security` | Graph直接终结 |
| 测试编辑唯一匹配失败、字段组合错误等 | `invalid_test_edit/invalid` | Graph决定是否修订 |
| 修复编辑唯一匹配失败、`full_file`误用、编辑重叠等 | `invalid_fix_edit/invalid` | Graph决定是否修订 |
| 需要新平台依赖 | `external_dependency_required/permanent` | 既有人工路由 |

稳定 `rule_id` 放入 `safe_details.rule_id`，用于细分直接原因，但不改变 code/kind 一对一。
规则白名单由安全文档与本地 Policy 共同拥有；本文只规定失败类别和短重试边界。

### 8.4 Publisher 与 Repository

Publisher 和 Repository 可以产生 `FailureCause` 并使用统一最终快照。只有没有外部副作用的
GitHub 源码版本读取和快照下载接入通用短重试；发布与数据库写入不接入：

- GitHub响应未知时由现有发布恢复先查询结果，不能直接重复 POST；
- GitHub源码只读GET的连接异常、408、限流和5xx最多三次attempt；每次读取相同仓库、分支
  或commit，不提高权限、不改变输入；
- GitHub源码401以及非限流403使用`source_auth_error/permanent`立即STOP，并把feedback置为
  `needs_human`；404和其他确定性4xx保留为源码revision/snapshot契约错误；
- Supabase条件更新冲突先读取现状并按 claim token、状态和 operation ID 对账；
- 认证、权限和完整性失败为 `permanent` 或 `security`；
- 数据库本身不可用时无法向同一数据库记录失败，先写脱敏结构化日志；原 run 保持可恢复，
  不引入第二个持久化数据库。

### 8.5 Runtime、源码与预算

第一版至少覆盖以下当前可能穿透 Controller 的边界：

| 条件 | code/kind | Retry Policy | 后续处理 |
|---|---|---|---|
| 工具未获当前节点授权 | `tool_not_authorized/security` | STOP | 安全终态 |
| 源码访问越权或受信重定向拒绝 | `source_access_denied/security` | STOP | 安全终态 |
| 快照归档含越界路径、不安全entry或完整性逃逸 | `source_snapshot_security_rejected/security` | STOP | 安全终态 |
| 源码快照无法物化、存储或通过普通格式校验 | `source_snapshot_error/permanent` | STOP | Failure Finalizer |
| 当前run预算耗尽 | `budget_exhausted/business` | STOP | 既有预算终态 |
| GitHub源码401或非限流403 | `source_auth_error/permanent` | STOP | feedback=`needs_human` |
| GitHub源码连接异常、408、限流或5xx | `repository_unavailable/transient` | RETRY | attempt耗尽后终结 |
| Repository确定性契约错误 | `repository_error/permanent` | STOP | 脱敏日志与人工排障 |
| 未登记普通异常 | `unexpected_error/permanent` | STOP | Failure Finalizer |

实施前必须审计 `AgentError` 全部子类并补齐表驱动注册表；未登记类仍按
`unexpected_error/permanent` 安全终结，不能穿透常驻循环。

## 9. Failure Recorder、Finalizer 与持久化

### 9.1 Failure Recorder

Failure Recorder 是旁路 Observer。每次处理记录：

```text
failure.code/kind/component/operation/phase/node
attempt/max_attempts
handling
delay_seconds（仅 transport_retry）
deadline_remaining_seconds（存在总 deadline 时）
safe_details
```

| 位置 | 内容 |
|---|---|
| 结构化日志 | 每次失败 attempt 与 handling；仅脱敏字段 |
| Langfuse | 每次 attempt、延迟、实际恢复方式和最终结果 |
| `agent_runs` | 只保存未恢复的最终 FailureSnapshot |
| Artifact | 继续保存既有测试、patch、JUnit和验证产物，不新增错误事件日志 |

日志或 Langfuse Recorder 故障必须 fail-open，不得改变业务结果。第一版不增加 append-only
failure event 表；成功恢复的中间失败只保留在日志和 Langfuse。

### 9.2 Failure Finalizer

Failure Finalizer 使用 LocatedFailure、checkpoint 和当前 run 生成最终事实：

- 合并 checkpoint 与数据库中可信 usage 的单调最大值；
- 按 failure kind/code 映射既有 run 与 feedback 终态；
- 条件更新 feedback 后再终结 run，并保持 claim token 校验；
- Repository 失败向 Scheduler 抛出，不能假装终结成功。

第一版终态约定：

| 失败 | run | feedback | 恢复语义 |
|---|---|---|---|
| Provider/Sandbox认证失败（claim后） | `failed` | `needs_human` | 修复凭据后由受信维护操作显式requeue |
| 启动配置错误（claim前） | 不创建run | 不领取 | 修复配置后重启 |
| 安全拒绝 | 既有安全终态 | 既有安全终态 | 不自动恢复 |
| publication错误 | `failed` | `failed` | 保留既有同run checkpoint发布恢复 |
| 其他未恢复失败 | `failed` | `failed` | 不自动恢复 |

`needs_human` 本身不会自动重新领取；它只防止基础设施认证问题被误写成普通业务失败，并让
维护者队列可见。阶段 J 不增加自动熔断或凭据恢复后自动 requeue。实施需显式补齐允许认证
失败从相关处理中状态进入 `needs_human` 的状态转换及测试。

### 9.3 最终 FailureSnapshot

Migration `008_failure_handling.sql` 为 `agent_runs` 新增可空 `failure jsonb`，同时保留
`error_code/error_message` 字段兼容旧调用方：

```text
error_code = failure.code
error_message = 固定安全摘要或异常类名
failure = {
  code, kind, component, operation, phase, node,
  handling, attempt, max_attempts, safe_details
}
```

成功终态的 `failure` 必须为 null。最终失败示例：

```json
{
  "code": "timeout",
  "kind": "transient",
  "component": "provider",
  "operation": "generate_test",
  "phase": "reproducing",
  "node": "generate_test_edit",
  "handling": "stop",
  "attempt": 3,
  "max_attempts": 3,
  "safe_details": {"timeout_type": "read"}
}
```

公开 Trace Site 只允许白名单展示 code、kind、component、phase、node、attempt、
max_attempts 与 handling，不公开 `safe_details`。数据库 migration 与公开投影仍须维护者
审查后手工执行。

这里的兼容是保留旧字段和已有单一语义 code；`sandbox_unavailable` 等当前含混 code 按本文
显式拆分属于目标契约变更。实施时必须同步 Repository、日志告警、Trace Site 白名单和兼容
测试，不得让旧消费方静默误判新 code。

## 10. 实施边界

实施顺序：

1. 建立稳定 code/kind 注册表、FailureCause、LocatedFailure、RetryContext 和纯 Retry Policy；
2. 增加 Graph 调用点位置补全、Controller 单次运行捕获、Failure Finalizer 和 Scheduler
   守护边界；
3. Provider 接入短重试策略，保持现有错误码和一次格式修正，并显式保护 Graph fallback；
   退避序列从当前 1 秒/4 秒调整为 1 秒/2 秒；
4. Sandbox 接入同一短重试规则、submit 总 deadline 和唯一错误码，并调整 Worker 为“先返回
   已保存匹配结果、再对新执行检查过期”；
5. 接入 Failure Recorder、最终 FailureSnapshot 与基础设施认证失败的终态映射；
6. 增加数据库列和公开白名单投影，但 migration 只由维护者审查后手工执行。

第一版不预先抽取通用 `ResilientExecutor`。Provider 与 Sandbox 的 deadline、请求协议和
结果对账不同；只有后续代码出现经过测试证明的真实重复时，才提取最小共享执行器。

失败分类与重试规则属于现有本地 Policy，修改时 bump `POLICY_VERSION`，不增加独立的
failure policy版本。跨字段规则同步写入提示词和本地 Policy，并按现有规则 bump 对应 Prompt
版本；不得通过重构改变路径、工具或发布权限。

## 11. 验收

### 11.1 捕获与终结

- 任一 Graph 节点注入未登记的普通 `Exception`，在 Repository 可用时转换为
  `unexpected_error/permanent`，Scheduler 不退出，最终快照包含真实 phase/node；
- 注入已知但此前未被 Controller 捕获的 Sandbox、Repository、Source Snapshot、Tool
  Authorization 与 Budget 异常，均由单次运行边界处理；
- Failure Finalizer 遇到 Repository 不可用时不伪造成功，Scheduler 保持运行，原 run 可在
  Repository 恢复后从 checkpoint 继续处理；
- 取消和进程控制信号仍能结束相应任务或进程，不被转换为业务 Failure。

### 11.2 重试与既有恢复

- Provider timeout、429、传输错误和 5xx 总共最多三次 attempt，等待 1 秒、2 秒；
- GitHub源码版本和快照只读GET的连接异常、408、限流和5xx同样最多三次attempt；401及非
  限流403不重试；
- 合法 Retry-After 被尊重但不超过 10 秒，非法值退回本地退避；
- Schema仍只格式修正一次；受信转换错误/Mermaid fallback 仍确定性接管且不增加模型调用；
- 普通业务编辑仍由 Graph 最多修订两轮，路径或权限越界仍立即停止；
- `stale_base` 仍只重排一次，不进入 Retry Policy；
- GitHub响应未知仍先查询既有结果，不因通用策略重复创建外部副作用。

### 11.3 Sandbox

- 临时传输最多三次 attempt，始终复用同一 job ID、幂等键和请求指纹；
- submit 总 wall time 不超过 Worker wall timeout 加 60 秒 reconciliation grace；
- 已保存的匹配结果在 Job 过期后仍可读取，过期且无结果的 Job 不启动容器；
- 无效200使用 `sandbox_invalid_response/permanent`，与
  `sandbox_unavailable/transient` 明确区分；
- 认证、冲突、确定性请求拒绝和任务执行超时不被当作传输重试。

### 11.4 记录、脱敏与兼容

- 最终 FailureSnapshot 能指认 phase、node、component、code、attempt 与 handling；
- 成功运行的中间失败在日志与 Langfuse 可见，数据库最终 `failure` 为 null；
- `invalid_response.safe_details.schema_errors` 只使用 `_schema_error_paths`，
  `_validation_error_hint` 不进入 Langfuse、数据库或公开投影；
- Failure Recorder 故障不改变业务结果，Failure Finalizer 故障不会被吞掉；
- Provider/Sandbox/GitHub源码认证失败发生在 claim 后时，run=`failed`、
  feedback=`needs_human`，且不会自动重新领取；
- 日志、Trace、数据库和公开投影均不出现用户原文、contact、Secret、完整Prompt、patch、
  源码或完整工具输出；
- 现有 checkpoint、预算、PR/Issue幂等、fallback、业务轮次和终态语义除本文明确变更外
  保持不变。

## 12. 非目标

- 不增加自动延迟恢复、`next_retry_at`、waiting状态或新的Scheduler优先级；
- 不增加消息队列、Redis、Circuit Breaker、多Provider自动切换或凭据恢复后自动requeue；
- 不引入通用重试依赖、Result Monad或第二套状态机；
- 不建立通用事件溯源和独立失败事件表；
- 不自动分析根因，Failure只陈述可证明的失败边界与直接原因；
- 不把 Provider 格式修正、Graph 业务修订、受信 fallback 或 `stale_base` 交给 Retry Policy；
- 不重试安全拒绝，不因重试提高权限或放宽Policy。
