# Docker沙箱

## 1. 沙箱解决什么问题

模型生成的测试和修复代码都可能出错，也可能包含危险行为。Agent主进程不能直接在自己的
Python进程或ECS工作目录中运行这些代码。系统把它们交给独立Sandbox Worker，由Worker
为每个任务启动一个全新Docker容器。

真正的优势不是“使用Docker”四个字，而是以下完整链条：

```text
结构化模型输出
  → 补丁白名单检查
  → 固定格式Job
  → Worker认证和哈希检查
  → 无网络、非root、限资源的一次性容器
  → 解析JUnit而不是相信输出文字
  → 检查运行前后源码差异
  → 全新容器最终验证
  → 只发布验证结果绑定的补丁
```

## 2. Worker和任务容器不是一回事

```text
Agent主进程
  持有数据库、模型、Langfuse和GitHub配置
        ↓ 内部HTTP + Bearer认证
Sandbox Worker
  只持有SANDBOX_*配置，可以调用Docker Engine
        ↓ docker run
任务容器
  没有业务Secret、网络或Docker Socket
```

生产中Agent主进程和Worker位于同一台私有ECS，但它们是不同systemd服务、不同Linux用户、
不同环境文件。Worker默认只监听`127.0.0.1:8090`。

## 3. Agent主进程提交什么

Sandbox Job只允许四种类型：

| 类型 | 固定执行内容 |
|---|---|
| `reproduce_target` | 原代码加测试补丁，运行指定回归测试 |
| `validate_target` | 原代码加测试和修复补丁，运行指定回归测试 |
| `validate_full` | 运行后端全量测试和DOCX检查 |
| `compile_patch` | 编译修改后的Python文件 |

Job字段包括：

```text
job_id, run_id, job_type, base_sha
source_snapshot_sha256
test_patch_sha256, fix_patch_sha256
target_test_selector
limits, expires_at
```

Job中没有Shell命令、环境变量、挂载目录或Docker参数。模型不能通过Job改变执行方式。

源码压缩包、测试补丁和修复补丁通过内部HTTP传给Worker，并分别与SHA-256绑定。Worker先
检查Bearer认证，而且认证发生在读取和解析最多71 MB的请求体之前。认证通过后才检查JSON
Schema、任务是否过期、文件大小和哈希，未认证请求不能利用大请求体消耗Base64解析资源。

## 4. Worker怎样准备工作区

Worker为每个Job创建新的临时目录：

```text
job-<job_id>/
  source.tar.gz
  workspace/
  result/
```

然后：

1. 安全解压固定源码快照；
2. 在容器看不到的位置建立Git基线；
3. 使用`git apply --check`检查补丁；
4. 依次应用已授权测试和修复补丁；
5. 记录容器执行前的完整diff和SHA-256；
6. 规范目录与文件权限，让固定非root用户可以读取；
7. 生成固定`docker run`参数。

补丁由Worker在容器启动前应用。容器不负责决定应用什么补丁。

## 5. Docker限制

每个任务容器使用：

```text
--rm
--network=none
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges
--memory=2147483648
--cpus=2.0
--pids-limit=256
--user=65532:65532
--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=536870912
```

含义如下：

| 限制 | 作用 |
|---|---|
| 固定镜像digest | 防止同名镜像被替换后环境悄悄变化 |
| `--network=none` | 测试不能访问GitHub、模型、云元数据或其他网络地址 |
| 非root UID 65532 | 测试进程没有root权限 |
| 根文件系统只读 | 不能修改镜像中的Python、Pandoc和受信检查代码 |
| 删除全部capability | 不能使用额外Linux特权 |
| no-new-privileges | 进程不能通过setuid等方式获得新权限 |
| CPU、内存、进程数限制 | 防止死循环、fork bomb和内存耗尽拖垮主机 |
| 墙钟超时 | 任务超时后终止容器 |

## 6. 哪些位置可以写

不能简单说“整个容器都不能写”。准确情况是：

- 容器根文件系统只读；
- `/tmp`是512 MiB临时内存盘，可写但`noexec/nosuid/nodev`；
- `/result`可写，用于pytest生成JUnit；
- `/workspace`作为受控挂载存在；源码目录通常为`0755`、文件为`0644`，固定非root用户
  只能读取现有源码；
