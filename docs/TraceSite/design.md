# Agent Trace 展示站 · 设计文档

状态：主体已部署；阶段 I 的分类展示与统计正确性已本地实现，待 migration 与生产部署。
阶段 I 设计分支：`feature/feature-issue-routing-design`。

本文档定义 MD To Word 修复 Agent 的公开 Trace 展示站。Agent 自身的目标、状态机、
接口和验收标准仍以 `docs/AgentRequirements/` 为唯一权威来源；本文档只描述**读取并
呈现**这些数据的前端系统，不改变任何 Agent 行为契约。

---

## 1. 目标与非目标

### 1.1 目标

向外部访客完整展示一次用户反馈如何被自动修复：走过哪些节点、调用了哪些工具和模型、
每一步耗时与用量、为什么做出该判定、最终是否产出 PR。

面向的读者是没有本项目上下文的人（面试官、同行、潜在用户），因此叙事清晰度优先于
运维信息密度。

### 1.2 非目标

- **不做控制面**。站点全程只读，不提供重跑、取消、改配置、触发领取等任何写操作。
  Agent Controller 与 Sandbox Worker 位于独立 ECS，Worker 仅监听 `127.0.0.1:8090`；
  为展示站开放任何公网写入口都会破坏既有安全边界。
- **不替代 Langfuse**。深度排障仍在 Langfuse 控制台完成，站点面向展示与叙事。
- **不展示用户原始内容**。见第 3 节。
- **不承载运维告警**。失败率、成本监控仍按 `observability.md` 第 10 节由数据库和
  Langfuse 统计。

---

## 2. 定位与信息架构

站点定位为 **Trace Explorer**：一个只读的、隐私保护的执行证据浏览器。

```
/                概览：叙事入口 + KPI + 结果分布 + 精选案例 + 最近运行
/runs            运行列表：可按 route / category / status / 时间筛选
/runs/[id]       单次运行详情（核心页）
/about           项目讲解：架构、安全设计、技术选型
```

### 2.1 首屏叙事锚点

采用已合并的真实案例作为 hero case，而非抽象说明：

| 项 | 值 |
|---|---|
| 用户反馈 | 导出 Word 后只显示 Mermaid 源码，未生成流程图 |
| Langfuse Trace ID | `5e497f5005ae3696ad50edc5e172837e` |
| 结果 | https://github.com/yyqqCoding/MDToWord/pull/1 （已合并） |

该案例同时覆盖 gate → 复现 → 修复 → 独立验证 → 发布 PR 的完整链路，且问题本身对
外行直观可懂，是首页和 `/about` 的默认示例。开发期也以此 Trace 作为真实 fixture。

---

## 3. 数据源与安全边界

### 3.1 两层数据

| 层次 | 来源 | 回答的问题 |
|---|---|---|
| 业务状态机 | Supabase `public.agent_runs` | 这次反馈走到哪、为什么停、用了多少 token/成本 |
| 执行细节 | Langfuse Trace | 具体执行了哪些 observation、各自耗时与状态 |

Trace 结构由 `docs/AgentRequirements/observability.md` 第 3 节固定约定，observation
名称稳定且不含动态 ID，因此前端可按已知名称做确定性布局，而非被动渲染任意树。

### 3.2 公开字段白名单

**这是本设计最关键的安全约束。`agent_runs` 的几个列和 JSONB 不能整块暴露。**

代码依据：

- `agent/domain/repair.py:180` → `_bounded_failure_summary(summary.target_message)`
- `agent/domain/repair.py:284` → 该函数只做 `strip()` 与截断 4096，**无任何脱敏**
- `agent/domain/repair.py:240` → `ValidationResult.failure_summary` 继承上述值
- `agent/repositories/supabase.py:571`、`:617` → 上述值写入 `agent_runs.error_message`

即：JUnit 的原始失败信息（可能含生成的测试源码片段、断言文本、用户 Markdown 片段）
会一路流入 `repair.failure_summary`、`validation.failure_summary` 和
`error_message` 列。这与 `observability.md` 第 4 节「JUnit failure message 不进入
Langfuse」的约束一致——数据库侧保留是为了维护者排障，**不代表可以公开**。

`agent_runs` 逐列裁决：

| 列 | 公开 | 说明 |
|---|---|---|
| `id` | ✅ | 作为详情页 URL |
| `status` / `route` / `area` / `category` / `dry_run` | ✅ | 枚举值 |
| `base_sha` / `extension_version` | ✅ | 公开仓库信息 |
| `provider` / `model` | ✅ | |
| `graph_version` / `prompt_versions` / `policy_version` | ✅ | 版本号，利于讲可复现性 |
| `model_calls` / `tool_calls` / `*_tokens` | ✅ | |
| `estimated_cost` | ⚠️ | 见 3.6 |
| `validated_patch_sha256` / `pr_url` / `issue_url` / `error_code` | ✅ | GitHub URL 已经公开 |
| `started_at` / `finished_at` | ✅ | |
| `classification` | 🔪 裁剪 | 只取 intent/area/category、布尔与数值分类；剔除 `reason`、Issue title/summary |
| `reproduction` | 🔪 裁剪 | 只取 `disposition` / `round` / `target_test_selector` / `expected_failure_kind` / `failure_code`；`failure_summary` 一律不取，见 3.3 |
| `repair` | 🔪 裁剪 | 只取 `disposition` / `round` / `failure_code` |
| `validation` | 🔪 裁剪 | 取 `passed` / `base_sha` / `target_test_selector` / 四个子验证对象 / `changed_files` / 各 `*_sha256`；剔除 `failure_summary` |
| `feedback_id` | ❌ | 改以 `left(md5(id::text), 12)` 作展示用 `run_ref` |
| `claim_token` | ❌ | 运维凭据 |
| `trace_id` / `langfuse_trace_id` | ❌ | 仅服务端拉快照时使用，不下发浏览器 |
| `artifact_path` / `task_artifact_ref` | ❌ | 服务器路径 |
| `error_message` | ❌ | **禁区**，来源见上 |

