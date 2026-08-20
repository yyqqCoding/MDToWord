# Langfuse与追踪网站

## 1. 三种记录各自解决什么问题

| 位置 | 负责回答的问题 |
|---|---|
| Supabase `agent_runs` | 任务现在是什么状态，最后结果是什么？ |
| Langfuse | 运行中调用了哪些模型和工具，每步耗时和Token是多少？ |
| 结构化服务日志 | Provider、网络、数据库或Worker为什么失败？ |

Langfuse是运行过程的观察副本，不是业务状态来源。Langfuse不可用时，Agent仍然可以继续
修复，最终状态和用量继续写入`agent_runs`。

## 2. Agent向Langfuse写什么

一次运行使用稳定的根名称：

```text
feedback-repair-run
```

下面记录：

- Gate模型调用；
- 复现计划模型调用；
- 测试生成模型调用；
- 修复生成模型调用；
- 源码读取；
- 补丁提交；
- Sandbox Job；
- GitHub发布；
- 每次调用的开始时间、耗时、状态、模型、Token和错误码。

Trace默认不保存完整输入和输出。上传的是脱敏摘要，例如路径、文件数量、哈希、状态和Token，
不上传联系方式、密钥、完整Markdown、完整源码、完整补丁或完整日志。

## 3. Agent什么时候通知网站

Scheduler完成一次运行并释放单任务锁后，通知模块执行：

1. 只处理已经进入终态的run；
2. 先调用Langfuse `flush()`，尽量发送尚未上传的记录；
3. 向Vercel发送HTTP POST；
4. 请求最多等待10秒；
5. 无论网站失败、超时还是返回5xx，都不改变Agent运行结果。

请求只包含：

```json
{
  "run_id": "运行UUID",
  "status": "completed"
}
```

并携带`x-webhook-secret`。它不包含Trace内容、反馈内容或补丁。

## 4. Vercel收到通知后做什么

接口为：

```text
POST /api/hooks/run-finished
```

处理顺序：

1. 检查Webhook Secret；
2. 检查`run_id`必须是UUID；
3. 立即让首页和运行列表缓存失效；
4. 返回HTTP 202，不让Agent等待Langfuse索引；
5. 在Vercel后台任务中根据`run_id`查询`agent_runs.langfuse_trace_id`；
6. 调用Langfuse API读取Trace；
7. 只保留网站需要的安全字段；
8. 写入Supabase `agent_run_traces`；
9. 让该运行的详情页缓存失效。

因此这是：

> Agent推送完成信号，网站根据ID主动读取真实数据。

## 5. 为什么需要4秒和12秒重试

Agent执行`flush()`只能保证客户端已经发送，Langfuse服务端建立索引仍可能需要几秒。
网站第一次查询不到时，分别等待4秒和12秒重试，总额外等待16秒。

后台抓取完成后，网站保存的是自己的展示快照。正常页面访问读取Supabase快照，不会每次
都调用Langfuse，从而避免网站可用性被Langfuse限流或短暂故障影响。

## 6. 通知丢失怎么办

这次推送是`at-most-once`：Agent只尝试一次，不建立复杂消息队列。如果Vercel部署、冷启动
或网络抖动导致通知丢失，任务详情页有兜底：

```text
用户打开终态运行详情
        ↓
网站发现agent_run_traces不存在或不完整
        ↓
当场从Langfuse读取一次
        ↓
成功后写回Supabase
```

页面兜底不重试，避免用户为了查看详情等待十几秒。失败时页面仍可根据`agent_runs`显示
阶段和最终状态。

## 7. 网站怎样读取数据

浏览器不会直接获得Supabase service role或Langfuse密钥。Vercel服务端读取：

- `agent_run_public`：字段白名单视图，提供运行摘要；
- `agent_run_traces`：已经整理的Trace树；
- GitHub公开PR：用于展示代码差异。

网站明确不查询`feedback`表。公开视图不包含用户Markdown、联系方式、内部错误正文和
`langfuse_trace_id`。

## 8. 是否实时

网站不是逐节点实时系统：

- 没有WebSocket或SSE；
- Agent运行中不会把每个LangGraph节点推给浏览器；
- Agent进入终态后立即通知网站；
- 新运行在缓存失效后的下一次页面请求出现；
- 调用明细通常还要等待Langfuse索引和后台快照抓取。

准确说法是“运行结束后近实时更新”。

对应实现：

- [agent/operations/site_notify.py](../../agent/operations/site_notify.py)
- [Vercel完成回调](../../trace-site/src/app/api/hooks/run-finished/route.ts)
- [Trace抓取](../../trace-site/src/lib/server/capture.ts)
- [Supabase读取边界](../../trace-site/src/lib/server/supabase.ts)
- [Langfuse数据投影](../../trace-site/src/lib/server/langfuse.ts)

## 9. 结合源码看“推信号、站点再拉数据”

[agent/operations/site_notify.py](../../agent/operations/site_notify.py)只发送`run_id`和终态：

```python
response = await self._client.post(
    self._endpoint,
    json={"run_id": str(outcome.run_id), "status": outcome.status.value},
    headers={"x-webhook-secret": self._secret},
    timeout=self._timeout_seconds,
)
```

Vercel在[route.ts](../../trace-site/src/app/api/hooks/run-finished/route.ts)校验后立即安排后台抓取，
并先返回`202`：

```typescript
after(async () => {
  try {
    await captureRunTrace(id, { retry: true });
  } catch {
    // 抓不到时由详情页按需补抓。
  }
  revalidatePath(`/runs/${id}`);
});

return NextResponse.json({ accepted: true }, { status: 202 });
```

这两段代码说明：Agent推送的不是完整Trace；`captureRunTrace()`才会读取Supabase摘要和
Langfuse调用树。站点慢或抓取失败不会阻塞Agent继续领取反馈。
