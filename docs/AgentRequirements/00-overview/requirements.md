# 需求规格(SRS)

> 原始完整版见 `../archive/MDToWord_Feedback_Repair_Agent_Requirements.md`。
> 本文件为精简后的权威版本;安全策略、白名单、阈值统一见 [security-policy.md](security-policy.md)。

## 1. 系统定义

读取 Supabase 中的用户反馈,自动判断问题类型,复现 Markdown 转 DOCX 故障,
生成回归测试,通过可替换的大模型 API 生成代码修复,执行 pytest 与 DOCX XML
结构验证,最终创建可供人工审核的 GitHub Pull Request。

## 2. 背景

现有生产链路:

```text
网页 AI 输出的 Markdown → Edge 插件侧边栏 → POST /convert → FastAPI 后端
  → normalize_markdown() → Pandoc → DOCX 三线表后处理与自检 → 可编辑 Word
```

已具备的基础:

- `backend/app/normalizer.py` 处理常见"脏 Markdown";
- `backend/app/pandoc_runner.py` 负责 Pandoc 转换、三线表边框、未解析表格自检;
- `backend/tests/` 已使用 pytest;
- `/feedback` 接口写入 Supabase(仅 `id / feedback_type / markdown_content / description / contact` 五个字段);
- 后端部署在 Render,合并 main 即可发布;插件发布需打 ZIP 送审 Edge 商店,周期长。

因此第一阶段**只自动修复后端问题**;插件前端问题只分类并创建 Issue。

## 3. 问题陈述

当前反馈处理全靠人工:查看 Supabase → 复现 → 定位 → 写测试 → 修码 → 测试 → 提交 → 部署。
痛点:重复性高(表格/公式/标题类问题相似)、常漏留回归测试、依赖开发者有空、
长 Markdown 定位成本高、无法系统记录尝试与成本、直接给通用 Agent 高权限风险过高。

## 4. 建设目标

```text
Supabase 反馈 → GitHub Actions 触发 → Python 状态机编排 → 模型分类与生成修复
  → 失败测试验证 → pytest + DOCX XML 验证 → GitHub PR → 人工审核合并
```

- 业务:常见后端解析问题从"人工全流程"降为"人工审核 PR";每个修复必带回归测试;
  后端修复合并即对全部用户生效;形成可写入简历的真实用户驱动 Agent 工程项目。
- 工程:Agent 为普通 Python 包,可本地与 CI 运行;模型经统一 `ModelProvider` 接口调用,
  只返回结构化结果,不接触密钥与工具;所有状态写回 Supabase;MVP 只建 PR 不合并。

## 5. 非目标(MVP 明确不做)

1. 不自动修改 `extension/` 前端代码;
2. 不自动构建/提交 Edge 商店 ZIP;
3. 不自动合并 PR;
4. 不自动修改 `.github/workflows/`、依赖清单、部署配置;
5. 不让模型执行 Shell / 访问文件系统 / 访问网络;
6. 不让模型接触 Supabase、GitHub Token、Render 密钥;
7. 不处理大型新功能需求;
8. 不保证修复所有视觉排版问题;
9. 不做常驻服务或独立管理后台;
10. 不引入通用 Agent Runtime 作为核心依赖。

## 6. 角色

| 角色 | 职责 | 边界 |
|---|---|---|
| 插件用户 | 提交反馈 | 不能触发 Agent、看不到日志与 PR |
| 项目维护者 | 触发工作流、审核 PR、决定合并 | 唯一拥有合并权的人 |
| Agent Orchestrator(Python 状态机) | 领取反馈、状态转换、调模型、构造受信上下文、应用与验证补丁 | 全部副作用由它执行 |
| Model Provider | 返回分类 / 复现策略 / 测试 / 修复 / 说明 | 无 Shell、无网络、无密钥、只见挑选后的文本 |
| Deterministic Validator | 独立判断补丁合法性、修复前失败、修复后通过、DOCX 结构 | 纯确定性代码,不含模型 |

## 7. 处理范围

### 7.1 自动修复(MVP)

| 类别 | 示例 | 等级 |
|---|---|---|
| `conversion_crash` | Pandoc 非零退出、转换异常 | 高 |
| `formula_parsing` | 公式残留、OMML 缺失、TeX 警告(注意:当前实现走 `ConversionError`/400 路径) | 高 |
| `table_parsing` | 表格导出成竖线文本、三线表未生成 | 高 |
| `heading_parsing` | 标题未映射、深层标题残留 | 高 |
| `list_parsing` | 列表紧贴正文未识别 | 中 |
| `docx_structure` | DOCX 缺少表格/公式节点 | 中 |
| `backend_normalization` | 特殊符号、全角字符、错误分隔符 | 高 |
| `preview_export_mismatch` | 后端可修、前端预览有差异 | 后端修复 + 前端 Issue |

### 7.2 不自动修复

| 类别 | 处理方式 |
|---|---|
| `extension_ui` | 标记 `needs_extension_release`,创建 Issue |
| `feature_request` | 创建 Issue,不生成补丁 |
| `visual_quality` | 生成分析,转人工 |
| `security_sensitive` | 直接转人工 |
| `invalid_feedback` | 标记无效并记录原因 |
| `duplicate` | 关联已有反馈或 PR |
| `cannot_reproduce` | 保存复现报告,转人工 |

文件修改白名单见 [security-policy.md §1](security-policy.md)。

## 8. MVP 验收标准

1. 可从 GitHub Actions 手动输入 Supabase `feedback_id` 触发;
2. 可原子领取反馈并创建 `agent_run`;
3. 切换 Provider 只改配置,状态机零改动;
4. 可对一条后端解析问题生成回归测试;
5. 新测试在基线代码上出现**目标失败**(非导入/语法错误);
6. 修复补丁只修改白名单文件;
7. 修复后目标测试与全量 pytest 通过;
8. DOCX XML 专项验证通过;
9. 自动创建含完整报告的 PR;
10. Agent 不修改 `extension/`,不自动合并;
11. 执行修改后代码的步骤不持有模型、Supabase、GitHub 写密钥;
12. Supabase 可查看状态、模型、测试结果、PR URL;
13. 失败任务有维护者可理解的明确原因,不静默失败。
