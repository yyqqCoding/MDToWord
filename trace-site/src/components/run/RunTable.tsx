"use client";

import Link from "next/link";
import { Reveal } from "@/components/ui/reveal";
import { StatusBadge } from "@/components/ui/badge";
import { describeOutcome } from "@/lib/run-graph";
import { categoryTitle } from "@/content/cases";
import {
  formatDateTime,
  formatDateTimeFull,
  formatDuration,
  formatInteger,
} from "@/lib/format";
import type { RunListItem } from "@/lib/types";

/**
 * 运行列表。6 列。
 *
 * 参考稿里的 FEEDBACK ID 是展示禁区（改用不可逆的 run_ref）；
 * COST 恒为 0（未配置单价），做成列只会是一整列空值；
 * RETRIES 存放在 feedback 表，不在公开视图里。三列都没有做。
 */
export function RunTable({ runs }: { runs: RunListItem[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[56rem] border-collapse">
        <thead>
          <tr className="border-b border-line text-left">
            {["RUN REF", "反馈", "类别", "终态", "耗时", "TOKEN"].map((label) => (
              <th
                key={label}
                className="px-5 py-3 text-xs font-medium tracking-wide text-ink-faint"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((item, index) => {
            const outcome = describeOutcome(item);
            return (
              /* 行用纯淡入（不动 transform），错峰延迟封顶，避免深处
                 的行滚入视口后还要等一长串 index 延迟 */
              <Reveal
                as="tr"
                key={item.id}
                variant="fade"
                delay={Math.min(index * 30, 240)}
                className="group row-hover relative border-b border-line/60 hover:bg-raised/70"
              >
                <td className="relative px-5 py-3.5">
                  {/* 悬停时左侧主色指示条从中间展开 */}
                  <span
                    aria-hidden
                    className="absolute left-0 top-1/2 h-0 w-0.5 -translate-y-1/2 rounded-r bg-accent transition-all duration-200 group-hover:h-3/5"
                  />
                  <Link
                    href={`/runs/${item.id}`}
                    className="font-mono text-sm text-accent transition-colors duration-200 group-hover:text-ink"
                  >
                    {item.run_ref}
                  </Link>
                </td>
                <td className="max-w-sm px-5 py-3.5">
                  <Link href={`/runs/${item.id}`} className="block">
                    <span className="line-clamp-1 text-sm text-ink">{item.title}</span>
                    <span
                      className="text-xs text-ink-faint transition-colors duration-200 group-hover:text-ink-muted"
                      title={formatDateTimeFull(item.started_at)}
                    >
                      {formatDateTime(item.started_at)}
                    </span>
                  </Link>
                </td>
                <td className="px-5 py-3.5">
                  <span className="font-mono text-sm text-ink-muted">
                    {categoryTitle(item.category, item.area)}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <StatusBadge tone={outcome.tone}>{outcome.label}</StatusBadge>
                </td>
                <td className="px-5 py-3.5 font-mono text-sm text-ink-muted transition-colors duration-200 group-hover:text-ink">
                  {item.durationMs !== null ? formatDuration(item.durationMs) : "—"}
                </td>
                <td className="px-5 py-3.5 font-mono text-sm text-ink-muted transition-colors duration-200 group-hover:text-ink">
                  {formatInteger(item.total_tokens)}
                </td>
              </Reveal>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
