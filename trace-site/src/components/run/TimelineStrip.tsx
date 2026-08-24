"use client";

import { useMemo } from "react";
import clsx from "clsx";
import { formatDuration } from "@/lib/format";
import type { StageView } from "@/lib/run-graph";

const STAGE_BAR_COLORS: Record<string, { bg: string; text: string }> = {
  claim: { bg: "bg-accent/40 hover:bg-accent/70", text: "text-accent" },
  gate: { bg: "bg-accent/60 hover:bg-accent/90", text: "text-accent" },
  prepare: { bg: "bg-accent/50 hover:bg-accent/80", text: "text-accent" },
  reproduce: { bg: "bg-accent hover:bg-accent-dim", text: "text-accent" },
  repair: { bg: "bg-accent-dim hover:bg-accent", text: "text-accent" },
  validate: { bg: "bg-good/60 hover:bg-good", text: "text-good" },
  publish: { bg: "bg-good/80 hover:bg-good", text: "text-good" },
};

/**
 * 全流程耗时比例分布条 (Timeline Distribution Strip)。
 *
 * 贯穿整场运行的时序缩略图：按每个阶段的实际执行时长分配宽度占比，
 * 让访客一眼看出大头耗费在哪个环节（如复现占用 65%、修复占用 20%）。
 * 点击任意色块可直接平滑切换到该阶段详情。
 */
export function TimelineStrip({
  stages,
  activeKey,
  onSelect,
}: {
  stages: StageView[];
  activeKey: string | null;
  onSelect?: (key: string) => void;
}) {
  const executedStages = useMemo(
    () => stages.filter((s) => s.durationMs !== null && s.durationMs > 0),
    [stages],
  );

  const totalMs = useMemo(
    () => executedStages.reduce((sum, s) => sum + (s.durationMs ?? 0), 0),
    [executedStages],
  );

  if (executedStages.length === 0 || totalMs === 0) return null;

  return (
    <div className="anim-fade mb-5 rounded-xl border border-line bg-surface/80 p-3.5 backdrop-blur">
      <div className="mb-2 flex items-center justify-between text-xs text-ink-faint">
        <span className="flex items-center gap-1.5 font-medium">
          <span className="size-1.5 rounded-full bg-accent animate-pulse" />
          全流程耗时分布 (总耗时 {formatDuration(totalMs)})
        </span>
        <span className="font-mono text-xs text-ink-muted">
          点击分段快速定位
        </span>
      </div>

      {/* 比例条容器 */}
      <div className="flex h-3 w-full overflow-hidden rounded-md bg-raised/80 p-0.5 gap-0.5">
        {executedStages.map((stage) => {
          const duration = stage.durationMs ?? 0;
          // 计算占比，最小保留 4% 宽度保证微小阶段依然可点
          const rawPct = (duration / totalMs) * 100;
          const flexGrow = Math.max(rawPct, 4);
          const color = STAGE_BAR_COLORS[stage.key] ?? {
            bg: "bg-accent/60",
            text: "text-accent",
          };
          const isActive = stage.key === activeKey;

          return (
            <button
              key={stage.key}
              type="button"
              onClick={() => onSelect?.(stage.key)}
              title={`${stage.label}: ${formatDuration(duration)} (${rawPct.toFixed(1)}%)`}
              style={{ flex: `${flexGrow} 0 0%` }}
              className={clsx(
                "group relative h-full rounded-sm transition-all duration-200 cursor-pointer outline-none",
                color.bg,
                isActive && "ring-2 ring-accent ring-offset-1 ring-offset-canvas brightness-125",
              )}
            >
              <span className="sr-only">
                {stage.label}: {formatDuration(duration)}
              </span>
            </button>
          );
        })}
      </div>

      {/* 阶段标签与耗时分布 */}
      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        {executedStages.map((stage) => {
          const duration = stage.durationMs ?? 0;
          const pct = ((duration / totalMs) * 100).toFixed(0);
          const isActive = stage.key === activeKey;
          return (
            <button
              key={stage.key}
              type="button"
              onClick={() => onSelect?.(stage.key)}
              className={clsx(
                "inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 transition-colors cursor-pointer text-left",
                isActive
                  ? "bg-accent/15 font-medium text-accent"
                  : "text-ink-faint hover:text-ink hover:bg-raised/60",
              )}
            >
              <span
                className={clsx(
                  "size-1.5 rounded-full",
                  stage.state === "failed"
                    ? "bg-critical"
                    : stage.state === "done"
                      ? "bg-accent"
                      : "bg-ink-faint",
                )}
              />
              <span>{stage.label}</span>
              <span className="font-mono text-ink-muted">
                {formatDuration(duration)}
              </span>
              <span className="font-mono text-[11px] text-ink-faint/80">
                ({pct}%)
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
