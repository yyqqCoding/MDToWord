"use client";

import clsx from "clsx";
import { Spotlight } from "@/components/ui/spotlight";
import { useReveal } from "@/components/ui/reveal";

export function Card({
  children,
  className,
  delay,
  interactive,
  spotlight,
}: {
  children: React.ReactNode;
  className?: string;
  /** 入场动画延迟（毫秒），用于同一屏内多张卡片错峰出现。 */
  delay?: number;
  /** 整卡可点击或需要悬停反馈时开启上浮与辉光。 */
  interactive?: boolean;
  /** 指针聚光边框，只给展示性主卡（精选案例），密集数据卡开了是视觉噪音。 */
  spotlight?: boolean;
}) {
  const classes = clsx(
    "panel rounded-xl border border-line bg-surface",
    interactive ? "lift hover:border-line-strong" : "transition-colors duration-200",
    className,
  );
  const style = delay ? { animationDelay: `${delay}ms` } : undefined;

  if (spotlight) {
    // 聚光主卡目前只出现在首页首屏，保持 mount 即播的入场；
    // Spotlight 自持内部 ref，不与 useReveal 组合。
    return (
      <Spotlight as="section" className={clsx(classes, "anim-rise")} style={style}>
        {children}
      </Spotlight>
    );
  }

  // 进入视口才播放入场（见 useReveal）：首屏以下的卡片不再在用户看到之前
  // 就把动画播完。动画类挂在本元素上，overflow-hidden 等裁剪语义不受影响。
  const reveal = useReveal<HTMLElement>({ delay });
  return (
    <section
      ref={reveal.ref}
      className={clsx(reveal.classes, classes)}
      style={reveal.style}
      onAnimationEnd={reveal.onAnimationEnd}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  aside,
}: {
  title: string;
  description?: string;
  aside?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
      <div className="min-w-0">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {description && (
          <p className="mt-1 text-sm leading-relaxed text-ink-faint">{description}</p>
        )}
      </div>
      {aside}
    </header>
  );
}
