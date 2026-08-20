# 反馈入口限流

## 1. 限制的是哪一个入口

限流只保护公开的`POST /feedback`。用户不需要登录就能提交问题，因此如果完全不限流，脚本
可以不断向Supabase写反馈，后面的Agent也会继续消耗模型、Docker和GitHub资源。

它不限制`POST /convert`，也不限制ECS内部的Scheduler或Sandbox Worker。

实际顺序是：

```text
浏览器插件
  → Cloudflare
  → Render /feedback
  → 取得可信客户端IP
  → 检查并消费限流额度
  → 写入Supabase feedback表
```

限流发生在写数据库之前，所以被拒绝的请求不会生成反馈记录，也不会被Agent领取。

## 2. 当前规则

默认额度为：

```text
同一IP：任意连续60秒最多1次
同一IP：任意连续1小时最多5次
同一IP：任意连续24小时最多10次
全部IP：任意连续1小时最多30次
```

前三个窗口限制单个来源，最后一个全局窗口保护Supabase和后续Agent总成本。只做单IP限制
不能应对许多不同IP同时提交；只做全局限制又可能让一个来源占满全部额度，因此两者同时
检查。

配置结构位于
[backend/app/feedback_rate_limit.py](../../backend/app/feedback_rate_limit.py)：

```python
@dataclass(frozen=True)
class FeedbackRateLimitPolicy:
    per_minute: int = 1
    per_hour: int = 5
    per_day: int = 10
    global_per_hour: int = 30
```

## 3. 怎样确定“同一个用户”

当前没有登录账户，所以限流只能使用网络来源，不能真正识别自然人。同一公司、学校或家庭
网络中的多人可能共享一个公网IP；一个人也可以切换Wi-Fi、手机流量或代理。

因此这里准确的说法是“按客户端公网IP限制”，不能说“按用户限流”。它是低成本资源保护，
不是身份认证，也不承诺抵御代理池。

### 为什么只读取CF-Connecting-IP

生产流量先经过Cloudflare，应用只读取边缘写入的单值`CF-Connecting-IP`：

```python
ip_key = resolve_cloudflare_client_ip(
    request.headers.get("CF-Connecting-IP")
)
```

没有使用`X-Forwarded-For`，因为调用方可以自己构造转发链。如果没有经过可信边缘覆盖，直接
相信任意请求头等于允许攻击者每次换一个身份。

当前生产黑盒验收已经确认：调用方伪造`CF-Connecting-IP`会被边缘拒绝或覆盖，伪造
`X-Forwarded-For`不能绕过分钟窗口。这个生产前提成立后，应用才可以信任该字段。

### IP怎样规范化

[resolve_cloudflare_client_ip()](../../backend/app/feedback_rate_limit.py)执行以下检查：

```python
candidate = raw_value.strip()
if not candidate or "," in candidate:
    raise ClientIpUnavailableError(...)

address = ipaddress.ip_address(candidate)
if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
    address = address.ipv4_mapped
if not address.is_global:
    raise ClientIpUnavailableError(...)

if isinstance(address, ipaddress.IPv6Address):
    return str(ipaddress.ip_network(f"{address}/64", strict=False))
return str(address)
```

具体含义是：

- 只接受一个地址，不接受逗号分隔的转发链；
- 拒绝空值、非法地址、回环地址、私网地址和保留地址；
- 把IPv4-mapped IPv6还原成IPv4，避免同一个地址出现两种键；
- IPv6按`/64`网段聚合，避免客户端通过频繁改变接口地址绕过限制；
- 无法获得可信公网IP时返回`503`，不会降级成不限流写入。

## 4. 使用的算法：滑动日志窗口

当前实现保存每次已接受请求的单调时间戳：

```python
self._events_by_ip: OrderedDict[str, deque[float]] = OrderedDict()
self._global_events: deque[float] = deque()
self._lock = asyncio.Lock()
```

