# Agent Notes

本文件只保留长期稳定的协作约定。业务口径、状态机、字段、部署步骤和验收证据以
`docs/` 为准，不在这里重复维护。

## 开发工作流

- 开发前检查 `git status --short`，从相关权威文档和现有调用、测试开始阅读。
- 不在 `main/master` 直接开发；功能、修复和文档分别使用语义明确的分支名。
- 只修改当前需求文件，不回滚、清理或提交他人的工作区改动。
- 优先复用现有边界和代码风格；必要注释用中文说明“做什么/为什么”。
- 口径不明或会改变接口、权限、数据、安全、兼容性时先确认，并同步权威文档。
- 提交前只暂存当前需求文件，检查 `.env`、密钥、构建产物和无关改动未进入提交。

## 项目与文档

- `backend/`：FastAPI、Pandoc 与公开转换/反馈服务。
- `extension/`：Chrome/Edge Manifest V3 扩展。
- `agent/`：反馈分类、复现、修复、验证与发布运行时。
- `trace-site/`：以 Supabase 为运行事实来源的公开 Trace 站点。
- `docs/AgentRequirements/`：Agent 唯一权威需求；从 `README.md` 开始。
- `docs/AgentRequirements/implementation-plan.md`：阶段状态和真实验收证据的唯一记录。
- `docs/AgentGuide/`：面向维护者的实现说明，不改变权威契约。
- `docs/AgentProblem/`：历史故障与结论，不反推当前行为。

## Agent 稳定边界

- `agent/` 是当前实现。删除或重建模块前核对调用、测试和当前阶段范围。
- Migration 与第三方 checkpoint Schema 只能由维护者审查后显式执行；测试、启动和普通
  开发命令不得修改生产 Schema。
- 生产 Secret 只通过部署环境注入；本地真实集成只使用被忽略的私有 `.env`，不得提交、
  记录或通过聊天传递密钥。
- 自动测试默认使用 Fake Provider。真实 Supabase、模型、Langfuse、GitHub 与 Sandbox
  只用于明确批准的手工验收，并使用可丢弃数据。
- 新平台依赖需维护者批准、固定版本并同步生产与 Sandbox 镜像；Agent 补丁仍不得修改
  依赖、Dockerfile 或受信平台模块。
- 模型生成内容始终是不可信输入。路径、工具、状态、预算、补丁和发布权限必须由本地
  Policy 与受信代码校验；白名单以 `docs/AgentRequirements/security-and-sandbox.md` 为准。
- 跨字段规则必须同时写入提示词与本地 Policy，并 bump 对应 Prompt 版本；校验错误要点名
  字段和可执行修正，便于模型格式重试与维护者排障。
- `provider_unavailable` 表示传输/上游失败，`invalid_response` 表示已收到但未通过严格
  Schema 或本地 Policy；详细排障按
  `docs/AgentRequirements/deployment-and-operations.md`。
- Sandbox 物化与补丁流程必须显式规范权限，并在 `UMask=0077`、固定非 root UID 下验证。

## 跨组件不变量

- 数学预览与导出必须保持一致：前端使用 `extension/src/normalizer.ts`，后端使用
  `backend/app/normalizer.py`；除明确的后端特例外，归一化行为和回归测试要同步更新。
- Trace Site 的运行摘要来自 Supabase；Langfuse 仅提供脱敏 observation 快照。展示缺失
  不等于 Agent 阶段未执行。
- Langfuse 索引异步：只有稳定命名的 `feedback-repair-run` 根可作为快照根；有调用计数但
  无 observation 后代的快照必须重抓，真实零调用运行除外。
- 扩展商店版本只修改 `extension/public/manifest.json`；`dist` 与压缩包是发布产物，不提交。
  只有商店实际上架后才能写“已发布”，构建完成只能记为“发布构建已准备”。

## 验证矩阵

Agent 代码或权威 Agent 文档变化：

```bash
.venv/bin/python -m pytest agent/tests -q
.venv/bin/python -m compileall -q agent
```

Trace Site 变化：

```bash
cd trace-site
npm test
npm run typecheck
npm run build
```

后端契约、共享依赖或转换行为变化时运行后端全量测试；数学归一化先运行
`backend/tests/test_normalizer.py`。扩展变化运行 `cd extension && npm run build`。
Sandbox/Docker 边界变化还要运行 `agent/tests/test_docker_integration.py`；环境缺失导致的
skip 或构建失败必须如实报告，真实服务验收与自动测试不能互相替代。

## 部署约定

- Backend 由 `backend/Dockerfile` 部署到 Render，健康检查为 `/health`；扩展不部署到
  Render。
- 生产 Agent 位于独立 Linux 主机。Controller 与 Worker 分离，Worker 只监听
  `127.0.0.1:8090`，不得公开 Worker、Docker Socket 或合并进 Render 服务。
- Agent 更新统一使用以下入口；脚本负责停止领取、安装、显式重启 Worker、审计和人工
  `ENABLE`。不要用服务显示 `active` 代替代码版本验证。

```bash
sudo git -C /opt/mdtoword pull --ff-only origin main
sudo bash /opt/mdtoword/deploy/agent/deploy.sh
```

完整部署、密钥、权限和故障恢复步骤见
`docs/AgentRequirements/deployment-and-operations.md`。