`public.feedback` 表**整表不参与展示站查询**。其 `contact` 与 `markdown_content`
均为用户内容，`contact` 在 `agent/domain/models.py` 中连 `repr` 都被禁用。反馈内容
在站点上只以「类别 + 人工撰写的案例说明」形式出现（见 3.7）。

**已知且接受的部分暴露：`feedback_id` 前 8 位十六进制。**
Trace 中 `publish-pr` 的 `input.feedback_id_prefix` 会带上这 8 个字符
（`observability.md` §7 明确只记录前缀）。它不可逆，且同一个值本就已经公开：
它嵌在我们有意展示的 `reproduction.target_test_selector`
（`test_feedback_892ff98b_…`）里，也嵌在已合并 PR 的分支名
`agent/feedback-892ff98b-docx_structure` 中。因此不做剥离——只在一处删掉、
另外两处照常展示是安全剧场，不是防护。完整的 `feedback_id` 仍然绝不出现。

> 核查禁区字段时必须用**精确键名**匹配。用子串匹配 `feedback_id` 会命中
> `feedback_id_prefix` 并给出假警报。

### 3.3 失败文案改为前端映射

不下发任何后端 `failure_summary`，改为在前端维护 `error_code` → 中文文案的映射表。

三个理由：

1. 彻底消除 3.2 中的泄漏路径，且不依赖「当前这些 summary 恰好是固定英文文案」这一
   会随代码演进失效的假设。
2. `ReproductionReport.failure_summary` 当前确由 `classify_reproduction_result`
   产出固定文案，但 `agent/graph.py:475` 存在另一处构造点；与其逐处审计并在每次改动
   时重新复核，不如从接口上排除。
3. 展示效果更好——可写成面向外行的中文解释，例如
   `target_passed` → 「基线上目标测试通过，说明该缺陷已被此前的改动修复」。

映射表未命中时回退显示原始 `error_code`，不显示任何后端文本。

### 3.4 公开视图 `public.agent_run_public`

按 3.2 白名单做**字段级**裁剪的只读视图，JSONB 用 `jsonb_build_object` 逐字段重建，
禁止 `select classification` 之类的整块投影。

视图不授予 `anon` / `authenticated`；服务端以 `service_role` 读取。设视图仅为把白名单
固化在数据库侧，形成第二道防线，避免未来某次前端改动误取禁区列。

> 需要一条新 migration。按 `CLAUDE.md`，migration 必须由维护者审查后手工执行，
> 应用启动和测试不得自动改 Schema。DDL 见
> `agent/migrations/006_trace_site_public_read.sql`，执行方式见第 12 节。

阶段 I 需要后续 migration 以追加方式重建该视图，公开新增 `area` 与 `issue_url`，并在
classification 裁剪中加入 `area`、排除 Issue title/summary。不得直接修改已执行的
`006`，也不得在站点启动时自动执行新 migration。

### 3.5 快照表 `public.agent_run_traces`

| 列 | 类型 | 说明 |
|---|---|---|
| `run_id` | uuid PK → `agent_runs(id)` | |
| `trace_id` | text | Langfuse trace id |
| `trace_json` | jsonb | 已裁剪的 observation 树 |
| `captured_at` | timestamptz | |
| `source` | text | `langfuse_api` / `manual` |

写入时机：Agent 的运行落终态后主动推送，站点收到后拉取并裁剪落库。
详见 3.5.1；早期的每日 Cron 回填已废弃。

采用快照而非每次实时查询，理由是：不把展示站可用性绑在第三方观测平台的保留策略、
限流与可用性上；终态 Trace 本身不可变，天然适合固化；离线也能演示。

#### 3.5.1 写入链路：推送为主，按需补抓兜底

原设计是 Vercel Cron 每日 03:00 批量回填。实际反馈量很低，这个方案两头不讨好：
绝大多数触发都是空跑，而新跑完的运行最长要等 24 小时才看得到调用明细。
改为**事件驱动**：

| 触发 | 路径 | 重试 | 用途 |
|---|---|---|---|
| Agent 运行落终态 | `POST /api/hooks/run-finished` | 有（0 / 4s / 12s） | 常规路径 |
| 访问详情页时无快照 | `getRunDetail` → `captureRunTrace` | 无 | 自愈兜底 |
| 维护者手动 | `GET /api/cron/snapshot` | 无 | 批量重刷、本地拉齐 |

三者共用 `src/lib/server/capture.ts` 的同一份实现。

**回调先应答再干活。** 校验通过立刻返回 `202`，抓取放进 Next 的 `after()`。
最初的实现是同步抓完再应答，上线首测就暴露了问题：新运行的 Trace 还没被 Langfuse
索引，重试阶梯走满 ~21 秒才成功，而 Agent 侧客户端超时是 10 秒 ——
日志记下 `ReadTimeout`，实际快照已经写成功了。两个数字分别设定、从未对齐。

