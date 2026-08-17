/** 运行记录骨架屏：标题 + 一整张表格。 */
export default function Loading() {
  return (
    <div className="px-5 py-7 lg:px-8" aria-busy="true">
      <span className="sr-only">页面加载中</span>

      <div className="mb-7">
        <div className="skeleton h-7 w-28 rounded-md" />
        <div className="skeleton mt-3.5 h-4 w-full max-w-md rounded" />
      </div>

      <div className="anim-rise rounded-xl border border-line bg-surface">
        <div className="border-b border-line px-5 py-4">
          <div className="skeleton h-5 w-32 rounded" />
        </div>
        {Array.from({ length: 10 }, (_, i) => (
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
