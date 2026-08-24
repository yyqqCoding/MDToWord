"use client";

import { useEffect, useState } from "react";
import { ArrowUp, CheckCircle, Code2, GitCommit, Layers, ShieldCheck } from "lucide-react";
import clsx from "clsx";
import { StatusBadge } from "@/components/ui/badge";
import type { StageView } from "@/lib/run-graph";
import type { RunPublic } from "@/lib/types";

export function StickyRunHeader({
  run,
  outcome,
  activeStage,
  hasDiff,
}: {
  run: RunPublic;
  outcome: { label: string; tone: "good" | "warn" | "serious" | "critical" | "neutral" | "accent" };
  activeStage?: StageView;
  hasDiff: boolean;
}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => {
      // 滚动超过 260px 展现吸顶状态栏
      if (window.scrollY > 260) {
        setVisible(true);
      } else {
        setVisible(false);
      }
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const scrollTo = (id: string) => {
    const el = document.getElementById(id);
    if (el) {
      const top = el.getBoundingClientRect().top + window.scrollY - 70;
      window.scrollTo({ top, behavior: "smooth" });
    }
  };

  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  if (!visible) return null;

  return (
    <div className="anim-slide-down fixed top-0 left-0 right-0 z-40 border-b border-line bg-canvas/90 px-5 py-2.5 backdrop-blur-md transition-all sm:px-8">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
        {/* 左侧：Run 基本信息与当前阶段 */}
        <div className="flex items-center gap-3 min-w-0">
          <span className="font-mono text-sm font-semibold text-accent">
            {run.run_ref}
          </span>
          <StatusBadge tone={outcome.tone}>{outcome.label}</StatusBadge>

          {activeStage && (
            <span className="hidden items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-0.5 text-xs text-ink-muted sm:inline-flex">
              <Layers className="size-3 text-accent" />
              当前阶段：<strong className="text-ink">{activeStage.label}</strong>
            </span>
          )}
        </div>

        {/* 右侧：快捷定位锚点 */}
        <div className="flex items-center gap-1.5 shrink-0 text-xs">
          <button
            type="button"
            onClick={() => scrollTo("section-stages")}
            className="hidden items-center gap-1 rounded-md px-2 py-1 text-ink-faint transition-colors hover:bg-raised hover:text-ink md:inline-flex cursor-pointer"
          >
            <GitCommit className="size-3.5" />
            阶段
          </button>

          {hasDiff && (
            <button
              type="button"
              onClick={() => scrollTo("section-diff")}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-ink-faint transition-colors hover:bg-raised hover:text-ink cursor-pointer"
            >
              <Code2 className="size-3.5" />
              代码
            </button>
          )}

          <button
            type="button"
            onClick={() => scrollTo("section-validation")}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-ink-faint transition-colors hover:bg-raised hover:text-ink cursor-pointer"
          >
            <ShieldCheck className="size-3.5" />
            验证
          </button>

          <button
            type="button"
            onClick={scrollToTop}
            title="回到顶部"
            className="inline-flex items-center justify-center size-7 rounded-md border border-line bg-surface text-ink-faint transition-colors hover:bg-raised hover:text-ink cursor-pointer"
          >
            <ArrowUp className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
