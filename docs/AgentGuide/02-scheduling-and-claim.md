# Scheduler发现和领取反馈

## 1. Scheduler是什么

Scheduler是私有ECS上持续运行的Python进程，由systemd启动和守护：

```text
systemd服务：mdtoword-scheduler
启动命令：python -m agent.cli scheduler --forever
运行用户：mdtoword-controller
```

它不是Linux cron。进程启动后一直运行，在循环中执行一次查询，然后等待约5秒再执行
下一轮。

## 2. 每轮先做什么

每轮只允许处理一个任务，执行顺序为：

```text
取得进程内执行锁
    ↓
查询是否存在未完成且可以恢复的agent_run
    ├─ 有：使用原run_id恢复旧运行
    └─ 没有：尝试领取一条新feedback
    ↓
执行这次LangGraph运行
    ↓
释放执行锁
    ↓
如果运行已结束，通知追踪网站
    ↓
等待约5秒
```

优先恢复旧运行可以避免服务器重启后旧任务被新反馈长期挤压。

## 3. 怎样领取一条反馈

Scheduler调用Supabase RPC：

```text
claim_next_agent_feedback
```

数据库在一个事务中：

1. 找到最早的一条`pending`反馈，或者领取租约已经过期的`claimed`反馈；
2. 使用`FOR UPDATE SKIP LOCKED`锁定这一行；
3. 把状态更新为`claimed`；
4. 增加`attempt_count`；
5. 写入`claimed_at`；
6. 生成新的`claim_token`；
7. 把领取后的记录返回给Scheduler。

`SKIP LOCKED`表示：如果另一个进程已经锁住某条反馈，当前领取操作跳过它，不等待，也
不会重复领取。

## 4. claim_token有什么用

`claim_token`证明“当前运行仍然拥有这条反馈”。后续每次更新反馈状态时，都要同时匹配：

```text
feedback_id + claim_token
```

如果旧进程暂停太久、租约到期，而新进程重新领取了反馈，旧进程手里的token已经失效，
不能继续覆盖新进程的状态。

`claim_token`只保存在私有数据库和LangGraph State，不写入模型消息、Langfuse或公开网站。

## 5. 租约和最大尝试次数

默认领取租约为300秒，最大领取次数为3次。

- Agent正常运行时，当前`claim_token`持续用于状态更新；
- Agent在创建运行前崩溃，租约到期后可以重新领取；
- 达到最大尝试次数后不再无限自动领取；
- 已经创建`agent_runs`和checkpoint的任务优先按原`run_id`恢复，不重新创建一套运行。

租约解决的是“领取后进程消失”，checkpoint解决的是“LangGraph执行到一半进程消失”。
两者作用不同。

## 6. 为什么当前只运行一个任务

Scheduler用`asyncio.Lock`覆盖领取和整次运行，因此单个生产Scheduler同一时间只处理一条
反馈。这样做符合当前反馈量，也减少以下冲突：

- 多个任务同时占用模型和Docker资源；
- 多个修复同时基于同一个`main`产生过期基线；
- 小型ECS发生内存和CPU争用；
- 排障时多个运行日志交错。

代价是：如果一条任务执行很久，后续反馈需要等待。当前查询延迟通常是0到5秒，但排队
时间还要加上正在执行任务的剩余时间。

## 7. 服务如何安全启用

生产Scheduler默认关闭。只有配置检查和只读审计通过，并显式设置：

```text
PRODUCTION_SCHEDULER_ENABLED=true
```

才允许领取反馈。标准部署先停止Scheduler、更新代码并重启Worker，再要求维护者输入
`ENABLE`恢复领取。不能把systemd显示`active`当作新代码已经加载。

对应实现：

- [agent/scheduler.py](../../agent/scheduler.py)
- [Supabase Repository](../../agent/repositories/supabase.py)
- [领取函数Migration](../../agent/migrations/001_agent_foundation.sql)
- [Scheduler systemd服务](../../deploy/agent/systemd/mdtoword-scheduler.service)

## 8. 结合源码看领取顺序

[agent/scheduler.py](../../agent/scheduler.py)的`_claim_and_run()`把“先恢复、再领取、单并发”
写成了明确顺序：

```python
async with self._run_lock:
    resumable = await self._run_repository.find_resumable()
    if resumable is not None:
        return await self._controller.resume(resumable.id)

    claimed = await self._feedback_repository.claim_next(
        now=datetime.now(UTC),
        lease_seconds=self._lease_seconds,
        max_attempts=self._max_attempts,
    )
    if claimed is None:
        return None
    return await self._controller.start(claimed)
```

常驻轮询也不是Linux cron，而是同一服务里的循环：

```python
while not stop_event.is_set():
    await self.run_once()
    try:
        await asyncio.wait_for(
            stop_event.wait(),
            timeout=self._poll_interval_seconds,
        )
    except TimeoutError:
        continue
```

真正的并发领取由[001_agent_foundation.sql](../../agent/migrations/001_agent_foundation.sql)中的
数据库函数完成；Python锁只保证当前Scheduler单并发，不能代替数据库行锁。
