import { NextResponse } from "next/server";
import { cronSecret, langfuseConfig, supabaseConfig } from "@/lib/server/env";
import { fetchTrace } from "@/lib/server/langfuse";
import {
  selectPendingTraceIds,
  selectSnapshottedRunIds,
  upsertTrace,
} from "@/lib/server/supabase";

/**
 * Trace 快照批量回填。
 *
 * **不再有定时调度。** 常规路径是 Agent 跑完推 /api/hooks/run-finished，
 * 漏掉的由详情页按需补抓自愈；反馈量低时每天空跑一次 Cron 没有意义。
 *
 * 这个入口保留下来只用于手动批量回填：接入新数据、改了投影逻辑要重刷历史、
 * 或本地开发环境（没有推送）想一次性把已有运行的快照拉齐。
 * 触发方式是带 CRON_SECRET 手动请求。
 *
 * 写的是我们自己的快照表，不触碰任何 Agent 运行时状态。
 */

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const BATCH = 20;

export async function GET(request: Request) {
  if (!cronSecret) {
    return NextResponse.json({ error: "cron is not configured" }, { status: 503 });
  }

  // Vercel Cron 会带上 Authorization: Bearer $CRON_SECRET
  const authorized =
    request.headers.get("authorization") === `Bearer ${cronSecret}` ||
    request.headers.get("x-cron-secret") === cronSecret;
  if (!authorized) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  if (!supabaseConfig || !langfuseConfig) {
    return NextResponse.json({ error: "data sources are not configured" }, { status: 503 });
  }

  const [candidates, snapshotted] = await Promise.all([
    selectPendingTraceIds(BATCH * 3),
    selectSnapshottedRunIds(),
  ]);

  const pending = candidates
    .filter((row) => row.langfuse_trace_id && !snapshotted.has(row.id))
    .slice(0, BATCH);

  let captured = 0;
  const missing: string[] = [];
  const failed: string[] = [];

  for (const row of pending) {
    try {
      const trace = await fetchTrace(row.langfuse_trace_id!, row.id);
      if (!trace) {
        // Langfuse 里没有这条 trace。Telemetry 是 fail-open 的，上报失败不影响业务，
        // 但确定性 trace_id 仍会入库，所以这是预期内的常态，与真正的错误分开统计。
        missing.push(row.id);
        continue;
      }
      await upsertTrace({
        run_id: row.id,
        trace_id: row.langfuse_trace_id!,
        trace_json: trace,
        source: "langfuse_api",
      });
      captured += 1;
    } catch {
      // 不回显异常内容：可能带 host 或观测细节
      failed.push(row.id);
    }
  }

  return NextResponse.json({
    considered: pending.length,
    captured,
    missing: missing.length,
    failed: failed.length,
  });
}
