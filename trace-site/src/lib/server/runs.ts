import "server-only";

import { usingRealData } from "@/lib/server/env";
import { selectRuns, selectTraces } from "@/lib/server/supabase";
import { captureRunTrace } from "@/lib/server/capture";
import { fetchPullDiff } from "@/lib/server/github";
import { CASE_NARRATIVES, fallbackTitle } from "@/content/cases";
import { runDurationMs } from "@/lib/format";
import { isTerminalStatus } from "@/lib/run-graph";
import { getMockRun } from "@/lib/mock/hero-run";
import { mockOverviewStats, mockRunList } from "@/lib/mock/runs";
import type {
  OverviewStats,
  RunDetailData,
  RunListItem,
  RunPublic,
  RunTrace,
} from "@/lib/types";

/**
 * 页面唯一的数据入口。
 *
 * Supabase 未配置时整体回落到构造数据，站点仍可运行 —— 本地做视觉迭代不需要密钥。
 * 页面组件不感知数据来源，只消费 DTO；M2 到 M3 的切换因此没有改动任何页面。
 */

const LIST_COLUMNS =
  "id,run_ref,status,route,category,total_tokens,pr_url,started_at,finished_at";

interface TraceRow {
  run_id: string;
  trace_json: RunTrace;
}

function toListItem(run: RunPublic): RunListItem {
  return {
    id: run.id,
    run_ref: run.run_ref,
    title: CASE_NARRATIVES[run.id]?.title ?? fallbackTitle(run.category),
    route: run.route,
    category: run.category,
    status: run.status,
    durationMs: runDurationMs(run.started_at, run.finished_at),
    total_tokens: run.total_tokens,
    pr_url: run.pr_url,
    started_at: run.started_at,
  };
}

export async function getRunList(limit = 50): Promise<RunListItem[]> {
  if (!usingRealData) return mockRunList;
  const rows = await selectRuns<RunPublic>(
    `select=${LIST_COLUMNS}&order=started_at.desc&limit=${limit}`,
    { revalidate: 300 },
  );
  return rows.map(toListItem);
}

export async function getOverviewStats(): Promise<OverviewStats> {
  if (!usingRealData) return mockOverviewStats();
  const rows = await selectRuns<RunPublic>(
    `select=id,run_ref,status,route,category,total_tokens,pr_url,started_at,finished_at&order=started_at.desc&limit=500`,
    { revalidate: 900 },
  );

  const durations = rows
    .map((row) => runDurationMs(row.started_at, row.finished_at))
    .filter((value): value is number => value !== null);

  return {
    totalRuns: rows.length,
    pullRequests: rows.filter((row) => row.pr_url).length,
    averageDurationMs:
      durations.length > 0
        ? Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length)
        : 0,
    totalTokens: rows.reduce((sum, row) => sum + (row.total_tokens ?? 0), 0),
  };
}

export async function getRunDetail(id: string): Promise<RunDetailData | null> {
  if (!usingRealData) return getMockRun(id);

  // UUID 之外的入参直接拒绝，不让任意字符串进到查询串里
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    return null;
  }

  const [run] = await selectRuns<RunPublic>(`select=*&id=eq.${id}&limit=1`, {
    revalidate: false,
  });
  if (!run) return null;

  const [traceRows, diff] = await Promise.all([
    // 快照查询只能短缓存。快照本身不可变，但"尚未回填"是个会变的否定结果 ——
    // 曾经按终态给它 86400s，导致回填完成后页面整整一天仍显示"快照尚未回填"。
    selectTraces<TraceRow>(`select=run_id,trace_json&run_id=eq.${id}&limit=1`, {
      revalidate: 600,
    }).catch(() => [] as TraceRow[]),
    fetchPullDiff(run.pr_url).catch(() => null),
  ]);

  const snapshot = traceRows[0]?.trace_json ?? null;

  return {
    run,
    trace: snapshot ?? (await backfillTrace(run)),
    narrative: CASE_NARRATIVES[run.id] ?? null,
    diff,
    isMock: false,
  };
}

/**
 * 按需补抓：终态运行还没有快照时，当场抓一次写回。
 *
 * 正常路径是 Agent 跑完推 /api/hooks/run-finished。但推送是 at-most-once ——
 * 站点冷启动、部署中、网络抖动都会丢，而且本地开发根本没有推送。
 * 这里是那条链路的自愈兜底：谁访问谁修，不需要任何定时任务。
 *
 * 三条约束：
 *   - 只对终态运行做，进行中的运行 Trace 本来就不完整
 *   - 不重试，页面不能为了一条时间线多等十几秒
 *   - 任何失败都静默返回 null，页面照常用运行摘要推导阶段
 */
async function backfillTrace(run: RunPublic): Promise<RunTrace | null> {
  if (!isTerminalStatus(run.status)) return null;
  try {
    const result = await captureRunTrace(run.id);
    return result.status === "captured" ? result.trace : null;
  } catch {
    return null;
  }
}
