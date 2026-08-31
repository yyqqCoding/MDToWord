# 部署与运行

本文面向维护者，说明 Agent 如何部署、启停、验收和排障。公开转换后端、Trace Site、
Controller 和 Sandbox Worker 是四个独立部署单元。

## 1. 生产拓扑

~~~text
Render
  └─ backend FastAPI / Pandoc / Chromium

独立 Linux 主机
  ├─ Controller + Scheduler
  │   ├─ Supabase/PostgreSQL
  │   ├─ OpenAI-compatible 主/备模型
  │   ├─ GitHub 只读和最小发布凭据
  │   └─ Langfuse
  └─ Sandbox Worker -> 127.0.0.1:8090 -> 临时无网络容器

Vercel
  └─ Trace Site（读取 Supabase 脱敏视图和 Langfuse 快照）
~~~

Worker 不公开端口，不挂载公开服务的 Docker Socket；任务容器不持有任何业务 Secret。
Controller 与 Worker 可在同一台受控 Linux 主机上运行，但进程、配置和权限仍然分离。

## 2. 代码更新

生产 Agent 的标准入口是仓库脚本。更新前先停止自动领取，更新后由脚本安装依赖、重启
Worker、审计，最后由维护者显式启用 Scheduler：

~~~bash
sudo mdtoword-agentctl disable
sudo git -C /opt/mdtoword pull --ff-only origin main
sudo bash /opt/mdtoword/deploy/agent/deploy.sh
sudo mdtoword-agentctl enable
sudo mdtoword-agentctl status
~~~

deploy.sh 或安装脚本任一步失败都应保持 Scheduler 关闭。不要直接编辑 systemd 单元，
也不要以服务显示 active 代替代码版本、端口认证和配置审计。

仓库中受版本控制的生产文件包括：

