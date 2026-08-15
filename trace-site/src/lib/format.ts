/** 展示层格式化。所有函数必须对 null / 缺失值给出明确文案，不返回空字符串。 */

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export function formatTokens(value: number): string {
  if (value < 1000) return String(value);
  return `${(value / 1000).toFixed(1)}k`;
}

export function formatInteger(value: number): string {
  return value.toLocaleString("en-US");
}

/**
 * estimated_cost 为 0 时必须显示"未配置单价"而不是 $0.00。
 * 依据 docs/AgentRequirements/observability.md:103,218：单价未配置时数据库成本保持 0，
 * 这只表示未估算，不表示上游 API 免费。
 */
export function formatCost(raw: string): { text: string; unpriced: boolean } {
  const value = Number(raw);
  if (!Number.isFinite(value) || value === 0) {
    return { text: "未配置单价", unpriced: true };
  }
  return { text: `$${value.toFixed(4)}`, unpriced: false };
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "进行中";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "时间无效";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function runDurationMs(startedAt: string, finishedAt: string | null): number | null {
  if (!finishedAt) return null;
  const start = new Date(startedAt).getTime();
  const end = new Date(finishedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return end - start;
}

/** 长哈希只展示头尾，完整值放进 title 供复制。 */
export function shortHash(value: string | null, head = 10): string {
  if (!value) return "—";
  if (value.length <= head + 4) return value;
  return `${value.slice(0, head)}…`;
}
