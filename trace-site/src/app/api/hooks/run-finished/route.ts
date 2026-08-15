import { NextResponse } from "next/server";
import { revalidatePath } from "next/cache";
import { siteWebhookSecret, supabaseConfig } from "@/lib/server/env";
import { captureRunTrace } from "@/lib/server/capture";

/**
 * Agent 运行完成回调。
 *
 * 取代了原先每天 03:00 的 Cron 回填。反馈量低时定时轮询几乎全是空跑，
 * 而新运行要等最长 24 小时才看得到调用明细。现在由 Agent 在运行落终态时主动推送。
 *
 * 两件事：
 *   1. 抓一次 Trace 快照写进 agent_run_traces（带重试，等 Langfuse 索引）
 *   2. 让首页、列表页、详情页的 ISR 缓存立刻失效
 *
 * 第 2 步即使第 1 步没抓到也照做 —— 运行本身应当马上出现在列表里，
 * 调用明细可以晚一步由详情页的按需补抓补上。
 *
 * 推送是 at-most-once：站点冷启动、部署中或网络抖动都会丢。丢了不补推，
 * 由 getRunDetail 的按需补抓自愈。
 */

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(request: Request) {
  if (!siteWebhookSecret) {
    return NextResponse.json({ error: "webhook is not configured" }, { status: 503 });
  }
  if (request.headers.get("x-webhook-secret") !== siteWebhookSecret) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  if (!supabaseConfig) {
    return NextResponse.json({ error: "data sources are not configured" }, { status: 503 });
  }

  let runId: unknown;
  try {
    runId = ((await request.json()) as { run_id?: unknown }).run_id;
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }
  if (
    typeof runId !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)
  ) {
    return NextResponse.json({ error: "run_id must be a uuid" }, { status: 400 });
  }

  const result = await captureRunTrace(runId, { retry: true });

  revalidatePath("/");
  revalidatePath("/runs");
  revalidatePath(`/runs/${runId}`);

  return NextResponse.json({ trace: result.status }, { status: 200 });
}
