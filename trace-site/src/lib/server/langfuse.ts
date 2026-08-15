import "server-only";

import { langfuseConfig } from "@/lib/server/env";
import type { Observation, ObservationType, RunTrace } from "@/lib/types";

/**
 * Langfuse 公开 API 取数与裁剪。
 *
 * 只在快照回填任务里调用，正常访问路径读 Supabase 快照表，
 * 不把站点可用性绑在第三方观测平台的限流与可用性上。
 *
 * Langfuse 侧的内容已由 Agent 的 masking 回调脱敏（TRACE_CONTENT=false），
 * 但本模块仍做一次**结构白名单**投影：只取本站会渲染的字段，
 * 其余一律丢弃。写入快照的是投影结果，不是原始响应。
 */

interface LangfuseObservation {
  id?: string;
  parentObservationId?: string | null;
  name?: string | null;
  type?: string | null;
  startTime?: string | null;
  endTime?: string | null;
  completionStartTime?: string | null;
  level?: string | null;
  statusMessage?: string | null;
  model?: string | null;
  input?: unknown;
  output?: unknown;
  metadata?: unknown;
  usage?: { input?: number; output?: number; total?: number } | null;
  usageDetails?: Record<string, number> | null;
  promptTokens?: number | null;
  completionTokens?: number | null;
  totalTokens?: number | null;
}

interface LangfuseTrace {
  id?: string;
  name?: string | null;
  observations?: LangfuseObservation[] | null;
}

/** Langfuse 的 type 大小写与命名随版本变化，统一收敛到本站的四类。 */
function normalizeType(raw: string | null | undefined): ObservationType {
  const value = (raw ?? "").toLowerCase();
  if (value === "generation" || value === "embedding") return "generation";
  if (value === "tool" || value === "retriever") return "tool";
  if (value === "agent" || value === "chain") return "agent";
  return "span";
}

