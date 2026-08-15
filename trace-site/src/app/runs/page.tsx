import { FlaskConical } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { RunTable } from "@/components/run/RunTable";
import { usingRealData } from "@/lib/server/env";
import { getRunList } from "@/lib/server/runs";
import { SITE_TIME_ZONE_LABEL } from "@/lib/format";

export const revalidate = 300;

export default async function RunsPage() {
  const runs = await getRunList(100);

  return (
    <div className="px-5 py-7 lg:px-8">
      {!usingRealData && (
        <p className="anim-fade mb-6 flex items-center gap-2.5 rounded-lg border border-warn/40 bg-warn/10 px-4 py-2.5 text-sm text-warn">
          <FlaskConical aria-hidden className="size-4 shrink-0" />
          构造数据：未配置 Supabase，当前展示的是结构对齐真实契约的示例数据。
        </p>
      )}

      <header className="anim-rise mb-7">
        <h1 className="text-2xl font-semibold text-ink">运行记录</h1>
        <p className="mt-2 max-w-3xl text-base leading-relaxed text-ink-muted">
          全部历史运行如实上站，包含无法复现与安全拦截。
          只展示成功案例的展示站没有说服力。
        </p>
      </header>

      <Card delay={80} interactive>
        <CardHeader title={`共 ${runs.length} 次运行`} description={`时间为 ${SITE_TIME_ZONE_LABEL}`} />
        {runs.length > 0 ? (
          <RunTable runs={runs} />
        ) : (
          <p className="px-5 py-16 text-center text-sm text-ink-faint">
            暂无运行记录。
          </p>
        )}
      </Card>
    </div>
  );
}
