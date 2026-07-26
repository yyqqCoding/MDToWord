# 阶段 08:权限分离工作流 + 自动 PR(第 4 个可用版本:完整闭环)

## 目标

在 GitHub-hosted runner 上运行完整流程并自动创建可审核 PR;
权限按 [security-policy §4/§5](00-overview/security-policy.md) 矩阵分离。

## 前置依赖

阶段 01–07 全部完成;GitHub Secrets/Variables 已配置
(`SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / MODEL_API_KEY`;
`MODEL_PROVIDER / MODEL_NAME / MODEL_BASE_URL`)。

## 交付物

```text
.github/workflows/feedback-repair-agent.yml(人工开发、审核、合并;之后 Agent 禁改 .github/)
agent/ 中 PR 正文生成逻辑(pr_body.py 或并入 report.py)
```

## 实施内容

### 1. 触发与顶层设置

```yaml
on:
  workflow_dispatch:
    inputs:
      feedback_id: { description: Supabase feedback UUID, required: true, type: string }
      dry_run:     { description: Analyze only,           required: true, default: true, type: boolean }
      provider:    { required: false, type: string }   # 可选覆盖
      model:       { required: false, type: string }
      force_retry: { required: false, default: false, type: boolean }

permissions: { contents: read }

concurrency:
  group: feedback-repair-${{ inputs.feedback_id }}
  cancel-in-progress: false
```

### 2. Job 拆分(权限矩阵见 security-policy §4)

**Job A fetch-task**(Supabase Secret):checkout → `pip install -r agent/requirements.txt`
→ `agent.cli fetch` 领取并输出脱敏 `task.json` → 上传 artifact。
workflow 中 `SUPABASE_SERVICE_ROLE_KEY` 映射为进程的 `SUPABASE_KEY`。

**Job B generate-patch**(模型 Key,step 级注入;ADR-007 修复循环在此):

```yaml
steps:
  - checkout / setup-python / 安装 agent + backend[dev](含 pypandoc_binary 提供 Pandoc)
  - name: classify
    env: { MODEL_API_KEY: ${{ secrets.MODEL_API_KEY }} }   # 仅本 step
    run: python -m agent.cli classify --task-file task.json
  - name: generate test          # 仅模型调用 step 带 Key
    env: { MODEL_API_KEY: ${{ secrets.MODEL_API_KEY }} }
    run: python -m agent.cli gen-test ...
  - name: reproduce (sandboxed)  # 零 Secret;docker --network=none
    run: docker run --rm --network=none --memory=2g --cpus=2 -v "$PWD:/w" -w /w <img> \
         python -m pytest backend/tests/test_feedback_regressions.py -k <sel> --junitxml=...
  - name: reset workspace
    run: git checkout -- . && git clean -fd -e '*.patch' -e '*.json' -e '*.xml'
  - name: generate fix (round loop 由 agent.cli repair 内部驱动,
          模型调用与沙箱执行的交替、每轮重置同样遵守 security-policy §5)
    env: { MODEL_API_KEY: ${{ secrets.MODEL_API_KEY }} }
    run: python -m agent.cli repair --task-file task.json
  - 上传 test.patch / fix.patch / classification.json / repair-result.json
```

Dry Run 时本 Job 只执行 classify 并上传分类结果,后续 Job 跳过。

**Job C validate-patch**(零 Secret,最终信任边界):全新 checkout →
安装 `agent/requirements.txt` + `pip install -e "backend[dev]"`
(必须,提供 Pandoc;`reference.docx` 位于 `backend/app/`,checkout 自带)→
Patch Policy → 仅 test patch 基线失败复验 → 加 fix patch → 目标测试 →
全量 pytest → DOCX 验证 → 产出 `validated.patch + validation.json`。
不信任 Job B 的任何执行结果,全部重跑。

**Job D publish-pr**(`contents: write, pull-requests: write`;
`if: inputs.dry_run == false` 且 C 通过):全新 checkout → 下载
`validated.patch` → 复核路径与 sha256 → 应用 → 分支/commit/push →
`gh pr create`。**不执行任何修改后的 Python 代码**。

**Job E finalize**(Supabase Secret,`if: always()`):按各 Job 结果更新
feedback 与 agent_run(状态、PR URL、错误码、token/成本、stage_timings);
PR 发布失败 → `validated_but_unpublished`(artifact 已保留可重试发布)。

### 3. 分支、Commit 与 PR

```text
分支:agent/feedback-<short-id>-<category>     (short-id = UUID 前 8 位)
Commit:fix: repair <category> for feedback <short-id>
```

PR 正文由 Python 从 `validation.json` 生成,必须包含:反馈 ID(短)、分类、
复现说明(修复前失败证据)、修改摘要与文件、目标/全量测试结果、DOCX 验证、
Provider 与模型名、风险等级、`extension_sync_required` 标记、
"联系方式未传递给模型"声明、"本 PR 不会自动合并"声明。
不复制完整用户 Markdown(仓库公开,防用户内容泄露)。

防重复:创建前 `gh pr list --state open --search "feedback <short-id> in:body"`
+ 检查 Supabase `pr_url`;PR 正文发布前跑密钥模式扫描(security-policy §9)。

### 4. 仓库设置

Settings → Actions → General → Workflow permissions:允许创建 PR;
YAML 内仍显式声明 Job 级最小权限。
(可选加强:为 publish-pr 配置 GitHub Environment + required reviewers,
获得"发布前人工批准"关卡,见阶段 10。)

## 验收清单

- [ ] `workflow_dispatch` 页面可见全部输入;未填 `feedback_id` 无法运行;
- [ ] 相同 `feedback_id` 并发触发,仅一个成功领取(concurrency + RPC 双保险);
- [ ] `dry_run=true`:只产出分类,`git branch -r` 无新分支,无 PR;
- [ ] Job B 中非模型 step 打印 `env` 无 `MODEL_API_KEY`(用 `env | grep -c MODEL_API_KEY || true` 自检 step 验证);
- [ ] Job C 的 step 无任何 Secret 注入(审查 YAML + 运行日志);
- [ ] 沙箱容器内 `curl example.com` 失败(network=none 生效,加一条自检);
- [ ] 端到端:用阶段 01 测试反馈跑 `dry_run=false`,PR 创建成功且正文含
      修复前失败证据、验证报告、无联系方式;
- [ ] 人为让全量 pytest 失败(fixture 案例),Job C 拦截,无 PR,
      finalize 写入 `failed` 与可读原因;
- [ ] 中途取消 workflow,反馈 2 小时后可被重新领取(阶段 01 超时回收);
- [ ] Supabase `agent_runs` 记录完整:provider/model/token/耗时/PR URL。

## 状态

未开始

## 验收记录

(完成后填写)