不能靠调大 Agent 超时解决：Agent 只是来发个信号，其 Scheduler 是单并发，
干等 20 秒期间领不了下一条反馈。正确的边界是站点自己吸收这段延迟。

列表与首页的 `revalidatePath` 在应答前就做掉（不依赖抓取结果，运行应尽快出现），
详情页的 `revalidatePath` 放在抓取之后。

**推送体只有 `run_id` 与 `status`。** 站点自己去 Langfuse 取 Trace、自己投影落库。
投影逻辑（`projectTrace`，含 3.5 那四条实测规则）因此只存在于站点一侧 ——
若让 Agent 直接写快照表，这套规则就得在 Python 里再维护一遍，两边必然漂移。

**推送前必须 flush。** `telemetry.flush()` 原本只在守护进程退出时调用
（`agent/runtime.py` 的 `finally`，包住的是 `run_forever` 的整个生命周期）。
Langfuse SDK 有后台批量上报，但根节点 `feedback-repair-run` 是最后才关闭的，
不 flush 就通知，站点大概率拿到不完整的树。因此 `TraceSiteNotifier` 在 POST 前
先 `asyncio.to_thread(telemetry.flush)`，站点侧再用重试覆盖服务端索引延迟。

**推送是 at-most-once，且绝不影响修复。** 站点不可达、超时、5xx 都只记一行日志
（只记异常类型，httpx 的异常文本会带完整 URL）。丢了不补推，由按需补抓自愈。
Scheduler 侧再兜一层 `except`，保证轮询循环不会被通知拖死。

**回调是可选能力。** `TRACE_SITE_WEBHOOK_URL` 与 `TRACE_SITE_WEBHOOK_SECRET`
缺任一即完全关闭推送，Agent 行为与接入前一致；站点退回按需补抓。

延迟实测（生产，2026-08-15）：

- 回调应答 `202`，Agent 侧不再等待抓取
- 生产真实运行：06:33:22 开始，06:33:59 快照落库（含 Langfuse 索引等待）
- 按需补抓（Vercel → Langfuse）3.5s 写回并渲染 24 条调用行；
  同一页面命中快照缓存时 1.0s

> 用「删掉一条快照再访问详情页」验证自愈时要注意：`selectTraces` 有 600s 数据缓存，
> 若该运行的详情页刚被访问过，页面会读到删除前的缓存结果，补抓分支根本不执行。
> 必须挑一条该环境从未渲染过的运行。真实场景不受影响 —— 新运行在缓存里没有条目，
> 首次请求直接落库、发现没有、立刻补抓。

#### 缓存：不要长缓存「否定结果」

快照查询只能短缓存（600s）。快照本身不可变，但「尚未回填」是个会变的否定结果。
最初按终态给它 86400s，导致回填完成后页面整整一天仍显示「快照尚未回填」。

#### 接入真实数据后实测出的四条现实

设计阶段基于 `observability.md` §3 的结构描述，实测与文档有出入，代码以实测为准：

1. **没有任何 observation 的 `parentObservationId` 是 null。** 真正的根节点其父指向
   未随响应返回的 OTEL span。根的判定必须是「父 ID 不在本次返回的 ID 集合内」。
   按「父 ID 为空」判定会一条根都识别不出来，整批回填静默失败。
2. **一条 Trace 里可能有多个根。** Controller 用确定性 Trace ID，同一次运行被
   checkpoint 恢复多次就会产生多个根，它们的 `metadata.run_id` 相同。
   站点按 `run_id` 取出属于本次运行的全部根，合并成一条时间轴，
   并在页面上注明「恢复 N 次」。
3. **结构是扁平的。** 文档 §3 里的 `gate-feedback` / `reproduce` / `repair` /
   `validate-final` 等分组节点实际不存在，调用直接挂在根下；名称也有出入
   （实际是 `prepare-source-snapshot`、`read-fix-source-file`）。
   因此阶段分组由**名称映射**完成，不依赖树的层级 —— 将来补上分组节点也仍然正确。
4. **库里有 `trace_id` 不代表 Langfuse 里有这条 Trace。** Telemetry 是 fail-open 的，
   上报失败不影响业务结果，但确定性 `trace_id` 仍会入库。32 条运行里有 4 条
   在 Langfuse 返回 404。回填任务把这种情况统计为 `missing`，与真正的错误分开。

#### 缓存：不要长缓存「否定结果」

快照查询只能短缓存（600s）。快照本身不可变，但「尚未回填」是个会变的否定结果。
最初按终态给它 86400s，导致回填完成后页面整整一天仍显示「快照尚未回填」。

> 更正一处此前的表述：「Trace 保留 14 天」出自
> `docs/AgentRequirements/observability.md:225`，是**本项目文档的约定**，不是 Langfuse
> 的平台限制。代码中真正实现的只有 Artifact 保留期（`agent/config.py:24`
> `artifact_retention_days=14`，可由 `ARTIFACT_RETENTION_DAYS` 覆盖）；仓库内没有任何
> 配置或代码控制 Langfuse trace 保留。
>
> 维护者已在 Langfuse 控制台核查，未找到 Data Retention 设置项，实际表现为长期保留。
> 因此「防过期」不再是快照的理由，保留快照的理由收敛为：正常访问路径不依赖第三方
> 平台的可用性与限流，且离线可演示。

> **区域必须与 Agent 一致。** 本项目 Agent 的 `.env` 用的是
> `https://jp.cloud.langfuse.com`（日本区）。展示站若沿用默认的
> `https://cloud.langfuse.com`，同一对 key 会返回 401
> （`Invalid credentials. Confirm that you've configured the correct host.`）。
> `.env.example` 已把默认值改为 jp 并加注说明。

