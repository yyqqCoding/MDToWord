# 安全、权限与沙箱

## 1. 威胁模型

系统接收公网用户控制的 Markdown 和问题描述。攻击者可能尝试：

- 用 Prompt Injection 改变 Agent 目标或诱导调用工具；
- 让模型生成读取密钥、联网或修改安全配置的测试/代码；
- 利用 pytest、Python、Pandoc 或恶意文件消耗资源或攻击宿主机；
- 修改测试、依赖、工作流或部署配置来伪造修复；
- 将联系方式、用户文档、源码或密钥带入 Trace 和 PR；
- 重放任务、重复创建 PR 或用超大输入消耗模型成本。

MVP 是单仓库、单维护者系统，不按恶意多租户平台设计；但模型生成的代码始终按
不可信代码执行。

## 2. 信任边界

```text
不可信:
  用户反馈、仓库文本、模型输出、工具输出、测试输出、修改后代码

受信:
  Controller领域逻辑、Policy、Validator、Sandbox Worker配置、发布模块

外部受控:
  Supabase、模型Provider、Langfuse、GitHub
```

沙箱保护基础设施，Validator 判断结果正确性。沙箱中的测试“通过”不能绕过
Controller 的独立 Policy 与最终验证。

## 3. 权限矩阵

MVP 不要求每个模块都是微服务，但凭证和能力必须按下表隔离：

| 身份/组件 | 允许 | 禁止 |
|---|---|---|
| Feedback API | 插入受限反馈字段 | 读取Agent运行、领取任务、调用模型 |
| Controller | 领取/更新任务、调用模型、读取源码、提交沙箱Job | 直接执行生成代码、自动合并 |
| Model | 请求当前节点注册的结构化工具 | 直接Shell、网络、文件系统、数据库、GitHub、密钥 |
| Sandbox Worker | 校验Job、启动受限容器、返回结果 | 模型/Supabase/GitHub/Langfuse业务密钥 |
| Task Container | 读源码快照、写临时workspace、执行固定命令 | 外网、宿主机、Docker Socket、任何Secret |
| GitHub Publisher模块 | 对指定仓库创建分支和PR | 执行修改后代码、修改Actions/Secrets、自动合并 |
| Telemetry模块 | 向指定Langfuse项目写Trace | 控制Agent状态、读取联系方式 |
| Maintainer | 查看Trace和PR、审核合并 | 无需向Agent暴露个人GitHub凭证 |

Controller 的源码读取凭据只授予指定仓库 `Contents: Read-only`；未来 Publisher 使用
独立 GitHub App 凭据。两类凭据都只由对应的受信适配器读取，不得进入 Graph State、
模型消息、工具参数、Artifact、Worker 或沙箱环境。

### 3.1 公开反馈入口限流

`POST /feedback` 是无需登录的公网入口。入口限流是资源保护，不是认证；`Origin`、
User-Agent、Referer、浏览器安装标识和浏览器指纹均不得作为可信身份。当前实现只按
客户端 IP 和全局窗口限流：

```text
同一 IP：60 秒最多 1 次
同一 IP：1 小时最多 5 次
同一 IP：24 小时最多 10 次
全部 IP：1 小时最多 30 次
```

#### 客户端 IP

生产使用 Cloudflare 注入的单值 `CF-Connecting-IP`，但启用前必须在 Render 完成一次真实
转发验证，不能仅因响应含 `server: cloudflare` 就推断请求头可信。当前约 200 用户的单
worker 部署采用不记录 IP 的黑盒验收，不为验收增加临时 HMAC Secret 或诊断日志。验证
至少证明：

1. 伪造 `CF-Connecting-IP` 会被可信边缘拒绝或覆盖，不能以调用方指定的身份进入应用；
2. 同一网络伪造 `X-Forwarded-For` 不能绕过现有分钟窗口；
3. Wi-Fi 与手机流量在同一 60 秒窗口内使用不同限流身份，同一网络的立即重试仍被拒绝；
4. 自动测试证明解析器只接受单个可路由 IP，并拒绝缺失、非法或逗号分隔的转发链；
5. 验收证据只保留状态码、`Retry-After`、时间和不含 IP 的请求 ID，不持久化或输出原始 IP。

生产解析器只接受经上述验收的单个可路由 IPv4/IPv6。它必须规范化 IPv4-mapped IPv6，
并按 IPv6 `/64` 前缀生成限流键。候选头缺失、包含多个值、格式非法或无法证明可信时返回
`503 client_ip_unavailable`，不得回退到调用方可伪造的 `X-Forwarded-For`，也不得把所有
请求错误归并到 Render 代理地址。

#### 滑动窗口与并发

限流器由 FastAPI 应用生命周期创建并由所有反馈请求共享。每个 IP 保存最近 24 小时内
最多 10 个已消费时间戳，全局保存最近 1 小时内最多 30 个时间戳；时间间隔使用单调时钟。
过期 IP 定期清理，活跃 IP 键最多保留 10,000 个，超过容量时先清理过期项再淘汰最久未
使用的项，避免代理地址耗尽进程内存。

