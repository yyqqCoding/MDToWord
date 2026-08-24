import type { OverviewStats, RunStatus } from "@/lib/types";

interface OverviewRow {
  status: RunStatus;
  pr_url: string | null;
  total_tokens: number;
  started_at: string;
  finished_at: string | null;
}

const TERMINAL = new Set<RunStatus>([
  "completed",
  "failed",
  "cancelled",
  "budget_exhausted",
  "security_rejected",
  "stale_base",
]);

export function calculateOverviewStats(rows: OverviewRow[]): OverviewStats {
  const durations = rows.flatMap((row) => {
    if (!TERMINAL.has(row.status) || !row.finished_at) return [];
    const duration = Date.parse(row.finished_at) - Date.parse(row.started_at);
    return Number.isFinite(duration) && duration >= 0 ? [duration] : [];
  });

  return {
    totalRuns: rows.length,
    pullRequests: new Set(
      rows.map((row) => row.pr_url).filter((url): url is string => Boolean(url)),
    ).size,
    averageDurationMs:
      durations.length > 0
        ? Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length)
        : 0,
    totalTokens: rows.reduce((sum, row) => sum + (row.total_tokens ?? 0), 0),
  };
}
