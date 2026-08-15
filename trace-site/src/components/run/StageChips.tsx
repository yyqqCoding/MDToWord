"use client";

import clsx from "clsx";
import { Check, CircleDashed, Loader, Minus, X } from "lucide-react";
import { formatDuration } from "@/lib/format";
import type { StageState, StageView } from "@/lib/run-graph";

const STATE_STYLE: Record<
  StageState,
  {
    box: string;
    hover: string;
    icon: typeof Check;
    iconColor: string;
    word: string;
  }
> = {
  done: {
    box: "border-accent/45 bg-accent/8",
    hover: "hover:border-accent hover:bg-accent/18",
    icon: Check,
    iconColor: "text-accent",
    word: "已完成",
  },
  failed: {
    box: "border-critical/50 bg-critical/10",
    hover: "hover:border-critical hover:bg-critical/18",
    icon: X,
    iconColor: "text-critical",
    word: "失败",
  },
  active: {
    box: "border-accent/60 bg-accent/12",
    hover: "hover:border-accent hover:bg-accent/20",
    icon: Loader,
    iconColor: "text-accent",
    word: "进行中",
  },
  skipped: {
    box: "border-dashed border-line-strong",
    hover: "hover:border-line-strong hover:bg-raised/60",
    icon: Minus,
    iconColor: "text-ink-faint",
    word: "未执行",
  },
  pending: {
    box: "border-dashed border-line",
    hover: "hover:border-line-strong hover:bg-raised/60",
    icon: CircleDashed,
    iconColor: "text-ink-faint",
    word: "待执行",
  },
};

/**
 * 阶段芯片条。7 个阶段固定，布局手工排定。
 *
 * 悬停反馈对所有芯片生效（含未执行的），不因为某页没传 onSelect 就变成死块 ——
 * 概览页的芯片同样需要"鼠标划过即突出"的反馈。是否可点击只影响光标与点击行为。
 */
export function StageChips({
  stages,
  selectedKey,
  onSelect,
}: {
  stages: StageView[];
  selectedKey?: string | null;
  onSelect?: (key: string) => void;
}) {
  return (
    <ol className="flex flex-col gap-2 xl:flex-row xl:items-stretch xl:gap-0">
      {stages.map((stage, index) => {
        const style = STATE_STYLE[stage.state];
        const Icon = style.icon;
        // 任何阶段都可选中查看详情，包括未执行的 —— 未执行本身也是需要解释的结果
        const clickable = Boolean(onSelect);
        const selected = selectedKey === stage.key;

        return (
          <li
            key={stage.key}
            className="anim-rise flex min-w-0 flex-1 items-center"
            style={{ animationDelay: `${index * 55}ms` }}
          >
            <button
              type="button"
              onClick={() => onSelect?.(stage.key)}
              aria-pressed={selected || undefined}
              className={clsx(
                "lift group h-full w-full min-w-0 rounded-lg border px-3.5 py-3 text-left",
                style.box,
                style.hover,
                clickable ? "cursor-pointer" : "cursor-default",
                selected && "glow-strong border-accent bg-accent/20",
              )}
            >
              <div className="flex items-center gap-2">
                <Icon
                  aria-hidden
                  className={clsx(
                    "size-4 shrink-0 transition-transform duration-200 group-hover:scale-110",
                    style.iconColor,
                    stage.state === "active" && "animate-spin",
                  )}
                />
                <span className="truncate text-sm font-medium text-ink">{stage.label}</span>
              </div>
              <div className="mt-1 flex items-baseline gap-2 pl-6">
                {stage.durationMs !== null && (
                  <span className="font-mono text-sm text-ink-muted transition-colors duration-200 group-hover:text-ink">
                    {formatDuration(stage.durationMs)}
                  </span>
                )}
                {/* 重试后成功是真实运行的常态，明说比隐藏更有说服力 */}
                {stage.retries > 0 && (
                  <span className="whitespace-nowrap text-xs text-warn">
                    重试 {stage.retries} 次
                  </span>
                )}
              </div>
              <span className="sr-only">状态：{style.word}</span>
            </button>

            {index < stages.length - 1 && (
              <span
                aria-hidden
                className="hidden h-px w-4 shrink-0 self-center bg-line-strong xl:block"
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
