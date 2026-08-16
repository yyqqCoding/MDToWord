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

## 问题 9：手工复制多层 shell、Python 和 systemd 命令容易产生语法错误

SSH 终端换行会把 `python -c` 的 import 拆开，前导空格又会触发 `IndentationError`；长
命令还难以审查是否意外加载了 Controller Secret 或开放 Worker 端口。

### 解决方案

把预检 Python、两个 systemd 单元、安装脚本和管理命令全部纳入版本控制。服务器只需
`git pull` 后运行 `deploy/agent/install.sh`。脚本不包含 Secret，读取既有隔离配置；每次
安装都保持 Scheduler 关闭，并通过 `mdtoword-agentctl enable` 的审计与二次确认单独启用。

## 问题 10：安装脚本偶发报告 Worker 端口拒绝连接

systemd 将 Worker 标记为 `active` 时，Python 进程可能仍在导入模块，尚未监听 8090
端口。安装脚本紧接着执行审计会产生一次假的健康检查失败；稍后查看服务时又已经正常。

### 解决方案

Worker 审计在确认 systemd 服务处于活动状态后，最多等待 30 秒并重复探测本机 HTTP
端点。就绪后继续镜像、监听地址及 Controller 预检；超时则自动输出服务状态和最近日志，
避免仅返回无法定位原因的 `connection refused`。

## 问题 11：模型列表接口正常，但生产任务仍失败

`/models` 返回 200 只说明 Base URL、网络和 API Key 可用。真实任务仍可能在 Chat
Completions 遇到上游 5xx/断开，或返回不符合复杂严格 Schema 的内容；把两类问题都当成
“API 不通”会误导排障。

### 解决方案

按稳定错误码和 Langfuse 节点定位：`provider_unavailable` 是传输或上游服务在有限重试
后仍失败；`invalid_response` 是收到内容后严格 Schema/本地 Policy 校验失败。单条 Gate
评测用于验证基础结构化生成，复杂阶段继续查看 `plan-reproduction`、`generate-test` 或
`generate-fix` 节点。终态失败不重新领取；部署修复后用新 feedback 验证。

## 问题 12：常驻 Agent 上线后，如何证明不会为已修复问题创建空 PR

仅验证 Worker/Scheduler 为 `active` 不能证明业务路由、沙箱和终态策略正确。

### 解决方案

生产小流量验收使用两类安全反馈：无关内容应进入 `rejected_irrelevant`；描述历史
Mermaid 缺陷、但当前代码已修复的反馈应走完整复现并进入 `cannot_reproduce`。2026-08-13
两条路径均已通过，后者实际执行受信测试回退和 Docker 复现，未生成补丁或 PR。Worker、
Scheduler 同时保持 `active/enabled`，确认 systemd 常驻链路正常。

## 问题 13：Gate 已确认后端转换缺陷，却因相关度为零转人工

2026-08-16 的真实 feedback `4b42428e-...` 包含会触发 Pandoc 公式转换失败的完整
Markdown，插件也能稳定得到 `Could not convert TeX math`。Gate 模型将其判断为
`bug_report/conversion_crash`，同时给出“信息充足、不依赖扩展、属于后端转换缺陷”的
理由，却又输出 `relevance=0.0`。旧 Policy 因 `confidence_below_threshold` 直接终结为
`needs_human`；该 run 没有生成 reproduction plan，也没有调用 Sandbox 或修复模型，不能
把它描述为“Sandbox 无法复现”。

### 根因

直接诱因是 `gate-v6` 只要求产品内问题保持高相关，没有明确 `relevance` 表示产品相关度、
不表示修复难度或模型信心，也没有点明已识别产品缺陷时字段必须一致。模型因此生成了
Schema 合法但跨字段矛盾的结果。第二层缺口在本地 Policy：已有“明确转换报错”证据只在
模型类别漂移时校正类别，没有覆盖类别正确但相关度异常的情况；用户描述中的
“Pandoc 无法转换”也不在原有报错短语集合内。

### 解决方案

`gate-v7` 明确相关度的跨字段口径：已判断为产品 Bug、功能、扩展或视觉问题时必须使用
不低于 `0.8` 的相关度，不能在理由中确认产品缺陷却输出低相关度。由于提示词不能作为
唯一正确性边界，`publication-policy-v6` 同时增加窄范围确定性兜底：在注入、无关内容和
前端范围外规则之后，非空 Bug Markdown 的描述若含明确后端转换报错或 Pandoc 失败签名，
即使模型类别或相关度不稳定，也按 `conversion_crash` 进入有界复现。没有明确错误证据的
低相关反馈仍转人工，安全与范围优先级不变。

回归测试固定本次真实矛盾形态，并另测“只有低相关 conversion_crash 类别、没有错误证据”
仍为 `needs_human`。Agent 全量结果为 300 passed、4 个 Docker 条件测试 skipped，编译检查
通过。历史 `needs_human` run 不重新打开；部署后使用新 feedback 验证完整复现、修复和
发布链路。

## 问题 14：生产更新需要手工串联多条管理命令

生产更新原本要求维护者依次停止 Scheduler、切换目录、拉取代码、运行安装脚本、重新启用
并查看状态。安全顺序本身必要，但每次复制六行命令容易漏掉最后的启用或状态检查。

### 解决方案

新增 `deploy/agent/deploy.sh` 作为标准生产更新入口。维护者只需先对 `/opt/mdtoword` 执行
`git pull --ff-only`，再运行部署脚本；脚本内部固定执行停止领取、底层安装与审计、交互式
`ENABLE` 确认和最终状态输出。任何步骤失败都因 `set -Eeuo pipefail` 立即停止，而
`install.sh` 与 `mdtoword-agentctl enable` 的 fail-safe 继续保证 Scheduler 保持关闭。
该入口只编排已有受信命令，不读取或改写 Secret，也不放宽 Worker 监听和 Docker 边界。