每个IP对应一个`deque`，里面是该IP最近24小时已消费的时间；`_global_events`保存所有IP最近
1小时的时间。这里使用`time.monotonic()`，系统时钟被校正或人工修改时不会让窗口倒退。

之所以叫滑动窗口，是因为它检查“当前时间往前连续60秒、1小时、24小时”，而不是按自然
分钟整点切块。

## 5. 一次请求怎样检查和消费额度

核心代码在`FeedbackRateLimiter.consume()`：

```python
now = self._clock()
async with self._lock:
    self._prune_deque(self._global_events, now - HOUR_SECONDS)

    events = self._events_by_ip.get(ip_key)
    if events is None:
        self._make_room_for_ip(now)
        events = deque()
        self._events_by_ip[ip_key] = events
    self._prune_deque(events, now - DAY_SECONDS)

    retry_after = self._retry_after(events, now)
    if retry_after is not None:
        return FeedbackRateLimitDecision(
            allowed=False,
            retry_after_seconds=retry_after,
        )

    events.append(now)
    self._global_events.append(now)
    return FeedbackRateLimitDecision(allowed=True)
```

顺序是：

1. 删除已经离开窗口的时间戳；
2. 同时检查分钟、小时、每日和全局窗口；
3. 任一窗口超限就返回拒绝；
4. 全部通过才把当前时间同时写入IP队列和全局队列。

“检查”和“写入”必须在同一把锁里完成。如果先检查、释放锁、再写入，20个并发请求可能都
看到剩余额度，然后全部通过。

对应并发测试会同时提交20次，并确认只有1次通过：

```python
results = await asyncio.gather(
    *(limiter.consume("8.8.8.8") for _ in range(20))
)
assert sum(result.allowed for result in results) == 1
```

测试位于
[backend/tests/test_feedback_rate_limit.py](../../backend/tests/test_feedback_rate_limit.py)。

## 6. Retry-After怎样计算

四个窗口会分别计算还需要等待多久：

```python
waits = [
    self._window_wait(
        events,
        now=now,
        window_seconds=MINUTE_SECONDS,
        limit=self._policy.per_minute,
    ),
    self._window_wait(
        events,
        now=now,
        window_seconds=HOUR_SECONDS,
        limit=self._policy.per_hour,
    ),
    self._window_wait(
        events,
        now=now,
        window_seconds=DAY_SECONDS,
        limit=self._policy.per_day,
    ),
    self._window_wait(
        self._global_events,
        now=now,
        window_seconds=HOUR_SECONDS,
        limit=self._policy.global_per_hour,
    ),
]
active_waits = [wait for wait in waits if wait > 0]
if not active_waits:
    return None
return max(1, math.ceil(max(active_waits)))
```

必须取所有超限窗口中的最长等待时间。假设分钟窗口还要等20秒，但每日窗口还要等3小时，
如果只返回20秒，用户20秒后重试仍然会被每日窗口拒绝。

单个窗口使用下面的计算：

```python
if len(recent) < limit:
    return 0.0
return max(0.0, recent[-limit] + window_seconds - now)
```

`recent[-limit]`表示要让当前请求获得一个名额，至少需要等哪一条旧记录退出窗口。最后向上
取整为秒，并通过`Retry-After`响应头返回。

## 7. 为什么锁内不写Supabase

[backend/app/main.py](../../backend/app/main.py)先消费额度，释放内存锁后才调用数据库：

```python
decision = await limiter.consume(ip_key)
if not decision.allowed:
    return JSONResponse(status_code=429, ...)

# consume已经退出asyncio.Lock
await _insert_feedback(payload)
```

Supabase网络请求可能需要几百毫秒，也可能超时。如果把它放在锁内，一个用户的慢请求会让
所有用户都无法检查额度。

数据库写入失败时不会返还刚才的额度。代价是正常用户可能需要等待后再试；好处是攻击者
不能故意触发失败路径来无限绕过限流。这是明确选择的失败关闭策略。

