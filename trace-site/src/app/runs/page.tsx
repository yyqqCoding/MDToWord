import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardHeader } from "@/components/ui/card";
import { MockBanner } from "@/components/ui/mock-banner";
import { RunsExplorer } from "@/components/run/RunsExplorer";
import { usingRealData } from "@/lib/server/env";
import { getRunList } from "@/lib/server/runs";
import { SITE_TIME_ZONE_LABEL } from "@/lib/format";

export const revalidate = 300;

export default async function RunsPage() {
  const runs = await getRunList(100);

  return (
    <div className="px-5 py-7 lg:px-8">
      {!usingRealData && (
        <MockBanner>
          构造数据：未配置 Supabase，当前展示的是结构对齐真实契约的示例数据。
        </MockBanner>
      )}

      <PageHeader
        title="运行记录"
        description="全部历史运行如实上站，包含无法复现与安全拦截。只展示成功案例的展示站没有说服力。"
        backdrop
      />

      <Card delay={80} interactive>
        <CardHeader title={`共 ${runs.length} 次运行`} description={`时间为 ${SITE_TIME_ZONE_LABEL}`} />
        {runs.length > 0 ? (
          <RunsExplorer runs={runs} />
        ) : (
          <p className="px-5 py-16 text-center text-sm text-ink-faint">
            暂无运行记录。
          </p>
        )}
      </Card>
    </div>
  );
}