### 3.6 成本字段的展示口径

`observability.md:218` 与 :103 明确：模型单价未配置时，数据库 `estimated_cost` 保持
`0`，这**只表示未配置估算单价，不表示上游 API 免费**。

因此当 `estimated_cost = 0` 时，站点必须显示「未配置单价」而非 `$0.00`，否则构成
误导。Langfuse 按自身价目表推算的成本是分析值，不回写数据库，也不在站点展示，避免
两个数字打架。

### 3.7 用户反馈内容的呈现方式

访问范围已定为「完全公开、内容脱敏」。因此站点不渲染 `feedback.description` 或
`markdown_content`。每条展示的 run 关联一条**人工撰写**的案例说明（标题 + 2~3 句
背景），存放于仓库内的 `content/cases/*.md`，与数据库解耦。

好处是：既有可读的叙事，又不存在用户内容外发路径；且案例说明可以针对外行读者优化
措辞，而原始反馈往往零散。

未登记人工案例的普通运行不能只按技术 `category` 回退为“未分类反馈”。阶段 I 使用公开
结构化字段按固定规则生成通用标题，不触碰用户原文：

| intent / area / route | “反馈”列 | “类别”列 | “终态” |
|---|---|---|---|
| `feature_request + backend + issue_required` | 后端功能需求 | 功能建议 · 后端 | 已创建 Issue |
| `feature_request + extension + issue_required` | 前端/扩展功能需求 | 功能建议 · 扩展 | 已创建 Issue |
| `feature_request + cross_component + issue_required` | 跨端功能需求 | 功能建议 · 前后端 | 已创建 Issue |
| `bug_report + extension + issue_required` | 前端/扩展缺陷 | 扩展缺陷 | 已创建 Issue |
| `rejected_irrelevant` | 无关内容 | 无关内容 | 已忽略 |
| `quarantined_security` | 提示词注入尝试 | 提示词注入 | 安全拦截 |
| `needs_human` + 未知类别 | 信息不足的反馈 | 待确认 | 转人工 |

标题和终态先按 `route` 判断，再使用 intent/area/category 细化；不能先按
`status=completed` 把 `quarantined_security` 显示成“已结束”。历史 `out_of_scope` 保持
“历史范围外结论”，不伪装成新 Issue，也不反向推断当时的 intent。

`issue_url` 可以公开并链接 GitHub，因为 Issue Publisher 已在发布前完成脱敏；站点仍不
从 Issue 反抓正文写入 DTO。详情页可链接公开 Issue，列表使用上述通用标题，不复制 Issue
标题或用户需求摘要。

---

## 4. 服务端接口契约

全部为 Next.js Server Component / Route Handler，运行在 Vercel 服务端。
`SUPABASE_SERVICE_ROLE_KEY`、`LANGFUSE_SECRET_KEY` 只存在于服务端环境变量，**不进入
任何客户端 bundle**。

| 接口 | 用途 | 缓存 |
|---|---|---|
| `getRunList(limit)` | 列表页 / 概览 | 统一 `runs` tag，最多 60s，推送时失效 |
| `getOverviewStats()` | 概览 KPI | 统一 `runs` tag，最多 60s，推送时失效 |
| `getRunDetail(id)` | 详情页主数据 | run 行 no-store；快照 600s |
| `fetchPullDiff(prUrl)` | GitHub 公开 PR diff | 86400s |
| `POST /api/hooks/run-finished` | Agent 运行完成回调 | 无 |
| `GET /api/cron/snapshot` | 手动批量回填 | 无 |

两个写端点各有自己的密钥，互不复用：

- `/api/hooks/run-finished` 以 `SITE_WEBHOOK_SECRET` 校验 `x-webhook-secret` 头，
  并要求 body 的 `run_id` 是合法 UUID；未授权 401，非法入参 400，未配置 503，
  正常返回 **202**（先应答，抓取在 `after()` 里做，理由见 3.5.1）。
  列表与首页的 ISR 缓存在应答前失效，详情页在抓取完成后失效 ——
  **即使这次没抓到快照，列表刷新也照做**，运行本身应当立刻出现，
  调用明细可以晚一步由按需补抓补上。

阶段 I 必须同时失效 `runs` 数据 tag 与 `/`、`/runs` 路径。`revalidatePath` 不能替代共享
查询 tag；回调丢失时 60 秒 TTL 是正确性兜底，而不是依赖访客等待 15 分钟统计缓存。
- `/api/cron/snapshot` 以 `CRON_SECRET` 校验（`Authorization: Bearer` 或
  `x-cron-secret`）。已无定时调度，仅供维护者手动批量回填。

两者都只写我们自己的快照表，不触碰任何 Agent 运行时状态。

客户端拿到的永远是已裁剪的 DTO，类型定义与视图列一一对应，不存在「前端自己决定不
显示某字段」的情况。

### 4.1 密钥不进客户端的三道保障

1. **`server-only` 导入守卫**。`src/lib/server/*` 全部 `import "server-only"`，
   任何客户端组件误引用都在构建期直接失败，而不是等密钥进了 bundle 才发现。
2. **资源白名单**。`supabase.ts` 的通用查询函数只允许 `agent_run_public` 与
   `agent_run_traces`；抓取快照需要读基表 `agent_runs` 的 `langfuse_trace_id`
   （公开视图刻意排除的禁区列），因此单独写成两个固定函数
   （`selectPendingTraceIds` 批量、`selectTraceIdForRun` 单条），
   列清单写死在代码里、不接受调用方传参，且单条版本自己再校验一次 UUID，
   保证「能读基表」这件事只存在于这两个入口。
