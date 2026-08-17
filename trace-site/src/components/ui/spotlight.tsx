"use client";

import clsx from "clsx";
import { useEffect, useRef } from "react";

/**
 * 指针聚光边框容器。配方来自 luster/assets/motion.mjs 的 initSpotlights。
 *
 * pointermove 时把指针坐标写进 --mx/--my（rAF 节流），globals.css 的 .spot::before
 * 消费这两个变量画跟随指针的边框光。伪元素只在 (pointer: fine) 下渲染，
 * 所以触屏与无 JS 环境外观退化为普通边框，内容完整。
 */
export function Spotlight({
  children,
  className,
  style,
  as = "div",
}: {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  /** 语义标签：Card 用 section，列表卡片用默认 div。 */
  as?: "div" | "section";
}) {
  const ref = useRef<HTMLElement | null>(null);
  const frame = useRef(0);
  const fine = useRef(false);

  useEffect(() => {
    fine.current = window.matchMedia("(pointer: fine)").matches;
    return () => cancelAnimationFrame(frame.current);
  }, []);

  // 动态标签（div/section）用回调 ref 绕开 HTMLElement 与 HTMLDivElement 的型变问题
  const Tag: React.ElementType = as;

  return (
    <Tag
      ref={(node: HTMLElement | null) => {
        ref.current = node;
      }}
      className={clsx("spot", className)}
      style={style}
      onPointerMove={(event) => {
        const el = ref.current;
        if (!el || !fine.current || frame.current) return;
        const { clientX, clientY } = event;
        frame.current = requestAnimationFrame(() => {
          frame.current = 0;
          const rect = el.getBoundingClientRect();
          el.style.setProperty("--mx", `${clientX - rect.left}px`);
          el.style.setProperty("--my", `${clientY - rect.top}px`);
        });
      }}
    >
      {children}
    </Tag>
  );
}
