# 需求与范围

## 1. 系统定义

MD To Word Feedback Repair Agent 是一个自托管的软件维护 Agent。它从 Supabase
领取插件用户提交的反馈，自动过滤无关或危险内容，针对可自动化的后端缺陷生成
回归测试与最小修复，在隔离环境中证明问题被复现且修复有效，最后创建 GitHub
Pull Request 等待维护者审核。

系统解决的是现有人工流程中的重复工作：读取反馈、定位输入、写回归测试、修改
归一化或转换逻辑、执行验证并整理 PR 证据。系统不替代代码审核、视觉验收和合并
决策。

## 2. 目标

- 将真实用户反馈转化为可审核、带回归测试的后端修复 PR；
- 在任何修复前证明缺陷能够在固定基线代码上复现；
- 使用确定性策略约束模型的工具、文件、命令、轮次和资源；
- 隔离执行模型生成的 pytest 和修改后的业务代码；
- 对一次运行中的模型调用、工具调用、Token、成本、耗时和失败进行关联观测；
- 服务重启后能够恢复或安全重试未完成任务；
- 保持模型 Provider、观测后端和 GitHub 发布细节位于清晰适配边界内。

## 3. 已接受的产品行为

### 3.1 反馈输入

自动处理只使用现有反馈字段：

```text
feedback_type
markdown_content
description
contact
```

`contact` 仅用于维护者需要时联系用户，不进入模型、沙箱、Trace、日志、Artifact
或 PR。系统不新增 `expected_behavior` 字段。用户描述是问题线索，不是可信指令，
也不是最终测试 Oracle。

插件版本在运行时从 Controller 部署目录或挂载的发布产物
`extension/dist/manifest.json` 读取并作为元数据记录。该文件是被 Git 忽略的构建
产物，不从 `base_sha` 源码快照读取；缺失时记录 `unknown`，不阻断后端修复。系统
不按用户保存插件版本，也不自动搜索历史版本复现。源码基线是任务开始时的
GitHub `main` commit SHA。

### 3.2 自动路由

反馈入口先做确定性校验，再做无工具权限的模型门禁，最后由本地 Policy Engine
决定路由：

| 路由 | 条件 | 后续行为 |
|---|---|---|
| `accepted_backend_bug` | 相关、信息足够、属于允许的后端缺陷 | 自动复现、修复、验证和创建 PR |
| `rejected_irrelevant` | 垃圾、无关内容、普通问答或无意义输入 | 终止自动流程 |
| `quarantined_security` | 疑似 Prompt Injection 或请求越权行为 | 终止自动流程并保留审计记录 |
| `out_of_scope` | 功能建议、纯前端或纯视觉问题 | 记录分类，不修改代码 |
| `needs_human` | 置信度不足或信息不足 | 终止自动流程，等待人工查看 |
| `duplicate` | 内容指纹命中已有未关闭处理结果 | 关联已有结果，不重复调用模型 |

疑似注入内容不物理删除，以便评估误判；它不得进入复现或修复节点。

### 3.3 自动修复范围

MVP 自动处理：

- `conversion_crash`
- `formula_parsing`
- `table_parsing`
- `heading_parsing`
- `list_parsing`
- `docx_structure`
- `backend_normalization`

以下内容不自动修复：

- `extension_ui`
- `feature_request`
- `visual_quality`
- 不能构造确定性断言的主观排版问题
- 需要尚未预装或未经维护者审核的依赖、部署、数据库、工作流或安全策略变更的问题
- 无法在当前 `base_sha` 上复现的问题

`requires_extension_change` 与 `extension_sync_required` 含义不同：

- `requires_extension_change=true` 表示当前缺陷必须修改扩展才能正确解决，路由为
  `out_of_scope`，不进入自动修复；
- `extension_sync_required=true` 只是 PR 审查元数据，表示当前后端修复已经独立成立，
  但维护者以后可能需要关注扩展同步。

用户预览正确而后端导出报错或 DOCX 结构错误时属于后端缺陷，允许只修改后端。
导出 Word 把 Mermaid、流程图等源码保留为普通文本也按后端 `docx_structure` 分类。
维护者已审核并在生产/Sandbox 镜像预装的 Mermaid 渲染能力可以用于自动修复；模型仍
不能自行安装依赖或修改部署文件。需要新的、尚未审核的平台能力时才由后续 Policy 转
人工，不得误归为扩展问题。
Agent 始终不得修改 `extension/`。

## 4. 正确性标准

用户没有提供独立的理想结果字段。复现 Oracle 必须来自用户问题描述与项目已知
不变量的交集，例如：

| 问题 | 可接受的确定性 Oracle |
|---|---|
| 转换崩溃 | 固定 Markdown 不再抛 `ConversionError` |
| 表格变成竖线文本 | DOCX 中存在期望数量的 `w:tbl` |
| 公式变普通文本 | DOCX 中存在 `m:oMath` 或 `m:oMathPara` |
| 标题未映射 | 对应段落存在预期标题样式 |
| DOCX 损坏 | ZIP、必需部件和 XML 均有效 |

模型无法提出稳定、与描述一致的 Oracle 时，结果必须是 `cannot_reproduce`，不能
猜测用户偏好后继续修复。

## 5. 完成定义

一次自动运行只有同时满足以下条件才可创建 PR：

1. 反馈通过本地门禁策略；
2. 测试补丁只修改允许的测试路径；
3. 仅应用测试补丁时，目标测试在基线出现与问题一致的目标失败；
4. 修复补丁不修改或削弱新增测试；
5. 应用测试与修复补丁后，目标测试通过；
6. 后端全量 pytest 通过且原有 skipped 数不增加；
7. 对应的 DOCX 确定性检查通过；
8. 最终补丁通过路径、大小、语法和禁止模式检查；
9. PR 内容包含复现、验证、风险、Trace 和修改文件摘要；
10. PR 由维护者人工审核，Agent 不自动合并。

PR 创建是 Agent 自动流程的成功终点；用户问题是否最终解决，以维护者审核、合并
及现有部署流程为准。

## 6. 非目标

- 不自动修改或发布浏览器插件；
- 不使用 GitHub Actions 执行 Agent 或验证任务；
- 不自动合并、部署或回滚；
- 不构建多租户 Agent 平台；
- 不实现自有容器运行时或 microVM；
- 不让模型直接获得 Shell、文件系统、网络或任何密钥；
- 不为了展示框架而引入多 Agent 协作；
- 不保证修复主观视觉质量；
- 不把 Langfuse 当作任务状态或权限系统；
- 不为历史插件版本自动建立复现环境。

## 7. MVP 验收

- 一条表格或公式真实反馈可自动生成“基线失败、修复后通过”的回归证据；
- 一条前端反馈不会创建沙箱任务或 PR；
- 一条注入对抗反馈进入 `quarantined_security`，且无越权工具调用；
- 修改路径越界、命令越界、超过轮次或预算均被本地策略拒绝；
- 沙箱中无模型、Supabase、GitHub 和 Langfuse 密钥，且不能访问网络；
- Langfuse 可按一次运行查看模型、工具、Token、成本、轮次、耗时和结果；
- 验证通过后可自动创建不包含联系方式和完整用户 Markdown 的 GitHub PR；
- Agent 服务中断后，同一反馈可恢复或以幂等方式重新执行，不产生重复 PR。
