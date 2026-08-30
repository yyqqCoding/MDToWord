# 失败、重试与稳定运行

## 1. Agent为什么比普通接口更难排错

普通接口通常执行固定代码，输入相同就容易重现。Agent同时依赖用户文本、模型输出、外部
API、数据库、源码版本、Docker和多阶段State。同一个表面现象可能来自不同位置，例如
“没有得到预期GitHub结果”可能是Issue信息不足，也可能是后端缺陷无法复现、模型格式错误、
补丁被拒绝、全量测试失败或GitHub不可用。

解决方法不是无限重试，而是：

```text
每一步明确输入和输出
每类错误使用稳定错误码
只重试可能恢复的错误
所有副作用带幂等标识
状态和大文件持久化
设置轮次、调用量和时间上限
```

## 2. 用户输入问题

| 问题 | 处理 |
|---|---|
| 字段缺失或类型错误 | FastAPI/Pydantic在入口拒绝 |
| Markdown过大 | Gate确定性校验拒绝 |
| 重复反馈 | 内容规范化后计算SHA-256，路由为`duplicate` |
| 提示注入 | Gate无工具，本地Policy路由隔离 |
| 信息不足 | `needs_human`，不让模型猜测 |
| 提交过快 | 返回`429`和`Retry-After` |

## 3. Scheduler和数据库失败

### 怎样避免重复领取

Supabase领取函数使用事务、行锁和`SKIP LOCKED`。每次领取生成`claim_token`，后续状态更新
必须同时匹配反馈ID和token。

### 数据库暂时不可用

数据库异常不会退回本地内存假装领取成功，也不会伪造FailureSnapshot已经持久化。
Scheduler会隔离单条运行的普通失败，等待下一轮继续；如果进程本身退出，systemd再按配置
重启。每次启动仍然先查询可恢复运行，再尝试领取新反馈。

### 进程在状态更新中间退出

LangGraph checkpoint、`agent_runs`、feedback状态和操作幂等键共同处理。恢复时使用原
`run_id`和`claim_token`，不会自动创建第二次运行。

## 4. 第一部分：统一失败事实与重试机制

### 4.1 为什么不直接对所有异常写`try/except + retry`

“发生异常”不等于“再次执行可以恢复”。系统把失败处理拆成三个小边界：

```text
Adapter   外部异常和HTTP状态 → FailureCause
Strategy  FailureCause + RetryContext → RETRY / STOP
Observer  记录每次失败、实际handling和最终快照
```

`FailureCause`只描述受信代码能够证明的事实：

```text
code          稳定错误码
kind          transient / invalid / business / security / permanent
component     provider / sandbox / policy / publisher / repository / runtime
operation     稳定操作名
safe_details  有界、脱敏的标量详情
```

Provider和Sandbox不知道自己位于哪个LangGraph节点。调用点再补充`phase/node`，形成
`LocatedFailure`。最终未恢复的失败写入`agent_runs.failure`，能够回答：

```text
在哪个阶段、哪个节点、哪个组件失败
稳定错误码和失败类别是什么
这是第几次尝试、总上限是多少
最终为什么停止
```

每次失败事件另外记录实际`handling`，例如`transport_retry`、`format_revise`、
`graph_revise`、`trusted_fallback`或`stop`；已经恢复的中间失败只保留在结构化日志和
Langfuse，不会让成功运行携带最终FailureSnapshot。

### 4.2 四层捕获边界

```text
外部Adapter
  ↓ 标准化已知错误
Graph调用点
  ↓ 补phase/node，未恢复则继续抛出
Controller单次运行边界
  ↓ 保存最终状态和FailureSnapshot
Scheduler守护边界
  ↓ 隔离单条任务，继续常驻循环
```

未知普通异常安全转换为`unexpected_error/permanent`，只记录异常类名，不记录异常文案里的
用户内容、URL、Header或Secret。`CancelledError`、`KeyboardInterrupt`和`SystemExit`等
进程控制信号不能被吞掉。Failure Recorder上报失败可以fail-open，但最终数据库写入失败
不能假装成功。

### 4.3 哪些模型错误可以重试

Provider把OpenAI-compatible接口差异转换成稳定错误码：

| 错误码 | 含义 | 相同传输操作是否重试 |
|---|---|---|
| `auth_error` | API Key或权限错误 | 不重试 |
| `rate_limit` | 上游限流 | 有限重试 |
| `timeout` | 请求超时 | 有限重试 |
| `provider_unavailable` | 网络、连接或上游5xx | 有限重试 |
| `invalid_response` | 已收到响应，但JSON Schema或本地Policy不通过 | 不做传输重试；走格式修正或既有Graph逻辑 |
| `context_too_large` | 输入超过模型限制 | 不重复发送相同请求 |
| `safety_refusal` | 模型拒绝响应 | 记录并终止当前自动路线 |

