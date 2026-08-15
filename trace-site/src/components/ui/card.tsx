import clsx from "clsx";

export function Card({
  children,
  className,
  delay,
  interactive,
}: {
  children: React.ReactNode;
  className?: string;
  /** 入场动画延迟（毫秒），用于同一屏内多张卡片错峰出现。 */
  delay?: number;
  /** 整卡可点击或需要悬停反馈时开启上浮与辉光。 */
  interactive?: boolean;
}) {
  return (
    <section
      className={clsx(
        "anim-rise rounded-xl border border-line bg-surface",
        interactive ? "lift hover:border-line-strong" : "transition-colors duration-200",
        className,
      )}
      style={delay ? { animationDelay: `${delay}ms` } : undefined}
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