3. **构建产物扫描**。每次交付前对 `.next/static` grep
   `SERVICE_ROLE` / `LANGFUSE_SECRET_KEY` / `SITE_WEBHOOK_SECRET` / `CRON_SECRET` /
   `service_role` / `rest/v1` / `api/public/traces`，全部必须为 0。

### 4.2 未配置数据源时的行为

Supabase **未配置**时整体回落到构造数据，站点仍可运行，页面顶部显示「未配置
Supabase」横幅。这样本地做视觉迭代不需要任何密钥，也避免了「没配好就白屏」。

Supabase 已配置但查询失败时不得回落到 Mock：生产错误不能伪装成一组看似可信的运行与
统计。页面应显示明确的“数据暂时不可用”错误态并允许重试，服务端只记录稳定错误类型，
不回显响应体、URL 查询串或密钥。

密钥通过 `trace-site/.env.local`（已被忽略）或 Vercel 项目环境变量注入，
配置名以 `.env.example` 为准。任何密钥都不得提交、记录或通过聊天传递。

---

## 5. 页面规格

### 5.0 整体外壳与参考稿取舍

采用控制台布局：左侧固定窄栏（三个入口）+ 右侧内容区，窄屏时侧栏转为顶部导航。

维护者提供的参考稿确定了视觉语言（深色控制台、信息条、阶段芯片带连接线、瀑布与
元数据左右分栏、底部卡片行），这部分已采纳。但参考稿约四成模块**不做**，原因分三类：

| 参考稿模块 | 不做的原因 |
|---|---|
| KPI 的 sparkline 与「vs 24h ago」环比 | 参考稿是 128 runs/24h；真实运行量是几十条量级，趋势线会退化成平线或两个点 |
| TOTAL COST 磁贴 | `estimated_cost` 恒为 0（未配置单价），做成大数字等于放空位 |
| RECENT ANOMALIES / 异常检测 | 无此能力；「2.1x above p95」在几十条样本上没有统计意义 |
| ARTIFACT SUMMARY、ARTIFACTS 下载 | 直接违反脱敏设计，`artifact_path` 是禁区列 |
| SYSTEM STATUS 健康探测 | 无 Redis / S3；Agent 主机 Worker 只监听回环地址，公网探不到，做出来只能是假的 |
| 侧栏 Traces / Spans / Feedback / Alerts / Settings | Trace 与 Span 只存在于单次运行内部；Feedback 是用户内容；Alerts 与 Settings 属控制面，本站只读 |
| 登录态 / Administrator | 公开只读站没有账户概念 |
| 顶部装饰性 CLI 命令行 | 没有该命令，放假 CLI 会削弱可信度 |
| Flamegraph 视图 | observation 树最深 3 层，火焰图退化成三个色块 |
| 表格中的 FEEDBACK ID / RETRIES / COST 列 | 分别是展示禁区、不在公开视图内、恒为空 |

侧栏底部保留了参考稿 SYSTEM STATUS 的位置，但改为说明数据来源与脱敏口径 ——
位置有用，内容必须真实。

### 5.1 `/` 概览

- KPI 磁贴（4 个）：总运行数、产出 PR 数、平均运行耗时、Token 合计。无 sparkline、无环比。
  Token 磁贴的副标注明「模型单价未配置，暂不估算成本」。
- 总运行数是全部 `agent_runs` 尝试数；产出 PR 是唯一非空 `pr_url` 数；平均运行耗时是
  全部已有 `finished_at` 的终态运行墙钟平均值，包含失败、拦截与无关反馈；Token 合计是
  全部运行的 `total_tokens` 之和。Issue 不混入 PR 指标，详情与列表单独展示 Issue 链接。
- 统计必须分页读取全部公开运行或使用等价的受信聚合查询，不得用 `limit=500` 的数组长度
  冒充总数。当前数据量低，优先复用显式分页，不为四个 KPI 新增数据库聚合服务。
- 精选案例卡：hero case 标题 + 说明 + 只读阶段芯片条，直达详情页。
- 最近运行表（6 列）：run_ref、反馈、类别、终态、耗时、Token。
- 结果分布图：M4 补，运行量足够时才有意义。

### 5.2 `/runs` 列表

筛选条件一行排布于表格上方：route / category / status / 时间范围。
表格列：run_ref、反馈、类别、终态、耗时、token，并在有结果时提供 PR 或 Issue 链接。
空态与筛选无结果态分别设计文案。

### 5.3 `/runs/[id]` 详情（核心页）

四个区块自上而下：

**① 顶部信息条**
八个字段横排：run_ref、类别、模型、版本、开始时间、总耗时、Token、调用次数。
不用大 KPI 磁贴 —— 磁贴适合概览页的少数关键指标，详情页的这些是上下文而非重点。

**② 阶段芯片条**
固定序列 `claim → gate → prepare-source → reproduce → repair → validate → publish`。
已执行实色，未执行虚线灰，失败用状态色。可点击联动 ④。
序列固定，布局手工排定，不使用自动布局算法——保证每次运行的图长得一样，利于对比与讲解。

**③ Span 瀑布 —— 按阶段分组的可折叠树**
数据来自快照的 observation 树。不引甘特库：层级最多 3 层、名称是稳定契约，
手写在响应式与键盘可达上都更可控。

