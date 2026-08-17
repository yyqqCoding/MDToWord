"use client";

import { useEffect, useRef, useState } from "react";
import { formatInteger } from "@/lib/format";

/**
 * 整数 count-up。配方来自 luster/assets/motion.mjs 的 initCountUps。
 *
 * SSR 与无 JS 环境直接渲染终值（内容完整，不设隐藏初始态）；
 * 水合后元素进入视口时从 0 滚到终值（easeOutCubic，950ms）。
 * prefers-reduced-motion 保持终值不动。tabular-nums 保证滚动中宽度不抖。
 *
 * 只接整数：耗时这类 "2m 05s" 复合文案没有可滚的单一数值，调用方不要传。
 */
export function CountUp({
  value,
  className,
}: {
  value: number;
  className?: string;
}) {
  const [display, setDisplay] = useState(value);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (value <= 0) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const el = ref.current;
    if (!el) return;

    let frame = 0;
    const duration = 950;
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(value * eased));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };

    // 在视口内才开滚；不支持 IO 的环境直接开滚
    if (!("IntersectionObserver" in window)) {
      frame = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(frame);
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        observer.disconnect();
        frame = requestAnimationFrame(tick);
      },
      { threshold: 0.4 },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [value]);

  return (
    <span ref={ref} className={className}>
      {formatInteger(display)}
    </span>
  );
}
