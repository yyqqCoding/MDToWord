# 部署与运行方式

本文说明各组件实际运行在哪里、什么时候需要 Docker，以及从用户转换到 Agent 自动修复
的完整链路。安全隔离规则仍以 [security-and-sandbox.md](security-and-sandbox.md) 为准。

## 1. 两条业务链路

### 1.1 用户转换链路

```text
浏览器扩展
  -> Render HTTPS /convert
  -> FastAPI 后端归一化 Markdown
  -> Mermaid CLI + Chromium（仅流程图，生成 PNG）
  -> Pandoc + reference.docx
  -> DOCX 结构与三线表后处理
  -> 浏览器下载 .docx
```

这条链路由 Render 上的后端 Docker 容器独立完成。Mermaid 图片渲染发生在 Render
容器内，不会调用开发者电脑上的 Docker、Worker 或 Agent。

### 1.2 反馈自动修复链路

```text
feedback 表中的 pending 反馈
  -> Agent Controller 原子领取
  -> Gate 分类、相关性和 Prompt Injection 检查
      |- 功能需求/前端缺陷 -> GitHub App 创建脱敏 Issue -> 维护者人工处理
      `- 后端缺陷
           -> 固定 GitHub main 的 base_sha 源码快照
           -> Docker Sandbox 复现缺陷（最多两轮）
           -> 生成受限后端修复（最多两轮）
           -> 全新 Sandbox 独立验证
           -> GitHub App 创建分支和 Pull Request
           -> 维护者人工 Review 与 Merge
           -> Render 自动部署
           -> 使用原 Markdown 人工回放 Word 导出
```

Agent 不自动合并 PR，也不直接部署 Render。用户反馈、运行状态和 checkpoint 保存在
Supabase/PostgreSQL；Langfuse 是可观测性副本，不是状态事实来源。
Agent 不自动修改 `extension/`；前端/扩展 Bug、展示、视觉、交互和布局需求只创建 Issue。

## 2. Docker 的两个用途

| Docker 组件 | 当前运行位置 | 用途 | 本地是否需要常驻 |
|---|---|---|---|
| `backend/Dockerfile` | Render | 公开转换 API；包含 Pandoc、Mermaid CLI、Chromium 和字体 | 不需要 |
| `agent/sandbox/Dockerfile` | 当前为本地 Docker Desktop/WSL | 隔离执行模型生成的测试和后端补丁 | 仅运行 Agent 或 Docker 测试时需要 |

Render 所说的 Docker 是“根据 Dockerfile 构建并运行后端镜像”，不等于当前项目已经在
Render 上部署了一个可供 Agent 创建子容器的 Docker Worker。关闭本地 Docker 后：

- 插件继续使用 Render 转换服务，普通 Markdown、公式、表格和 Mermaid 导出不受影响；
- 本地 Gate-only、纯离线评估和不使用 Sandbox 的命令仍可运行；
- 本地 reproduce、repair、publish 全链路会因 Worker 或 Docker 不可用而返回
  `sandbox_unavailable`。

## 3. 当前推荐运行模式

### 日常使用插件

无需启动本地 Docker、Sandbox Worker 或 Agent Controller。只需保证 Render 服务健康，
并在扩展中使用线上服务地址。

### 手工运行一条 Agent 任务

1. 启动 Docker Desktop，并启用当前 WSL 发行版集成；
2. 构建或确认固定 Sandbox 镜像及其 `SANDBOX_IMAGE_DIGEST`；
3. 使用仅含 `SANDBOX_*` 配置的环境启动 `agent.sandbox.worker_http`；
4. 在另一个终端加载 Controller 私有配置并运行 `agent.cli`；
5. 完成后可以停止 Worker 和 Docker Desktop。

具体命令见 [agent/README.md](../../agent/README.md)。

阶段 I 中，显式 `--publish` 同时授权“后端修复创建 PR”和“功能需求/前端缺陷创建
Issue”；不带该开关的 Gate/dry-run 不产生真实 GitHub 写入。两条发布分支使用不同的最小
权限令牌和恢复 checkpoint。

### 7×24 小时自动处理反馈

使用一台受控的独立 Linux 主机或虚拟机部署：

```text
私有主机
  |- Agent Controller + Scheduler（持有数据库、模型、Langfuse、GitHub 凭据）
  `- Sandbox Worker + Docker Engine（只持有 SANDBOX_* 配置）
       `- 临时无网络任务容器（不持有任何业务 Secret）
