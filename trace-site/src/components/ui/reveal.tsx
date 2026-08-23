"use client";

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

/**
 * 滚动显现。进入视口才播放入场动画（rise/fade），替换 mount 即播的 anim-*：
 * 位于首屏以下的卡片此前在用户滚到之前就已播完，动画等于不存在。
 *
 * 三态是与现有 hover 语汇共存的关键：
 *   hidden    初始隐藏。只在 html.has-js 下生效（layout 内联脚本首帧前写入），
 *             因此无 JS 环境 SSR 内容永远完整可见；
 *   animating 加 is-visible 播放关键帧，delay 走 animationDelay 错峰；
 *   done      自己的入场动画结束后摘掉全部动画类 —— fill-mode: both 会永久钉住
 *             transform，不摘会压住 .lift:hover 的上浮。终态关键帧与自然态相同，
 *             摘除不产生跳变。
 *
 * 两种用法：
 *   - useReveal()   元素自己持有动画类（Card / StatCard 的单元素场景，
 *                   不引入额外包裹层，避免破坏 overflow-hidden 裁剪与布局）；
 *   - <Reveal>      包裹层，用于表格行、列表项等不便自持状态的元素。
 */

type Phase = "hidden" | "animating" | "done";

/* 全站共享一个 IntersectionObserver：运行记录页有上百行需要各自观察，
   不应开出上百个实例。触发即退订 —— 入场是一次性动画，无需持续监听。 */
const callbacks = new WeakMap<Element, () => void>();
let sharedObserver: IntersectionObserver | null = null;

function observeOnce(el: Element, onVisible: () => void): () => void {
  if (!sharedObserver) {
    sharedObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          callbacks.get(entry.target)?.();
          callbacks.delete(entry.target);
          sharedObserver?.unobserve(entry.target);
        }
      },
      // 露出 12% 才算到达；阈值过高会让很高的证据卡迟迟不触发
      { threshold: 0.12 },
    );
  }
  callbacks.set(el, onVisible);
  sharedObserver.observe(el);
  return () => {
    callbacks.delete(el);
    sharedObserver?.unobserve(el);
  };
}

interface UseRevealOptions {
  /** 进入视口后的错峰延迟（毫秒）。 */
  delay?: number;
  /** rise 带位移上浮；fade 纯淡入，用于表格行等密集元素，避免整页都在动。 */
  variant?: "rise" | "fade";
}

/** 让任意元素自持滚动显现状态；把返回值展开到该元素上即可。 */
export function useReveal<T extends HTMLElement>({
  delay = 0,
  variant = "rise",
}: UseRevealOptions = {}) {
  const [phase, setPhase] = useState<Phase>("hidden");
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || phase !== "hidden") return;
    if (!("IntersectionObserver" in window)) {
      setPhase("done");
      return;
    }
    return observeOnce(el, () => setPhase("animating"));
  }, [phase]);

  return {
    ref,
    // done 之后摘掉全部 reveal 类：opacity 回到自然值 1，transform 归还
    // 给 .lift 等 hover 语汇，动画不再占用层叠。
    classes:
      phase === "done"
        ? undefined
        : clsx(
            "reveal",
            variant === "fade" && "reveal-fade",
            phase === "animating" && "is-visible",
          ),
    style:
      phase === "animating" && delay > 0 ? { animationDelay: `${delay}ms` } : undefined,
    onAnimationEnd: (event: React.AnimationEvent) => {
      // 子元素的动画事件会冒泡上来（如骨架微光、连接线生长），只认自己的入场动画
      if (event.target === event.currentTarget) setPhase("done");
    },
  };
}

export function Reveal({
  children,
  className,
  delay,
  variant,
  as = "div",
}: UseRevealOptions & {
  children: React.ReactNode;
  className?: string;
  /** 渲染标签：列表项用 li，表格行用 tr（fade 配合，不动 transform）。 */
  as?: "div" | "header" | "p" | "li" | "tr";
}) {
  const reveal = useReveal<HTMLElement>({ delay, variant });

  // 动态标签用宽松类型绕开各元素 ref 型变问题（与 Spotlight 同一处理）
  const Tag: React.ElementType = as;

  return (
    <Tag
      ref={(node: HTMLElement | null) => {
        reveal.ref.current = node;
      }}
      className={clsx(reveal.classes, className)}
      style={reveal.style}
      onAnimationEnd={reveal.onAnimationEnd}
    >
      {children}
    </Tag>
  );
}
