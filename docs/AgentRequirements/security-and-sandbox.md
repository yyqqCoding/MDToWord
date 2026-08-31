# 安全、权限与沙箱

本文定义自动修复的安全边界。模型输出、用户反馈、源码、测试结果和工具结果一律视为
不可信数据；只有 Controller、本地 Policy、Validator、Sandbox Worker 和 Publisher 属于
受信执行面。

## 1. 信任边界

~~~text
公网用户
  -> Feedback API
     -> Supabase 业务状态
     -> Controller / LangGraph
        -> create_agent 工具循环
           -> 内部 Sandbox Worker
        -> GitHub 只读源码 / PR 或 Issue Publisher
        -> 脱敏 Langfuse / Trace Site
~~~

模型可以提出下一步工具调用和候选编辑，但不能直接执行命令、访问网络、读取凭据、写
数据库、改状态、创建发布对象或决定安全边界。意图识别只负责路由信号，不能替代本地
授权。

## 2. 权限矩阵

| 组件 | 允许 | 禁止 |
|---|---|---|
| Feedback API | 写入受限反馈字段 | 读取运行、领取任务、调用模型 |
| Controller | 领取任务、读取固定源码快照、调用模型和 Worker | 在宿主机执行模型代码、自动合并 |
| Repair Agent | 请求当前阶段已注册工具 | Shell、任意文件系统、网络、GitHub、数据库 |
| Sandbox Worker | 校验 Job、启动隔离容器、返回结果 | 模型、数据库、GitHub、Langfuse 凭据 |
| Task Container | 读取快照、写临时 workspace、执行固定 argv | 外网、Docker Socket、宿主机路径和 Secret |
| PR Publisher | 创建指定仓库分支、commit、PR | 合并、Actions、Secrets、Issue |
| Issue Publisher | 创建脱敏 Issue | 读取源码、创建 PR、关闭或编辑 Issue |
| Telemetry | 写指定项目的脱敏 observation | 改变 Agent 状态、读取联系方式 |

生产凭据只注入拥有相应能力的受信进程。凭据不进入模型消息、Graph State、checkpoint、
Artifact、日志、Trace、PR、Issue 或任务容器。

## 3. 输入与提示词注入

1. 用户字段使用结构化边界传给 Gate 和 Repair Agent，并明确标记为数据而非指令。
2. Gate 不注册工具；提示词注入、越权意图和无法判断的内容由本地 Policy 隔离。
3. 工具按阶段动态注册，未注册工具没有执行入口；函数内部仍做第二次授权检查。
4. 工具结果和测试日志重新作为不可信数据处理，不能改变阶段、路径或预算。
5. 模型输出的路径、补丁、测试选择器和完成结论必须经过 Schema、Policy、预算和状态校验。
6. 关键词扫描可以辅助观测，但不是安全边界。

无关内容或提示词注入不进入复现和修复；它们只保留脱敏分类结果，供路由和评估使用。

## 4. 源码读取

允许读取的范围由受信 Policy 固定：

~~~text
backend/app/**/*.py
backend/tests/**/*.py
backend/pyproject.toml
AGENTS.md
README.md
~~~

读取规则：

- 只接受仓库相对路径；拒绝绝对路径、路径穿越、仓库外符号链接和敏感文件；
- 单文件和单次工具结果有大小上限，过大的上下文必须由模型缩小范围；
- 默认先 search，再按行读取相关函数和测试，不把整个仓库放入提示词；
- 读取白名单不等于写入白名单；settings、渲染器、配置和扩展仍只能诊断。

## 5. 自动修改范围

自动 PR 只允许：

~~~text
backend/app/normalizer.py
backend/app/pandoc_runner.py
backend/tests/test_feedback_regressions.py
backend/tests/fixtures/feedback/**/*
~~~

测试阶段只能写测试文件和反馈固件；修复阶段只能写两个后端实现文件。明确禁止修改：

~~~text
extension/**, .github/**, .git/**, agent/**
backend/app/settings.py, backend/app/reference.docx
backend/pyproject.toml, backend/tests/conftest.py
Dockerfile, compose*.yml, render.yaml, *.env, 依赖锁文件、证书、密钥和部署策略
~~~

补丁还必须满足文件数、增删行数、字节数、文本编码、无二进制/符号链接/权限变化、
测试与修复路径互斥、git diff --check 和新增测试不被削弱等 Policy。越界补丁直接进入
security_rejected，不因模型解释而放行或重试。

## 6. Sandbox Worker

Worker 只监听受控主机的 127.0.0.1:8090 或内网地址，不公开 Docker Socket。Controller
发送结构化 Job 和已校验的快照/补丁；Worker 不接受命令字符串、工作目录或环境变量。
请求先完成 Bearer 认证，再解析有大小上限的正文和校验 Job。

每个 Job 使用新的临时容器和 workspace。容器约束：

~~~text
固定镜像 digest；无网络；非 root UID/GID；只读 root filesystem
cap-drop=ALL；no-new-privileges；固定 CPU/内存/PID 上限
仅挂载临时 workspace 和 tmpfs；不挂载 Docker Socket、宿主机敏感路径或任何 Secret
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1；固定 argv；禁止 pip install、下载源码和外部服务
~~~

Worker 串行运行 Sandbox；重试复用同一幂等 Job，但已经完成的结果不重复执行。超时、容器
错误、JUnit 解析失败和 workspace 越界都返回结构化结果，不能由测试日志改变发布权限。

## 7. 发布与公开数据

- PR/Issue 只能由外层受信 Publisher 创建，模型没有发布工具；
- 发布前检查 base_sha、patch hash、验证结果和幂等 marker；
- PR 和 Issue 不包含联系方式、完整反馈、完整源码、密钥或原始日志；
- Supabase 保存业务状态，Artifact 保存大对象，checkpoint 保存私有工具循环；
- Langfuse 和 Trace Site 只接收脱敏字段，公开站缺少观测不代表阶段未执行；
- GitHub App 不提供合并、部署、Actions 或 Secrets 权限。

## 8. 安全验收

至少验证以下行为：

1. 注入和越权反馈的工具调用数为零；
2. 绝对路径、路径穿越、符号链接、黑名单路径和越界补丁在执行前拒绝；
3. 未认证 Worker 请求不解析大正文，任务容器无网络和业务 Secret；
4. 模型不能提交命令字符串或未注册工具；
5. 测试、修复和发布权限按阶段隔离；
6. 公开日志、Artifact、Trace、PR、Issue 均通过敏感信息扫描；
7. 同一 Job、PR、Issue 的恢复不会产生重复副作用。

扩大白名单、增加网络能力或改变凭据权限必须由维护者修改本文与机器可读 Policy，并重新
进行容器和发布验收；Agent 不能自我授权。
