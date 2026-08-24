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

数据库异常不会退回本地内存假装领取成功。Scheduler进程失败后由systemd按配置重启；下次
启动先查询可恢复运行，再尝试领取新反馈。

### 进程在状态更新中间退出

LangGraph checkpoint、`agent_runs`、feedback状态和操作幂等键共同处理。恢复时使用原
`run_id`和`claim_token`，不会自动创建第二次运行。

## 4. 模型调用失败

Provider把厂商差异转换成稳定错误码：

| 错误码 | 含义 | 是否重试 |
|---|---|---|
| `auth_error` | API Key或权限错误 | 不重试 |
| `rate_limit` | 上游限流 | 有限重试 |
| `timeout` | 请求超时 | 有限重试 |
| `provider_unavailable` | 网络、连接或上游5xx | 有限重试 |
| `invalid_response` | 已收到响应，但JSON Schema或本地Policy不通过 | 一次格式修正或本轮修订 |
| `context_too_large` | 输入超过模型限制 | 不重复发送相同请求 |
| `safety_refusal` | 模型拒绝响应 | 记录并终止当前自动路线 |

默认传输失败最多额外重试2次，等待约1秒、4秒。模型返回429并提供秒数形式的
`Retry-After`时，等待时间取本地退避与该值中的较大值，但最多10秒，避免上游给出异常值
导致Scheduler长期停住。认证、请求过大等确定性错误不重试。

必须区分：

- `/models`返回200只说明基础网络和认证可用；
- `provider_unavailable`说明没有取得可用响应；
- `invalid_response`说明已经取得响应，但结构或本地业务规则不接受。

## 5. 模型回答格式正确但内容不合理

严格Schema只能保证字段存在和类型正确，不能保证测试真的有效。系统继续检查：

- 分类字段是否互相一致；
- 文件是否来自允许列表；
- Edit字段组合是否符合当前文件状态；
- `search`是否唯一匹配；
- 测试是否被pytest收集；
- 原始代码是否按预期失败；
- 修复后目标与全量测试是否通过。

因此“模型返回JSON成功”只代表可以进入下一层检查，不代表任务成功。

## 6. 工具调用失败

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

## 7. 补丁失败

常见情况及处理：

- 补丁无法解析或不能应用到`base_sha`：本轮编辑无效；
- 修改禁止文件：立即`security_rejected`；
- 文件数、增删行或字节超限：立即拒绝；
- 测试补丁修改业务代码：拒绝；
- 修复补丁修改测试：拒绝；
- 测试包含网络、Shell、Secret、pytest Hook：拒绝；
- 运行后workspace出现额外diff：`workspace_modified`。

安全拒绝不要求模型“解释一下”再放行，也不会通过下一轮扩大白名单。

## 8. Sandbox失败

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
更重要。连接异常或408、429、5xx默认只额外重试一次，并复用同一个`job_id`；Worker的
幂等结果存储保证请求重放不会再次启动容器。

## 9. GitHub发布失败

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

## 10. Langfuse和网站失败

- Langfuse上报失败不阻断修复，数据库仍保存权威状态和用量；
- 网站通知在运行结束后执行，不占住Scheduler运行锁；
- 通知失败只记录安全摘要，不把URL、Secret或响应正文写入日志；
- 网站通知只尝试一次，丢失后由详情页按需补抓；
- Langfuse尚未建立索引时，Vercel后台等待4秒和12秒重试；
- Trace仍缺失时，网站继续显示Supabase运行摘要。

## 11. 预算和停止条件

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

## 12. 日常排障顺序

遇到失败时按下面的顺序查看：

1. 在`agent_runs`确认`status`、`route`和`error_code`；
2. 在Langfuse确认最后成功的是哪个模型或工具调用；
3. 区分Provider传输失败和结构失败；
4. 查看本机结构化日志中的字段路径或Worker错误码；
5. 根据`run_id`查看本地运行文件是否齐全；
6. 检查checkpoint的下一节点和State预算；
7. 涉及Docker时查看Worker服务、固定镜像和残留容器；
8. 涉及发布时检查`base_sha`、当前main和现有PR。

不要只看到“run failed”就重新提交反馈。先判断该错误能否用同一`run_id`安全恢复。

## 13. 当前方案刻意没有增加什么

当前反馈量和单维护者场景不需要引入：

- 多Agent自治协商；
- 通用任意Shell工具；
- 为每个节点建立消息队列；
- 自动合并和自动部署；
- 为提示词修改建立大型评测平台；
- 复杂自动rebase协议；
- 为小流量反馈入口立即引入Redis。

系统优先把已有链路的状态、权限、错误和恢复写清楚，再根据真实故障增加最小改动。

## 14. 结合源码看有限重试

模型传输重试在
[agent/providers/openai_compatible.py](../../agent/providers/openai_compatible.py)中只接受限流、
超时和普通传输错误：

```python
retryable = type(error) in {
    ModelRateLimitError,
    ModelProviderError,
    ModelTimeoutError,
}
if not retryable or attempt >= self._max_transport_retries:
    raise error
await self._sleep(_transport_retry_delay(attempt, response))
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
