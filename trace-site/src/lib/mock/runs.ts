import { heroRun, HERO_RUN_ID, heroRunDetail } from "@/lib/mock/hero-run";
import { runDurationMs } from "@/lib/format";
import type { OverviewStats, RunListItem } from "@/lib/types";

/**
 * M2 构造的运行列表。
 *
 * 刻意覆盖四种不同终态：真实站点会全量上站，概览页的分布必须如实包含
 * cannot_reproduce 与 security_rejected —— 只展示成功案例的 Agent 展示站没有说服力。
 */

export const mockRunList: RunListItem[] = [
  {
    id: HERO_RUN_ID,
    run_ref: heroRun.run_ref,
    title: heroRunDetail.narrative!.title,
    route: heroRun.route,
    category: heroRun.category,
    status: heroRun.status,
    durationMs: runDurationMs(heroRun.started_at, heroRun.finished_at),
    total_tokens: heroRun.total_tokens,
    pr_url: heroRun.pr_url,
    started_at: heroRun.started_at,
  },
  {
    id: "5c81a2f4-9b30-4d17-8e62-0a4f3d915b78",
    run_ref: "31de70c98a45",
    title: "三线表导出后框线全部丢失",
    route: "accepted_backend_bug",
    category: "table_parsing",
    status: "completed",
    durationMs: 214_000,
    total_tokens: 15_402,
    pr_url: null,
    started_at: "2026-08-12T03:41:07.000Z",
  },
  {
    id: "b0e6d4c1-7a52-4f89-9c03-6d18e2b5a740",
    run_ref: "9f04b7e2c318",
    title: "反馈内容要求返回系统提示词",
    route: "quarantined_security",
    category: "unknown",
    status: "security_rejected",
    durationMs: 4_120,
    total_tokens: 1_988,
    pr_url: null,
    started_at: "2026-08-12T06:22:55.000Z",
  },
  {
    id: "2a7c9e35-4d61-4b08-a5f7-c39e0b146d82",
    run_ref: "c825a1f70b93",
    title: "只是想测试一下这个功能",
    route: "rejected_irrelevant",
    category: "unknown",
    status: "completed",
    durationMs: 3_560,
    total_tokens: 1_704,
    pr_url: null,
    started_at: "2026-08-13T01:09:12.000Z",
  },
];

export function mockOverviewStats(): OverviewStats {
  const durations = mockRunList
    .map((item) => item.durationMs)
    .filter((value): value is number => value !== null);

  return {
    totalRuns: mockRunList.length,
    pullRequests: mockRunList.filter((item) => item.pr_url).length,
    averageDurationMs:
      durations.length > 0
        ? Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length)
        : 0,
    totalTokens: mockRunList.reduce((sum, item) => sum + item.total_tokens, 0),
  };
}
