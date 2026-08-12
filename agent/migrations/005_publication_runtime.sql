-- 阶段 F：publishing checkpoint 可恢复；PR URL 使用阶段 A 已有列持久化。

drop index if exists public.agent_runs_resumable_idx;

create index agent_runs_resumable_idx
    on public.agent_runs (status, started_at)
    where status in (
        'created', 'gating', 'preparing_source', 'reproducing',
        'repairing', 'validating', 'publishing'
    );