初版是 22 行平铺列表，实测**读不出结构**，三个具体原因：缩进只差不到 1rem，层级看
起来像噪声；`SPAN`/`AGENT`/`MODEL`/`TOOL` 四种等重方框标签抢走了名称的视觉权重；
原始英文名对没有项目上下文的访客不传达任何信息，同一次复现里两条
`read-source-file` 更是完全无法区分。

改法是四条：

- **视觉容器分组优先于缩进**。8 个顶层阶段各自是一个带边框的组，子节点包在组内，
  层级由容器边界表达，不再依赖缩进宽度。
- **中文名做主标题**。`run-target-validation` → 「目标测试验证」。原始名称仍是
  Langfuse 侧契约，保留在右侧面板标题下方，两者不冲突。
- **类型改用图标**。模型调用用 `Sparkles`、工具用 `Wrench`、阶段用实心/空心圆点，
  颜色区分角色，不再用四个等重方框。
- **同名节点加区分后缀**。取 `input.path` 的文件名，两条读取源码分别显示
  `pandoc_runner.py` 与 `normalizer.py`。

交互：每组可单独折叠，另有「全部收起 / 全部展开」。折叠动画用
`grid-template-rows: 0fr → 1fr`——这是对未知高度做平滑过渡的唯一纯 CSS 方案。
子节点带树形引导线，末行竖线只画到中点。

**④ 常驻元数据面板**
与 ③ 左右分栏，宽屏时 sticky。四个标签页：概览 / 输入 / 输出 / 元数据。
**不用弹出抽屉** —— 弹层会遮挡瀑布图，而实际使用方式是「点一条看一条」，需要保持
上下文可见。面板底部常驻脱敏说明并链接 `/about`，把脱敏讲成设计选择而非数据缺失。

**⑤ 代码改动**
真实 diff，数据源是 GitHub 公开 API 的已合并 PR，**不是** Agent 的受控 artifact。
公开仓库的 PR diff 本就是公开信息，这样既能展示最有说服力的一屏，又完全绕开脱敏边界。
未产出 PR 的运行此处不渲染 —— 语义上本就不该有 diff。
Issue 运行不渲染代码 diff；结果区域提供公开 Issue 链接与“交由维护者人工处理”说明。

**⑥ 底部三卡**
测试汇总（`full_validation` 四个计数 + 四道验证逐项）、补丁策略检查、结果。
补丁策略的限额与白名单取自 `agent/policies/patch_policy.json`（`patch-policy-v2`），
不是通用的 lint / license / secret 扫描 —— 展示真实存在的约束，否则只是装饰。

### 5.4 `/about`

讲三件事：整体架构（Render 转换后端 / ECS Agent / Sandbox 隔离）、安全设计（Agent 只
改后端白名单文件、oracle 只能从登记断言中选、验证通过才建 PR、绝不自动合并）、以及
Trace 脱敏策略。这一页是把工程严谨度讲出来的地方。

---

## 6. 前端技术选型

| 用途 | 选型 | 理由 |
|---|---|---|
| 框架 | Next.js 15 App Router + TypeScript | Server Component 天然隔离密钥；Vercel 一等公民 |
| 样式 / 组件 | Tailwind v4 + 手写组件 | 只需要 Badge / Card / Table / 分栏面板等少数几个，未引入组件库 |
| 状态机图 | 手写 flex + 连接线 | 见下方说明 |
| Span 瀑布 | 手写 CSS Grid | 见 5.3 |
| Diff 渲染 | 手写 table | 数据源已是结构化 hunk，无需 diff 库 |
| 统计图表 | Recharts | 留给 M4 的结果分布图 |
| 图标 | lucide-react | 与 `extension/` 一致 |
| 字体 | Geist / Inter + JetBrains Mono | 哈希、SHA、路径用等宽 |

部署：Vercel。Function region 对齐 Supabase region 以降低往返延迟。
Vercel Hobby + Supabase 免费层足以承载当前运行量。

**代码位置：`trace-site/`。** 不用 `site/`，因为仓库根 `.gitignore` 继承自 Python
模板，含未锚定的 `/site` 与 `lib/` 规则会静默忽略源码。`src/lib` 由
`trace-site/.gitignore` 中的 `!/src/lib/` 显式恢复，根 `.gitignore` 保持不动，
以免影响 `backend/` 与 `agent/` 的既有忽略行为。

**状态机图不使用 React Flow。** 本文档 5.3 已确定该图是固定 7 节点、布局手工排定、
不需要自动布局，因此图库的核心能力（动态布局、画布拖拽、minimap）全部用不上，却要
为一张静态图付出约 100KB 依赖，且窄屏转竖排反而更难实现。改为手写 flex + 连接线，
响应式与键盘可达性都更可控。详情页 First Load JS 因此保持在 129KB。

---

## 7. 视觉与图表规范

深色为主的 observability console 风格：中性灰阶表面，一个主色承载「正常执行」，
状态色专用于终态。

### 7.1 字号阶梯

正文基准 15px。**`text-xs`（12px）是硬性下限，不使用 10px / 11px。**

| 层级 | 用途 |
|---|---|
| `text-2xl` | 页面标题 |
| `text-3xl` mono | KPI 数字、测试总数 |
| `text-base` | 卡片标题、案例说明正文 |
| `text-sm` | 正文、表格、标签、状态徽标（主力字号） |
| `text-xs` | 仅限密集等宽内容：瀑布节点类型徽标、diff 行号、时间轴刻度 |