- workspace顶层保留创建必要临时内容的能力；
- 容器结束后Worker重新计算Git diff，任何超出批准补丁的变化都被拒绝。

如果执行后diff与执行前批准的diff不一致：

```text
status = security_rejected
error_code = workspace_modified
```

## 7. 容器中没有什么

任务容器不会继承主机完整环境。启动Docker CLI时只保留`PATH`和`LANG`，容器内只设置固定
测试变量。它没有：

- `SUPABASE_AGENT_KEY`；
- 模型API Key；
- Langfuse Secret；
- GitHub App私钥；
- Worker认证Token；
- 代理环境变量；
- `/var/run/docker.sock`；
- Agent主进程目录；
- 用户主目录和`.env`。

受信任DOCX检查代码位于镜像只读层`/opt/trusted/docx_assertions.py`，模型不能通过修改
workspace替换它。

## 8. 固定命令而不是模型命令

Worker根据`job_type`选择Python参数。例如目标测试固定为：

```text
python -m pytest tests/test_feedback_regressions.py
  -k <已校验的测试名>
  -q -p no:cacheprovider
  --junitxml=/result/junit.xml
```

全量验证固定运行后端pytest，编译任务固定运行`python -m compileall`。代码不使用`sh -c`
或`bash -c`，因此模型不能借参数拼接新命令。

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`阻止开发机或环境中未知pytest插件自动加载。

## 9. 怎样判断执行结果

Worker返回固定结构：

```text
job_id, status, exit_code, timed_out
started_at, finished_at, duration_ms
junit_summary, docx_summary
stdout_tail, stderr_tail
workspace_diff_sha256
resource_summary, error_code
```

stdout和stderr分别最多4 KiB，并清理控制字符和疑似密钥。Agent主进程使用JUnit XML中的
测试数量、失败数量、目标测试是否收集和失败类型判断，不解析普通输出中的成功文字。

## 10. 超时、清理和幂等

超时时Worker：

1. 杀死Docker进程；
2. 调用`docker rm -f <固定容器名>`；
3. 返回`sandbox_timeout`；
4. 删除本次临时工作目录；
5. 后续验证创建新容器，不复用旧工作区。

相同`job_id`和相同请求重复提交时，Worker从本地结果文件返回第一次结果，不重复启动
容器。Sandbox Client遇到网络异常或408、429、5xx时默认只额外重试一次，并继续使用同一个
`job_id`和`Idempotency-Key`；无效成功响应、认证失败和请求冲突不会重试。相同`job_id`但
请求内容不同则返回冲突。

## 11. 最终验证为什么使用新容器

复现、目标验证和全量验证分别创建容器。最终验证重新从`base_sha`开始：

```text
容器A：原代码 + 测试补丁，目标必须失败
容器B：原代码 + 测试补丁 + 修复补丁，目标必须通过
容器C：重新创建环境，完成全量测试和DOCX检查
```

这可以排除缓存、临时文件、测试顺序和上一次执行副作用造成的假通过。

## 12. 已实际验证的隔离项

生产部署和真实Docker测试已经确认：

- Worker和Scheduler由systemd常驻运行；
- Worker只监听本机8090，安全组未公开端口；
- 使用固定镜像digest；
- 容器没有网络和业务密钥；
- 容器不是root，看不到Docker Socket；
- 根文件系统只读、capability为空、`NoNewPrivs`生效；
- 2 GiB内存、2 CPU和256进程限制生效；
- 超时容器与临时目录能够清理；
- 相同Job不会重复执行；
- 严格`UMask=0077`下新增fixture仍能被固定非root UID读取；
- 修复后会在新容器中重新证明基线失败并验证目标与全量测试。

对应实现：

- [Docker Runner](../../agent/sandbox/docker_runner.py)
- [Worker](../../agent/sandbox/worker.py)
- [Job和Result结构](../../agent/sandbox/contracts.py)
- [Sandbox镜像](../../agent/sandbox/Dockerfile)
- [真实Docker测试](../../agent/tests/test_docker_integration.py)
