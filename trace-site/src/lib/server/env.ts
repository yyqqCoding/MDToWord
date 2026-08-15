import "server-only";

/**
 * 服务端配置读取。
 *
 * 这些值只存在于服务端环境变量，**不得**以 NEXT_PUBLIC_ 前缀暴露。
 * 本模块导入 server-only：任何客户端组件误引用都会在构建期直接报错，
 * 而不是等到密钥泄漏进 bundle 才发现。
 *
 * 任一数据源未配置时对应能力自动降级，站点仍可用构造数据运行，
 * 便于本地开发与视觉迭代。
 */

function optional(name: string): string | null {
  const value = process.env[name];
  return value && value.trim().length > 0 ? value.trim() : null;
}

export const supabaseConfig = (() => {
  const url = optional("SUPABASE_URL");
  const key = optional("SUPABASE_SERVICE_ROLE_KEY");
  return url && key ? { url: url.replace(/\/+$/, ""), key } : null;
})();

export const langfuseConfig = (() => {
  const publicKey = optional("LANGFUSE_PUBLIC_KEY");
  const secretKey = optional("LANGFUSE_SECRET_KEY");
  const host = optional("LANGFUSE_HOST") ?? "https://cloud.langfuse.com";
  return publicKey && secretKey
    ? { host: host.replace(/\/+$/, ""), publicKey, secretKey }
    : null;
})();

export const githubConfig = {
  owner: optional("GITHUB_REPO_OWNER") ?? "yyqqCoding",
  repo: optional("GITHUB_REPO_NAME") ?? "MDToWord",
  /** 可选。公开仓库无需 token，仅在触发限流时用于提高配额。 */
  token: optional("GITHUB_TOKEN"),
};

export const cronSecret = optional("CRON_SECRET");

/**
 * Agent 运行完成回调的共享密钥，须与 Agent 侧 TRACE_SITE_WEBHOOK_SECRET 一致。
 * 未配置时 /api/hooks/run-finished 直接返回 503，不做任何工作。
 */
export const siteWebhookSecret = optional("SITE_WEBHOOK_SECRET");

/** 数据源是否就绪；页面据此决定使用真实数据还是构造数据。 */
export const usingRealData = supabaseConfig !== null;