## 8. 怎样控制内存

当前默认最多保存10,000个IP键。每100次消费会清理已经24小时没有事件的IP；达到容量时先
清理过期项，仍然超限才淘汰最久未使用的键：

```python
if len(self._events_by_ip) >= self._max_ip_keys:
    self._events_by_ip.popitem(last=False)
```

因为每个IP最多成功消费10个每日额度，全局每小时最多30个，所以单个键和全局队列都不会
无限增长。10,000个键是防止大量不同来源只请求一次也让字典持续扩大。

## 9. 为什么没有使用固定窗口

固定窗口通常按整分钟或整小时计数，实现更简单，但边界会允许突发：

```text
12:00:59 提交1次
12:01:01 再提交1次
```

两个请求分别属于不同自然分钟，固定窗口会放行；但它们在任意连续60秒内已经出现2次，
违反当前“60秒最多1次”的规则。滑动日志直接保存实际时间，可以严格表达当前产品口径。

## 10. 为什么没有使用令牌桶

令牌桶的做法是：按固定速度补充令牌，请求拿到令牌才能执行；桶容量决定允许多大的瞬时
突发。它特别适合“平均每秒多少请求，同时允许短时突发”的接口，例如高吞吐查询API。

当前反馈入口不希望突发：

- 一分钟额度就是为了阻止双击和连续提交；
- 小时、每日额度要求任意滑动区间内的硬上限；
- 全局窗口还要给出精确`Retry-After`；
- 单个IP每天最多只保存10个成功时间戳，滑动日志的内存成本非常小。

使用令牌桶并不是错误，但要精确表达当前四条规则，需要分别维护分钟、小时、每日和全局
多个桶，还要解释桶容量与补充速度是否允许突发。最后实现并不会比直接检查四个时间窗口更
清楚，而且行为会更难和产品文案一一对应。

如果未来目标变成“转换接口平均每秒100次，允许瞬时200次”，令牌桶会比保存大量请求时间
更合适。但那是另一种流量和产品要求，不能因为Java后端经常使用令牌桶就机械套用。

## 11. 为什么没有使用漏桶、Redis或数据库

| 方案 | 当前没有选择的原因 | 什么时候应该使用 |
|---|---|---|
| 漏桶 | 常用于把流量排队后匀速处理；反馈提交应该立即成功或返回429，不应让HTTP请求长时间排队 | 后台任务确实需要固定速度消费时 |
| Redis原子限流 | 当前Render只有一个Uvicorn worker和一个实例，约200用户；增加Redis会增加部署、网络和故障点 | 多worker、多实例、要求跨重启保留额度时 |
| 数据库计数表 | 每次恶意请求都会先访问要保护的数据库，还需要过期清理和并发事务 | 已有可靠共享数据库限流设施，且吞吐能够承担时 |
| Cloudflare/WAF规则 | 适合做更外层的粗粒度防护，但难以独立表达应用的四个窗口、插件提示和精确响应契约 | 遭遇明显攻击时作为第一层，与应用限流并用 |
| 验证码 | 会增加正常反馈成本，当前滥用规模不需要 | 代理池使IP限流失效，且滥用成本明显上升时 |

当前方案不是认为这些技术“不好”，而是当前单实例、小流量、明确四窗口规则下没有必要支付
额外基础设施成本。

## 12. 当前方案的限制和升级条件

进程内限流有两个明确限制：

1. Render重启或重新部署后，内存计数清空；
2. 多个Uvicorn worker或多个Render实例之间不共享计数。

10,000个IP达到容量后淘汰最久未使用键也是内存保护取舍，不适合把大量轮换IP视为强身份。
如果真实攻击长期触发该上限，应迁移到共享存储或增加边缘防护，而不是只把容量继续调大。

