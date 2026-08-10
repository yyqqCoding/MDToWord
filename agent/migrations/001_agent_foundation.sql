-- 阶段 A：原子领取反馈并持久化 Agent 运行摘要。
-- 迁移不会由应用自动执行，必须由 feedback 表所有者在备份后手工应用。

alter table public.feedback
    add column if not exists status text not null default 'pending',
    add column if not exists category text,
    add column if not exists risk text not null default 'unknown',
    add column if not exists content_fingerprint text,
    add column if not exists attempt_count integer not null default 0,
    add column if not exists stale_requeue_count integer not null default 0,
    add column if not exists claimed_at timestamptz,
    add column if not exists claim_token uuid,
    add column if not exists last_error_code text,
    add column if not exists last_error_message text,
    add column if not exists pr_url text,
    add column if not exists resolved_at timestamptz,
    add column if not exists updated_at timestamptz not null default now();

create index if not exists feedback_agent_claim_idx
    on public.feedback (status, claimed_at, created_at);

create index if not exists feedback_content_fingerprint_idx
    on public.feedback (content_fingerprint)
    where content_fingerprint is not null;

do $migration$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'feedback_agent_status_check'
          and conrelid = 'public.feedback'::regclass
    ) then
        alter table public.feedback add constraint feedback_agent_status_check check (
            status in (
                'pending', 'claimed', 'gating', 'rejected_irrelevant',
                'quarantined_security', 'out_of_scope', 'needs_human', 'duplicate',
                'reproducing', 'repairing', 'validating', 'cannot_reproduce',
                'security_rejected', 'failed', 'validated', 'publishing',
                'stale_base', 'pr_opened'
            )
        ) not valid;
    end if;
    if not exists (
        select 1 from pg_constraint
        where conname = 'feedback_agent_counts_check'
          and conrelid = 'public.feedback'::regclass
    ) then
        alter table public.feedback add constraint feedback_agent_counts_check check (
            attempt_count >= 0 and stale_requeue_count between 0 and 1
        ) not valid;
    end if;
end;
$migration$;

create table if not exists public.agent_runs (
    id uuid primary key,
    feedback_id uuid not null references public.feedback(id),
    status text not null,
    base_sha text,
    extension_version text not null default 'unknown',
    provider text,
    model text,
    graph_version text,
    prompt_versions jsonb not null default '{}'::jsonb,
    policy_version text,
    langfuse_trace_id text,
    classification jsonb,
    reproduction jsonb,
    validation jsonb,
    model_calls integer not null default 0,
    tool_calls integer not null default 0,
    input_tokens bigint not null default 0,
    output_tokens bigint not null default 0,
    total_tokens bigint not null default 0,
    estimated_cost numeric(18, 8) not null default 0,
    validated_patch_sha256 text,
    artifact_path text,
    pr_url text,
    error_code text,
    error_message text,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    constraint agent_runs_attempt_counts_nonnegative check (
        model_calls >= 0 and tool_calls >= 0 and input_tokens >= 0
        and output_tokens >= 0 and total_tokens >= 0
    )
);

-- 只补列、不删除旧列，以兼容可能执行过历史 Agent migration 的数据库。
alter table public.agent_runs
    add column if not exists base_sha text,
    add column if not exists extension_version text not null default 'unknown',
    add column if not exists graph_version text,
    add column if not exists prompt_versions jsonb not null default '{}'::jsonb,
    add column if not exists policy_version text,
    add column if not exists langfuse_trace_id text,
    add column if not exists validation jsonb,
    add column if not exists tool_calls integer not null default 0,
    add column if not exists input_tokens bigint not null default 0,
    add column if not exists total_tokens bigint not null default 0,
    add column if not exists validated_patch_sha256 text,
    add column if not exists artifact_path text;

alter table public.agent_runs
    alter column provider drop not null,
    alter column model drop not null,
    alter column estimated_cost type numeric(18, 8);

alter table public.agent_runs enable row level security;

create index if not exists agent_runs_feedback_started_idx
    on public.agent_runs (feedback_id, started_at desc);

create or replace function public.claim_next_agent_feedback(
    p_claim_token uuid,
    p_lease_seconds integer,
    p_max_attempts integer
)
returns setof public.feedback
language plpgsql
security definer
-- SECURITY DEFINER 必须固定 search_path，防止同名对象劫持。
set search_path = public
as $$
begin
    if p_lease_seconds < 1 or p_max_attempts < 1 then
        raise exception 'claim limits must be positive';
    end if;

    update public.feedback
    set status = 'needs_human',
        claimed_at = null,
        claim_token = null,
        last_error_code = 'claim_attempts_exhausted',
        last_error_message = 'claim lease expired after maximum attempts',
        updated_at = now()
    where (
          status = 'pending'
          or (
              status = 'claimed'
              and claimed_at <= now() - make_interval(secs => p_lease_seconds)
          )
      )
      and attempt_count >= p_max_attempts;

    -- SKIP LOCKED 使并发 Controller 各自领取不同记录，不互相等待或重复领取。
    return query
    with candidate as (
        select id
        from public.feedback
        where attempt_count < p_max_attempts
          and (
              status = 'pending'
              or (
                  status = 'claimed'
                  and claimed_at <= now() - make_interval(secs => p_lease_seconds)
              )
          )
        order by created_at, id
        for update skip locked
        limit 1
    )
    update public.feedback as feedback
    set status = 'claimed',
        attempt_count = feedback.attempt_count + 1,
        claimed_at = now(),
        claim_token = p_claim_token,
        last_error_code = null,
        last_error_message = null,
        updated_at = now()
    from candidate
    where feedback.id = candidate.id
    returning feedback.*;
end;
$$;

-- 领取 RPC 只授权 service_role，浏览器使用的 anon/authenticated 均不得调用。
revoke execute on function public.claim_next_agent_feedback(uuid, integer, integer)
    from public, anon, authenticated;
grant execute on function public.claim_next_agent_feedback(uuid, integer, integer)
    to service_role;
