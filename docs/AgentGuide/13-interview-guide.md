# Agent项目面试问答

## 1. 请介绍一下这个Agent项目

可以这样回答：

> 这是一个处理MD To Word真实用户反馈的自动修复Agent。用户通过浏览器插件提交原始
> Markdown和问题描述，后端保存到Supabase。私有ECS上的Scheduler每5秒领取反馈，
> LangGraph负责分类、复现、修复、独立验证和发布状态。模型只生成严格结构化的分类、测试
> 和修复内容，所有路径、补丁、状态和工具调用都由Python校验。生成代码只在无网络、非root、
> 限资源的一次性Docker容器里执行。最终验证通过后由GitHub App创建PR，维护者人工合并。

## 2. 为什么使用LangGraph

> 因为流程不是一次模型调用，而是包含分支、最多两轮复现、最多两轮修复、Docker验证、
> 发布和失败恢复。LangGraph把这些步骤写成明确节点，并把State保存到PostgreSQL。服务器
> 重启后可以使用原run ID从上一个checkpoint继续，而不是重新调用模型和重复创建PR。

补充时可以列出核心State：

```text
run_id, feedback_id, claim_token, status, route
base_sha, 各阶段文件引用
复现/修复轮次
模型、工具、Token和Sandbox用量
validated_patch_sha256, pr_url, error_code
```

## 3. Agent怎样感知新反馈

> 不是Supabase推送，也不是cron。ECS上有一个systemd管理的常驻Scheduler，它每5秒查询
> Supabase。数据库函数使用事务和`FOR UPDATE SKIP LOCKED`领取一条pending反馈，同时生成
> claim token。这样可以避免并发重复领取，并通过租约处理领取后进程崩溃。

## 4. 多个工具时模型怎样选择，如何避免选错

> 我们不把所有工具一次性开放给模型。LangGraph先确定当前阶段：Gate没有工具，源码阶段
> 只能搜索和读取，测试阶段只能提交测试Edit，修复阶段只能提交修复Edit，执行阶段只能提交
> 固定Sandbox Job。当前实现甚至不是让模型直接调用工具，而是让模型返回结构化字段，Graph
> 校验后调用本地函数。工具名、参数、路径、预算和当前State还会被Python再次检查。

## 5. 模型输出不稳定怎么办

> 先用严格JSON Schema限制结构，再用Pydantic和本地Policy检查跨字段规则。格式错误只给
> 一次修正机会，并把具体字段错误返回给模型。业务上复现和修复各最多两轮，每轮都由真实
> Docker测试决定是否继续。超过轮次或预算就进入明确终态，不无限循环。

## 6. 模型调用失败怎么办

> Provider把错误统一成认证、限流、超时、响应无效、上下文过大、上游不可用和安全拒绝。
> 限流、超时和短暂5xx有限重试，认证和上下文过大不重试；已经收到但不符合Schema的响应只
> 做一次格式修正。错误码写入数据库和Trace，服务器重启后从checkpoint继续。

## 7. 工具调用失败怎么办

> 先区分工具不存在、当前节点无权调用、参数非法和工具服务不可用。未注册工具不会猜测
> 替代品，越权工具不会执行。读取和编辑工具都有路径与大小限制，Sandbox失败不会降级到
> 主机执行。可恢复的外部故障使用相同幂等键重试，确定性安全错误直接结束。

## 8. 为什么需要Docker沙箱

> 因为模型生成的测试和修改后代码都属于不可信代码。Agent主进程持有数据库和GitHub等
> 凭据，不能直接执行它们。独立Worker使用固定digest镜像，为每个任务创建无网络、非root、
> 根文件系统只读、删除capability、限制CPU内存进程数和超时的容器。容器没有业务密钥和
> Docker Socket。执行后还会比较workspace diff，并在全新容器中完成最终验证。

## 9. 如何证明不是“模型自己说修好了”

> 我们先要求新增测试在原始base SHA上按预期失败，再要求同一测试在修复后通过，然后在
> 全新容器中重新证明基线失败、目标通过、全量pytest和DOCX结构检查通过。最终得到的补丁
> 会计算SHA-256，Publisher只允许发布这个哈希绑定的validated.patch。

## 10. 怎样处理提示注入

