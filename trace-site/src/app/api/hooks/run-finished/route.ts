import { NextResponse, after } from "next/server";
import { revalidatePath, revalidateTag } from "next/cache";
import { siteWebhookSecret, supabaseConfig } from "@/lib/server/env";
import { captureRunTrace } from "@/lib/server/capture";

/**
 * Agent 运行完成回调。
 *
 * 取代了原先每天 03:00 的 Cron 回填。反馈量低时定时轮询几乎全是空跑，
 * 而新运行要等最长 24 小时才看得到调用明细。现在由 Agent 在运行落终态时主动推送。
 *
 * **先应答，再干活。** 校验通过就立刻返回 202，抓取放到 after() 里。
 * 抓取必须等 Langfuse 索引，重试阶梯走满要 ~20 秒；而 Agent 只是来发个信号，
 * 不该为此干等 —— 它的 Scheduler 是单并发，等待期间领不了下一条反馈。
 * 上线首测就撞上了这个：Agent 侧 10 秒 ReadTimeout，站点其实在第 21 秒
 * 把快照写成功了，功能没坏但日志误导，且白白占住了 Scheduler。
 *
 * 列表与首页的刷新不依赖抓取结果，在响应前就做掉，让新运行尽快出现；
 * 详情页的刷新等抓完再做。
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

  // 运行本身应当立刻出现在列表里，这一步不依赖抓取结果。
  revalidateTag("runs");
  revalidatePath("/");
  revalidatePath("/runs");

  const id = runId;
  after(async () => {
    try {
      await captureRunTrace(id, { retry: true });
    } catch {
      // 不回显异常内容；抓不到由按需补抓自愈。
    }
    revalidatePath(`/runs/${id}`);
  });

  return NextResponse.json({ accepted: true }, { status: 202 });
}
