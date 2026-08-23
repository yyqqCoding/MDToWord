import clsx from "clsx";

/**
 * 页面标题区。收拢此前在概览/运行记录/项目说明三处手写的同一组合：
 * 标题 + 描述 + （可选）首屏网格纹理。
 *
 * 标题阶梯见 globals.css 字号契约：h1 用 text-3xl lg:text-4xl tracking-tight。
 */
export function PageHeader({
  title,
  description,
  backdrop = false,
  className = "mb-7",
}: {
  title: string;
  description?: string;
  /** 首屏网格背景。只有整宽的内容页（概览/运行记录）开启；文档式版面（项目说明）不带。 */
  backdrop?: boolean;
  /** 外边距等布局微调；默认与页面第一个区块拉开 mb-7。传空串表示由后续 section 自行控制间距。 */
  className?: string;
}) {
  return (
    <header className={clsx("anim-rise relative", className)}>
      {backdrop && <div aria-hidden className="grid-backdrop" />}
      {/* 网格之上要包一层 relative，让文字盖在纹理上 */}
      <div className={backdrop ? "relative" : undefined}>
        <h1 className="text-3xl font-semibold tracking-tight text-ink lg:text-4xl">
          {title}
        </h1>
        {description && (
          <p className="mt-2.5 max-w-3xl text-base leading-relaxed text-ink-muted">
            {description}
          </p>
        )}
      </div>
    </header>
  );
}