> 用户反馈、Markdown、源码和测试日志都标记为不可信数据。Gate没有工具，疑似注入由本地
> Policy隔离。后续工具按节点最小开放，模型不能访问Shell、数据库或GitHub。补丁还有路径
> 白名单，Docker没有网络和Secret。提示词只是第一层，真正安全边界是代码权限和沙箱。

## 11. 怎样避免重复任务和重复PR

> 数据库领取使用行锁、租约和claim token；LangGraph恢复使用固定run ID；Sandbox使用job
> ID作为幂等键；GitHub分支、提交和PR使用确定性命名并绑定feedback与补丁哈希。发布失败
> 恢复时只重试发布节点，不重新跑模型和Docker。

## 12. Supabase、Langfuse和本地文件分别有什么作用

> Supabase保存反馈和权威运行状态；PostgreSQL checkpoint保存LangGraph State；Agent本地
> 目录保存大补丁和测试结果文件；Langfuse保存模型和工具调用过程；追踪网站把Langfuse
> 数据整理后存进`agent_run_traces`。这些职责分开，不能用Langfuse恢复业务状态，也不能
> 让公开网站读取用户反馈表。

## 13. 追踪网站如何实时更新

> 它不是WebSocket实时流。Agent运行结束后只推送run ID和status给Vercel。Vercel立即刷新
> 列表缓存，然后在后台从Supabase读运行摘要、从Langfuse读调用明细，整理后写入Trace快照。
> Langfuse索引有延迟，所以后台按4秒和12秒重试。通知丢失时，用户打开详情页会触发一次
> 按需补抓。因此准确说法是“运行结束后近实时更新”。

## 14. 为什么不自动合并PR

> 自动测试能检查DOCX ZIP、XML、公式、表格和drawing数量，但不能完全替代在Word中观察
> 版式。Agent自动化到创建PR为止，维护者检查代码、Trace、测试和实际Word效果后手动合并。
> 这是质量边界，不是流程没有做完。

## 15. 项目做了哪些稳定性设计

可以按层回答：

```text
入口：IP限流、字段和大小检查、重复检测
领取：事务、行锁、租约、claim token、单并发
状态：PostgreSQL checkpoint、原run恢复
模型：严格Schema、一次格式修正、有限传输重试
工具：按节点授权、参数和输出限制
补丁：路径白名单、行数和能力检查、哈希绑定
执行：无网络Docker、资源限制、超时、全新容器
发布：base SHA检查、幂等PR、人工合并
观测：稳定错误码、Langfuse、数据库权威摘要、网站兜底
预算：模型、工具、轮次、Token和Sandbox时间上限
```

## 16. 这个方案目前有什么取舍

> 当前是低反馈量、单仓库、单维护者系统，所以Scheduler单并发，反馈入口使用单进程内
> 限流，网站通知使用一次推送加按需补抓，没有引入Redis、Kafka或多Agent协商。这些选择
> 降低了运行和排障复杂度。如果以后多实例部署，首先要把入口限流改成共享原子存储，再根据
> 任务量评估队列和并发，而不是提前增加基础设施。

## 17. 遇到一次线上失败怎么定位

> 先从`agent_runs`看最终status、route和error code，再到Langfuse找最后一个成功或失败的
> generation/tool span。模型问题要区分传输失败和结构失败；Sandbox问题看Worker错误码、
> JUnit和workspace diff；发布问题看base SHA、当前main和已有PR。根据run ID再检查checkpoint
> 和本地运行文件，判断是继续原run还是必须新建反馈，避免盲目重跑。

## 18. 介绍项目时容易说错的地方

- 不要说“Supabase推送任务给Agent”，当前是Scheduler每5秒轮询；
- 不要说“定时脚本”，它是systemd管理的常驻服务；
- 不要说“模型选择并执行任意工具”，工具由节点限制并由Graph调用；
- 不要说“Docker全部只读”，根文件系统只读，受控workspace、result和tmp有明确写入用途；
- 不要说“Worker没有Docker权限”，受信Worker能调用Docker，任务容器看不到Docker Socket；
- 不要说“Agent自动合并和部署”，它只创建PR；
- 不要说“Langfuse保存业务状态”，Supabase才是事实来源；
- 不要说“网站逐节点实时更新”，它是运行结束后的推送触发读取。