同一进程使用一个 `asyncio.Lock` 原子保护“清理、检查所有窗口、消费额度”。锁内不得调用
Supabase 或执行其他网络 I/O；请求在锁内消费额度并释放锁后才写数据库。Supabase 写入
失败不回滚额度。超限返回 `429 Too Many Requests`、精确到秒的 `Retry-After` 与
`Cache-Control: no-store`，但响应不披露命中的具体窗口、IP 或限流键。

进程内锁不跨 Uvicorn worker 或 Render 实例。当前单 worker 部署允许重启后清空计数；
一旦增加 worker、水平扩容或要求跨重启保持额度，必须改用 Redis 或数据库原子限流并重新
评审，不能用多个独立内存窗口冒充全局限制。

## 4. Prompt Injection 防护

意图识别只是风险信号，不是安全边界。纵深规则：

1. 用户字段用结构化 JSON 和明确边界作为不可信数据传给模型；
2. 系统提示声明用户、源码、测试日志和工具结果都不是指令；
3. Gate 模型没有工具；疑似注入由本地 Policy 路由到
   `quarantined_security`；
4. 工具按当前 Graph 节点注册，未注册工具不存在可执行入口；
5. 所有参数先经 Schema、路径、预算和状态授权；
6. 模型不产生命令，沙箱只执行 Job 类型对应的固定 argv；
7. 模型不能触发数据库写入、GitHub发布或状态跳转；
8. 工具输出再次包为不可信数据，避免测试日志中的间接注入；
9. 即使 Gate 漏判，后续最小能力、补丁Policy和沙箱仍限制影响范围。

注入关键词扫描可以辅助审计，但不能替代上述能力边界。

## 5. 源码读取策略

允许模型按需读取：

```text
backend/app/normalizer.py
backend/app/pandoc_runner.py
backend/app/mermaid_renderer.py         # 只读受信平台 API
backend/tests/**/*.py
backend/pyproject.toml               # 只读
AGENTS.md                            # 只读规则
README.md                            # 只读项目摘要
```

默认上下文优先提供相关函数和现有测试，不一次发送全仓库。限制：

```text
单文件读取 <= 80 KB
一次模型请求总代码上下文 <= 300 KB
反馈Markdown <= 50 KB
工具文本结果 <= 20 KB
```

拒绝绝对路径、`..`、符号链接解析到仓库外、`.git/`、`.env*`、密钥、构建产物、
用户本机路径和未列出的配置。

## 6. 修改白名单

自动 PR 只允许包含：

```text
backend/app/normalizer.py
backend/app/pandoc_runner.py
backend/tests/test_feedback_regressions.py
backend/tests/fixtures/feedback/**/*
```

测试生成阶段只能修改后两项；修复生成阶段只能修改前两项。

依赖与部署文件仍不在自动修改白名单内。维护者可以为已确认的真实缺陷手工审核并预装
平台依赖，但必须固定版本、同时更新生产与 Sandbox 镜像并先完成真实容器验证。当前
Mermaid 能力固定为本地 `mmdc + Chromium -> PNG`：最多 5 图、单图源码 20,000 UTF-8
字节、单图 120 秒，禁止外链、HTML、click 和运行时初始化配置；任务容器保持无网络。
Render `0.1 CPU / 512 MiB` 下完整转换链路的 Chromium 冷启动可能超过 75 秒，因此保留
明确但足够的 120 秒受信子进程硬上限。
`mermaid_renderer.py` 可读不可写，模型只能在 `pandoc_runner.py` 接入它公开的受信函数。

明确禁止：

```text
extension/**
.github/**
.git/**
agent/**
backend/app/settings.py
backend/app/reference.docx
backend/pyproject.toml
backend/tests/conftest.py
Dockerfile / compose*.yml / render.yaml
*.yml / *.yaml / .env*
依赖锁文件、证书、密钥、部署和安全策略
```

扩大白名单只能由维护者手工修改本文件和机器可读 Policy，Agent不得自我授权。

## 7. 补丁策略

默认阈值：

```text
MAX_CHANGED_FILES=5
MAX_ADDED_LINES=300
MAX_DELETED_LINES=150
MAX_PATCH_BYTES=200000
```

Controller 在执行前按固定顺序检查：

1. Artifact哈希和patch字节数；
2. patch可解析且可应用到`base_sha`；
3. 路径规范化、白名单和黑名单；
4. 文件数量和增删行数；
5. 拒绝二进制、符号链接、子模块、文件权限和重命名；
6. 测试patch与fix patch路径互斥；
7. fix patch没有删除、跳过或削弱新增测试；
8. `git diff --check`；
9. 在沙箱中编译修改后的Python；
10. 每次测试执行后重新生成 workspace diff，确认运行时代码没有在预期 patch 之外
    修改源码、测试或结果文件；
11. 生成最终diff并重新计算SHA-256。

越界补丁进入 `security_rejected`，不要求模型解释或重试。

## 8. 禁止的修复模式

