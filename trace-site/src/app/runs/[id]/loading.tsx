/**
 * 运行详情骨架屏。这一页要依次拉 Supabase 视图、Langfuse 快照和 GitHub diff，
 * 冷导航等待最长，骨架的收益最大：结论 → 数字 → 阶段芯片 → 详情 → 证据卡。
 */
export default function Loading() {
  return (
    <div className="px-5 py-7 lg:px-8" aria-busy="true">
      <span className="sr-only">页面加载中</span>

      {/* 面包屑 */}
      <div className="mb-4 flex items-center gap-2">
        <div className="skeleton h-4 w-16 rounded" />
        <div className="skeleton h-4 w-3 rounded" />
        <div className="skeleton h-4 w-20 rounded" />
      </div>

      {/* 结论 */}
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="skeleton h-7 w-2/3 max-w-md rounded-md" />
          <div className="skeleton mt-3.5 h-4 w-full max-w-2xl rounded" />
        </div>
        <div className="skeleton h-7 w-20 shrink-0 rounded-full" />
      </div>

      {/* 四个关键数字 */}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div
            key={i}
            className="rounded-xl border border-line bg-surface px-5 py-4"
          >
            <div className="skeleton h-4 w-14 rounded" />
            <div className="skeleton mt-3 h-6 w-20 rounded" />
            <div className="skeleton mt-2.5 h-3.5 w-24 rounded" />
          </div>
        ))}
      </div>

      {/* 阶段芯片 */}
      <div className="mb-5 flex flex-col gap-2 xl:flex-row">
        {Array.from({ length: 7 }, (_, i) => (
          <div key={i} className="skeleton h-16 flex-1 rounded-lg" />
        ))}
      </div>

      {/* 阶段详情 */}
      <div className="mb-6 rounded-xl border border-line bg-surface p-5">
        <div className="grid gap-5 lg:grid-cols-[1fr_1.1fr]">
          <div>
            <div className="skeleton h-3.5 w-20 rounded" />
            <div className="skeleton mt-2.5 h-6 w-40 rounded" />
            <div className="skeleton mt-4 h-4 w-full rounded" />
            <div className="skeleton mt-2 h-4 w-5/6 rounded" />
            <div className="skeleton mt-5 h-28 w-full rounded-xl" />
          </div>
          <div className="space-y-2.5">
            <div className="skeleton h-4 w-24 rounded" />
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="skeleton h-10 w-full rounded-lg" />
            ))}
          </div>
        </div>
      </div>

      {/* 证据卡 */}
      <div className="grid gap-5 lg:grid-cols-2">
        {Array.from({ length: 2 }, (_, i) => (
          <div key={i} className="rounded-xl border border-line bg-surface">
            <div className="border-b border-line px-5 py-4">
              <div className="skeleton h-5 w-24 rounded" />
            </div>
            <div className="space-y-3 p-5">
              {Array.from({ length: 4 }, (_, j) => (
                <div key={j} className="skeleton h-4 w-full rounded" />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