配套原则：**不靠密集小字承载补充说明**。阶段芯片不再单列状态词（交给图标、颜色和
`sr-only`），KPI 磁贴不再挂副标注释，元数据面板的脱敏说明收进 `/about`。需要解释的
内容用正常字号写清楚，不需要解释的直接删掉。

### 7.2 动效

统一缓动 `cubic-bezier(0.16, 1, 0.3, 1)`（起步快、收尾稳），全部用 CSS keyframes
实现，不引动画库。

| 动效 | 用在哪 |
|---|---|
| `anim-rise` 淡入上浮 450ms | 卡片、页头、KPI、阶段芯片；同屏元素按 55–70ms 错峰 |
| `anim-fade` 淡入 350ms | 表格行、瀑布行、标签页内容切换 |
| `anim-grow-x` 横向展开 700ms | 瀑布条，`transform-origin: left`，按行号递增延迟 |
| `anim-slide-in` 右滑入 350ms | 元数据面板切换选中节点时的头部 |
| `.lift` 悬停上浮 2px | KPI 磁贴、PR 按钮 |

侧栏激活项有左侧竖条指示，悬停时右移 2px 且图标放大；「查看完整执行流程」的箭头悬停右移。
`prefers-reduced-motion: reduce` 下所有动画与位移一律关闭，不是缩短而是取消。

### 7.3 交互反馈：三档，全站统一

不允许各处各写一套悬停样式。三个语义档位定义在 `globals.css`：

| 档位 | 用在哪 | 表现 |
|---|---|---|
| `.lift` | 可点击的块：阶段芯片、KPI 磁贴、卡片、PR 按钮 | 上浮 2px + 边框转主色 + 主色辉光；按下时回落（80ms） |
| `.glow-strong` | 当前选中的块 | 常驻双层辉光 + 主色边框，与临时 hover 明确区分 |
| `.row-hover` | 信息行、表格行、diff 行、检查项、元数据条目 | 只提亮背景与文字，不上浮 —— 它们不是独立可点块 |

配套细节：图标在所属块 hover 时放大 10%；表格行 hover 时左侧主色指示条从中点纵向展开；
瀑布条 hover 时增高并提亮，树形引导线转主色；标签页选中项带主色下划线。

**阶段芯片的悬停反馈对所有页面、所有状态生效**，包括未执行的芯片和没有传
`onSelect` 的概览页 —— 是否可点击只影响光标与点击行为，不影响「鼠标划过即突出」
这一反馈本身。早期版本把两者绑在一起，导致概览页的芯片是死块。

### 7.3 图表规则

以下在实现图表时强制执行：

- **状态色保留**。good / warning / serious / critical 分别对应
  `pr_opened` / `cannot_reproduce` / `security_rejected` / `failed`，
  不得复用为图表的第 N 个系列色。
- **状态永远是 图标 + 文字 + 颜色**，不靠颜色单独表意。
- **单轴**。token 与 cost 分成两张图，绝不做双 Y 轴。
- **分类色按固定顺序分配**，不随筛选结果重排——筛掉一个类别不得导致其余类别改色。
- **上线前跑调色板校验**（明暗两套各验一次），不靠肉眼判断色盲可分辨度。
- ≥2 个系列必有图例，≤4 个系列同时直接标注；单系列不加图例框。
- 网格与坐标轴保持弱化；数值与标签使用文本色而非系列色。
- 深色模式独立取色，不是浅色模式的自动反转。

---

## 8. 缓存与性能

终态 run 的数据不可变，这是缓存策略的基础：

- run 详情：`no-store`，每次读库。详情页是单条查询，代价可控，
  换来「跑完立刻能看到结论」。
- 快照：600s。快照不可变，但「尚未回填」是会变的否定结果，不能长缓存（见 3.5）。
- 列表与统计：共享 `runs` tag，TTL 60s；Agent 推送时同时用 tag 与 path 立即失效。
- 页面渲染路径原则上不调用 Langfuse；唯一例外是详情页发现终态运行无快照时的
  按需补抓，它不重试、失败静默，页面照常用运行摘要推导阶段。

新反馈的端到端可见延迟：

| 环节 | 延迟 | 依据 |
|---|---|---|
| Scheduler 领取 | ≤ 5s | `POLL_INTERVAL_SECONDS=5` |
| Agent 跑完一次修复 | 分钟级 | 取决于复现/修复轮次 |
| 全站可见（含时间线） | 秒级 | 运行落终态即推送 + `revalidatePath` |
| 推送丢失时的列表/统计 | ≤ 60s | `runs` tag TTL 兜底 |
| 推送丢失时的 Trace 详情 | 首次访问详情页 | 按需补抓自愈 |

---

## 9. 安全清单

实现完成后逐条核对：

- [ ] 客户端 bundle 中不含 `SERVICE_ROLE`、`LANGFUSE_SECRET_KEY`、
      `SITE_WEBHOOK_SECRET`（构建产物 grep 验证）