统一传输上限包含首次调用最多三次，退避为：

```text
attempt 1失败 → 等待1秒
attempt 2失败 → 等待2秒
attempt 3失败 → STOP
```

模型返回429并提供合法秒数形式的`Retry-After`时，等待本地退避与该值中的较大值，但最多
10秒。只有`kind=transient`、操作幂等、attempt未耗尽，并且预算和deadline允许下一次调用
时，纯本地Retry Policy才返回`RETRY`。认证、配置、安全、上下文、预算和未知错误立即
`STOP`。

### 4.4 传输重试、格式修正和业务修订不是一回事

```text
请求没有取得可用响应
  → transport_retry，同一受信输入再次传输

已经取得响应，但严格Schema不合法
  → format_revise，最多一次，向模型返回脱敏字段错误

测试没有按预期失败或修复没有通过验证
  → graph_revise，由LangGraph既有条件边决定下一业务轮
```

Retry Policy只负责第一类，不成为第二套工作流状态机。转换错误和Mermaid的确定性测试模板
仍属于Graph的`trusted_fallback`；发布`stale_base`仍由既有规则最多重排一次。

必须区分：

- `/models`返回200只说明基础网络和认证可用；
- `provider_unavailable`说明没有取得可用响应；
- `invalid_response`说明已经取得响应，但结构或本地业务规则不接受；
- `model_calls=0`可能表示三次传输都没有取得可统计usage的成功响应，不表示没有发请求。

## 5. 第二部分：统一主/备用模型接口

### 5.1 为什么仍然只有一个Provider

系统没有增加多Agent，也没有增加供应商路由状态机。主接口和备用接口都实现同一个
OpenAI-compatible Chat Completions协议，对Graph继续表现为：

```text
provider = openai_compatible
```

Gate、复现计划、测试生成和修复生成仍只依赖原`ModelProvider`端口，不知道当前HTTP请求
使用哪个Base URL。这样不会把模型平台选择扩散到每个LangGraph节点。

### 5.2 三次总attempt怎样分配

启用备用接口后，单次传输操作固定为：

```text
attempt 1  主接口
attempt 2  主接口
attempt 3  备用接口
```

它不是“主接口三次，再让备用接口三次”，因此最大传输attempt仍是三次。只有前一次失败被
分类为`transient`时才会继续；主接口返回401、403、配置错误、安全拒绝、上下文过大或无效
响应时立即STOP，不借备用接口绕过。

从第二次失败进入第三次请求时仍记录普通`transport_retry`，不新增`provider_failover`
事件，也不在数据库、Trace或公开网站区分供应商。两套模型名、Base URL、API Key和可选
单价只存在Controller的受信配置中；Authorization Header按请求构造，备用Key不会发送给
主接口。

### 5.3 配置和启动校验

主接口继续使用原配置：

```dotenv
MODEL_NAME=...
MODEL_API_KEY=...
MODEL_BASE_URL=https://主接口地址/v1
```

备用接口显式启用：

```dotenv
FALLBACK_MODEL_ENABLED=true
FALLBACK_MODEL_NAME=...
FALLBACK_MODEL_API_KEY=...
FALLBACK_MODEL_BASE_URL=https://备用接口地址/v1
FALLBACK_MODEL_INPUT_COST_PER_MILLION=0
FALLBACK_MODEL_OUTPUT_COST_PER_MILLION=0
```

三个核心备用字段必须一起存在，否则生产预检失败，Scheduler不能领取反馈。Base URL只写到
API根路径，不包含`/chat/completions`。Secret只保存在服务器Controller环境文件中，不进入
仓库、聊天、命令参数、State或Trace。

配置完成后使用`sudo mdtoword-agentctl audit`做只读预检；只有看到
`production_preflight_ready`后，才通过统一部署入口审核并启用Scheduler。完整命令仍以
[部署与运行方式](../AgentRequirements/deployment-and-operations.md)为准。

Gate使用独立的`GATE_MODEL_TIMEOUT_SECONDS`，允许30～120秒；复现和修复的长源码请求使用
`REPRODUCTION_MODEL_TIMEOUT_SECONDS`，允许30～300秒。超时是每次HTTP请求的上限，不是
整次Agent运行的总deadline；因此120秒Gate超时配合两次主请求时，上游完全无响应可能在
进入第三次备用请求前等待较久。