```

Controller 与 Worker 通过内网通信。Worker 端口不得暴露到公网，Docker Socket 不得挂载
到公开转换服务或任务容器。生产 Scheduler 只有在全部配置检查通过并显式设置
`PRODUCTION_SCHEDULER_ENABLED=true` 后才领取反馈；默认保持关闭。

## 4. 部署职责

- `git push main` 触发 Render 构建和部署转换后端；
- 浏览器扩展由维护者构建 `extension/dist` 并在浏览器中刷新，不部署到 Render；
- 阶段 I 已包含功能建议表单的公开发布提示；该文案属于扩展源码变更，仍按商店发布
  流程由维护者构建、审核和上架，不由 Agent 自动发布；
- Agent Controller/Sandbox 不随 Render 后端部署；当前独立部署在受控 Linux ECS，由
  systemd 管理 Scheduler 与 Worker，不影响插件转换服务的独立运行；
- 后端部署完成后必须用原始失败 Markdown 再次导出并用 Word 打开确认；自动化 DOCX
  结构断言不能完全替代视觉验收。

## 5. 当前完成状态

截至 2026-08-16，阶段 A～G 的开发、自动测试、真实模型评估、真实 GitHub App PR、
人工合并、Render 部署和 Mermaid 原样例回放均已完成。常驻 Agent 已在独立 Linux ECS
上线：Worker 与 Scheduler 均为 `active/enabled`，Worker 仅监听本机 8090。无关反馈已在
生产环境路由为 `rejected_irrelevant`；已修复 Mermaid 反馈经受信测试回退与 Docker
复现后路由为 `cannot_reproduce`，没有生成补丁或 PR。部署 Worker 权限修复后，真实公式
反馈 `41d6c497-...` 已完成复现、修复、独立验证，并由 GitHub App 自动创建 PR #2；该证据
只证明 Agent 发布闭环，PR 合并和后端部署仍由维护者单独执行。

阶段 I 的 Issue 路由、追加 migration、插件提示与 Trace Site 数据修正截至 2026-08-24
已完成本地实现和自动测试，但尚未执行 migration、增加 GitHub Issues 权限、创建真实
Issue 或部署；当前生产仍运行旧语义并可能产生 `out_of_scope`。

## 6. Linux 常驻 Agent 一键安装

仓库提供以下受版本控制的生产文件：

```text
deploy/agent/install.sh
deploy/agent/deploy.sh
deploy/agent/mdtoword-agentctl
deploy/agent/requirements.lock
deploy/agent/systemd/mdtoword-worker.service
deploy/agent/systemd/mdtoword-scheduler.service
```

适用前提：仓库位于 `/opt/mdtoword`，虚拟环境、`mdtoword-controller`、
`mdtoword-worker`、运行目录、Docker 镜像以及 `/etc/mdtoword/controller.env`、
`worker.env` 已按本方案准备完成。日常更新只执行以下两条命令：

```bash
sudo git -C /opt/mdtoword pull --ff-only origin main
sudo bash /opt/mdtoword/deploy/agent/deploy.sh
```

`deploy.sh` 先停止 Scheduler，再调用底层 `install.sh`。`install.sh` 会先停止 Scheduler 与
Worker，使用 Python 自带 `ensurepip` 补齐可能不存在的 pip，再从
`deploy/agent/requirements.lock` 安装 `uv.lock` 已解析的精确生产依赖并执行 `pip check`；
依赖完成后才安装 systemd 单元与管理命令、重启 Worker 并执行只读审计。随后调用 `enable`
再次审计并要求维护者输入
`ENABLE`，最后输出 Worker 与 Scheduler 状态。任一步失败都会保持 Scheduler 关闭。脚本
不会创建、复制或输出 Secret，也不会覆盖两份环境文件。审计会等待 Worker 最多 30 秒完成
端口绑定；未携带凭据的就绪请求必须返回 `401`，同时证明 HTTP 服务和认证边界已经生效，
且不会把 Worker Secret 放进 curl 参数或日志。审计只输出活动状态数量，不读取反馈正文、
联系方式或 Artifact；Worker 超时未就绪时会直接附带 systemd 状态和最近日志。

常用命令：

```bash
sudo mdtoword-agentctl audit    # 配置、Worker、镜像、监听地址和数据库状态计数
sudo mdtoword-agentctl enable   # 审计后要求输入 ENABLE，才开启自动领取
sudo mdtoword-agentctl disable  # 立即停止领取，并把生产开关恢复为 false
sudo mdtoword-agentctl status
sudo mdtoword-agentctl logs
sudo mdtoword-agentctl model-smoke  # Scheduler关闭时，用合成数据验证真实主/备模型
```

`enable` 会先备份 `controller.env`，再原子更新
`PRODUCTION_SCHEDULER_ENABLED=true`。Scheduler 启动后优先恢复审计中列出的活动 run，随后
领取 `pending` 反馈；因此启用前必须人工核对两个状态计数对象。`deploy.sh` 只是把既有安全
顺序编排成一个入口；底层 `install.sh` 单独执行时仍会保持 Scheduler 关闭。

`uv.lock` 是依赖解析权威；每次修改根 `pyproject.toml` 或 `uv.lock` 后必须同步生成生产
导出，不能手工修改版本：

```bash
uv export --frozen --no-dev --no-emit-project --no-annotate \
  --format requirements.txt --output-file deploy/agent/requirements.lock
