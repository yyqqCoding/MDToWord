import { notFound } from "next/navigation";
import { RunDetail } from "@/components/run/RunDetail";
import { getRunDetail } from "@/lib/server/runs";

/**
 * 数据来自 agent_run_public 视图 + agent_run_traces 快照 + GitHub 公开 PR diff。
 * 未配置 Supabase 时自动回落到构造数据，页面组件不感知来源 ——
 * 这正是先定 DTO 再做页面的收益：M2 到 M3 没有改动任何展示组件。
 */
export default async function RunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const data = await getRunDetail(id);
  if (!data) notFound();
  return <RunDetail data={data} />;
}
