import { FlaskConical } from "lucide-react";

/**
 * 构造数据横幅。未配置真实数据源时出现在受影响页面的顶部，
 * 明说当前展示的是结构对齐真实契约的示例数据 —— 不假装是真实运行。
 * 各页口径略有差异，文案由调用方给出。
 */
export function MockBanner({ children }: { children: React.ReactNode }) {
  return (
    <p className="anim-fade mb-6 flex items-center gap-2.5 rounded-lg border border-warn/40 bg-warn/10 px-4 py-2.5 text-sm text-warn">
      <FlaskConical aria-hidden className="size-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}
