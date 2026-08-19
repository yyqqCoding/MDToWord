# 权限控制

## 1. 基本做法

系统不依赖模型“自觉”遵守安全要求，而是让每个组件只能拿到完成当前工作所需的最小权限。
即使模型判断错误，后续Python检查、服务账号和Docker限制仍然生效。

## 2. 每个组件能做什么

| 组件 | 可以做 | 不能做 |
|---|---|---|
| 浏览器插件 | 调用公开转换和反馈接口 | 直接访问Supabase、Agent或Worker |
| Feedback API | 写入规定的反馈字段 | 读取Agent运行、领取反馈、调用模型 |
| Agent主进程 | 领取任务、调用模型、读固定源码、更新状态、提交Sandbox Job | 直接执行模型生成代码、自动合并 |
| 分类模型 | 返回分类JSON | 使用任何工具 |
| 复现模型 | 选择允许读取的文件、生成结构化测试Edit | Shell、网络、数据库、GitHub |
| 修复模型 | 生成白名单内结构化修复Edit | 修改测试、依赖、部署、Agent或扩展 |
| Sandbox Worker | 校验Job并启动受限容器 | 读取模型、Supabase、Langfuse和GitHub业务密钥 |
| 任务容器 | 运行固定测试命令 | 外网、Docker Socket、宿主机敏感目录和Secret |
| GitHub Publisher | 在指定仓库创建分支、提交和PR | 修改Secrets、Actions、仓库管理设置、自动合并 |
| Vercel网站服务端 | 读取公开运行视图和Trace快照 | 读取`feedback`表并向浏览器暴露服务密钥 |

## 3. 工具按节点开放

代码中登记了六个工具名称：

```text
search_source
read_source_file
submit_test_edits
run_reproduction
submit_fix_edits
run_target_validation
```

每个LangGraph阶段只允许自己的工具：

| 节点阶段 | 可用工具 |
|---|---|
| Gate | 无 |
| 查看复现所需源码 | `search_source`、`read_source_file` |
| 提交测试修改 | `submit_test_edits` |
| 运行复现 | `run_reproduction` |
| 提交修复修改 | `submit_fix_edits` |
| 运行目标验证 | `run_target_validation` |

未注册工具返回“工具不存在”，已注册但不属于当前节点的工具返回“当前节点无权调用”。两种
情况都不会进入真实适配器。

当前OpenAI兼容Provider本身不接受模型工具调用；模型返回结构化计划或Edit，Graph再按
当前节点调用本地工具。这进一步减少了模型在一个响应中任意选择执行能力的机会。

## 4. 源码读取权限

源码读取使用独立、指定仓库的只读凭据。模型只能读取Policy白名单中的固定`base_sha`
快照，不能浏览：

```text
.git/**
.env*
本机用户目录
构建产物
证书和密钥
未登记的配置
```

读取凭据不进入State、提示词、Agent本地运行文件、Worker或Docker容器。

## 5. 修改权限

测试阶段和修复阶段使用不同白名单：

```text
测试阶段：
  backend/tests/test_feedback_regressions.py
  backend/tests/fixtures/feedback/**/*

修复阶段：
  backend/app/normalizer.py
  backend/app/pandoc_runner.py
```

扩大白名单必须由维护者修改代码和权威安全文档。模型不能通过提示词、输出字段或工具参数
给自己增加可写路径。

## 6. 密钥隔离

生产配置分为两份：

```text
/etc/mdtoword/controller.env
  Supabase、模型、Langfuse、GitHub、Worker连接等配置

/etc/mdtoword/worker.env
  只包含SANDBOX_*配置
```

Agent主进程和Worker使用不同Linux用户。Worker进程可以通过`docker`组调用Docker Engine，
但它没有业务密钥；任务容器既没有业务密钥，也没有Docker Socket。

## 7. GitHub权限

读取源码和发布PR使用不同凭据：

- 源码读取凭据只有指定仓库`Contents: Read-only`；
- 发布时GitHub App换取短期installation token；
- 发布令牌只允许`contents:write`和`pull_requests:write`；
- 如果GitHub返回额外权限，Publisher拒绝继续；
- token不写入State、文件、Trace或日志。

## 8. 网站权限

追踪网站使用服务端密钥，但通用查询函数只允许：

```text
agent_run_public
agent_run_traces
```

读取内部`agent_runs.langfuse_trace_id`只存在两个固定函数中，而且只查询写死的列。浏览器
拿到的是已经成型的页面数据，不会拿到service role。

## 9. systemd限制

Scheduler和Worker服务还使用：

```text
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictSUIDSGID=true
UMask=0077
```

它们只能写入各自指定的`/var/lib`运行目录。systemd限制保护服务进程，Docker参数保护每个
不可信任务容器，两层不能互相替代。

对应实现：

- [工具授权](../../agent/tools/authorization.py)
- [补丁Policy](../../agent/workspace/patch_policy.py)
- [安全权威要求](../AgentRequirements/security-and-sandbox.md)
- [生产systemd配置](../../deploy/agent/systemd)