```

导出文件列出全部精确版本与制品哈希，生产安装使用 `--no-deps --require-hashes`，避免服务器
再次解析出另一套依赖图或下载未经锁文件登记的制品。
项目源码由 systemd 的固定 `WorkingDirectory=/opt/mdtoword` 直接导入，不需要 editable
安装。依赖安装或 `pip check` 失败时不得启动 Worker 或 Scheduler。

## 7. 生产巡检与 Provider 排障

日常状态和最近日志：

```bash
sudo mdtoword-agentctl status
sudo mdtoword-agentctl logs
```

预期 Worker、Scheduler 均为 `active/enabled`。需要停止自动领取时执行
`sudo mdtoword-agentctl disable`；该操作不停止公开 Render 转换后端。

模型失败必须按稳定错误码区分：

- `provider_unavailable`：连接、远端断开或上游 5xx 在有限重试后仍失败；
- `invalid_response`：接口已经返回内容，但严格 JSON Schema 或本地 Policy 在一次格式
  修正后仍未通过；
- `/models` 返回 200 只验证 Base URL、网络和认证，复杂 `generate-test` 仍可能失败；
- 配置 `FALLBACK_MODEL_ENABLED=true` 后，前两次临时传输失败仍使用主接口，第三次使用备用
  OpenAI-compatible 接口；日志和 Trace 继续按统一 Provider 口径展示，不区分实际接口；
- Repair Agent 要求备用模型完整配置。自定义模型若没有 LangChain profile，必须填写
  `MODEL_CONTEXT_WINDOW` 与 `FALLBACK_MODEL_CONTEXT_WINDOW`，否则 audit 在领取反馈前失败；
- 部署后先 `disable` Scheduler，再运行 `sudo mdtoword-agentctl model-smoke`。该命令会产生
  少量模型费用，但只使用合成消息，不读反馈/源码、不启动 Sandbox、不写数据库或 GitHub；
  输出验证主备 tool calling、只读工具并行、usage/cache 字段、Summary 内容与比例阈值；
- `GATE_MODEL_TIMEOUT_SECONDS` 默认 30 秒、允许 30～120 秒；调整前应在生产 Agent 主机上
  使用相同模型与结构化请求测量耗时，避免用过长等待掩盖上游故障；
- 可用 `agent.evals.runner --provider configured --case-id <id>` 验证单条 Gate，但阶段 D
  仍应以 Langfuse 的具体 generation 节点和数据库阶段字段定位。

历史 `failed` feedback/run 保留用于审计，不重新打开。修复部署后使用新的 `pending`
反馈验证；如果当前代码已经解决问题，正确终态是 `cannot_reproduce`，不是创建空修复 PR。

源码准备阶段的稳定错误码必须区分：`source_auth_error`表示`GITHUB_READ_TOKEN`失效或没有
仓库读取权限，会转入`needs_human`且不重试；`repository_unavailable`表示GitHub只读请求
在三次有界attempt后仍受限、连接失败或返回5xx；`source_revision_error`只表示main版本
响应或确定性请求没有通过本地契约。`mdtoword-agentctl audit`当前只验证配置存在，不证明
Token可被GitHub接受；更新读取Token后应先做一次不输出Token的已认证`commits/main`只读
探测，再重新启用Scheduler。

阶段 I 上线前，维护者必须在 GitHub App 设置中显式增加 `Issues: Read and write`，随后
重新执行只读权限预检。预检分别申请 PR 权限组和 Issue 权限组：前者只能含
`contents:write + pull_requests:write`，后者只能含 `issues:write`；任一响应出现未允许权限
均停止 Scheduler 部署。权限变更本身不得由安装脚本、应用启动或 migration 自动执行。

Issue 发布失败使用同一 run 的稳定 marker 恢复，不重新执行 Gate；历史
`out_of_scope` 运行不批量补建 Issue。若维护者需要处理历史记录，必须逐条复核并单独批准
真实 GitHub 写入。

## 8. 展示站点完成回调（可选）

公开 Trace 展示站（`trace-site/`，部署在 Vercel）需要知道一次运行何时结束，才能立即
抓取 Trace 快照并刷新页面缓存。Agent 侧因此支持一个可选的完成回调。

`/etc/mdtoword/controller.env` 新增两项：

```text
TRACE_SITE_WEBHOOK_URL=https://<站点域名>/api/hooks/run-finished
TRACE_SITE_WEBHOOK_SECRET=<与站点 SITE_WEBHOOK_SECRET 相同的值>
```

行为约束：

- **两项缺任一即完全关闭推送**，Agent 行为与接入前完全一致。这是可选能力，
  不配置不会影响任何修复流程。
- **只在运行落终态时推送**，恢复中的运行不推 —— 此时 Trace 还不完整。
- **推送体只有 `run_id` 与 `status`**，不含反馈正文、补丁、日志或任何观测内容。
  站点自己去 Langfuse 取 Trace。
- **推送失败绝不影响修复**：站点不可达、超时、返回 5xx 都只记一行 WARNING，
  且只记异常类型（httpx 的异常文本会带完整 URL）。推送是 at-most-once，
  丢了不补推，由站点访问详情页时的按需补抓自愈。
- 推送前会先 `flush` 一次 Langfuse 客户端，否则站点会拿到不完整的树。
- `flush` 只保证客户端已发送，不保证 Langfuse Cloud 同时完成根节点与子节点索引。站点只在
  响应中出现稳定命名的 `feedback-repair-run` 根后才固化快照；若子调用先到而根尚未出现，
  本次按缺失处理并进入既有重试，不能把孤儿调用合成为空明细快照。
- 数据库摘要已有 `model_calls/tool_calls`、但快照合成根下没有任何 observation 时，该快照
  视为不完整。详情页按需补抓和手工 `/api/cron/snapshot` 都必须重新抓取；真实零调用运行
  仍允许只有合成根。精确 `run_id` 因脱敏缺失或被部分替换时，只能退回同一 Trace 中稳定
  命名的 Agent 根，不能退回任意 observation。
- 站点正常返回 `202`（先应答、后台抓取）。若日志出现
  `trace site notify failed: ReadTimeout`，说明站点侧在应答前做了耗时工作，
  属于站点缺陷而非 Agent 配置问题 —— Agent 只发信号，不应为抓取干等。

展示站部署后至少抽查一条 `model_calls/tool_calls > 0` 的 PR 运行和一条
`cannot_reproduce` 运行：工具调用卡片必须能展开实际 observation，不能只显示“阶段已执行、
调用明细未上报”的摘要兜底。若摘要计数与快照不一致，先按上述完整性规则回填，再判断为
Telemetry 确实缺失；不得用页面文案代替 Supabase/Langfuse 对账。

网络前提：ECS 需允许出站访问站点域名的 HTTPS。不需要开放任何入站端口。

密钥仍按既有约定管理：只写入 `/etc/mdtoword/controller.env`，不提交、不记录、
不通过聊天传递；`install.sh` 不会创建或覆盖该文件。
