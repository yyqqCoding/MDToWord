# 阶段 01:基线验证 + Supabase 迁移

## 目标

确认现有后端可安装、测试、转换(后续失败才能归因);扩展 `feedback` 表、
新增 `agent_runs` 表与原子领取 RPC,使任务可领取、可追踪、可重试、**不会卡死**。

## 前置依赖

- 无(第一个阶段);在新分支 `feat/feedback-repair-agent` 上开发;
- Supabase 项目控制台访问权限。

## 交付物

```text
supabase/migrations/20260710_feedback_repair_agent.sql
(基线记录:当前测试数量与 commit SHA,写入本文件底部"验收记录")
```

## 实施内容

### 1. 后端基线

```powershell
cd backend
uv venv .venv
uv pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -v
```

再跑最小转换脚本确认 Pandoc 可用(`convert_markdown_to_docx` 输出以 `PK` 开头)。

### 2. 数据库迁移

向后兼容说明:现有 `/feedback` 只写 `id / feedback_type / markdown_content /
description / contact`,不写 `status`;本迁移给 `status` 设 `default 'pending'`,
旧写入路径自动落到 `pending`,无需改后端。

```sql
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
```

### 3. 原子领取 RPC(含超时回收与重试上限)

与原始文档的差异:① 卡在 `claimed` 超过 2 小时的记录可被重新领取
(workflow 被取消导致 finalize 未跑时的自愈路径);② `attempt_count`
上限直接在 SQL 层校验,不只依赖 Python。

```sql
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
```

权限:限制 RPC 调用角色,不开放匿名(`revoke execute ... from anon;`)。
密钥使用规则见 [security-policy.md §9](00-overview/security-policy.md)。

### 4. 测试数据

```sql
insert into public.feedback (id, feedback_type, markdown_content, description, status, agent_approved)
values (gen_random_uuid(), 'bug',
        '# 测试反馈' || chr(10) || chr(10) || '这里放一个已知可复现的 Markdown 样例',
        'Agent 开发测试,请勿当作真实用户反馈', 'pending', false);
```

## 验收清单

- [x] 后端全量测试通过 —— `cd backend; .venv\Scripts\python.exe -m pytest -q`,预期 exit 0,记录用例数;
- [x] 最小转换脚本输出 DOCX,文件头 `PK`,Word 能打开;
- [ ] `feedback` 新字段存在 —— Supabase 控制台或 `select column_name from information_schema.columns where table_name='feedback';`;
- [ ] `agent_runs` 表存在;
- [ ] `claim_feedback` 首次调用返回记录,同一反馈第二次调用返回空;
- [ ] 手工把测试记录改回 `claimed` 且 `claimed_at` 设为 3 小时前,再次调用可领取(超时回收生效);
- [ ] `attempt_count` 达到 `p_max_attempts` 后调用返回空;
- [ ] 匿名角色调用 RPC 被拒绝;
- [x] 基线 commit SHA 与测试数量已记录到下方"验收记录"。

## 状态

进行中(本地基线完成,待在 Supabase 控制台执行迁移并验证 RPC)

## 验收记录

- 日期:2026-07-26;执行:本地(Windows,uv + CPython 3.12.10)
- 基线 commit:`27f7978`(main);开发分支:`feat/feedback-repair-agent`
- 后端全量测试:**42 passed**(pytest -q,exit 0)
- 基线修正:原 `.venv` 为 Linux 结构不可用,已重建;
  `test_pandoc_runner.py` 中两个测试依赖仓库外未跟踪文件 `logs/runlog.txt`
  (已被无关日志覆盖导致失败),改为使用新增 fixture
  `backend/tests/fixtures/sample_three_line_table.md`
- 最小转换:PK 头 / `w:tbl` / `m:oMath` 节点均通过(10938 bytes)
- 迁移文件已创建:`supabase/migrations/20260710_feedback_repair_agent.sql`
  (待在 Supabase SQL Editor 执行)
