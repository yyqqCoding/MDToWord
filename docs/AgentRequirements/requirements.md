# 产品需求与范围

## 1. 系统定义

MD To Word Feedback Repair Agent 是一个自托管的软件维护 Agent。它接收扩展用户提交的
Markdown 与问题描述，判断反馈类型，并把可自动处理的后端转换缺陷变成经过测试和验证的
Pull Request。功能需求和前端/扩展问题只生成脱敏 Issue，交给维护者处理。

Agent 的自动化终点是“产生可审核的 PR 或 Issue”，不是自动合并、部署或替用户确认
Word 的最终视觉效果。

## 2. 产品目标

- 减少人工读取反馈、编写回归测试、定位转换代码和整理验证证据的重复工作；
- 对每一个自动修复候选提供固定源码版本、失败基线、修复结果和独立验证证据；
- 让模型可以在有限范围内自主选择工具，同时保证工具、文件、权限、预算和执行环境由
  受信代码控制；
- 让服务重启、短暂网络故障和发布响应丢失可以恢复，且不重复执行有副作用的操作；
- 让维护者能从运行 ID 定位失败阶段、节点、错误码、尝试次数和相关 Artifact。

## 3. 反馈输入与信任等级

系统接收以下字段：

```text
feedback_type
markdown_content
description
contact
```

`feedback_type` 只是表单提示，不能代替最终分类。Markdown、description、源码和测试
输出全部是不可信数据，可能包含提示词注入或错误指令。

`contact` 只保存在反馈表供维护者联系用户，不进入模型、Sandbox、Trace、日志、Artifact、
PR 或 Issue。系统不新增 `expected_behavior` 字段；用户描述只能作为问题线索，不能直接
成为测试 Oracle。

插件版本从 Agent 主机上的 `extension/dist/manifest.json` 读取，缺失时记录 `unknown`，
不阻断后端修复。每个修复运行在开始时固定 GitHub `main` 的 `base_sha`。

## 4. 路由

所有反馈先经过入口校验，再经过无工具 Gate，最后由本地 Policy 产生稳定路由：

| 路由 | 适用条件 | 后续动作 |
|---|---|---|
| `accepted_backend_bug` | 相关、信息充分、属于允许的后端缺陷 | 复现、修复、验证、创建 PR |
| `issue_required` | 功能需求或前端/扩展缺陷，且信息充分 | 创建脱敏 Issue |
| `rejected_irrelevant` | 无关、垃圾、普通问答或无意义内容 | 结束运行 |
| `quarantined_security` | Prompt Injection、越权或其他安全风险 | 结束并保留安全审计摘要 |
| `needs_human` | 置信度不足、信息不足或超出自动化范围 | 交给维护者 |
| `duplicate` | 内容指纹命中已有处理结果 | 复用已有结果 |

注入和无关反馈不能进入复现、修复或发布。历史数据库中可能存在 `out_of_scope`，新运行
不再产生该路由。

Gate 的分类维度相互独立：

```text
intent: bug_report | feature_request | unrelated | spam | unknown
area: backend | extension | cross_component | none | unknown
category: conversion_crash | formula_parsing | table_parsing | heading_parsing |
          list_parsing | docx_structure | backend_normalization |
          extension_ui | feature_request | visual_quality |
          irrelevant_content | prompt_injection | unknown
```

前端/扩展 Bug 和功能需求均不启动 Sandbox。只有 `accepted_backend_bug` 可以进入自动
修复；新建 PR 或 Issue 都必须经过固定的本地发布契约。

## 5. 自动修复范围

允许自动修复的后端问题包括：

- 转换直接抛错（`conversion_crash`）；
- 公式、表格、标题、列表和后端归一化错误；
- 可由确定性 DOCX 结构断言证明的 `docx_structure` 问题。

以下情况不自动修改：

- 必须修改扩展、部署、数据库、依赖或安全策略的问题；
- 需要尚未预装或未审核平台能力的问题；
- 只有主观视觉偏好、无法形成稳定断言的问题；
- 在当前固定 `base_sha` 上无法复现的问题。

Agent 始终不能修改 `extension/`、依赖清单、Dockerfile、沙箱受信模块或部署配置。

## 6. 当前处理流程

```text
feedback
  -> Gate（无工具）
  -> 路由
      |- rejected / quarantine / needs_human
      |- issue_required -> 脱敏 Issue
      `- accepted_backend_bug
           -> 固定 base_sha 和源码快照
           -> conversion probe
           -> create_agent ReAct 工具循环
           -> 新 Sandbox 独立验证
           -> PR
```

conversion probe 先只判断当前 Markdown 是否抛出转换错误：

- 如果抛错，Controller 生成固定转换回归测试，模型不需要猜测 Oracle；
- 如果转换成功，Agent 才根据用户描述、源码和产物设计语义回归测试；
- 语义测试必须先在基线失败，修复后再在目标 Sandbox 通过；
- 模型无法构造稳定测试时，进入 `cannot_reproduce` 或 `needs_human`。

## 7. 成功标准

只有以下条件全部成立，才允许创建 PR：

1. Gate 和本地 Policy 接受反馈；
2. 测试补丁和修复补丁只触及各自白名单；
3. 基线 + 测试补丁实际出现预期失败；
4. 修复补丁没有削弱或删除新增回归测试；
5. 测试补丁 + 修复补丁通过目标测试；
6. 后端全量测试和 DOCX 专项检查通过；
7. 最终 diff、路径、大小、语法和哈希检查通过；
8. PR 只包含脱敏摘要、验证证据和 Trace 链接；
9. PR 由维护者审核、合并和部署。

模型说“完成”、Sandbox 返回普通文本或单次测试通过，都不能单独证明成功。

## 8. 非目标

- 不自动修改、发布或部署浏览器扩展；
- 不自动合并、回滚或部署 PR；
- 不让模型直接使用 Shell、任意 Filesystem、网络、数据库、GitHub 或密钥；
- 不构建多租户 Agent 平台、自有容器运行时或 microVM；
- 不为了展示复杂度引入多 Agent；
- 不把 Langfuse 当作业务状态或权限系统；
- 不保证主观 Word 视觉质量可以完全自动判断；
- 不为历史插件版本自动建立复现环境；
- 不在当前版本实现数据飞轮、自进化训练或自动生成 Skill。
