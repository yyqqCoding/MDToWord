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

/**
 * 展示时区固定为 Asia/Shanghai，不跟随访问者本地时区。
 *
 * 原实现用 date.getHours() 这类本地时区取值器，在 Vercel 上「本地」就是 UTC：
 *   - RunTable 是服务端组件，只在服务端算一次 → 列表页永远显示 UTC，差 8 小时
 *   - RunDetail 是客户端组件，服务端渲 UTC、水合时变本地 → hydration 不匹配 + 闪烁
 * 跟随访问者时区就必然带上这个不确定性，只能靠仅客户端渲染规避，代价是首屏闪烁。
 * 这是面向中文读者的展示站，固定 UTC+8 既确定又符合预期，服务端与客户端结果一致。
 */
export const SITE_TIME_ZONE = "Asia/Shanghai";
export const SITE_TIME_ZONE_LABEL = "UTC+8";

const PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: SITE_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function zoned(date: Date): Record<string, string> {
  return Object.fromEntries(
    PARTS.formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "进行中";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "时间无效";
  const p = zoned(date);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** 带秒与时区的完整形式，用于 title 悬停，正文不铺开。 */
export function formatDateTimeFull(iso: string | null): string {
  if (!iso) return "进行中";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "时间无效";
  const p = zoned(date);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} (${SITE_TIME_ZONE_LABEL})`;
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
