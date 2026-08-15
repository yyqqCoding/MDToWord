"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookText, LayoutDashboard, ListTree } from "lucide-react";

/**
 * 左侧固定导航。只有三个入口。
 *
 * 参考稿里的 Traces / Spans / Artifacts / Alerts / Settings 都没有做：
 * Trace 与 Span 只存在于单次运行内部，独立成页没有意义；
 * Artifacts 含未脱敏内容，是明确的展示禁区；
 * Alerts 与 Settings 属于控制面，本站全程只读。
 */

const NAV = [
  { href: "/", label: "概览", icon: LayoutDashboard, exact: true },
  { href: "/runs", label: "运行记录", icon: ListTree, exact: false },
  { href: "/about", label: "项目说明", icon: BookText, exact: false },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    // sticky + h-dvh：页面滚动时侧栏常驻，不必回到顶部才能换页
    <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-line bg-surface lg:flex">
      <div className="px-6 py-6">
        <p className="text-base font-semibold leading-snug text-ink">MD To Word</p>
        <p className="text-base font-semibold leading-snug text-accent">Repair Agent</p>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-3">
        <ul className="space-y-1.5">
          {NAV.map((item, index) => {
            const active = item.exact
              ? pathname === item.href
              : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <li
                key={item.href}
                className="anim-rise"
                style={{ animationDelay: `${index * 70}ms` }}
              >
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={clsx(
                    "group relative flex items-center gap-3 rounded-lg px-3.5 py-2.5 text-sm transition-all duration-200",
                    active
                      ? "bg-accent/12 text-accent"
                      : "text-ink-muted hover:translate-x-0.5 hover:bg-raised hover:text-ink",
                  )}
                >
                  {active && (
                    <span
                      aria-hidden
                      className="anim-fade absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-accent"
                    />
                  )}
                  <Icon
                    aria-hidden
                    className="size-5 shrink-0 transition-transform duration-200 group-hover:scale-110"
                  />
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/*
        参考稿此处是 SYSTEM STATUS 健康探测。本站不做：我们没有 Redis / S3，
        且 Agent 主机的 Worker 只监听回环地址，公网探不到，做出来只能是假的。
      */}
      <div className="border-t border-line px-6 py-5">
        <p className="text-sm text-ink-faint">数据来源</p>
        <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
          Supabase + Langfuse
        </p>
      </div>
    </aside>
  );
}

/** 窄屏顶部导航，替代侧栏；同样吸顶。 */
export function MobileNav() {
  const pathname = usePathname();
  return (
    <nav className="sticky top-0 z-30 flex items-center gap-4 border-b border-line bg-surface/95 px-5 py-3.5 backdrop-blur lg:hidden">
      <span className="text-base font-semibold text-ink">
        MD To Word <span className="font-normal text-accent">Repair Agent</span>
      </span>
      <div className="ml-auto flex gap-4 text-sm">
        {NAV.map((item) => {
          const active = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "transition-colors",
                active ? "text-accent" : "text-ink-muted",
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
