import clsx from "clsx";
import {
  AlertTriangle,
  Check,
  CircleDashed,
  Loader,
  ShieldAlert,
  X,
  type LucideIcon,
} from "lucide-react";
import type { Tone } from "@/lib/run-graph";

/**
 * 状态一律以 图标 + 文字 + 颜色 三重编码呈现，不允许只靠颜色表意。
 * 状态色是保留槽位，不得复用为图表系列色。
 */

const TONE_CLASS: Record<Tone, string> = {
  good: "border-good/35 bg-good/10 text-good",
  warn: "border-warn/35 bg-warn/10 text-warn",
  serious: "border-serious/35 bg-serious/10 text-serious",
  critical: "border-critical/35 bg-critical/10 text-critical",
  accent: "border-accent/35 bg-accent/10 text-accent",
  neutral: "border-line-strong bg-raised text-ink-muted",
};

const TONE_ICON: Record<Tone, LucideIcon> = {
  good: Check,
  warn: AlertTriangle,
  serious: ShieldAlert,
  critical: X,
  accent: Loader,
  neutral: CircleDashed,
};

export function StatusBadge({
  tone,
  children,
  className,
}: {
  tone: Tone;
  children: React.ReactNode;
  className?: string;
}) {
  const Icon = TONE_ICON[tone];
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-sm font-medium",
        TONE_CLASS[tone],
        className,
      )}
    >
      <Icon aria-hidden className="size-4 shrink-0" />
      {children}
    </span>
  );
}

/** 中性标签，用于版本号、类别等非状态信息。 */
export function MetaBadge({
  children,
  mono,
  title,
}: {
  children: React.ReactNode;
  mono?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center rounded-md border border-line bg-raised px-2.5 py-1 text-sm text-ink-muted",
        mono && "font-mono",
      )}
    >
      {children}
    </span>
  );
}