/** 只保留标量与浅层对象；深层结构与非白名单类型直接丢弃。 */
function projectSummary(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const out: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (item === null || item === undefined) continue;
    const kind = typeof item;
    if (kind === "string" || kind === "number" || kind === "boolean") {
      // 截断异常长的字符串，避免快照被单个字段撑大
      out[key] = kind === "string" ? (item as string).slice(0, 500) : item;
    } else if (kind === "object") {
      out[key] = JSON.parse(JSON.stringify(item).slice(0, 2000));
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function toMillis(start?: string | null, end?: string | null): number {
  if (!start || !end) return 0;
  const value = new Date(end).getTime() - new Date(start).getTime();
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function pickUsage(raw: LangfuseObservation) {
  if (raw.usage && typeof raw.usage.total === "number" && raw.usage.total > 0) {
    return {
      input: raw.usage.input ?? 0,
      output: raw.usage.output ?? 0,
      total: raw.usage.total,
    };
  }
  const details = raw.usageDetails;
  if (details) {
    const input = details.input ?? details.input_tokens ?? 0;
    const output = details.output ?? details.output_tokens ?? 0;
    const total = details.total ?? input + output;
    if (total > 0) return { input, output, total };
  }
  if (typeof raw.totalTokens === "number" && raw.totalTokens > 0) {
    return {
      input: raw.promptTokens ?? 0,
      output: raw.completionTokens ?? 0,
      total: raw.totalTokens,
    };
  }
  return undefined;
}

function metadataRunId(raw: LangfuseObservation): string | null {
  const meta = raw.metadata;
  if (typeof meta !== "object" || meta === null) return null;
  const value = (meta as Record<string, unknown>).run_id;
  return typeof value === "string" ? value : null;
}

/**
 * 把 Langfuse 的扁平 observation 列表转成本站的树。
 *
 * 三个实测出来的坑，都与文档描述不同：
 *
 * 1. **没有任何 observation 的 parentObservationId 是 null**。真正的根节点其父指向
 *    未随响应返回的 OTEL span，因此根的判定必须是「父 ID 不在本次返回的 ID 集合里」，
 *    而不是「父 ID 为空」。早期按后者判定，结果一条都识别不出根。
 * 2. **一条 Trace 里可能有多个根**。Controller 用确定性 Trace ID，同一次运行被
 *    checkpoint 恢复多次就会产生多个根，它们的 metadata.run_id 相同。
 *    站点按 run_id 取出属于本次运行的全部根，合并成一条时间轴。
 * 3. **结构是扁平的**。文档 §3 里的 gate-feedback / reproduce / repair 等分组节点
 *    实际并不存在，调用直接挂在根下。因此阶段分组由名称映射完成，不依赖树的层级。
 */
export function projectTrace(trace: LangfuseTrace, runId: string): RunTrace | null {
  const raw = (trace.observations ?? []).filter((item) => item.id && item.startTime);
  if (raw.length === 0) return null;

  const ids = new Set(raw.map((item) => item.id!));
  const rootsAll = raw.filter(
    (item) => !item.parentObservationId || !ids.has(item.parentObservationId),
  );
  const matched = rootsAll.filter((item) => metadataRunId(item) === runId);
  // 没有 run_id 元数据的历史 Trace：退回全部根，至少不丢数据
  const roots = matched.length > 0 ? matched : rootsAll;
  if (roots.length === 0) return null;

  const rootIds = new Set(roots.map((item) => item.id!));

  // 只保留从选中根可达的节点
  const kept = new Set(rootIds);
  let grew = true;
  while (grew) {
    grew = false;
    for (const item of raw) {
      const parent = item.parentObservationId;
      if (parent && kept.has(parent) && !kept.has(item.id!)) {
        kept.add(item.id!);
        grew = true;
      }
    }
  }

  const scoped = raw.filter((item) => kept.has(item.id!));
  const originMs = Math.min(...scoped.map((item) => new Date(item.startTime!).getTime()));
  const endMs = Math.max(
    ...scoped.map((item) =>
      item.endTime ? new Date(item.endTime).getTime() : new Date(item.startTime!).getTime(),
    ),
  );

  const nodes = new Map<string, Observation>();
  for (const item of scoped) {
    nodes.set(item.id!, {
      id: item.id!,
      name: item.name ?? "unnamed",
      type: normalizeType(item.type),
      startMs: Math.max(new Date(item.startTime!).getTime() - originMs, 0),
      durationMs: toMillis(item.startTime, item.endTime),
      status: (item.level ?? "").toUpperCase() === "ERROR" ? "error" : "success",
      errorCode: item.statusMessage ?? undefined,
      model: item.model ?? undefined,
      usage: pickUsage(item),
      input: projectSummary(item.input),
      output: projectSummary(item.output),
      metadata: projectSummary(item.metadata),
      children: [],
    });
  }

  // 多个根合并到一个合成根下；根自身不进入子列表，避免重复渲染
  const merged: Observation[] = [];
  for (const item of scoped) {
    const node = nodes.get(item.id!)!;
    if (rootIds.has(item.id!)) continue;
    const parent = item.parentObservationId ? nodes.get(item.parentObservationId) : undefined;
    if (parent && !rootIds.has(item.parentObservationId!)) {
      parent.children.push(node);
    } else {
      merged.push(node);
    }
  }
  merged.sort((a, b) => a.startMs - b.startMs);

  const first = nodes.get(roots[0].id!)!;
  const root: Observation = {
    id: `run-${runId}`,
    name: "feedback-repair-run",
    type: "agent",
    startMs: 0,
    durationMs: Math.max(endMs - originMs, 1),
    status: merged.some((item) => item.status === "error") ? "error" : "success",
    input: first.input,
    output: first.output,
    metadata: first.metadata,
    children: merged,
  };

  return {
    runId,
    totalDurationMs: root.durationMs,
    attempts: roots.length,
    root,
  };
}

export async function fetchTrace(traceId: string, runId: string): Promise<RunTrace | null> {
  if (!langfuseConfig) return null;
  const auth = Buffer.from(
    `${langfuseConfig.publicKey}:${langfuseConfig.secretKey}`,
  ).toString("base64");

  const response = await fetch(
    `${langfuseConfig.host}/api/public/traces/${encodeURIComponent(traceId)}`,
    {
      headers: { Authorization: `Basic ${auth}`, Accept: "application/json" },
      cache: "no-store",
    },
  );

  // 404 是常态而非异常：Agent 的 Telemetry 是 fail-open 的，上报失败不影响业务结果，
  // 但 Controller 仍会把确定性 trace_id 写进数据库。因此确实存在
  // 「库里有 trace_id、Langfuse 里没有这条 trace」的运行。
  if (response.status === 404) return null;

  if (!response.ok) {
    // 不回显响应体：可能带 host 或观测内容
    throw new Error(`langfuse trace fetch responded ${response.status}`);
  }
  return projectTrace((await response.json()) as LangfuseTrace, runId);
}
