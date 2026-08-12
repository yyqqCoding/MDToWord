# 阶段 C：源码工具与 Docker Sandbox 问题/解决方案

## 问题 1：模型不能拥有任意源码和 Shell 权限

开放式 Agent 可以读取 Secret、修改扩展或部署文件、执行任意命令，无法作为自动修复
系统的安全基础。

### 解决方案

源码固定到 GitHub `base_sha`，读取仅允许白名单路径和 UTF-8 普通文件；编辑只接受
`search_replace` 或受限 `full_file`。Patch Policy 在执行前拒绝路径逃逸、符号链接、
依赖、部署、测试基础设施和非授权文件，模型永远拿不到 Shell 工具。

## 问题 2：Docker Desktop 已启动但 WSL 内无法访问 Docker Engine

Windows 界面显示 Engine running，不代表当前 Ubuntu 发行版已接入；未启用 WSL
Integration 时，项目命令仍无法构建或运行容器。

### 解决方案

在 Docker Desktop 的 Settings → Resources → WSL Integration 中启用当前发行版并重启，
然后在 WSL 内用 `docker version` 和最小镜像命令验证 Client/Server 都可访问。

## 问题 3：Docker 构建受到 Windows/WSL 代理边界影响

Docker VM 无法使用只监听 Windows `127.0.0.1` 的代理时，拉取镜像出现
`proxyconnect tcp` 或连接拒绝。直接修改用户级代理又会破坏大量 CMD 依赖。

### 解决方案

保留现有用户级代理，只配置 Docker Engine 可访问的专用代理或临时转发。代理只用于构建，
不得写入 Dockerfile、`.env.example`、最终镜像或提交记录；构建后检查镜像环境没有残留。

## 问题 4：Worker 启动时报缺少 `SANDBOX_WORKER_CREDENTIAL`

使用 `env -i` 启动 Worker 会清空此前 shell 未导出的变量；仅执行 `source .env` 而没有
`set -a` 时，变量也不会进入子进程环境。

### 解决方案

Worker 使用独立最小环境，只显式注入 `SANDBOX_IMAGE_DIGEST`、Job Root、Bind 地址和
Credential。加载 `.env` 时先导出变量，随后再用最小环境启动；绝不把包含模型、Supabase、
Langfuse 或 GitHub Secret 的 Controller 环境整体传给 Worker。

## 问题 5：可变镜像标签无法证明执行环境一致

`mdtoword-sandbox:latest` 可能在两次运行间指向不同内容，导致测试、修复和最终验证不是
同一个环境。

### 解决方案

构建后用 `docker image inspect` 取得不可变 `sha256` ID，写入
`SANDBOX_IMAGE_DIGEST`。Job 契约和 Worker 都校验固定 digest；生产与 Sandbox 的
Mermaid 依赖使用同一锁文件和固定版本。

## 问题 6：本地 Docker 与 Render Docker 的职责被混淆

容易误以为关闭本地 Docker 会让线上插件无法转换，或认为 Render 后端已经能替 Agent
启动隔离子容器。

### 解决方案

明确两套部署：Render 的 `backend/Dockerfile` 服务插件转换；本地
`agent/sandbox/Dockerfile` 服务 Agent 验证。普通插件使用不需要本地 Docker。常驻 Agent
需要独立私有服务器上的 Docker Engine、Controller 和 Worker，不能复用公开后端容器。

## 问题 7：不可信任务可能访问网络、Secret 或宿主机

仅把命令放进容器还不足以构成隔离，默认容器仍可能联网、以 root 运行或继承敏感挂载。

### 解决方案

Runner 使用固定 argv，不使用 `sh -c`；启用无网络、只读根、非 root、能力清空、
`no-new-privileges`、CPU/内存/PID/超时限制和独立 tmpfs。任务容器不挂 Docker Socket，
执行结束销毁临时 workspace，并核对实际 diff 与授权补丁哈希。
