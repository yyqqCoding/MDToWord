/**
 * 概览骨架屏。灰块形状对齐真实布局（KPI 磁贴 → 精选案例 → 表格），
 * 微光扫过只动 transform。真实数据源冷查询需要数百毫秒，骨架比空白页诚实。
 */
export default function Loading() {
  return (
    <div className="px-5 py-7 lg:px-8" aria-busy="true">
      <span className="sr-only">页面加载中</span>

      <div className="mb-7">
        <div className="skeleton h-7 w-20 rounded-md" />
        <div className="skeleton mt-3.5 h-4 w-full max-w-xl rounded" />
      </div>

      <div className="anim-rise mb-7 grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div
            key={i}
            className="rounded-xl border border-line bg-surface px-5 py-4"
          >
            <div className="skeleton h-4 w-16 rounded" />
            <div className="skeleton mt-3.5 h-7 w-24 rounded" />
          </div>
        ))}
      </div>

      <div className="mb-7 rounded-xl border border-line bg-surface">
        <div className="border-b border-line px-5 py-4">
          <div className="skeleton h-5 w-28 rounded" />
        </div>
        <div className="p-5">
          <div className="skeleton h-5 w-2/3 max-w-md rounded" />
          <div className="skeleton mt-3 h-4 w-full max-w-2xl rounded" />
          <div className="mt-5 flex flex-col gap-2 xl:flex-row">
            {Array.from({ length: 7 }, (_, i) => (
              <div key={i} className="skeleton h-16 flex-1 rounded-lg" />
            ))}
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-line bg-surface">
        <div className="border-b border-line px-5 py-4">
          <div className="skeleton h-5 w-24 rounded" />
        </div>
        {Array.from({ length: 6 }, (_, i) => (
          <div
            key={i}
            className="flex items-center gap-6 border-b border-line/60 px-5 py-4 last:border-b-0"
          >
            <div className="skeleton h-4 w-20 rounded" />
            <div className="skeleton h-4 w-full max-w-sm flex-1 rounded" />
            <div className="skeleton hidden h-4 w-16 rounded sm:block" />
            <div className="skeleton h-6 w-16 rounded-full" />
            <div className="skeleton hidden h-4 w-12 rounded md:block" />
            <div className="skeleton hidden h-4 w-14 rounded lg:block" />
          </div>
        ))}
      </div>
    </div>
  );
}