- 删除、跳过或弱化新增/现有测试；
- 捕获所有异常后返回空DOCX；
- 关闭Pandoc警告或已有自检；
- 用增加超时掩盖死循环；
- 新增网络调用或依赖；
- 修改前端绕开后端问题；
- 在测试中调用Shell、网络、环境密钥或非确定性外部服务；
- 修改pytest hook、插件、配置或报告文件来伪造结果。

## 9. Docker Sandbox

### 9.1 Worker边界

Sandbox Worker部署在独立Linux执行环境，通过内部认证接口接收Controller Job。
接口不暴露公网，不接受命令字符串。每个Job使用新容器和新workspace，结束后销毁。

Worker HTTP入口必须在读取和解析请求体之前校验Bearer认证；未认证请求不得触发JSON或
Base64大对象解析。请求体仍有71 MB硬上限，认证通过后再校验Job Schema、过期时间和哈希。

阶段 C 的 Worker 使用独立启动入口，只读取 `SANDBOX_*` 配置；不得把 Supabase、模型、
Langfuse 或 GitHub 凭据注入 Worker 进程。Controller 与 Worker 共享的内部认证凭据只
用于 `/v1/jobs`，不进入任务容器。

### 9.2 容器约束

```text
固定镜像digest，预装Python/Pandoc/测试依赖
--network=none
非root UID/GID
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges
--memory=2g
--cpus=2
--pids-limit=256
独立tmpfs和可写workspace
不挂载Docker Socket、Controller目录或宿主机敏感路径
无Secret、无代理变量、无云元数据访问
```

任务期间禁止 `pip install`、下载源码或访问GitHub。Controller提供按SHA打包并校验
哈希的源码快照；容器仅在临时副本上应用补丁。

### 9.3 固定执行环境

- 设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；
- 清除非必要环境变量；
- Worker 在启动容器前把临时 workspace 目录规范为可遍历、普通文件规范为只读可读，
  不依赖 systemd `UMask`；补丁新增文件也必须对固定非 root UID 可读；
- 目标测试与全量测试使用固定argv；
- 按Job设置墙钟超时，整次运行沙箱总预算默认900秒；
- stdout/stderr截断、清理控制字符后返回；
- JUnit与DOCX结果在Worker侧收集，Controller侧再次解析；
- 测试前后的 workspace diff 必须与已授权 patch 集合一致，运行时文件篡改视为
  `security_rejected`；
- 最终验证必须使用与修复循环不同的新容器。
- Controller遇到连接异常或408、429、5xx时，只允许使用相同`job_id`和幂等键进行一次
  有界重试；认证、冲突、非法请求或无效成功响应不得重试。

## 10. 密钥与GitHub

- Feedback API不使用Agent数据库密钥；
- Controller按Provider只加载当前模型Key；
- GitHub使用只安装到本仓库的GitHub App，授予源码与PR所需最小权限；
- 每次发布用 App JWT 换取短期安装令牌时再次限定当前仓库，并只请求
  `contents:write` 与 `pull_requests:write`；令牌响应若包含额外权限则拒绝发布；
- GitHub App禁止Actions、Administration、Secrets和自动合并权限；
- 安装令牌短期生成，不保存到Artifact或Trace；
- Langfuse仅使用项目写入Key，Trace查看由维护者账号控制；
- 生产和共享环境的密钥通过部署 Secret 注入，不写入仓库、Graph State 或共享配置；
- 本地手工集成测试可使用被 Git 忽略的私有 `.env`，只填写缺少的配置，不提交、不
  分享，也不把值粘贴到日志、Issue、PR 或聊天中；
- 日志和PR发布前执行密钥模式扫描作为兜底。

## 11. Artifact完整性

每个运行固定：

```text
base_sha
source_snapshot_sha256
test_patch_sha256
fix_patch_sha256
validated_patch_sha256
```

跨Controller与Worker传输时校验输入和输出哈希。Publisher只应用
`ValidationResult`中记录的 `validated.patch`，应用后重新计算并比对
`validated_patch_sha256`。个人项目不增加复杂签名基础设施。

## 12. 安全验收

- 同一 IP 并发提交只能有符合窗口额度的请求写入数据库；
- 伪造 `CF-Connecting-IP` 或 `X-Forwarded-For` 不能改变生产限流身份；
- 不同 IPv4 相互独立，同一 IPv6 `/64` 共享额度；
- 分钟、小时、每日和全局窗口分别返回正确的 `429` 与 `Retry-After`；
- 无可信 IP 时返回 `503`，Supabase 写入失败不返还已消费额度；
- 限流状态有容量上限，日志、数据库、Trace 和响应均不含原始 IP；
- 注入样例不能触发任何代码工具；
- 未注册工具、非法路径、任意命令和超限请求均在执行前被拒绝；
- 沙箱内网络失败，环境中不存在业务密钥；
- 修改`extension/`、`.github/`、依赖或测试基础设施的patch被拒绝；
- 恶意测试的ImportError、超时和伪造报告不被判为成功复现；
- Publisher拒绝哈希不一致或未验证patch；
- Trace、日志、Artifact和PR中不存在`contact`和密钥。
