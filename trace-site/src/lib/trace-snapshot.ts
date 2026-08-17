import type { Observation, RunTrace } from "@/lib/types";

/** Trace 合成根以下的 observation 数量。 */
export function traceObservationCount(root: Observation): number {
  return root.children.reduce(
    (total, child) => total + 1 + traceObservationCount(child),
    0,
  );
}

/**
 * 快照能否代表运行摘要里的调用事实。
 *
 * 零调用运行允许只有合成根；有模型或工具调用的运行若没有任何 observation 明细，
 * 说明抓取发生在 Langfuse 根节点完成索引之前，必须重新抓取而不是永久命中坏缓存。
 */
export function isTraceSnapshotUsable(
  trace: RunTrace | null | undefined,
  expectedCalls: number,
): trace is RunTrace {
  if (!trace) return false;
  return expectedCalls <= 0 || traceObservationCount(trace.root) > 0;
}