### 5.4 怎样证明备用接口真的工作

真实验收不能只调用`/models`。维护者应在Scheduler关闭时使用一个可丢弃Gate评估案例：

1. 正常配置运行一次，证明主模型、严格Schema和格式修正链路可用；
2. 只对测试进程把主Base URL临时指向不可达本机端口，不修改服务器环境文件；
3. 确认日志出现`attempt=1/3`和`attempt=2/3`的`transport_retry`；
4. 确认第三次请求返回成功，最终仍显示统一`openai_compatible`；
5. 确认Gate路由、类别和`schema_compliance`正确。

当前真实本地验收中，主接口经过一次格式修正后成功；强制前两次主连接失败后，备用接口在
第三次请求成功，最终Gate准确率、类别准确率和Schema合规率均为1.0。

## 6. 模型回答格式正确但内容不合理

严格Schema只能保证字段存在和类型正确，不能保证测试真的有效。系统继续检查：

- 分类字段是否互相一致；
- 文件是否来自允许列表；
- Edit字段组合是否符合当前文件状态；
- `search`是否唯一匹配；
- 测试是否被pytest收集；
- 原始代码是否按预期失败；
- 修复后目标与全量测试是否通过。

因此“模型返回JSON成功”只代表可以进入下一层检查，不代表任务成功。

## 7. 工具调用失败

| 问题 | 处理 |
|---|---|
| 工具名不存在 | `tool is not registered`，不猜测相近名称 |
| 当前节点无权调用 | `tool is not authorized for this node` |
| 路径越界 | 在读取或生成补丁前拒绝 |
| 参数超限 | Schema或本地Policy拒绝 |
| 搜索内容匹配0次或多次 | 返回明确编辑错误，允许有限修订 |
| 工具输出过长 | 截断或拒绝，不无限塞入模型上下文 |
| 调用次数超限 | `budget_exhausted` |

模型生成的工具请求不能直接触发数据库、GitHub或Shell。所有副作用工具由确定性Graph节点
调用。

## 8. 补丁失败

常见情况及处理：

- 补丁无法解析或不能应用到`base_sha`：本轮编辑无效；
- 修改禁止文件：立即`security_rejected`；
- 文件数、增删行或字节超限：立即拒绝；
- 测试补丁修改业务代码：拒绝；
- 修复补丁修改测试：拒绝；
- 测试包含网络、Shell、Secret、pytest Hook：拒绝；
- 运行后workspace出现额外diff：`workspace_modified`。

安全拒绝不要求模型“解释一下”再放行，也不会通过下一轮扩大白名单。

## 9. Sandbox失败

| 问题 | 结果 |
|---|---|
| Worker认证失败 | `sandbox_auth_error` |
| Worker无法访问或返回非法结果 | `sandbox_unavailable` |
| 同一job_id对应不同内容 | `sandbox_job_conflict` |
| 任务超时 | `sandbox_timeout`并删除容器 |
| JUnit缺失或无效 | 测试无效，不能判定复现或修复成功 |
| Docker内部失败 | `sandbox_execution_error`或受控失败码 |
| workspace越权变化 | `security_rejected` |

Sandbox不可用时，系统不会改为直接在Agent主进程执行模型代码。这种“失败关闭”比完成率
更重要。连接异常或408、429、5xx包含首次最多三次attempt，等待1秒、2秒，并始终复用同一
`job_id`、`Idempotency-Key`和请求指纹。提交操作还受Worker墙钟上限加60秒结果对账窗口
约束；Worker的幂等结果存储保证请求重放不会再次启动容器。

### GitHub源码读取失败

固定`main`版本和下载该commit快照都是无副作用的只读GET。连接异常、408、GitHub限流和
5xx包含首次最多三次attempt，等待1秒、2秒；401或非限流403不会重试，使用
`source_auth_error`并把feedback转为`needs_human`。404、无效JSON/SHA和非法归档分别保留
为revision或snapshot契约错误。

最终FailureSnapshot和Langfuse保存`http_status`、`rate_limited`、稳定`reason`或异常类名
等`safe_details`；本机日志也打印同一份受校验详情，但不会记录GitHub响应正文、Header或
Token。公开Trace不投影`safe_details`，而是通过更具体的稳定code显示维护者可理解的原因。

## 10. GitHub发布失败

