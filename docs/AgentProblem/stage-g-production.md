# 阶段 G：评估、部署与生产运行问题/解决方案

## 问题 1：少量成功样例不能证明 Gate 可以投产

只验证后端 Bug 会掩盖前端、功能建议、无关内容、信息不足和 Prompt Injection 的误路由。

### 解决方案

建立 12 条脱敏离线评估，报告 Gate accuracy、automatable precision、Schema compliance、
注入召回/误报、Token、成本、延迟和 Oracle 覆盖。Fake 评估不访问外部服务；真实评估只
运行 Gate 并写 Langfuse，不领取生产反馈。

## 问题 2：Scheduler 一上线就自动领取真实反馈风险过高

配置缺失、Worker 不可用或发布权限错误会让一批反馈同时失败。

### 解决方案

`PRODUCTION_SCHEDULER_ENABLED=false` 作为默认硬开关。启用前一次性校验 D→E→F 所有配置，
Scheduler 恢复优先、进程内并发固定为 1。开关只控制是否领取反馈，不增加逐条人工批准，
也不赋予自动合并权限。

## 问题 3：Render 生产 Mermaid 转换在 20 秒后超时

固定 CLI 与 Chromium 已正确安装，前端预览也正确，但 Render 低 CPU 容器首次启动浏览器
明显慢于开发机，`/convert` 返回 `Mermaid rendering timed out`。

### 解决方案

在 `0.1 CPU / 512 MiB` 约束下复现生产环境。20 秒稳定失败，完整转换链路存在超过 75 秒
的冷启动波动；最终把单图硬上限设为 120 秒，仍保持有界执行。生产等价容器实测 81.515
秒成功，DOCX 含 1 个 drawing 和 1 个媒体文件，且正文没有泄漏 Mermaid 源码。

## 问题 4：单测通过不代表 Render 完整链路可用

单独调用渲染器曾在约 50～57 秒成功，但加入真实 `pandoc_runner`、冷镜像和资源限制后，
75 秒仍可能超时。

### 解决方案

增加三层验证：渲染器单测检查固定超时和安全错误；后端全量回归检查转换契约；生产等价
Docker 运行完整 Mermaid→PNG→Pandoc→DOCX，并打开 ZIP 检查 drawing、media 和源码泄漏。
部署后再用插件和 Microsoft Word 人工回放原始反馈。

## 问题 5：本地 Sandbox、Render 后端和常驻 Agent 容易被当成同一个服务

这会导致“是否必须一直开本地 Docker”以及“是否要在 Render 再装 Docker”的疑问。

### 解决方案

Render 后端容器独立完成插件转换，本地 Docker 可以关闭。Agent Sandbox 当前在本地，只有
运行自动复现/修复/发布时需要。若要常驻自动处理反馈，单独准备带 Docker Engine 的私有
Linux 服务器部署 Controller 和 Worker；不改造公开 Render 后端来运行 Docker Worker。

## 问题 6：自动创建 PR 不等于生产闭环完成

PR 可能未合并、Render 构建可能失败，或者自动 DOCX 结构检查通过但 Word 视觉效果仍不对。

### 解决方案

最终验收固定为：真实反馈生成 validated patch 和 PR；维护者人工 Review/Merge；等待 Render
部署；用原 Markdown 从插件重新导出；在 Word 中确认流程图、表格、公式和样式。PR #1 已
完成该闭环，原 Mermaid 测试转换成功。

## 问题 7：开发完成与 7×24 小时运维上线被混为一谈

Scheduler、Controller 和 Worker 已实现并验证，不代表维护者必须立即承担常驻服务器和
监控成本。

### 解决方案

把“阶段 A～G 开发和首条生产闭环完成”与“常驻 Scheduler 部署”分开记录。当前插件和
Render 转换服务可以正常使用；常驻 Agent 是后续可选运维决策，启用时再配置私有服务器、
Docker、Secret、告警和小流量观察。

## 问题 8：Mermaid 修复合并后，Docker 回归仍把当前代码当作故障基线

服务器首次运行 4 项 Docker 集成测试时有 1 项失败：测试直接复制已经包含 Mermaid
接入的当前 `backend/app`，drawing 断言因此直接通过，JUnit 没有失败类型；旧断言却仍
要求 `target_failure_type=AssertionError`。该测试只在 PR 合并前的未修复分支上成立。

### 解决方案

测试先从当前已修复 `pandoc_runner.py` 确定性移除 Mermaid 接入，构造只存在于临时
workspace 的旧基线，再用 test patch 证明 drawing 断言失败，并把当前实现作为 fix patch
重新应用后证明通过。生产源码不回退。修正后非 Docker Agent 测试 252 passed，真实
Docker 集成 4 passed。
