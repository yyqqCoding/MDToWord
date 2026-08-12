# Agent 项目问题与解决方案

本目录按阶段记录 Agent 从基础持久化到真实生产闭环期间遇到的问题与最终解决方案。
这些文档用于复盘和排障，不是当前接口或安全策略的权威定义；现行规则以
[AgentRequirements](../AgentRequirements/README.md) 为准。

## 总体方案

项目包含两条相互独立但最终闭环的链路。

### Markdown 转 Word

```text
浏览器扩展预览
  -> Render /convert
  -> 后端 Markdown 归一化
  -> Mermaid CLI + Chromium 生成 PNG（仅流程图）
  -> Pandoc 生成 DOCX
  -> 表格样式与 DOCX 结构后处理
  -> 用户下载并用 Word 打开
```

公式和表格尽量保持 Word 原生可编辑结构；Mermaid 流程图通过受信本地渲染器转换为
PNG 后嵌入 Word，因此图内元素不是 Word 原生可编辑形状。

### 用户反馈自动修复

```text
Supabase feedback
  -> Controller 原子领取
  -> Gate 分类与安全检查
  -> 固定 GitHub main/base_sha
  -> Docker Sandbox 复现
  -> 受限后端修复
  -> 全新 Sandbox 独立验证
  -> GitHub App 创建 PR
  -> 人工 Review/Merge
  -> Render 部署
  -> 原反馈回放
```

Agent 只自动修改后端白名单文件，不修改扩展、不自动合并，也不直接部署。完整运行拓扑见
[部署与运行方式](../AgentRequirements/deployment-and-operations.md)。

## 阶段文档

| 阶段 | 主题 | 文档 |
|---|---|---|
| A | 基线、配置、数据库和持久化 | [stage-a-foundation.md](stage-a-foundation.md) |
| B | Gate、LangGraph、模型和 Langfuse | [stage-b-gate-runtime.md](stage-b-gate-runtime.md) |
| C | 源码工具、Policy 和 Docker Worker | [stage-c-sandbox.md](stage-c-sandbox.md) |
| D | 自动生成可信复现 | [stage-d-reproduction.md](stage-d-reproduction.md) |
| E | 修复循环、Mermaid 能力和独立验证 | [stage-e-repair.md](stage-e-repair.md) |
| F | GitHub App、PR 发布和幂等恢复 | [stage-f-publication.md](stage-f-publication.md) |
| G | 评估、部署回放和生产运行 | [stage-g-production.md](stage-g-production.md) |

## Docker 结论

- 插件使用的是 Render 后端 Docker；本地 Docker 关闭不影响线上 Word 转换。
- Agent Sandbox 当前运行在本地 Docker Desktop/WSL；只有本地复现、修复、发布和 Docker
  集成测试需要开启。
- 需要 7×24 小时自动处理反馈时，应在独立私有服务器部署 Controller、Worker 和 Docker
  Engine。不要把 Worker 或 Docker Socket 暴露到公开 Render 转换服务。
