"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookText, LayoutDashboard, ListTree } from "lucide-react";

/**
 * 顶部导航，全宽吸顶。
 *
 * 只有三个入口的侧栏横向占 w-60 且大部分区域空置（维护者验收反馈），
 * 改为品牌居左 + 胶囊导航居右的单行头部 —— 内容区拿回全部宽度，
 * 桌面与窄屏共用同一套结构（原 MobileNav 的样式升格而来）。
 *
 * 参考稿里的 Traces / Spans / Artifacts / Alerts / Settings 都没有做：
 * Trace 与 Span 只存在于单次运行内部，独立成页没有意义；
 * Artifacts 含未脱敏内容，是明确的展示禁区；
 * Alerts 与 Settings 属于控制面，本站全程只读。
 * 原侧栏「数据来源」注脚一并删除：项目说明页有更完整的同名章节。
 */

const NAV = [
  { href: "/", label: "概览", icon: LayoutDashboard, exact: true },
  { href: "/runs", label: "运行记录", icon: ListTree, exact: false },
  { href: "/about", label: "项目说明", icon: BookText, exact: false },
] as const;

export function Header() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line bg-surface/95 px-5 py-3 backdrop-blur lg:px-8">
      <Link href="/" className="flex items-baseline gap-2">
        <span className="text-base font-semibold leading-snug text-ink">MD To Word</span>
        <span className="brand-sheen text-base font-semibold leading-snug">
          Repair Agent
        </span>
      </Link>

      <nav aria-label="主导航" className="ml-auto flex gap-1.5 text-sm">
        {NAV.map((item) => {
          const active = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={clsx(
                "row-hover inline-flex items-center gap-2 rounded-full px-3.5 py-1.5",
                active
                  ? "bg-accent/12 text-accent"
                  : "text-ink-muted hover:bg-raised hover:text-ink",
              )}
            >
              <Icon aria-hidden className="size-4 shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
