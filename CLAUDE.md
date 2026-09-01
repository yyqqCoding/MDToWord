# Repository guidance

本文件只保留长期稳定的协作约定。Agent 的目标、状态、字段、权限、部署步骤和验收证据
以 docs/AgentRequirements/ 为准；面向维护者的说明以 docs/AgentGuide/ 为准。

## 工作方式

- 开始前检查 git status --short，并阅读相关权威文档、现有调用和测试。
- 不在 main/master 直接开发；使用语义明确的功能、修复或文档分支。
- 只修改当前需求文件；保留工作区中不相关的修改，不使用破坏性回滚。
- 优先复用现有边界和代码风格；必要注释说明做什么以及为什么。
- 会改变接口、权限、数据、安全或兼容性的选择，先确认并同步权威文档。
- 提交前只暂存当前需求，检查 .env、密钥、构建产物和无关文件没有进入提交。

## 项目边界

- backend：FastAPI、Pandoc 和公开转换服务。
- extension：Chrome/Edge Manifest V3 扩展。
- agent：反馈分类、外层 LangGraph、内层 create_agent ReAct、Sandbox、验证和发布。
- trace-site：基于 Supabase 脱敏投影的运行展示。
- docs/AgentRequirements：Agent 唯一权威需求，从 README.md 开始阅读。
- docs/AgentGuide：实现与运维阅读指南，不改变契约。
- docs/AgentProblem：归纳后的面试问题和历史结论，不反推当前行为。

## 稳定约束

- 模型输出、用户反馈、源码、工具结果和测试输出都是不可信输入。
- Controller、本地 Policy、Validator、Sandbox Worker 和 Publisher 才能决定权限、状态、
  路径、预算、补丁和发布；模型不能自我授权。
- 自动测试默认使用 Fake Provider；真实 Supabase、模型、Langfuse、GitHub 和 Sandbox
  只用于明确批准的手工验收，并使用可丢弃数据。
- 生产 Secret 只通过部署环境注入；不得提交、记录或通过聊天传递。
- Migration 与第三方 checkpoint Schema 只能由维护者审查后显式执行。
- 新平台依赖需维护者批准、固定版本并同步生产与 Sandbox；Agent 补丁不得修改依赖、
  Dockerfile 或受信平台模块。
- 前端预览与后端导出必须保持既有数学归一化不变量；改变时同步更新两端和回归测试。
- Trace Site 的业务状态来自 Supabase；Langfuse 只提供脱敏观测，展示缺失不代表阶段未执行。
- 扩展商店版本只修改 manifest；dist 和压缩包是发布产物，不提交。

## 验证

Agent 代码或权威需求变化后运行：

~~~bash
.venv/bin/python -m pytest agent/tests -q
.venv/bin/python -m compileall -q agent
~~~

按变更范围追加后端测试、Trace Site test/typecheck/build、扩展 build 或
agent/tests/test_docker_integration.py。环境缺失导致的 skip 或构建失败必须如实报告。

## 部署

生产 Agent 位于独立 Linux 主机；Controller/Scheduler 与 Worker 分离，Worker 只监听
127.0.0.1:8090，不公开 Worker、Docker Socket 或合并进 Render 服务。标准更新入口：

~~~bash
sudo git -C /opt/mdtoword pull --ff-only origin main
sudo bash /opt/mdtoword/deploy/agent/deploy.sh
~~~

脚本会停止领取、安装依赖、显式重启 Worker 和审计；维护者确认后再执行
sudo mdtoword-agentctl enable。详细配置、排障和恢复步骤只写在
docs/AgentRequirements/deployment-and-operations.md。
