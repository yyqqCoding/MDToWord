import "server-only";

import { langfuseConfig, supabaseConfig } from "@/lib/server/env";
import { fetchTrace } from "@/lib/server/langfuse";
import { selectTraceIdForRun, upsertTrace } from "@/lib/server/supabase";
import type { RunTrace } from "@/lib/types";

/**
 * 单条运行的 Trace 快照抓取。
 *
 * 两个调用方共用这一份实现：
 *   /api/hooks/run-finished  —— Agent 跑完立刻推送，带重试
 *   getRunDetail             —— 访问详情页时发现没快照就顺手补一次，不重试
 *
 * 之所以要重试：Langfuse 的写入是异步的。Agent 在推送前会 flush，
 * 但服务端索引仍有秒级延迟，第一次 GET 常常还查不到。
 */

export type CaptureResult =
  | { status: "captured"; trace: RunTrace }
  /** Langfuse 里查不到。可能还没索引完，也可能是 fail-open 上报本就丢了。 */
  | { status: "missing" }
  /** 数据源未配置，或调用过程出错。 */
  | { status: "unavailable" };

/** 重试间隔，单位毫秒。总等待 4 + 12 = 16 秒，留在 Vercel 函数 60 秒上限内。 */
const RETRY_DELAYS_MS = [4_000, 12_000];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function captureRunTrace(
  runId: string,
  options?: { retry?: boolean },
): Promise<CaptureResult> {
  if (!supabaseConfig || !langfuseConfig) return { status: "unavailable" };

  let traceId: string | null;
  try {
    traceId = await selectTraceIdForRun(runId);
  } catch {
    return { status: "unavailable" };
  }
  // Telemetry 完全没起来的运行库里就没有 trace_id，重试再多次也不会出现。
  if (!traceId) return { status: "missing" };

  const delays = options?.retry ? RETRY_DELAYS_MS : [];
  let errored = false;
  for (let attempt = 0; attempt <= delays.length; attempt += 1) {
    if (attempt > 0) await sleep(delays[attempt - 1]);
    try {
      const trace = await fetchTrace(traceId, runId);
      if (!trace) continue;
      await upsertTrace({
        run_id: runId,
        trace_id: traceId,
        trace_json: trace,
        source: "langfuse_api",
      });
      return { status: "captured", trace };
    } catch {
      // 不回显异常内容：可能带 host 或观测细节。
      // 抛错和 404 要分开：前者是调用失败，后者是「确实还没有这条 trace」。
      errored = true;
    }
  }
  return { status: errored ? "unavailable" : "missing" };
}
