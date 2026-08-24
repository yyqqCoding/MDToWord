# 用户提交反馈与保存

## 1. 谁触发

用户在Edge或Chrome插件中填写反馈并点击提交。插件调用
`submitFeedback()`，向公开转换服务发送：

```http
POST /feedback
Content-Type: application/json
```

请求字段为：

```text
feedback_type       bug或feature
markdown_content    出现问题的Markdown，可选
description         用户描述
contact             联系方式，可选
```

插件不会直接连接Supabase，也不会持有Supabase密钥。

Feature表单会提示：建议可能经脱敏整理后公开为GitHub Issue，请勿填写隐私信息。这个提示
不改变请求字段；后续Gate仍会独立判断它究竟是功能需求、Bug、无关内容还是提示词注入。

## 2. Render后端做什么

FastAPI收到请求后按下面的顺序处理：

1. 从经过生产验证的`CF-Connecting-IP`取得客户端IP；
2. 检查IP格式，拒绝缺失、非法或包含多个值的请求；
3. 在进程内检查同一IP和全部用户的提交频率；
4. 生成新的`feedback_id`；
5. 使用后端保存的Supabase密钥写入`feedback`表；
6. 成功后把`feedback_id`返回给插件。

数据库为新记录自动设置：

```text
status = pending
```

后端请求体没有主动填写`status`，因此该初始值来自数据库默认值。

## 3. 为什么先限流再写数据库

`POST /feedback`不要求登录，任何人都可以访问。当前小流量部署使用单个FastAPI进程内的
滑动窗口：

```text
同一IP：60秒最多1次
同一IP：1小时最多5次
同一IP：24小时最多10次
全部IP：1小时最多30次
```

限流器使用一个`asyncio.Lock`保护“清理旧记录、检查额度、消费额度”。锁内不访问
Supabase，避免数据库延迟阻塞其他请求。

如果已经消费限流额度但Supabase写入失败，不返还额度。否则攻击者可以利用数据库失败
路径反复发送请求。

## 4. 可能返回什么

| HTTP状态 | 含义 | 插件行为 |
|---|---|---|
| `200` | 反馈已保存 | 显示提交成功 |
| `429` | 提交过于频繁 | 读取`Retry-After`并提示等待时间 |
| `503` | 无法确认可信客户端IP | 提示稍后重试 |
| `502` | Supabase暂时无法保存 | 提示反馈服务不可用 |

错误响应不会包含客户端IP、限流键、Supabase响应正文或密钥。

## 5. Agent什么时候看到这条反馈

保存成功不等于Agent立即收到通知。这里没有使用Supabase Realtime、数据库Webhook或消息
队列。反馈保持`pending`，等待ECS上的Scheduler下一轮查询。具体领取过程见
[发现和领取反馈](02-scheduling-and-claim.md)。

## 6. 数据安全

- 联系方式只保存在反馈数据中，不进入模型提示词；
- 用户Markdown和描述被当作不可信数据；
- Agent生成的公开Trace、PR和Issue不得包含联系方式；
- 追踪网站不会查询`feedback`表；
- Feedback API只能写入规定的反馈字段，不能领取任务或读取Agent运行。

对应实现：

- [extension/src/api.ts](../../extension/src/api.ts)
- [backend/app/main.py](../../backend/app/main.py)
- [backend/app/feedback_rate_limit.py](../../backend/app/feedback_rate_limit.py)

## 7. 结合源码看反馈入口

插件在[extension/src/api.ts](../../extension/src/api.ts)的`submitFeedback()`中发起普通HTTP
请求。它不会直接连接Supabase：

```typescript
const response = await fetch(`${trimTrailingSlash(serviceUrl)}/feedback`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});
```

Render后端在[backend/app/main.py](../../backend/app/main.py)的`feedback()`中先取得可信IP，
再消费限流额度，最后才调用Supabase写入函数：

```python
ip_key = resolve_cloudflare_client_ip(
    request.headers.get("CF-Connecting-IP")
)
decision = await limiter.consume(ip_key)
if not decision.allowed:
    return JSONResponse(status_code=429, ...)

await _insert_feedback(payload)
```

这里的顺序很重要：超限请求不会进入`_insert_feedback()`。限流实现和方案选择见
[反馈入口限流](15-feedback-rate-limiting.md)。
