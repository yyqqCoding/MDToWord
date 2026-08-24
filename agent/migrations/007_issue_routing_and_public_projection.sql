-- 阶段 I：Issue 分流、细分类别与 Trace Site 公开投影。
--
-- 必须由维护者审查后手工执行；应用启动、测试和普通开发命令不得执行本文件。
-- 前置依赖：001_agent_foundation.sql ~ 006_trace_site_public_read.sql 已应用。

alter table public.feedback
    add column if not exists area text not null default 'unknown',
    add column if not exists issue_url text;

alter table public.agent_runs
    add column if not exists area text not null default 'unknown',
    add column if not exists issue_url text;

-- 旧 constraint 无法通过 add-if-not-exists 扩展枚举值，必须显式替换；不改写历史行。
alter table public.feedback
    drop constraint if exists feedback_agent_status_check;

alter table public.feedback add constraint feedback_agent_status_check check (
    status in (
        'pending', 'claimed', 'gating', 'issue_required',
        'rejected_irrelevant', 'quarantined_security', 'out_of_scope',
        'needs_human', 'duplicate', 'publishing_issue', 'issue_opened',
        'reproducing', 'repairing', 'validating', 'cannot_reproduce',
        'security_rejected', 'failed', 'validated', 'publishing',
        'stale_base', 'pr_opened'
    )
) not valid;

drop index if exists public.agent_runs_resumable_idx;

create index agent_runs_resumable_idx
    on public.agent_runs (status, started_at)
    where status in (
        'created', 'gating', 'publishing_issue', 'preparing_source',
        'reproducing', 'repairing', 'validating', 'publishing'
    );

-- 按白名单重建视图。Issue 候选标题和摘要只存在于私有 classification JSON，绝不公开。
drop view if exists public.agent_run_public;

create view public.agent_run_public
with (security_invoker = true) as
select
    r.id,
    left(md5(r.feedback_id::text), 12) as run_ref,
    r.status,
    r.route,
    r.area,
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
    r.estimated_cost,
    r.validated_patch_sha256,
    r.pr_url,
    r.issue_url,
    r.error_code,
    r.started_at,
    r.finished_at,
    case
        when r.classification is null then null
        else jsonb_build_object(
            'route', r.classification -> 'route',
            'area', coalesce(r.classification -> 'area', to_jsonb(r.area)),
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
                    'intent', r.classification #> '{classification,intent}',
                    'area', coalesce(
                        r.classification #> '{classification,area}',
                        to_jsonb(r.area)
                    ),
                    'category', r.classification #> '{classification,category}',
                    'relevance', r.classification #> '{classification,relevance}',
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
grant select on public.agent_run_public to service_role;

comment on view public.agent_run_public is
    '公开 Trace 展示站只读投影；Issue 候选内容与用户原文不公开。';
