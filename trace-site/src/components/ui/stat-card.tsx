"use client";

import clsx from "clsx";
import { CountUp } from "@/components/ui/count-up";
import { useReveal } from "@/components/ui/reveal";

/**
 * 关键数字卡。合并自概览页 Kpi 与运行详情 Stat —— 同一规则只留一份实现：
 * label + 数值（count 整数水合后滚动 / value 复合文案二选一）+ 可选注脚 +
 * 入场错峰延迟。数值统一 text-3xl，hover 边框统一 accent，
 * 不再允许两个调用点各自漂移。入场由 useReveal 接管（进入视口才播），
 * 动画类挂在本元素上，保持自身就是网格子项。
 */
export function StatCard({
  label,
  value,
  count,
  note,
  noteTitle,
  delay,
}: {
  label: string;
  /** 静态展示值（耗时、"+12 −3" 这类复合文案）；与 count 二选一。 */
  value?: string;
  /** 整数值：水合后从 0 滚到终值；无 JS 时直接渲染终值。 */
  count?: number;
  note?: string;
  noteTitle?: string;
  /** 入场动画延迟（毫秒），同屏多张卡片错峰出现。 */
  delay?: number;
}) {
  const reveal = useReveal<HTMLDivElement>({ delay });
  return (
    <div
      ref={reveal.ref}
      className={clsx(
        reveal.classes,
        "lift panel rounded-xl border border-line bg-surface px-5 py-4 hover:border-accent/40",
      )}
      style={reveal.style}
      onAnimationEnd={reveal.onAnimationEnd}
    >
      <p className="text-sm text-ink-faint">{label}</p>
      <p className="mt-2 font-mono text-3xl leading-none text-ink">
        {count !== undefined ? (
          <CountUp value={count} className="tabular-nums" />
        ) : (
          value
        )}
      </p>
      {note && (
        <p className="mt-1.5 text-sm text-ink-faint" title={noteTitle}>
          {note}
        </p>
      )}
    </div>
  );
}
