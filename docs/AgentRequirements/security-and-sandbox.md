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

Controller 可在同一服务中包含 Publisher，但 GitHub App 凭证只由受信发布代码读取，
不得进入 Graph State、模型消息、工具参数、Artifact 或沙箱环境。

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
- 目标测试与全量测试使用固定argv；
- 按Job设置墙钟超时，整次运行沙箱总预算默认900秒；
- stdout/stderr截断、清理控制字符后返回；
- JUnit与DOCX结果在Worker侧收集，Controller侧再次解析；
- 测试前后的 workspace diff 必须与已授权 patch 集合一致，运行时文件篡改视为
  `security_rejected`；
- 最终验证必须使用与修复循环不同的新容器。

## 10. 密钥与GitHub

- Feedback API不使用Agent数据库密钥；
- Controller按Provider只加载当前模型Key；
- GitHub使用只安装到本仓库的GitHub App，授予源码与PR所需最小权限；
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

- 注入样例不能触发任何代码工具；
- 未注册工具、非法路径、任意命令和超限请求均在执行前被拒绝；
- 沙箱内网络失败，环境中不存在业务密钥；
- 修改`extension/`、`.github/`、依赖或测试基础设施的patch被拒绝；
- 恶意测试的ImportError、超时和伪造报告不被判为成功复现；
- Publisher拒绝哈希不一致或未验证patch；
- Trace、日志、Artifact和PR中不存在`contact`和密钥。