- [ ] 无任何查询触达 `public.feedback`
- [ ] `error_message` / `*.failure_summary` 不出现在任何 DTO、日志或页面
- [ ] `claim_token` / `artifact_path` / `task_artifact_ref` 不出现在任何 DTO
- [ ] `classification.reason` 不出现在任何 DTO
- [ ] `issue_url` 可公开，但 Issue 标题/摘要/正文不从 GitHub 反抓进 DTO
- [ ] `estimated_cost = 0` 显示为「未配置单价」而非 `$0.00`
- [ ] `/api/hooks/run-finished` 校验 `SITE_WEBHOOK_SECRET` 且强校验 `run_id` 为 UUID
- [ ] `/api/cron/snapshot` 校验 `CRON_SECRET`
- [ ] 视图未授予 `anon` / `authenticated`
- [ ] 站点仅有的写操作是快照表 upsert，不触碰 Agent 运行时状态
- [ ] Agent 推送体只含 `run_id` 与 `status`，不含任何内容
- [ ] Agent 侧推送失败只记异常类型，不记 URL、密钥或响应体
- [ ] Supabase 已配置但查询失败时显示错误态，不静默回退 Mock
- [ ] `quarantined_security` 显示“提示词注入 / 安全拦截”，不显示“未分类 / 已结束”
- [ ] 历史 `out_of_scope` 不改写、不显示为已创建 Issue

---

## 10. 里程碑

| 阶段 | 内容 | 产出 |
|---|---|---|
| M0 | 本设计文档评审通过 | 本文件 |
| M1 | migration（视图 + 快照表）DDL 待维护者执行 | `agent/migrations/006_trace_site_public_read.sql` ✅ 已提交待审 |
| M2 | Next.js 骨架 + DTO 层 + mock 数据跑通详情页 | 可本地预览 |
| M3 | 接入真实 Supabase + hero case Trace 快照 | 真实数据详情页 |
| M4 | 概览、列表、about 页与视觉打磨 | 完整站点 |
| M4.5 | Agent 完成回调取代每日 Cron | 秒级可见，见 3.5.1 |
| M5 | 安全清单核对 + Vercel 部署 | 线上地址 |
| M6 | 阶段 I 分类展示、Issue 链接与统计正确性 | 与 Supabase 全量对账、最长 60s 可见 |

M2 使用 mock 是有意为之：先把最难做好看的详情页做出可视效果供你评价，再接真实数据，
避免在数据接入上耗时后才发现视觉方向要改。

---

## 11. 已确认事项

1. **Langfuse 保留策略**：控制台无 Data Retention 设置项，实际为长期保留。hero trace
   `5e497f5005ae3696ad50edc5e172837e` 存在。快照方案保留，理由见 3.5 修订说明。
2. **migration 执行**：DDL 见 `agent/migrations/006_trace_site_public_read.sql`，
   由维护者在 Supabase SQL Editor 手工执行。执行方式与回滚见第 12 节。
3. **展示范围**：全部历史 run 上站，首页只推精选案例。含义见 12.3。
4. **域名**：使用 Vercel 默认 `*.vercel.app`，暂不接自定义域名。
5. **同步方式**：定时回填废弃，改为 Agent 运行落终态后主动推送 + 详情页按需补抓。
   理由是反馈量低，定时轮询绝大多数是空跑，却仍让新运行等最长 24 小时。见 3.5.1。
   生产 Agent 所在 ECS 需允许出站访问站点域名（维护者已确认放行）。
6. **阶段 I 统计口径**：总运行数按全部 run，PR 按唯一 URL，平均耗时按全部终态，Token
   按全部 run；Issue 不混入 PR 指标。
7. **阶段 I 分类展示**：新运行不再产生 `out_of_scope`；前端/扩展 Bug 和所有功能需求
   显示为人工 Issue 路由，无关和注入分别显示明确类别与终态，历史记录只兼容不回写。

---

## 12. 迁移执行与全量上站的补充约定

### 12.1 为什么放在 `agent/migrations/`

同一个 Postgres 数据库只维护一条有序迁移序列，维护者按编号顺序执行时不必在两个目录
之间切换。该文件不引入任何 Agent 运行时依赖，只新增一个视图和一张表，不修改既有列、
约束、函数或权限，因此不影响 Controller / Scheduler / Worker 的现有行为。

如果你更希望按组件分目录，这个文件可以移到独立位置，改动成本只有路径。

### 12.2 执行与回滚

执行：Supabase Dashboard → SQL Editor → 粘贴 `006_trace_site_public_read.sql` → Run。
需要 owner 权限，因此只能由你执行，我这边既没有凭据，`CLAUDE.md` 也禁止任何自动改
Schema 的路径。

执行后建议先自查两条：

```sql
-- 应返回 0 行；确认浏览器角色拿不到任何权限
select grantee, privilege_type
from information_schema.role_table_grants
where table_name in ('agent_run_public', 'agent_run_traces')
  and grantee in ('anon', 'authenticated');

-- 应确认禁区字段确实不存在
select * from public.agent_run_public limit 1;
```

回滚：

```sql
drop view if exists public.agent_run_public;
drop table if exists public.agent_run_traces;
```

两者都是新增对象，回滚不影响任何既有数据。

### 12.3 全量上站的两点含义

**其一，站点统计是真实的。** 因为不做人工挑选，概览页的结果分布会如实包含
`cannot_reproduce`、`failed` 等非成功终态。这是加分项——一个只展示成功案例的
Agent 展示站没有说服力，而如实展示「哪些情况 Agent 会主动放弃」恰好能讲清楚它的
判定边界。文案上要主动解释，而不是让访客自己发现失败率。

**其二，`quarantined_security` 的 run 也会上站。** 这类 run 来自 prompt injection
尝试，展示它能直接证明防护有效。安全性上没有额外风险：注入内容本身位于
`feedback.markdown_content` 与 `classification.reason`，两者都已排除在公开投影之外，
页面上只会出现路由结果与 `tool_calls=0` 这一事实。

`dry_run = true` 的运行需要在列表和详情页明确打标，避免被误读为真实修复。
