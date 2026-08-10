-- 阶段 B2：Gate 运行恢复元数据与私有 LangGraph checkpoint Schema。
-- 本文件不创建第三方 checkpoint 表；执行后再显式运行 checkpoint setup 命令。

create schema if not exists agent_runtime;
revoke all on schema agent_runtime from public, anon, authenticated;

-- 防止未来由 migration owner 创建的表意外继承 PUBLIC 权限。
alter default privileges in schema agent_runtime
    revoke all on tables from public, anon, authenticated;

alter table public.agent_runs
    add column if not exists claim_token uuid,
    add column if not exists trace_id text,
    add column if not exists route text,
    add column if not exists category text,
    add column if not exists dry_run boolean not null default true,
    add column if not exists task_artifact_ref text;

create index if not exists agent_runs_resumable_idx
    on public.agent_runs (status, started_at)
    where status in ('created', 'gating');

create or replace function public.claim_agent_feedback(
    p_feedback_id uuid,
    p_claim_token uuid,
    p_lease_seconds integer,
    p_max_attempts integer
)
returns setof public.feedback
language plpgsql
security definer
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
    where id = p_feedback_id
      and attempt_count >= p_max_attempts
      and (
          status = 'pending'
          or (
              status = 'claimed'
              and claimed_at <= now() - make_interval(secs => p_lease_seconds)
          )
      );

    return query
    with candidate as (
        select id
        from public.feedback
        where id = p_feedback_id
          and attempt_count < p_max_attempts
          and (
              status = 'pending'
              or (
                  status = 'claimed'
                  and claimed_at <= now() - make_interval(secs => p_lease_seconds)
              )
          )
        for update skip locked
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

revoke execute on function public.claim_agent_feedback(uuid, uuid, integer, integer)
    from public, anon, authenticated;
grant execute on function public.claim_agent_feedback(uuid, uuid, integer, integer)
    to service_role;
