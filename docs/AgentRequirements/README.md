# MD To Word 用户反馈自动修复 Agent

> 以 Supabase 用户反馈为输入,以经过确定性验证的 GitHub Pull Request 为输出,
> 使用可替换模型 API 进行分类、测试生成和代码修复的后端软件维护 Agent。
>
> 核心原则:后端优先、模型可替换、测试先行、自动创建 PR、人工审核合并。

## 文档结构

| 目录/文件 | 内容 |
|---|---|
| `00-overview/requirements.md` | 需求规格:目标、非目标、角色、处理范围、MVP 验收标准 |
| `00-overview/architecture.md` | 总体架构、状态机、数据模型、关键决策记录(ADR) |
| `00-overview/security-policy.md` | **安全策略唯一事实来源**:白名单、权限矩阵、沙箱分层、注入防护、阈值 |
| `01`~`10` 阶段文件 | 每阶段一个自包含 spec:目标 / 前置依赖 / 交付物 / 实施内容 / 验收清单 |
| `archive/` | 拆分前的原始两份长文档(仅存档,不再维护,以拆分后文档为准) |

策略类内容(白名单、阈值、权限矩阵)只在 `security-policy.md` 维护一份,
阶段文件一律引用不复制。

## 实施进度

| 阶段 | 内容 | 可用增量 | 状态 | 验收日期 | PR |
|---|---|---|---|---|---|
| [01](01-foundation.md) | 基线验证 + Supabase 迁移 | 任务可领取、可追踪 | 进行中 | — | — |
| [02](02-agent-skeleton.md) | Agent Python 骨架 + Repository | CLI 可读取反馈 | 未开始 | — | — |
| [03](03-model-provider.md) | Model Provider 抽象 | 可切换模型 API | 未开始 | — | — |
| [04](04-classification-dryrun.md) | 分类 Dry Run | **第 1 个可用版本:反馈分诊** | 未开始 | — | — |
| [05](05-patch-safety.md) | Workspace + 补丁安全策略 | 补丁可安全应用 | 未开始 | — | — |
| [06](06-test-reproduction.md) | 测试生成与复现验证 | **第 2 个可用版本:自动复现 Bug** | 未开始 | — | — |
| [07](07-repair-and-validate.md) | 修复循环 + pytest/DOCX 验证 | **第 3 个可用版本:验证过的补丁** | 未开始 | — | — |
| [08](08-github-actions.md) | 权限分离工作流 + 自动 PR | **第 4 个可用版本:完整闭环** | 未开始 | — | — |
| [09](09-evals-observability.md) | 离线评估集 + 可观测体系 | 评估与指标看板 | 未开始 | — | — |
| [10](10-rollout.md) | 演练、首次真实 PR、多模型、定时 | 稳定投产 | 未开始 | — | — |

状态取值:`未开始` / `进行中` / `已验收(日期)`。每完成一个阶段,更新本表并在阶段文件底部记录验收结果。

## 日常使用方式(建成后)

```text
Supabase 复制 feedback_id
  ↓
GitHub → Actions → Feedback Repair Agent → Run workflow(先 dry_run=true)
  ↓
审核分类结果 → dry_run=false → 等待 PR
  ↓
人工审核并 Merge → Render 部署后端 → 用户无需更新插件即可获得修复
```

## 与原始文档的差异

拆分时合并了以下已确认的设计修订(原始文档未包含):

1. **修复循环收敛到 generate-patch Job 内部**,用 step 级密钥隔离 + 工作区重置解决
   "循环需要模型调用与测试执行交替"与"Job 拆分"的矛盾(见 `08` 与 security-policy §沙箱分层)。
2. **模型输出改为整文件/搜索替换格式**,unified diff 由 Harness 确定性生成(ADR-006),
   规避 LLM 产 diff 行号错位的高失败率。
3. **claim 超时回收 + 重试上限下沉到 SQL**(见 `01`),避免 workflow 被取消后反馈卡死在 `claimed`。
4. **内容指纹改用 `feedback_type`** 而非模型分类结果,使去重可在调用模型之前完成。
5. **新增阶段 09:离线评估集与全链路可观测**(分类准确率、补丁可应用率、分阶段耗时/Token、
   反馈→合并转化漏斗)。
6. MVP Provider **只实现 `openai_compatible`**,Anthropic 原生 Provider 推迟到阶段 10。
7. 复现判定改用 `pytest --junitxml` 结构化解析,不再靠正则解析文本日志。
8. 勘误:`reference.docx` 位于 `backend/app/reference.docx`;状态机补充
   `validated_but_unpublished`、`security_rejected` 两个状态。
