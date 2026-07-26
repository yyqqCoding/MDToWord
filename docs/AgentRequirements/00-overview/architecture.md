# 总体架构与关键决策

## 1. 逻辑架构

```text
┌────────────────────────────────────────────────────────┐
│ 现有生产链路: Edge 插件 → FastAPI /feedback → Supabase │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ GitHub Actions(workflow_dispatch 手动触发)            │
│                                                        │
│ Job A fetch-task      读取并原子领取反馈,输出脱敏 task │
│ Job B generate-patch  状态机 + 模型:分类 → 生成测试   │
│                       → 沙箱复现 → 生成修复 → 沙箱重测 │
│                       (循环 ≤ N 轮,step 级密钥隔离)  │
│ Job C validate-patch  零密钥全新环境独立复验:          │
│                       补丁策略 → 修复前失败 → 修复后    │
│                       通过 → 全量 pytest → DOCX XML    │
│ Job D publish-pr      仅 GitHub 写权限:应用已验证补丁, │
│                       建分支、commit、PR               │
│ Job E finalize        仅 Supabase 写权限:回写状态/结果 │
└────────────────────────────────────────────────────────┘
```

Job 权限矩阵与沙箱分层见 [security-policy.md](security-policy.md) §4、§5。

## 2. 部署模型

无常驻服务器。Agent 源码在本仓库;运行环境为 GitHub-hosted runner(一次性 VM);
任务存储 Supabase;交付物为 PR;合并后沿用 Render 现有部署;前端仍走人工 ZIP 送审。

## 3. 仓库目录

```text
MDToWord/
├── agent/
│   ├── cli.py  config.py  domain.py  state_machine.py  exceptions.py
│   ├── feedback_repository.py  workspace.py  patching.py  context_builder.py
│   ├── logging_utils.py  policy.yaml
│   ├── prompts/        classify.md  generate_test.md  generate_fix.md
│   ├── providers/      base.py  factory.py  openai_compatible_provider.py  (后续 anthropic_provider.py)
│   ├── schemas/        classification.py  test_generation.py  fix_generation.py
│   ├── validators/     patch_policy.py  pytest_validator.py  docx_validator.py  report.py
│   ├── evals/          cases/  runner.py            # 阶段 09
│   ├── fixtures/       feedback_cases/
│   └── tests/
├── backend/            # 现有 FastAPI 服务(勘误:reference.docx 在 backend/app/)
├── extension/          # 现有插件,Agent 禁改
├── supabase/migrations/
└── .github/workflows/feedback-repair-agent.yml
```

## 4. 状态机

### 4.1 Feedback 状态

```text
pending → (approved) → claimed → classified
  ├─→ invalid / duplicate / needs_human / needs_extension_release
  └─→ reproducing → repairing → validating
        ├─→ failed
        ├─→ security_rejected            # 补丁越界,不重试
        ├─→ validated_but_unpublished    # PR 发布失败,补丁 artifact 已保留,可重试发布
        └─→ pr_opened → resolved(人工合并后更新)
```

### 4.2 Agent Run 状态

```text
created → fetching_context → classifying → generating_test → verifying_reproduction
  → generating_fix → validating → ready_for_pr → pr_created
  (任意节点可 → failed / cancelled)
```

### 4.3 转换规则

- 仅 `pending / approved / failed`(且未超重试上限)或**超时的 `claimed`** 可被领取;
- 领取为原子 SQL 更新(RPC 内含超时回收与 `attempt_count` 上限校验,见阶段 01);
- 每次运行独立 `agent_runs` 记录,失败不覆盖历史;
- 超过最大重试进入 `needs_human`;
- `pr_opened` 不允许再次自动生成 PR,除非维护者显式重开。

## 5. 数据模型摘要

- `feedback` 扩展字段:`status / category / automatable / agent_approved /
  expected_behavior / content_fingerprint / source_version(预留,现恒 null) /
  attempt_count / claimed_at / claim_token / last_error / resolution_type /
  pr_url / resolved_at / updated_at`;
- `agent_runs`:每次运行的 provider、model、分类、复现、验证摘要、改动文件、
  patch hash、token、成本、分阶段耗时(`stage_timings jsonb`,阶段 09)、PR URL、错误码;
- 完整 SQL 见 [01-foundation.md](../01-foundation.md)。

## 6. 关键设计原则

1. **模型可替换**:状态机只依赖 `ModelProvider` 协议;业务代码禁止出现按模型名分支的逻辑;
   切换模型仅改 GitHub Variables/Secrets。
2. **模型不拥有工具权限**:无 Shell、无文件系统、无网络、无密钥;只接收挑选后的文本,
   返回符合 Schema 的结构化结果;一切副作用由 Python Harness 执行。
3. **测试先行**:修复被接受的前提是——测试补丁可应用、仅测试补丁时基线**必失败**、
   加修复补丁后通过、全量测试通过、DOCX 验证通过、改动均在白名单。
4. **后端优先**:上下文不含 `extension/`,分类指向前端即 `needs_extension_release + issue_only`。
5. **人工合并**:Agent 只建 PR;维护者审核代码、测试质量、范围、兼容性、是否需 Word 手工验收。

## 7. 关键决策记录(ADR)

### ADR-001 使用模型 API,而非固定 Coding CLI
自由换模型;状态机统一管理重试/成本/结构化输出;模型无需 Shell 权限;权限隔离更容易。

### ADR-002 MVP 运行在 GitHub Actions
无常驻服务器;与代码、测试、PR 天然集成;日志与 artifact 可审计;支持手动/定时/外部触发。

### ADR-003 后端优先,不自动修改插件前端
后端合并即全量生效;前端受 Edge 商店审核周期制约;后端问题可用 pytest + DOCX XML 自动验证。

### ADR-004 测试与修复补丁分离
证明问题在修复前真实存在;防止模型生成永真测试自证成功;留下"失败→修复通过"完整证据链。

### ADR-005 不自动合并
自动验证无法替代 Word 视觉检查;项目规模允许人工审核;降低误修对生产用户影响。

### ADR-006 模型输出整文件/搜索替换,diff 由 Harness 生成
LLM 直接产 unified diff 的 hunk 行号与上下文极易错位,`git apply` 失败率高,
白白消耗修复轮次。改为:模型返回目标文件完整内容(`normalizer.py` 等均为小文件)
或搜索/替换块,Harness 在基线工作区落盘后用 `git diff` 确定性生成补丁。
白名单、行数上限等策略检查全部作用于生成后的 diff,安全模型不变。

### ADR-007 修复循环收敛在 generate-patch Job 内
FR"修复失败→错误摘要回喂模型→重试 ≤ N 轮"要求模型调用与测试执行交替,
而 GitHub Actions Job 之间无法循环。方案:循环整体放在 Job B,
`MODEL_API_KEY` 只注入模型调用 step;不可信测试在 `docker --network=none`
容器中执行;每轮模型调用前 `git checkout -- . && git clean -fd` 重置工作区,
防止不可信代码篡改 Agent 自身后窃取下一 step 的密钥。
Job C 保留为零密钥全新环境的**最终独立复验**,信任边界不变。
