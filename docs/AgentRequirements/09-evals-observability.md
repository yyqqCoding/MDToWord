# 阶段 09:离线评估集 + 全链路可观测体系

> 新增阶段(原始文档没有)。这是"评估可观测"叙事线的落点:
> 换模型/改 Prompt 有回归保障,运行质量与成本有数据可讲。

## 目标

1. **离线评估(Evals)**:golden 用例集 + 一条命令的 eval runner,
   量化分类准确率、Schema 合规率、补丁可应用率、修复成功率;
2. **可观测**:分阶段耗时/Token 落库,反馈→合并转化漏斗 SQL 视图,定期报告。

## 前置依赖

阶段 04(分类)可先做评估部分;完整指标依赖阶段 08 跑通。

## 交付物

```text
agent/evals/cases/<case-id>/feedback.json + expected.json   # 10~20 条 golden 用例
agent/evals/runner.py                                        # python -m agent.evals.runner
agent/evals/adversarial/                                     # 注入攻击用例(见下)
supabase/migrations/2026xxxx_agent_metrics_views.sql         # 漏斗视图
scripts/agent_report.py                                      # 生成 Markdown 月报
```

## 实施内容

### 1. 离线评估集

每条用例:`feedback.json`(构造或脱敏后的真实反馈)+ `expected.json`
(期望分类、automatable、期望复现方向,可选期望修改文件)。
来源:真实反馈脱敏、`docs/samples/`、按 DOCX 场景清单构造
(普通表格 / 全角竖线表格 / 中文破折号分隔行 / 行内与块级公式 / 多公式 /
代码块内公式符号不转换 / 标题缺空格 / 超六级标题 / 独立 `---` / 列表紧贴正文 / 中文正文)。

eval runner 输出:

```text
classification accuracy: 17/20    schema compliance: 20/20
automatable precision:   .../...  provider=... model=... prompt=classify-v2
per-case 明细 + 与上次运行的 diff
```

用途:**换模型或改 Prompt 前后各跑一次**,结果存
`agent/evals/results/<date>-<model>-<prompt-version>.json` 纳入版本管理。

### 2. 对抗性评估(注入)

`adversarial/` 收录 security-policy §6 的攻击样式
(Ignore previous instructions / 读环境变量 / 改 workflows / 删测试 / 外传 Key),
runner 断言:输出仍为合法 Schema、`injection_suspected` 命中率、分类不被劫持。

### 3. 分阶段指标(运行时埋点)

`agent_runs.stage_timings`(阶段 01 已建字段)结构:

```json
{ "classify":   { "ms": 2100, "input_tokens": 1800, "output_tokens": 120 },
  "gen_test":   { "ms": 8300, "input_tokens": 5200, "output_tokens": 900 },
  "reproduce":  { "ms": 41000 },
  "gen_fix":    { "rounds": [ { "ms": 9100, "input_tokens": 6100, "output_tokens": 1400 } ] },
  "validate":   { "ms": 95000 } }
```

结构化 JSON 日志每条带 `feedback_id / agent_run_id / workflow_run_id`
(即天然 trace id,可跨 Job 关联);禁止记录项见 security-policy §8。

### 4. 转化漏斗与报告

SQL 视图(示意):

```sql
create or replace view agent_funnel as
select count(*)                                            as feedback_total,
       count(*) filter (where automatable)                 as automatable,
       count(*) filter (where status = 'pr_opened')        as pr_created,
       count(*) filter (where resolution_type = 'backend_pr'
                         and resolved_at is not null)      as merged_resolved
from public.feedback;
```

`scripts/agent_report.py` 汇总月度:反馈总数、后端问题比例、可自动化比例、
PR 创建/合并/关闭数、平均修复轮数、无法复现比例、Patch Policy 拒绝次数、
注入命中次数、平均 Token/成本/耗时、结构化输出失败率、合并后回滚数。
可由 GitHub Actions `schedule` 每月生成 Markdown 报告存入 `docs/reports/`。

关键质量指标(不要只看 PR 数量):自动 PR 合并率、合并后无回归率、
测试真正复现率、维护者平均审核时间。

## 验收清单

- [ ] `python -m agent.evals.runner --provider fake` 全量跑通,输出汇总与明细;
- [ ] 评估集 ≥ 10 条,覆盖 ≥ 5 个分类类别 + ≥ 3 条对抗用例;
- [ ] 真实模型跑一次评估,结果文件已提交到 `agent/evals/results/`;
- [ ] 换 `MODEL_NAME` 再跑,两次结果可对比(证明回归保障可用);
- [ ] 一次真实 workflow 运行后,`agent_runs.stage_timings` 各阶段字段非空;
- [ ] `select * from agent_funnel;` 返回合理数字;
- [ ] `python scripts/agent_report.py` 生成的报告不含联系方式与完整用户 Markdown。

## 状态

未开始

## 验收记录

(完成后填写)