当前部署明确只有一个worker，并接受重启后清空。如果以后出现以下任一变化，就必须升级为
共享原子存储，不能继续声称它是全局限流：

- Uvicorn增加到两个或更多worker；
- Render开始水平扩容；
- 额度必须跨重启保留；
- 多台服务共同写入同一反馈表；
- IP轮换攻击已经超过当前方案能力。

升级时可以把四个窗口迁移到Redis，并通过Lua脚本或等价事务一次完成“清理、检查、消费”，
继续保证并发原子性。迁移后仍要保留可信IP解析、全局额度、`Retry-After`和失败关闭行为。

## 13. API和插件怎样处理超限

后端超限时返回：

```python
return JSONResponse(
    status_code=429,
    headers={
        "Retry-After": str(retry_after),
        "Cache-Control": "no-store",
    },
    content={"error": "rate_limited", ...},
)
```

插件读取`Retry-After`后提示用户等待，但不会自动重试，也不会清空用户已经填写的内容：

```typescript
if (response.status === 429) {
  const retryAfter = Number.parseInt(response.headers.get('Retry-After') ?? '', 10);
  const retryAfterSeconds = Number.isFinite(retryAfter) && retryAfter > 0
    ? retryAfter
    : undefined;
  const waitHint = retryAfterSeconds
    ? `，请约 ${retryAfterSeconds} 秒后再试`
    : '，请稍后再试';
  throw new FeedbackSubmissionError(
    `反馈提交过于频繁${waitHint}`,
    response.status,
    retryAfterSeconds,
  );
}
```

不自动重试很重要：限流请求立即重试只会再次得到429，还可能延长用户实际等待时间。

## 14. 自动测试和生产验证

自动测试覆盖：

- IPv4、IPv4-mapped IPv6和IPv6 `/64`规范化；
- 缺失、非法、私网和多值IP失败关闭；
- 分钟、小时、每日和全局窗口；
- 精确`Retry-After`；
- 20个并发请求只能消费一次额度；
- 超限请求不会第二次写Supabase；
- Supabase失败不返还额度；
- Supabase慢请求不占用限流锁；
- 过期IP清理和容量上限。

生产还实际验证了伪造头不能绕过、Wi-Fi与手机流量使用不同身份，以及同一网络立即重试返回
429。自动测试证明算法，生产黑盒测试证明Cloudflare到Render的真实信任链；两者不能互相
替代。

## 15. 面试时可以怎样回答

> 反馈接口是无需登录的公网写入口，我们在写Supabase之前做了单进程滑动日志限流。身份只
> 使用经过Cloudflare覆盖验证的客户端公网IP，IPv6按/64聚合；缺少可信IP就返回503，不
> 回退到可伪造的X-Forwarded-For。限流同时检查每IP一分钟、一小时、一天和全局一小时四个
> 窗口，用asyncio.Lock原子完成检查与消费，锁外才访问数据库。当前单实例、约200用户，每个
> IP最多只保留10个时间戳，所以没有引入Redis或令牌桶。令牌桶更适合允许突发的平均速率
> 控制，而我们的规则要求任意滑动区间的硬上限和精确Retry-After。将来多worker或水平扩容
> 时，会迁移到Redis原子脚本，保持同一行为契约。

## 16. 源码阅读顺序

1. [backend/app/feedback_rate_limit.py](../../backend/app/feedback_rate_limit.py)：IP解析、窗口
   算法、并发锁和内存清理；
2. [backend/app/main.py](../../backend/app/main.py)的`feedback()`：限流与Supabase写入顺序；
3. [backend/tests/test_feedback_rate_limit.py](../../backend/tests/test_feedback_rate_limit.py)：
   每个边界怎样验证；
4. [extension/src/api.ts](../../extension/src/api.ts)的`submitFeedback()`：插件怎样处理429；
5. [限流权威要求](../AgentRequirements/security-and-sandbox.md)：当前生产信任边界和升级条件。
