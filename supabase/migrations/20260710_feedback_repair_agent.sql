-- =============================================================================
-- MD To Word 反馈自动修复 Agent — 阶段 01 数据库迁移
-- 规格:docs/AgentRequirements/01-foundation.md
--
-- 向后兼容:现有 /feedback 只写 id / feedback_type / markdown_content /
-- description / contact,不写 status;status 默认 'pending',旧写入路径
-- 自动落到 pending,无需改后端。
-- =============================================================================

-- 1. feedback 表扩列 --------------------------------------------------------

alter table public.feedback
  add column if not exists status text not null default 'pending',
  add column if not exists category text,
  add column if not exists automatable boolean,
  add column if not exists agent_approved boolean not null default false,
  add column if not exists expected_behavior text,
  add column if not exists content_fingerprint text,
  -- 预留:插件与 /feedback 尚未采集版本号,短期恒 null
  add column if not exists source_version text,
  add column if not exists attempt_count integer not null default 0,
  add column if not exists claimed_at timestamptz,
  add column if not exists claim_token uuid,
  add column if not exists last_error text,
  add column if not exists resolution_type text,
  add column if not exists pr_url text,
  add column if not exists resolved_at timestamptz,
  add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_feedback_status_created_at
  on public.feedback(status, created_at);
create index if not exists idx_feedback_fingerprint
  on public.feedback(content_fingerprint);

-- 2. agent_runs 表 -----------------------------------------------------------

create table if not exists public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  feedback_id uuid not null references public.feedback(id),
  workflow_run_id text,
  provider text not null,
  model text not null,
  status text not null,
  classification jsonb,
  reproduction jsonb,
  validation_summary jsonb,
  stage_timings jsonb,              -- 阶段 09 使用:各阶段耗时与 token
  changed_files text[],
  patch_sha256 text,
  prompt_tokens integer,
  output_tokens integer,
  estimated_cost numeric(12, 6),
  pr_url text,
  error_code text,
  error_message text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_agent_runs_feedback_id
  on public.agent_runs(feedback_id, created_at desc);

-- agent_runs 仅供 Agent(service_role)读写,锁死匿名访问
alter table public.agent_runs enable row level security;

-- 3. 原子领取 RPC(含 2 小时超时回收 + SQL 层重试上限)------------------------

create or replace function public.claim_feedback(
  p_feedback_id uuid,
  p_claim_token uuid,
  p_max_attempts integer default 3
)
returns setof public.feedback
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  update public.feedback
  set status = 'claimed',
      claim_token = p_claim_token,
      claimed_at = now(),
      attempt_count = coalesce(attempt_count, 0) + 1,
      updated_at = now()
  where id = p_feedback_id
    and coalesce(attempt_count, 0) < p_max_attempts
    and (
      status in ('pending', 'approved', 'failed')
      or (status = 'claimed' and claimed_at < now() - interval '2 hours')
    )
  returning *;
end;
$$;

-- 4. 权限:RPC 不开放匿名/普通登录角色,仅 service_role 可调用 -----------------
-- (密钥使用规则见 docs/AgentRequirements/00-overview/security-policy.md §9)

revoke execute on function public.claim_feedback(uuid, uuid, integer) from public;
revoke execute on function public.claim_feedback(uuid, uuid, integer) from anon;
revoke execute on function public.claim_feedback(uuid, uuid, integer) from authenticated;
grant  execute on function public.claim_feedback(uuid, uuid, integer) to service_role;

-- =============================================================================
-- 测试数据(不属于迁移本身;首次验收时在 SQL Editor 手动执行一次):
--
-- insert into public.feedback
--   (id, feedback_type, markdown_content, description, status, agent_approved)
-- values
--   (gen_random_uuid(), 'bug',
--    '# 测试反馈' || chr(10) || chr(10) || '这里放一个已知可复现的 Markdown 样例',
--    'Agent 开发测试,请勿当作真实用户反馈', 'pending', false);
-- =============================================================================
