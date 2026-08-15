-- Trace 展示站阶段 1：公开只读投影与 Langfuse Trace 快照。
--
-- 本文件必须由维护者审查后手工执行；应用启动、自动测试和普通开发命令不得执行。
-- 前置依赖：001_agent_foundation.sql ~ 005_publication_runtime.sql 已应用。
--
-- 本迁移只新增一个视图和一张表，不修改既有列、约束、函数或权限，
-- 因此不影响 Controller、Scheduler、Worker 的任何现有行为。

-- ---------------------------------------------------------------------------
-- 1. Langfuse Trace 快照
-- ---------------------------------------------------------------------------
-- 终态运行的 Trace 不可变，固化后展示站的正常访问路径不再调用 Langfuse。
-- trace_json 由服务端裁剪后写入，本表不负责脱敏。

create table if not exists public.agent_run_traces (
    run_id uuid primary key references public.agent_runs(id),
    trace_id text not null,
    trace_json jsonb not null,
    source text not null default 'langfuse_api',
    captured_at timestamptz not null default now(),
    constraint agent_run_traces_source_check
        check (source in ('langfuse_api', 'manual'))
);

alter table public.agent_run_traces enable row level security;

-- 浏览器角色一律不可读；展示站服务端以 service_role 访问。
revoke all on public.agent_run_traces from public, anon, authenticated;

create index if not exists agent_run_traces_captured_idx
    on public.agent_run_traces (captured_at desc);

comment on table public.agent_run_traces is
    'Langfuse Trace 快照，仅供只读展示站使用；写入前已由服务端裁剪。';

-- ---------------------------------------------------------------------------
-- 2. 公开只读投影
-- ---------------------------------------------------------------------------
-- 采用白名单逐字段重建，不使用 `jsonb - key` 之类的黑名单删除：
-- 白名单在领域模型新增字段时默认不暴露（fail closed），黑名单会默认暴露。
--
-- 明确排除且不得加回的内容：
--   agent_runs.error_message          -- 源自未脱敏的 JUnit target_message
--   *.failure_summary                 -- 同上，见 agent/domain/repair.py:180,284
--   classification.classification.reason -- 模型对用户反馈的复述
--   claim_token / artifact_path / task_artifact_ref -- 运维凭据与服务器路径
--   trace_id / langfuse_trace_id      -- 仅服务端拉快照使用，不下发浏览器
--   feedback_id                       -- 以不可逆 run_ref 代替
--   validation.validated_patch_ref    -- Artifact 引用
--
-- security_invoker = true：即使未来有人误将本视图授予 anon，
-- agent_runs 自身的 RLS 仍然生效，不会因视图所有者权限而被绕过。

create or replace view public.agent_run_public
with (security_invoker = true) as
select
    r.id,
    left(md5(r.feedback_id::text), 12) as run_ref,
    r.status,
    r.route,
    r.category,
    r.dry_run,
    r.base_sha,
    r.extension_version,
    r.provider,
    r.model,
    r.graph_version,
    r.prompt_versions,
    r.policy_version,
    r.model_calls,
    r.tool_calls,
    r.input_tokens,
    r.output_tokens,
    r.total_tokens,
    -- 单价未配置时该值为 0，只代表未估算，不代表上游免费；展示层必须区分。
    r.estimated_cost,
    r.validated_patch_sha256,
    r.pr_url,
    r.error_code,
    r.started_at,
    r.finished_at,

    -- Gate 结果：policy_reason 为代码字面量（见 agent/domain/policy.py），可公开；
    -- 嵌套的 classification.reason 是自由文本，必须排除。
    case
        when r.classification is null then null
        else jsonb_build_object(
            'route', r.classification -> 'route',
            'category', r.classification -> 'category',
            'risk', r.classification -> 'risk',
            'policy_reason', r.classification -> 'policy_reason',
            'model_calls', r.classification -> 'model_calls',
            'tool_calls', r.classification -> 'tool_calls',
            'classification',
            case
                when jsonb_typeof(r.classification -> 'classification') <> 'object'
                    then null
                else jsonb_build_object(
                    'intent',
                        r.classification #> '{classification,intent}',
                    'category',
                        r.classification #> '{classification,category}',
                    'relevance',
                        r.classification #> '{classification,relevance}',
                    'sufficient_information',
                        r.classification #> '{classification,sufficient_information}',
                    'injection_suspected',
                        r.classification #> '{classification,injection_suspected}',
                    'requires_extension_change',
                        r.classification #> '{classification,requires_extension_change}'
                )
            end
        )
    end as classification,

    -- 复现结果：只保留结构化判定，失败文案由前端按 failure_code 映射。
    case
        when r.reproduction is null then null
        else jsonb_build_object(
            'disposition', r.reproduction -> 'disposition',
            'round', r.reproduction -> 'round',
            'target_test_selector', r.reproduction -> 'target_test_selector',
            'expected_failure_kind', r.reproduction -> 'expected_failure_kind',
            'failure_code', r.reproduction -> 'failure_code'
        )
    end as reproduction,

    case
        when r.repair is null then null
        else jsonb_build_object(
            'disposition', r.repair -> 'disposition',
            'round', r.repair -> 'round',
            'failure_code', r.repair -> 'failure_code'
        )
    end as repair,

    -- 四个子验证对象当前只含布尔与计数（agent/domain/repair.py 中均为 frozen 模型），
    -- 因此整体透传。若将来向这些模型新增任何自由文本字段，必须回来改这里。
    case
        when r.validation is null then null
        else jsonb_build_object(
            'passed', r.validation -> 'passed',
            'base_sha', r.validation -> 'base_sha',
            'source_snapshot_sha256', r.validation -> 'source_snapshot_sha256',
            'test_patch_sha256', r.validation -> 'test_patch_sha256',
            'fix_patch_sha256', r.validation -> 'fix_patch_sha256',
            'target_test_selector', r.validation -> 'target_test_selector',
            'baseline_reproduction', r.validation -> 'baseline_reproduction',
            'target_validation', r.validation -> 'target_validation',
            'full_validation', r.validation -> 'full_validation',
            'docx_validation', r.validation -> 'docx_validation',
            'changed_files', r.validation -> 'changed_files',
            'validated_patch_sha256', r.validation -> 'validated_patch_sha256',
            'failure_code', r.validation -> 'failure_code'
        )
    end as validation
from public.agent_runs as r;

revoke all on public.agent_run_public from public, anon, authenticated;

comment on view public.agent_run_public is
    '公开 Trace 展示站的只读投影；字段级白名单，禁止改为整块 JSONB 投影。';
