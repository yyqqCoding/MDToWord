-- 阶段 J：统一最终失败快照与公开脱敏投影。
--
-- 必须由维护者审查后手工执行；应用启动、测试和普通开发命令不得执行本文件。
-- 前置依赖：001_agent_foundation.sql ~ 007_issue_routing_and_public_projection.sql 已应用。

alter table public.agent_runs
    add column if not exists failure jsonb;

alter table public.agent_runs
    drop constraint if exists agent_runs_failure_shape_check;

alter table public.agent_runs add constraint agent_runs_failure_shape_check check (
    failure is null
    or (
        status in ('failed', 'security_rejected', 'budget_exhausted')
        and jsonb_typeof(failure) = 'object'
        and failure ?& array[
            'code', 'kind', 'component', 'operation', 'phase', 'node',
            'handling', 'attempt', 'max_attempts', 'safe_details'
        ]
        and jsonb_typeof(failure -> 'code') = 'string'
        and jsonb_typeof(failure -> 'kind') = 'string'
        and jsonb_typeof(failure -> 'component') = 'string'
        and jsonb_typeof(failure -> 'operation') = 'string'
        and jsonb_typeof(failure -> 'phase') = 'string'
        and jsonb_typeof(failure -> 'node') = 'string'
        and jsonb_typeof(failure -> 'handling') = 'string'
        and jsonb_typeof(failure -> 'attempt') = 'number'
        and jsonb_typeof(failure -> 'max_attempts') = 'number'
        and jsonb_typeof(failure -> 'safe_details') = 'object'
    )
) not valid;

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
    end as validation,
    case
        when r.failure is null then null
        else jsonb_build_object(
            'code', r.failure -> 'code',
            'kind', r.failure -> 'kind',
            'component', r.failure -> 'component',
            'phase', r.failure -> 'phase',
            'node', r.failure -> 'node',
            'attempt', r.failure -> 'attempt',
            'max_attempts', r.failure -> 'max_attempts',
            'handling', r.failure -> 'handling'
        )
    end as failure
from public.agent_runs as r;

revoke all on public.agent_run_public from public, anon, authenticated;
grant select on public.agent_run_public to service_role;

comment on view public.agent_run_public is
    '公开 Trace 展示站只读投影；最终失败只暴露固定白名单，不公开 safe_details。';