| 问题 | 处理 |
|---|---|
| 当前`main`变化 | 第一次重新排队，第二次转人工 |
| App认证失败 | 保存发布错误，不重新跑模型和Docker |
| 网络或GitHub暂时失败 | 使用原run恢复发布节点 |
| 分支或PR似乎已存在 | 根据确定性名称和补丁哈希确认是否为本次操作 |
| 补丁哈希不一致 | 拒绝发布 |
| Issue似乎已存在 | 按run reference和内容指纹marker复用开放或关闭Issue |
| Issue标题/摘要触发敏感规则 | 拒绝公开写入，保留受控发布错误 |
| 固定`bug`/`enhancement`标签缺失 | 失败关闭，不自动创建仓库标签 |

PR恢复只复用已经验证的`validated.patch`，不会重新生成另一个补丁；Issue恢复只复用已校验
的`IssueDraft`，不重跑Gate或进入Sandbox。

## 11. Langfuse和网站失败

- Langfuse上报失败不阻断修复，数据库仍保存权威状态和用量；
- 网站通知在运行结束后执行，不占住Scheduler运行锁；
- 通知失败只记录安全摘要，不把URL、Secret或响应正文写入日志；
- 网站通知只尝试一次，丢失后由详情页按需补抓；
- Langfuse尚未建立索引时，Vercel后台等待4秒和12秒重试；
- Trace仍缺失时，网站继续显示Supabase运行摘要。

## 12. 预算和停止条件

默认限制包括：

```text
复现最多2轮
修复最多2轮
格式修正最多1次
每次运行最多8次模型调用
每次运行最多30次工具调用
Token总量使用配置上限
Sandbox累计时间默认最多900秒
单个Sandbox Job最长900秒
```

每次模型或工具调用前都检查剩余预算。预算耗尽后写入明确终态，不继续尝试“最后一次”。

## 13. 日常排障顺序

遇到失败时按下面的顺序查看：

1. 在`agent_runs`确认`status`、`route`、`error_code`和最终`failure`快照；
2. 从`failure`确认`phase/node/component/code/attempt/handling`；
3. 在Langfuse确认最后成功的是哪个模型或工具调用；
4. 区分Provider传输失败、格式失败和Graph业务修订；
5. 查看本机结构化日志中的字段路径或Worker错误码；
6. 根据`run_id`查看本地运行文件是否齐全；
7. 检查checkpoint的下一节点和State预算；
8. 涉及Docker时查看Worker服务、固定镜像和残留容器；
9. 涉及发布时检查`base_sha`、当前main和现有PR。

不要只看到“run failed”就重新提交反馈。先判断该错误能否用同一`run_id`安全恢复。

## 14. 当前方案刻意没有增加什么

当前反馈量和单维护者场景不需要引入：

- 多Agent自治协商；
- 供应商级路由、熔断器或跨运行健康状态；
- 通用任意Shell工具；
- 为每个节点建立消息队列；
- 自动合并和自动部署；
- 为提示词修改建立大型评测平台；
- 复杂自动rebase协议；
- 为小流量反馈入口立即引入Redis。

系统优先把已有链路的状态、权限、错误和恢复写清楚，再根据真实故障增加最小改动。

## 15. 结合源码看有限重试

失败事实和纯Retry Policy定义在
[agent/domain/failures.py](../../agent/domain/failures.py)：

```python
if (
    failure.kind is not FailureKind.TRANSIENT
    or not context.idempotent
    or not context.budget_remaining
    or context.attempt >= context.max_attempts
):
    return RetryDecision.STOP
return RetryDecision.RETRY
```

模型传输和第三次备用目标选择位于
[agent/providers/openai_compatible.py](../../agent/providers/openai_compatible.py)：

```python
for attempt in range(self._max_transport_retries + 1):
    target = self._request_target(attempt)
    ...
    decision = self._retry_policy.decide(...)
    if decision is RetryDecision.STOP:
        raise error
    await self._sleep(delay)

def _request_target(self, attempt: int) -> _RequestTarget:
    if attempt == 2 and self._fallback_target is not None:
        return self._fallback_target
    return self._primary_target
```

Sandbox重试在[agent/sandbox/client.py](../../agent/sandbox/client.py)中使用更小集合，并始终复用
原Job：

```python
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

headers={
    "Authorization": "Bearer " + self._credential.get_secret_value(),
    "Idempotency-Key": str(artifacts.job.job_id),
}
```

复现、修复轮次则不在网络Client里重试，而是在
[agent/graph.py](../../agent/graph.py)的条件边中检查`reproduction_round`和`repair_round`。这能
区分三类完全不同的失败：请求没送达、返回结构不合规、业务测试没有达到目标。

稳定错误码、Failure字段、公开投影和验收边界以
[失败处理与重试契约](../AgentRequirements/failure-handling-and-retries.md)为准；本文只帮助
维护者理解当前实现。
