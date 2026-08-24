"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { RunTable } from "@/components/run/RunTable";
import {
  RUN_FILTERS,
  filterGroupOf,
  matchRunFilter,
  type RunFilterKey,
} from "@/lib/run-filters";
import type { RunListItem } from "@/lib/types";

/** 每页行数。运行量是百条级，客户端切片即可，不值得为翻页加一次网络往返。 */
const PAGE_SIZE = 20;

/**
 * 运行记录筛选器 + 分页。状态分组与搜索都是纯客户端过滤，
 * 筛选或搜索变化时回到第 1 页 —— 结果集变了，旧页码没有意义。
 *
 * chip 上的计数随搜索词联动 —— 先看搜索命中了什么，再看各结论的分布，
 * 两个条件各自收窄时用户始终知道还剩多少空间。
 */
export function RunsExplorer({ runs }: { runs: RunListItem[] }) {
  const [group, setGroup] = useState<RunFilterKey>("all");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);

  const selectGroup = (key: RunFilterKey) => {
    setGroup(key);
    setPage(1);
  };
  const onSearch = (value: string) => {
    setQuery(value);
    setPage(1);
  };

  const searched = useMemo(
    () => runs.filter((item) => matchRunFilter(item, "all", query)),
    [runs, query],
  );

  const visible = useMemo(
    () =>
      group === "all"
        ? searched
        : searched.filter((item) => filterGroupOf(item) === group),
    [searched, group],
  );

  const counts = useMemo(() => {
    const c: Record<RunFilterKey, number> = {
      all: searched.length,
      published: 0,
      unfixed: 0,
      failed: 0,
      neutral: 0,
      active: 0,
    };
    for (const item of searched) c[filterGroupOf(item)] += 1;
    return c;
  }, [searched]);

  // 页码夹取：删除类操作不存在，这里只是防手动越界的兜底
  const pageCount = Math.max(Math.ceil(visible.length / PAGE_SIZE), 1);
  const safePage = Math.min(page, pageCount);
  const paged = visible.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 border-b border-line px-5 py-3">
        <div role="group" aria-label="按结论筛选" className="flex flex-wrap gap-1.5">
          {RUN_FILTERS.map((filter) => {
            const selected = group === filter.key;
            return (
              <button
                key={filter.key}
                type="button"
                aria-pressed={selected}
                onClick={() => selectGroup(filter.key)}
                className={clsx(
                  "row-hover rounded-full border px-3 py-1.5 text-sm",
                  selected
                    ? "border-accent/60 bg-accent/12 text-accent"
                    : "border-line text-ink-muted hover:bg-raised hover:text-ink",
                )}
              >
                {filter.label}
                <span
                  className={clsx(
                    "ml-1.5 font-mono text-xs",
                    selected ? "text-accent" : "text-ink-faint",
                  )}
                >
                  {counts[filter.key]}
                </span>
              </button>
            );
          })}
        </div>

        <label className="relative ml-auto block">
          <span className="sr-only">搜索运行</span>
          <Search
            aria-hidden
            className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-faint"
          />
          <input
            type="search"
            value={query}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="搜索 ref / 反馈 / 类别"
            className="w-52 rounded-lg border border-line bg-canvas py-1.5 pl-8 pr-3 text-sm text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent/60 sm:w-64"
          />
        </label>
      </div>

      {visible.length > 0 ? (
        <>
          <RunTable runs={paged} />

          {pageCount > 1 && (
            <div className="flex items-center justify-between gap-4 border-t border-line px-5 py-3 text-sm text-ink-faint">
              <p>
                共 <span className="font-mono text-ink-muted">{visible.length}</span>{" "}
                条 · 第{" "}
                <span className="font-mono text-ink-muted">{safePage}</span> /{" "}
                <span className="font-mono">{pageCount}</span> 页
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setPage(safePage - 1)}
                  disabled={safePage <= 1}
                  className={clsx(
                    "row-hover inline-flex items-center gap-1 rounded-lg border px-3 py-1.5",
                    safePage <= 1
                      ? "cursor-not-allowed border-line opacity-40"
                      : "border-line text-ink-muted hover:bg-raised hover:text-ink",
                  )}
                >
                  <ChevronLeft aria-hidden className="size-4" />
                  上一页
                </button>
                <button
                  type="button"
                  onClick={() => setPage(safePage + 1)}
                  disabled={safePage >= pageCount}
                  className={clsx(
                    "row-hover inline-flex items-center gap-1 rounded-lg border px-3 py-1.5",
                    safePage >= pageCount
                      ? "cursor-not-allowed border-line opacity-40"
                      : "border-line text-ink-muted hover:bg-raised hover:text-ink",
                  )}
                >
                  下一页
                  <ChevronRight aria-hidden className="size-4" />
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="px-5 py-16 text-center text-sm text-ink-faint">
          没有匹配的运行。换个关键词，或切回「全部」。
        </p>
      )}
    </div>
  );
}