~~~text
deploy/agent/deploy.sh
deploy/agent/install.sh
deploy/agent/mdtoword-agentctl
deploy/agent/requirements.lock
deploy/agent/systemd/*
~~~

依赖从锁文件安装并执行 pip check。依赖或平台镜像变化必须经过维护者审查、固定版本，
并同时验证生产和 Sandbox；普通 Agent 运行不会修改数据库 Schema。

## 3. 配置

生产 Secret 只写入受保护的 /etc/mdtoword/controller.env 或 worker.env，不提交仓库，
不放进命令行和日志。主要 Controller 配置：

~~~text
AGENT_DATABASE_URL / AGENT_CHECKPOINT_SCHEMA
MODEL_PROVIDER / MODEL_NAME / MODEL_API_KEY / MODEL_BASE_URL
MODEL_CONTEXT_WINDOW
FALLBACK_MODEL_ENABLED / FALLBACK_MODEL_NAME / FALLBACK_MODEL_API_KEY
FALLBACK_MODEL_BASE_URL / FALLBACK_MODEL_CONTEXT_WINDOW
GATE_MODEL_TIMEOUT_SECONDS
REPRODUCTION_MODEL_TIMEOUT_SECONDS
MAX_MODEL_CALLS_PER_RUN / MAX_TOOL_CALLS_PER_RUN
MAX_SANDBOX_SECONDS_PER_RUN
GITHUB_REPOSITORY / GITHUB_READ_TOKEN
GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY
SANDBOX_WORKER_URL / SANDBOX_WORKER_CREDENTIAL / SANDBOX_IMAGE_DIGEST
LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
TRACE_SITE_WEBHOOK_URL / TRACE_SITE_WEBHOOK_SECRET
PRODUCTION_SCHEDULER_ENABLED
~~~

REPRODUCTION_MODEL_TIMEOUT_SECONDS 是每次 Repair Agent 模型请求的上限，默认 180 秒，
允许 30～300 秒；它不是整个运行的总时长。MODEL_CONTEXT_WINDOW 是上下文窗口，用于
Summary 的 65% soft trigger、85% hard limit，不是请求超时。

启用备用模型时必须同时配置名称、API、Base URL 和上下文窗口。自定义模型没有 LangChain
profile 时，audit 会因缺少 max_input_tokens 失败；不能用随意的超大值掩盖真实窗口。

## 4. 首次或更新后的验收

按顺序执行：

~~~bash
sudo mdtoword-agentctl audit
sudo mdtoword-agentctl model-smoke
sudo mdtoword-agentctl enable
sudo mdtoword-agentctl status
~~~

audit 应输出 files_and_permissions_ready、worker_ready 和 production_preflight_ready。
model-smoke 使用合成消息，不读取真实反馈、不启动 Sandbox、不写数据库或 GitHub，但会
消耗少量主/备模型额度；它验证：

- 主模型和备用模型 profile、tool calling；
- 同一响应中的只读工具并行与副作用工具串行；
- usage/cache 字段；
- Summary 的目标、已完成、下一步、禁止事项、未完成事项和脱敏；
- 65%/85% 上下文阈值。

随后使用可丢弃的后端反馈做一次真实全流程验收，人工确认基线失败、修复后目标测试
通过、全量测试通过、DOCX 结构正确、PR/Issue 脱敏且没有越权文件。真实验收不能用
model-smoke 或 Fake Provider 代替。

## 5. 常用运维命令

~~~bash
sudo mdtoword-agentctl status
sudo mdtoword-agentctl logs
sudo mdtoword-agentctl audit
sudo mdtoword-agentctl disable
sudo mdtoword-agentctl enable
~~~

disable 只停止 Scheduler 领取，不影响 Render 转换服务。enable 会再次审计，并要求
维护者输入 ENABLE；审计失败时不能开启自动领取。

需要手工运行单条反馈时，优先使用生产用户加载环境，恢复使用同一 run ID：

~~~bash
sudo runuser -u mdtoword-controller -- env -i \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 HOME=/var/lib/mdtoword-controller \
  /bin/bash -c '
    set -a; source /etc/mdtoword/controller.env; set +a
    cd /opt/mdtoword
    exec .venv/bin/python -m agent.cli run \
      --resume-run-id <run-id> --provider configured
  '
~~~

dry-run 不写 GitHub；真实 PR/Issue 发布必须显式使用 publish，并由维护者批准可丢弃数据。

## 6. 排障顺序

先看业务结果和 agent_runs.failure，再看 Scheduler/Worker 日志和 Langfuse 脱敏 Trace：

1. 确认 run 的 phase、node、component、error_code、attempt、handling 和 safe_details；
2. 确认是否为模型传输、工具前置条件、Sandbox、源码读取、验证或发布错误；
3. 对照失败策略决定重试、恢复、重排或人工处理；
4. 不把模型正文、源码、联系方式和 Secret 复制到聊天、日志或公开站。

常见错误含义：

| 错误 | 含义 | 处理 |
|---|---|---|
| provider_unavailable / timeout | 模型或上游暂时不可用 | 主、主、备最多三次，退避 1/2 秒 |
| invalid_response | 已收到但严格 Schema/Policy 不通过 | 返回字段级校验提示，按格式修正；不是传输重试 |
| sandbox_unavailable | Worker/连接/5xx 暂时失败 | 同一 job_id 最多三次，退避 1/2 秒 |
| source_auth_error | GitHub 读取凭据失效或无权限 | 不重试，修复凭据后显式恢复 |
| source_access_denied | Agent 请求越过源码权限 | 不重试，进入安全终态 |
| tool_precondition_failed | 缺少当前阶段前置产物 | 返回 required_action，由同一 run 修正 |
| budget_exhausted | 模型或工具累计预算用尽 | 不自动清零；调整配置后用原 run 显式恢复 |
| stale_base | 发布时 main 已变化 | 既有一次性重排，重新基于新 main 运行 |

未知异常也必须被 Controller 捕获并记录完整 FailureSnapshot，不能让 Scheduler 进程退出
而留下空白运行。

## 7. 备份、恢复和幂等

- Supabase 是业务状态来源；checkpoint 是工具循环恢复点；Artifact 保存大对象；
- 恢复复用 repair:<run_id>、base_sha、patch 引用和累计预算；
- 已完成 Sandbox、PR 或 Issue 通过 job_id/patch hash/marker 复用，不重复副作用；
- 历史失败运行保留用于审计，不批量重开、不批量创建发布对象；
- Schema migration 和 checkpoint 建表只由维护者审查后手工执行。

## 8. 外部服务边界

Render 只部署公开转换服务；扩展构建和商店发布由维护者执行；Trace Site 只显示脱敏
投影。GitHub App 不提供合并、部署、Actions 或 Secrets 权限。Langfuse 不可用时不能
阻断业务终态，但应记录观测降级；Trace Site 缺少快照也不代表 Agent 阶段没有执行。
