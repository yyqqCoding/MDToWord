# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

长期协作规则只放稳定约定；业务口径、接口、字段和验收清单以 `docs/` 为准。

## 工作规则

- 开发前先看 `git status --short`、相关 `docs/` 和现有实现。
- 不在 `master/main` 直接开发；修复使用 `fix/*`，功能使用 `feature/*` 等符合 Git
  开发规范的分支名。
- 不回滚他人改动；只改当前需求相关文件。
- 复用现有封装和代码风格；必要注释用中文说明“做什么/为什么”。
- 口径不明时先确认，并同步更新对应 `docs/`。
- 提交前只暂存当前需求文件，确认没有把本地 `.env`、构建产物或他人改动带入提交。

## Project

MD To Word converts AI-generated Markdown into editable Word `.docx`.

- `backend/`: FastAPI + Pandoc conversion service.
- `extension/`: Chrome/Edge Manifest V3 extension.
- `agent/`: self-hosted feedback triage and repair runtime.
- `docs/AgentRequirements/`: Agent 权威需求、架构、接口和验收记录。
- `docs/AgentProblem/`: 按阶段整理的历史问题与解决方案，不替代权威需求。
- `logs/`: real user samples and failure cases.

## Agent Development

- Agent 的目标、范围、架构、接口和验收标准以
  `docs/AgentRequirements/` 为唯一权威来源；从其 `README.md` 开始阅读。
- 阶段状态和真实验收证据只更新
  `docs/AgentRequirements/implementation-plan.md`，不要写入本文件。
- `agent/` 是当前实现，不是历史代码；删除或重建其中模块前必须先核对现有调用、测试
  和当前阶段边界。
- 数据库 migration 和第三方 checkpoint 建表只能由维护者审查后显式执行；测试、应用
  启动和普通开发命令不得自动修改数据库 Schema。
- 生产密钥通过部署 Secret 注入。本地集成测试只使用被 Git 忽略的私有 `.env`，配置名
  以 `.env.example` 为准；不得提交、记录或通过聊天传递任何密钥。
- Fake Provider 是自动测试默认值。真实 Supabase、模型、Langfuse、GitHub 或沙箱调用
  只用于明确批准的手工集成验收，并使用可丢弃的测试数据。
- 真实缺陷允许在维护者明确批准后增加平台依赖；必须固定版本、提交锁文件，并同步进入
  生产与 Sandbox 镜像。Agent 生成的补丁仍不得修改依赖、Dockerfile 或受信平台模块。
- 历史故障与排障结论写入 `docs/AgentProblem/`；稳定行为或接口变化仍必须同步更新
  `docs/AgentRequirements/`，避免从复盘文档反推当前契约。
- Provider 排障必须区分传输失败与结构失败：`provider_unavailable` 表示传输/上游服务在
  有限重试后仍不可用，`invalid_response` 表示已收到响应但严格 Schema/本地 Policy 校验
  失败。`/models` 返回 200 只证明基础连通与认证，不能替代代表性的结构化生成验证。

Agent 代码或权威设计文档变更后至少运行：

```bash
.venv/bin/python -m pytest agent/tests -q
.venv/bin/python -m compileall -q agent
```

如果变更影响共享依赖、后端契约或转换行为，还要运行后端全量测试。真实服务验收不能
替代自动测试，自动测试也不能冒充真实服务验收。

Sandbox 或 Docker 边界变化时还要运行 `agent/tests/test_docker_integration.py`。缺少
Docker 时可以在开发过程中跳过，但交付时必须明确报告，不能把 skipped 记为通过。

## Core Rule

Preview and export must normalize math consistently.

- Frontend preview uses `extension/src/normalizer.ts`.
- Backend export uses `backend/app/normalizer.py`.
- When changing math normalization, update both unless the behavior is intentionally backend-only.

## Math Normalization Requirements

Support common AI output, not only strict Markdown.

- Convert bare block math:
  - `[` newline formula newline `]` -> `$$...$$`
- Convert AI inline math:
  - `(z_T)` -> `$z_T$`
  - Keep normal text such as `(Render)` unchanged.
- Repair AI subscript mistakes inside math:
  - `*{LL}` -> `_{LL}`
  - `*2` -> `_2`
  - `*i` -> `_i`
- Preserve visible set braces:
  - `m\in{0,1}^{L}` -> `m\in\{0,1\}^{L}`
  - Do not break `\frac{}`, `\mathbb{}`, `\underbrace{}`, etc.
- Repair single backslash line breaks in math environments:
  - In `cases`, `aligned`, `matrix`, etc., line-end `\` -> `\\`.
- Expand tall parentheses for Word:
  - `f(\underbrace{...}_{x_t},t)` -> `f\left(\underbrace{...}_{x_t},t\right)`.

## Verification

Run focused tests after normalization changes:

```bash
cd backend
.venv/bin/python -m pytest tests/test_normalizer.py -v
```

Run full backend verification before claiming done:

```bash
# Linux/macOS backend venv
cd backend && .venv/bin/python -m pytest -v

# Windows backend venv from WSL repository root
backend/.venv/Scripts/python.exe -m pytest backend/tests -v
```

Run extension build after frontend changes:

```bash
cd extension
npm run build
```

For real regressions, reproduce with `logs/runlog.txt` before and after the fix.

## Deployment

Backend deploys to Render from `backend/`.

- Environment: Docker
- Health path: `/health`
- Public service URL: `https://mdtoword.onrender.com`

Extension is not deployed to Render. Build and load `extension/dist` in browser extensions.

Docker 有两个互相独立的用途：

- Render 根据 `backend/Dockerfile` 构建并运行公开转换后端，容器内包含 Pandoc、Mermaid
  CLI 和 Chromium；插件使用线上服务时不依赖开发者电脑，也不要求本地 Docker 常驻。
- `agent/sandbox/Dockerfile` 是 Agent 执行不可信测试和补丁的隔离镜像。当前开发环境由
  本地 Docker Desktop/WSL 和 `agent.sandbox.worker_http` 提供；只有执行 Agent 的
  reproduce/repair/publish 或 Docker 集成测试时才需要开启。

生产常驻 Agent 必须把 Controller 与 Sandbox Worker 部署到受控的独立主机/内网；该
主机需要 Docker Engine。不要把 Worker 合并进公开 Render 转换服务，也不要公开暴露
Docker Socket 或 Worker 端口。完整拓扑与启停说明见
`docs/AgentRequirements/deployment-and-operations.md`。

生产 Agent 的标准部署形态是独立 Linux ECS：Controller/Scheduler 与 Worker 由 systemd
管理，Worker 只监听 `127.0.0.1:8090`，本地电脑和 Docker Desktop 无需常驻。更新生产
Agent 前先停止自动领取，更新后用仓库脚本重新安装并审计，再由维护者显式启用 Scheduler：

```bash
sudo mdtoword-agentctl disable
cd /opt/mdtoword
sudo git pull --ff-only origin main
sudo bash deploy/agent/install.sh
sudo mdtoword-agentctl enable
sudo mdtoword-agentctl status
```

`install.sh` 有意让 Scheduler 保持关闭；只有审计结果正确并输入 `ENABLE` 后才恢复自动
领取。不要绕过该顺序直接编辑 systemd 单元或在公开安全组中开放 8090/Docker Socket。

After backend changes:

```bash
git push
```

Wait for Render deployment.

After extension changes:

```bash
cd extension
npm run build
```

Then refresh the unpacked extension in `chrome://extensions` or `edge://extensions`.