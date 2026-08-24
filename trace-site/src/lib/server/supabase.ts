import "server-only";

import { supabaseConfig } from "@/lib/server/env";
import type { RunTrace } from "@/lib/types";

/**
 * Supabase PostgREST 访问。
 *
 * 通用查询路径（request）只允许两个对象：
 *   public.agent_run_public   —— 字段级白名单视图（006 migration）
 *   public.agent_run_traces   —— Trace 快照表
 *
 * 基表 public.agent_runs 只能通过本文件末尾那两个固定形状的函数读取，
 * 且只取 id 与 langfuse_trace_id —— 详见各自的注释。
 *
 * 绝不触达 public.feedback：其 contact 与 markdown_content 是用户内容。
 * 使用 service_role 是因为这些对象都不对 anon / authenticated 授权；
 * 密钥只在服务端，客户端拿到的永远是已成型的 DTO。
 */

const ALLOWED_RESOURCES = new Set(["agent_run_public", "agent_run_traces"]);

interface RequestOptions {
  revalidate?: number | false;
  tags?: readonly string[];
}

async function request<T>(
  resource: string,
  query: string,
  init?: RequestOptions,
): Promise<T[]> {
  if (!supabaseConfig) throw new Error("supabase is not configured");
  if (!ALLOWED_RESOURCES.has(resource)) {
    // 兜底防线：即使将来有人手滑写了别的表名，也在这里挡住。
    throw new Error(`resource ${resource} is not allowed`);
  }

  const response = await fetch(`${supabaseConfig.url}/rest/v1/${resource}?${query}`, {
    headers: {
      apikey: supabaseConfig.key,
      Authorization: `Bearer ${supabaseConfig.key}`,
      Accept: "application/json",
    },
    next:
      init?.revalidate === false
        ? undefined
        : {
            revalidate: init?.revalidate ?? 300,
            tags: init?.tags ? [...init.tags] : undefined,
          },
    cache: init?.revalidate === false ? "no-store" : undefined,
    signal: AbortSignal.timeout(4000),
  });

  if (!response.ok) {
    // 不回显响应体：PostgREST 的错误信息可能带上查询串。
    throw new Error(`supabase ${resource} responded ${response.status}`);
  }
  return (await response.json()) as T[];
}

export async function selectRuns<T>(
  query: string,
  options?: RequestOptions,
): Promise<T[]> {
  return request<T>("agent_run_public", query, options);
}

export async function selectTraces<T>(
  query: string,
  options?: RequestOptions,
): Promise<T[]> {
  return request<T>("agent_run_traces", query, options);
}

export async function upsertTrace(row: {
  run_id: string;
  trace_id: string;
  trace_json: unknown;
  source: string;
}): Promise<void> {
  if (!supabaseConfig) throw new Error("supabase is not configured");
  const response = await fetch(`${supabaseConfig.url}/rest/v1/agent_run_traces`, {
    method: "POST",
    headers: {
      apikey: supabaseConfig.key,
      Authorization: `Bearer ${supabaseConfig.key}`,
      "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates,return=minimal",
    },
    body: JSON.stringify(row),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`supabase trace upsert responded ${response.status}`);
  }
}

/**
 * 快照回填专用：读 agent_runs 的 id 与 langfuse_trace_id。
 *
 * trace_id 是公开视图刻意排除的禁区列，浏览器永远拿不到它。但回填任务必须用它去
 * 调 Langfuse，因此这里直接读基表。刻意写成没有查询参数的固定函数，而不是放宽
 * request() 的资源白名单 —— 保证「能读 agent_runs」这件事只存在于这一个入口，
 * 且列清单写死在代码里，不接受调用方传入。
 */
export async function selectPendingTraceIds(
  limit: number,
): Promise<
  {
    id: string;
    langfuse_trace_id: string | null;
    model_calls: number;
    tool_calls: number;
  }[]
> {
  if (!supabaseConfig) return [];
  const query = new URLSearchParams({
    select: "id,langfuse_trace_id,model_calls,tool_calls",
    langfuse_trace_id: "not.is.null",
    order: "started_at.desc",
    limit: String(limit),
  });
  const response = await fetch(
    `${supabaseConfig.url}/rest/v1/agent_runs?${query.toString()}`,
    {
      headers: {
        apikey: supabaseConfig.key,
        Authorization: `Bearer ${supabaseConfig.key}`,
        Accept: "application/json",
      },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(`supabase agent_runs responded ${response.status}`);
  }
  return (await response.json()) as {
    id: string;
    langfuse_trace_id: string | null;
    model_calls: number;
    tool_calls: number;
  }[];
}

/**
 * 单条运行的 langfuse_trace_id。
 *
 * 与 selectPendingTraceIds 同属基表读取的受限入口：完成回调和按需补抓都只关心一条运行，
 * 没必要拉整批。同样写成固定形状 —— 列清单写死，调用方只能传一个 UUID，
 * 且 UUID 格式在这里再校验一次，不依赖调用方。
 */
export async function selectTraceIdForRun(runId: string): Promise<string | null> {
  if (!supabaseConfig) return null;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)) {
    throw new Error("run id must be a uuid");
  }
  const query = new URLSearchParams({
    select: "langfuse_trace_id",
    id: `eq.${runId}`,
    limit: "1",
  });
  const response = await fetch(
    `${supabaseConfig.url}/rest/v1/agent_runs?${query.toString()}`,
    {
      headers: {
        apikey: supabaseConfig.key,
        Authorization: `Bearer ${supabaseConfig.key}`,
        Accept: "application/json",
      },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(`supabase agent_runs responded ${response.status}`);
  }
  const rows = (await response.json()) as { langfuse_trace_id: string | null }[];
  return rows[0]?.langfuse_trace_id ?? null;
}

/** 已有快照及其投影，用于区分完整快照与需要重抓的空明细快照。 */
export async function selectTraceSnapshots(): Promise<Map<string, RunTrace>> {
  const rows = await selectTraces<{ run_id: string; trace_json: RunTrace }>(
    "select=run_id,trace_json",
    {
      revalidate: false,
    },
  );
  return new Map(rows.map((row) => [row.run_id, row.trace_json]));
}
