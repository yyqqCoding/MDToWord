import { describeOutcome } from "@/lib/run-graph";
import type { Tone } from "@/lib/run-graph";
import type { RunListItem } from "@/lib/types";

/**
 * 运行列表筛选。分组不发明第二套终态口径 —— 直接沿用 describeOutcome 的
 * tone 归类（设计决定见 docs/WebRequirements/trace-site-ui-design.md）：
 *   good     已建 PR
 *   warn     未修复（无法复现、预算耗尽、基线过期）
 *   serious/critical  失败 · 拦截（安全拦截、运行失败）
 *   neutral  其他结论（无关反馈、超出范围、重复、已结束、已取消）
 *   accent   进行中
 */

export type RunFilterKey =
  | "all"
  | "pr"
  | "unfixed"
  | "failed"
  | "neutral"
  | "active";

export const RUN_FILTERS: ReadonlyArray<{ key: RunFilterKey; label: string }> = [
  { key: "all", label: "全部" },
  { key: "pr", label: "已建 PR" },
  { key: "unfixed", label: "未修复" },
  { key: "failed", label: "失败 · 拦截" },
  { key: "neutral", label: "其他结论" },
  { key: "active", label: "进行中" },
];

const TONE_TO_GROUP: Record<Tone, Exclude<RunFilterKey, "all">> = {
  good: "pr",
  warn: "unfixed",
  serious: "failed",
  critical: "failed",
  neutral: "neutral",
  accent: "active",
};

export function filterGroupOf(item: RunListItem): Exclude<RunFilterKey, "all"> {
  return TONE_TO_GROUP[describeOutcome(item).tone];
}

/** 是否同时满足分组与搜索词（run_ref / 标题 / 类别的子串匹配，大小写不敏感）。 */
export function matchRunFilter(
  item: RunListItem,
  group: RunFilterKey,
  query: string,
): boolean {
  if (group !== "all" && filterGroupOf(item) !== group) return false;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    item.run_ref.toLowerCase().includes(q) ||
    item.title.toLowerCase().includes(q) ||
    (item.category ?? "").toLowerCase().includes(q)
  );
}
