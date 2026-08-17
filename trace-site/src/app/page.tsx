import Link from "next/link";
import { ArrowRight, FlaskConical } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { CountUp } from "@/components/ui/count-up";
import { RunTable } from "@/components/run/RunTable";
import { StageChips } from "@/components/run/StageChips";
import { deriveStages } from "@/lib/run-graph";
import { formatDuration } from "@/lib/format";
import { usingRealData } from "@/lib/server/env";
import { getFeaturedRunDetail, getOverviewStats, getRunList } from "@/lib/server/runs";
import { fallbackTitle } from "@/content/cases";

/**
 * 概览页。
 *
 * KPI 只保留真实可算的四项，不做 sparkline 与 24h 环比 ——
 * 真实运行量是几十条量级，趋势线会退化成一条平线或两个点，属于编数据。
 * 参考稿里的成本磁贴同样没有做：estimated_cost 恒为 0（未配置单价）。
 */

export const revalidate = 300;

function Kpi({
  label,
  value,
  count,
  delay,
}: {
  label: string;
  /** 静态展示值（耗时等复合文案）；与 count 二选一。 */
  value?: string;
  /** 整数值：水合后从 0 滚到终值；无 JS 时直接渲染终值。 */
  count?: number;
  delay: number;
}) {
  return (
    <div
      className="anim-rise lift panel rounded-xl border border-line bg-surface px-5 py-4 hover:border-line-strong"
      style={{ animationDelay: `${delay}ms` }}
    >
      <p className="text-sm text-ink-faint">{label}</p>
      <p className="mt-2 font-mono text-3xl leading-none text-ink">
        {count !== undefined ? (
          <CountUp value={count} className="tabular-nums" />
        ) : (
          value
        )}
      </p>
    </div>
  );
}

export default async function HomePage() {
  const [stats, runs] = await Promise.all([getOverviewStats(), getRunList(8)]);
  // 精选案例取最近一次产出 PR 的运行，由 getFeaturedRunDetail 直接按
  // pr_url 非空倒序查询 —— 不在「最近 N 条」窗口里碰运气，也不回退到普通运行：
  // 这个区块只讲完整的修复故事，宁缺毋滥。
  const featured = await getFeaturedRunDetail();
  const stages = featured ? deriveStages(featured.run, featured.trace) : [];

  return (
    <div className="px-5 py-7 lg:px-8">
      {!usingRealData && (
        <p className="anim-fade mb-6 flex items-center gap-2.5 rounded-lg border border-warn/40 bg-warn/10 px-4 py-2.5 text-sm text-warn">
          <FlaskConical aria-hidden className="size-4 shrink-0" />
          构造数据：未配置 Supabase，当前展示的是结构对齐真实契约的示例数据。
        </p>
      )}

      <header className="anim-rise relative mb-7">
        {/* 网格背景向边缘溶解，只给首屏头部加纹理，不出横向滚动条 */}
        <div aria-hidden className="grid-backdrop" />
        <div className="relative">
          <h1 className="text-2xl font-semibold text-ink">概览</h1>
          <p className="mt-2 max-w-3xl text-base leading-relaxed text-ink-muted">
            用户提交一条反馈，Agent 自动分类、复现、修复、验证，最后提交 Pull Request。
            每一步都留有可审计的执行证据。
          </p>
        </div>
      </header>

      <div className="mb-7 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Kpi label="总运行数" count={stats.totalRuns} delay={60} />
        <Kpi label="产出 PR" count={stats.pullRequests} delay={110} />
        <Kpi label="平均耗时" value={formatDuration(stats.averageDurationMs)} delay={160} />
        <Kpi label="TOKEN 合计" count={stats.totalTokens} delay={210} />
      </div>

      {featured && (
        <Card className="mb-7" delay={260} interactive spotlight>
          <CardHeader
            title="精选案例"
            description="一次完整的缺陷修复：从反馈到 Pull Request"
            aside={
              <Link
                href={`/runs/${featured.run.id}`}
                className="group inline-flex items-center gap-2 text-sm text-accent transition-colors hover:text-ink"
              >
                查看完整执行流程
                <ArrowRight
                  aria-hidden
                  className="size-4 transition-transform duration-200 group-hover:translate-x-1"
                />
              </Link>
            }
          />
          <div className="p-5">
            <p className="text-base font-medium text-ink">
              {featured.narrative?.title ?? fallbackTitle(featured.run.category)}
            </p>
            {featured.narrative && (
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-muted">
                {featured.narrative.summary}
              </p>
            )}
            <div className="mt-5">
              <StageChips stages={stages} />
            </div>
          </div>
        </Card>
      )}

      <Card delay={320} interactive>
        <CardHeader
          title="最近运行"
          aside={
            <Link
              href="/runs"
              className="text-sm text-accent transition-colors hover:text-ink"
            >
              全部运行
            </Link>
          }
        />
        <RunTable runs={runs} />
      </Card>
    </div>
  );
}
