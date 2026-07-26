# 阶段 10:演练、首次真实修复、多模型与稳定运行

## 目标

从 Fake 演练 → 线上 Dry Run → 首次真实 PR → 多模型 → 批准后定时,
逐级放量投产。

## 前置依赖

阶段 08 闭环可用;阶段 09 至少评估部分可用(换模型前要有回归基线)。

## 实施内容

### 1. 本地完整演练(Fake Provider)

`FakeModelProvider`(预制分类/测试/修复响应,`agent/tests/fakes.py`)+
`FakeFeedbackRepository` + 人为制造的简单 Bug(如临时分支去掉某全角分隔符处理):

```bash
python -m pytest agent/tests -v
python -m agent.cli run --feedback-id 00000000-...-000000000001 --repository fake --provider fake
```

覆盖:完整成功 / 无法复现 / 第二轮修复成功 / 两轮失败 / 补丁越界 /
全量回归 / 前端问题转 Issue / 重复反馈 / PR 已存在。

### 2. 首次线上 Dry Run

配好 Secrets 后,用阶段 01 测试反馈跑 `dry_run=true`,核对:
领取成功、task 无 contact、分类合法、`agent_runs` 记录 provider/model、
日志无 Key、无分支无 PR。

### 3. 首次真实修复 PR

选反馈标准:Markdown 短、问题明确、后端可复现、修改范围小、不涉视觉/前端、
你已知大致修法。先 Dry Run 确认分类,再 `dry_run=false`。

审核清单:测试是否真表达用户问题 / 是否过度依赖内部实现 / 修复是否最小 /
是否误伤普通文本与代码块 / 有无宽泛正则 / 是否需反例测试 / 是否需手动开 Word /
是否需前端同步 Issue。

合并后:Render 部署 → 用原反馈 Markdown 调线上 `/convert` → Word 验收 →
feedback 置 `resolved`。(可选自动化:加一个手动触发的 post-deploy 验证 Job,
回放原 Markdown 并自动更新 resolved——高性价比的"生产回归验证闭环"。)

### 4. 多模型(此时才加第二个 Provider)

新增 `anthropic_provider.py`(原生 Messages API),过阶段 03 契约测试;
用阶段 09 评估集对比两家:分类准确率、Schema 成功率、补丁可应用率、
修复成功率、Token、耗时。差异(围栏/截断/字段名)必须封装在 Provider 内。
稳定后可启用模型分工:`CLASSIFIER_MODEL`(低成本)/ `REPAIR_MODEL`(强代码)。

### 5. 批准后定时处理(启用条件:人工触发成功 10~20 条、无越权补丁、成本可控)

```yaml
on:
  schedule: [ { cron: "17 * * * *" } ]   # 避开整点
concurrency: { group: feedback-repair-scheduled, cancel-in-progress: false }
```

扫描 `status='approved' and agent_approved=true` 最早一条,每次仅 1 条;
数据库原子领取仍是最终防重复机制。

### 6. Webhook 实时触发(可选,不建议过早)

`approved` 更新 → Supabase Database Webhook → Edge Function →
`workflow_dispatch` API。Edge Function 只传 feedback ID、只允许固定仓库与
workflow、Token 存 Function Secret。普通 INSERT 不触发。

### 7. 前端问题与 Edge 发布批次

前端类反馈:`automatable=false + needs_extension_release + issue_only`,
Issue 权限与 PR 分离。后端已修但预览可能不一致的:PR 标记
`extension_sync_required=true` + 前端同步 Issue,不阻塞后端。
维护 Milestone `Edge Extension Next Release` 集中同步、构建 ZIP、送审。

### 8. 日常操作与故障排查

处理一条反馈:Supabase 查看 → 复制 ID → Actions 先 `dry_run=true` →
核分类 → `dry_run=false` → 审 PR → Merge → 查 Render → 线上验证 → `resolved`。

失败排查(`agent_runs.error_code`):

```text
model_rate_limit → 稍后重跑          model_invalid_output → 换模型/修 Prompt
cannot_reproduce → 人工补预期结果     patch_policy_rejected → 检查越界尝试
pytest_regression → 看 validation    pr_publish_failed → validated patch 可重试发布
supabase_error → 查 URL/Key/RLS
```

暂停 Agent:disable workflow / 移除 schedule / 不设 `agent_approved` /
默认 `dry_run=true`。无需动 Render 与插件。

## 验收清单

- [ ] Fake E2E 九类场景全通过 —— `python -m pytest agent/tests -q`;
- [ ] 线上 Dry Run 六项核对全过(见 §2);
- [ ] 首条真实反馈 PR 创建→审核→合并→线上验证→`resolved` 全链路完成,
      过程记录到 README 进度表;
- [ ] 第二个 Provider 过契约测试,评估集对比结果已存档;
- [ ] 切换 Provider 仅改 Variables/Secrets,零代码改动(用 git diff 证明);
- [ ] 定时模式试运行一周:无重复 PR、无越权、成本在预算内;
- [ ] 安全回归:注入用例反馈跑全流程,`.github` 修改被拒、日志无 Secret、
      task 无 contact、不可信代码只在零密钥沙箱执行。

## 状态

未开始

## 验收记录

(完成后填写)
