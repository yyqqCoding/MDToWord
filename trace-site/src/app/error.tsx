"use client";

import { useEffect } from "react";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 只记录错误类型；Supabase 响应体和查询串不会进入浏览器日志。
    console.error("trace site data unavailable", error.name);
  }, [error]);

  return (
    <main className="mx-auto flex min-h-[60vh] max-w-xl flex-col justify-center px-6 text-center">
      <p className="font-mono text-xs uppercase tracking-widest text-ink-faint">
        Data source unavailable
      </p>
      <h1 className="mt-3 text-2xl font-semibold text-ink">数据暂时不可用</h1>
      <p className="mt-3 text-sm leading-6 text-ink-muted">
        已配置的数据源暂时无法读取。页面没有回退到构造数据，请稍后重试。
      </p>
      <button
        type="button"
        onClick={reset}
        className="mx-auto mt-6 rounded-lg border border-accent/50 px-4 py-2 text-sm text-accent hover:bg-accent/10"
      >
        重新加载
      </button>
    </main>
  );
}
