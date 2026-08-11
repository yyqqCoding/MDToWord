-- 阶段 D：把源码准备与自动复现中的运行纳入可恢复索引。
-- PostgreSQL 的状态列没有枚举约束；领域层和条件更新继续负责状态机校验。

drop index if exists public.agent_runs_resumable_idx;

create index agent_runs_resumable_idx
    on public.agent_runs (status, started_at)
    where status in ('created', 'gating', 'preparing_source', 'reproducing');
