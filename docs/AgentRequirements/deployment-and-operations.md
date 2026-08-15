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
- Agent Controller/Sandbox 不随 Render 后端部署；当前独立部署在受控 Linux ECS，由
  systemd 管理 Scheduler 与 Worker，不影响插件转换服务的独立运行；
- 后端部署完成后必须用原始失败 Markdown 再次导出并用 Word 打开确认；自动化 DOCX
  结构断言不能完全替代视觉验收。

## 5. 当前完成状态

截至 2026-08-13，阶段 A～G 的开发、自动测试、真实模型评估、真实 GitHub App PR、
人工合并、Render 部署和 Mermaid 原样例回放均已完成。常驻 Agent 已在独立 Linux ECS
上线：Worker 与 Scheduler 均为 `active/enabled`，Worker 仅监听本机 8090。无关反馈已在
生产环境路由为 `rejected_irrelevant`；已修复 Mermaid 反馈经受信测试回退与 Docker
复现后路由为 `cannot_reproduce`，没有生成补丁或 PR。

## 6. Linux 常驻 Agent 一键安装

仓库提供以下受版本控制的生产文件：

```text
deploy/agent/install.sh
deploy/agent/mdtoword-agentctl
deploy/agent/systemd/mdtoword-worker.service
deploy/agent/systemd/mdtoword-scheduler.service
```

适用前提：仓库位于 `/opt/mdtoword`，虚拟环境、`mdtoword-controller`、
`mdtoword-worker`、运行目录、Docker 镜像以及 `/etc/mdtoword/controller.env`、
`worker.env` 已按本方案准备完成。更新代码后执行：

```bash
cd /opt/mdtoword
sudo git pull --ff-only origin main
sudo bash deploy/agent/install.sh
```

安装脚本不会创建、复制或输出 Secret，也不会覆盖两份环境文件。它会安装最新 systemd
单元与管理命令、启动并启用 Worker、明确停止并禁用 Scheduler，然后执行只读审计。审计
会等待 Worker 最多 30 秒完成端口绑定，并只输出活动状态数量，不读取反馈正文、联系方式
或 Artifact；Worker 超时未就绪时会直接附带 systemd 状态和最近日志。

常用命令：

```bash
sudo mdtoword-agentctl audit    # 配置、Worker、镜像、监听地址和数据库状态计数
sudo mdtoword-agentctl enable   # 审计后要求输入 ENABLE，才开启自动领取
sudo mdtoword-agentctl disable  # 立即停止领取，并把生产开关恢复为 false
sudo mdtoword-agentctl status
sudo mdtoword-agentctl logs
```

`enable` 会先备份 `controller.env`，再原子更新
`PRODUCTION_SCHEDULER_ENABLED=true`。Scheduler 启动后优先恢复审计中列出的活动 run，随后
领取 `pending` 反馈；因此首次启用前必须人工核对两个状态计数对象。运行代码更新时重新
执行安装脚本是有意的 fail-safe：它会关闭 Scheduler，完成审计后再由维护者重新启用。

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
- 可用 `agent.evals.runner --provider configured --case-id <id>` 验证单条 Gate，但阶段 D
  仍应以 Langfuse 的具体 generation 节点和数据库阶段字段定位。

历史 `failed` feedback/run 保留用于审计，不重新打开。修复部署后使用新的 `pending`
反馈验证；如果当前代码已经解决问题，正确终态是 `cannot_reproduce`，不是创建空修复 PR。

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

网络前提：ECS 需允许出站访问站点域名的 HTTPS。不需要开放任何入站端口。

密钥仍按既有约定管理：只写入 `/etc/mdtoword/controller.env`，不提交、不记录、
不通过聊天传递；`install.sh` 不会创建或覆盖该文件。
