import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardHeader } from "@/components/ui/card";
import { MockBanner } from "@/components/ui/mock-banner";
import { StatCard } from "@/components/ui/stat-card";
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

export const revalidate = 60;

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
        <MockBanner>
          构造数据：未配置 Supabase，当前展示的是结构对齐真实契约的示例数据。
        </MockBanner>
      )}

      <PageHeader
        title="概览"
        description="Agent 安全分类反馈：后端缺陷经复现、修复和验证后提交 PR，功能需求与前端缺陷创建脱敏 Issue。"
        backdrop
      />

      <div className="mb-7 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="总运行数" count={stats.totalRuns} delay={60} />
        <StatCard label="产出 PR" count={stats.pullRequests} delay={110} />
        <StatCard
          label="平均运行耗时"
          value={formatDuration(stats.averageDurationMs)}
          delay={160}
        />
        <StatCard label="TOKEN 合计" count={stats.totalTokens} delay={210} />
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
              {featured.narrative?.title ??
                fallbackTitle(
                  featured.run.category,
                  featured.run.route,
                  featured.run.area,
                )}
            </p>
            {featured.narrative && (
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-muted">
                {featured.narrative.summary}
              </p>
            )}
            <div className="mt-5">
              {/* 静态呈现终态即可；重放动画经维护者验收后撤下（见设计文档 C 节） */}
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
