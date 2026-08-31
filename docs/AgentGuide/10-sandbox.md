# Sandbox Worker

## 1. 为什么需要 Sandbox

Agent 会让模型生成测试和候选后端代码。即使模型可信，代码、依赖和测试输出也可能有
错误或恶意行为，所以不能在 Controller 主机直接执行。Sandbox Worker 在固定镜像的新
容器中运行，返回结构化结果。

## 2. Worker 边界

Worker 是独立进程，生产只监听 127.0.0.1:8090 或受控内网。它：

- 先校验 Bearer 认证，再解析有大小上限的请求；
- 只接受 Job Schema、快照和 patch hash，不接受命令字符串；
- 使用固定镜像、固定 argv、临时 workspace 和新容器；
- 串行执行 Job，不持有模型、数据库、GitHub 或 Langfuse 凭据；
- 任务结束销毁容器和 workspace。

## 3. 容器限制

~~~text
固定 image digest；--network=none；非 root；read-only root filesystem
cap-drop=ALL；no-new-privileges；固定 CPU、内存和 PID 限制
只挂载临时 workspace/tmpfs；不挂载 Docker Socket 和宿主机敏感目录
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1；禁止 pip install、下载源码和外部网络
~~~

容器内只运行预先登记的 Python/Pandoc/pytest 流程。测试选择器先通过安全正则，再作为
独立 argv 参数，不能经 shell 拼接。

## 4. Job 类型

| 类型 | 运行内容 |
|---|---|
| reproduce_target | 基线 + 测试补丁，运行目标测试 |
| validate_target | 基线 + 测试/修复补丁，运行目标测试 |
| validate_full | 基线 + 测试/修复补丁，运行全量测试和 DOCX 检查 |
| compile_patch | 应用补丁后编译和 diff 检查 |

Controller 根据 phase、base_sha、patch 引用和固定配置生成 Job。模型只能调用 run_sandbox
并填写 reason，不能选择 Job 类型、命令、超时、workspace 或 job_id。

## 5. 结果和幂等

Worker 返回状态、退出码、是否超时、开始/结束时间、耗时、JUnit 摘要、DOCX 摘要、有限
stdout/stderr 尾部、workspace diff hash、资源摘要和 error_code。完整日志放在受控
Artifact，ToolMessage 只返回脱敏有限结果。

同一 job_id 必须对应同一请求指纹；相同请求恢复时复用已完成结果，指纹不同返回冲突。
Sandbox 临时连接错误由 Middleware 使用同一 job_id 最多重试三次，退避 1 秒和 2 秒。
401、409、非法请求、无效 200 和安全拒绝不重试。

## 6. 宿主机部署

常驻生产由 systemd 管理。Worker 与 Controller 可同机但使用不同用户、配置和服务；审计
必须证明端口已监听、未认证请求返回 401、镜像 digest 正确、任务容器没有业务 Secret。
Worker 未就绪时 Scheduler 不应恢复自动领取。

本地开发可使用 Docker Desktop/WSL 启动 Worker；这只是开发环境，不是生产依赖。生产更新
按 deployment-and-operations.md 的 deploy.sh 入口执行。

## 7. 验收

至少验证：

1. 未认证请求在读取大正文前被拒绝；
2. 容器无法访问网络、Docker Socket、宿主机敏感路径和 Secret；
3. 命令字符串、任意路径和未登记 Job 被拒绝；
4. 资源和超时限制有效；
5. 同一 job_id 重试不重复执行已完成结果；
6. 容器和 workspace 在结束后销毁；
7. Sandbox 失败能关联到 run、phase、node 和 FailureSnapshot。
