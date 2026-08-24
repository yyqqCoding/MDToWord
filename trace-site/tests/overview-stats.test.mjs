import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = await readFile(
  new URL("../src/lib/overview-stats.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const module = { exports: {} };
new Function("exports", "module", "require", compiled)(
  module.exports,
  module,
  require,
);
const { calculateOverviewStats } = module.exports;

test("概览按全部 run、唯一 PR、全部终态耗时和全部 token 聚合", () => {
  const rows = [
    {
      status: "completed",
      pr_url: "https://github.test/pull/1",
      total_tokens: 100,
      started_at: "2026-08-24T00:00:00Z",
      finished_at: "2026-08-24T00:00:10Z",
    },
    {
      status: "failed",
      pr_url: "https://github.test/pull/1",
      total_tokens: 200,
      started_at: "2026-08-24T00:00:00Z",
      finished_at: "2026-08-24T00:00:30Z",
    },
    {
      status: "gating",
      pr_url: null,
      total_tokens: 50,
      started_at: "2026-08-24T00:00:00Z",
      finished_at: null,
    },
  ];

  assert.deepEqual(calculateOverviewStats(rows), {
    totalRuns: 3,
    pullRequests: 1,
    averageDurationMs: 20_000,
    totalTokens: 350,
  });
});
