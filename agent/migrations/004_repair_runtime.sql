-- 阶段 E：保存修复摘要，并把修复与验证节点纳入可恢复运行索引。

alter table public.agent_runs
    add column if not exists repair jsonb;

drop index if exists public.agent_runs_resumable_idx;

create index agent_runs_resumable_idx
    on public.agent_runs (status, started_at)
    where status in (
        'created', 'gating', 'preparing_source', 'reproducing',
        'repairing', 'validating'
    );
