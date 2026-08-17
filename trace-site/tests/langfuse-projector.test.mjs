import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");

async function loadProjectTrace() {
  const sourceUrl = new URL("../src/lib/server/langfuse.ts", import.meta.url);
  let source = await readFile(sourceUrl, "utf8");

  // projectTrace 本身是纯函数。测试加载器只移除服务端配置依赖，并截取纯投影部分，
  // 以便在不启动 Next.js、也不连接 Langfuse 的情况下执行生产实现。
  source = source
    .replace('import "server-only";\n', "")
    .replace('import { langfuseConfig } from "@/lib/server/env";\n', "")
    .replace(
      'import type { Observation, ObservationType, RunTrace } from "@/lib/types";\n',
      "",
    );
  source = source.slice(0, source.indexOf("export async function fetchTrace"));

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
  return module.exports.projectTrace;
}

const projectTrace = await loadProjectTrace();

test("根 observation 尚未索引时不生成空调用明细快照", () => {
  const runId = "d771e2a9-6ce3-4b08-bbfb-15182ec72514";
  const trace = {
    id: "trace-partial",
    observations: [
      {
        id: "generation-before-root",
        parentObservationId: "root-indexed-later",
        name: "generate-reproduction",
        type: "GENERATION",
        startTime: "2026-08-16T04:57:27.100Z",
        endTime: "2026-08-16T04:57:27.400Z",
      },
      {
        id: "tool-before-root",
        parentObservationId: "root-indexed-later",
        name: "run-reproduction",
        type: "TOOL",
        startTime: "2026-08-16T04:57:27.500Z",
        endTime: "2026-08-16T04:57:28.300Z",
      },
    ],
  };

  assert.equal(projectTrace(trace, runId), null);
});

test("根 observation 已到达时保留其调用子节点", () => {
  const runId = "d771e2a9-6ce3-4b08-bbfb-15182ec72514";
  const trace = {
    id: "trace-complete",
    observations: [
      {
        id: "root",
        parentObservationId: "otel-parent-not-returned",
        name: "feedback-repair-run",
        type: "AGENT",
        metadata: { run_id: runId },
        startTime: "2026-08-16T04:57:27.000Z",
        endTime: "2026-08-16T04:57:29.000Z",
      },
      {
        id: "tool",
        parentObservationId: "root",
        name: "run-reproduction",
        type: "TOOL",
        startTime: "2026-08-16T04:57:27.500Z",
        endTime: "2026-08-16T04:57:28.300Z",
      },
    ],
  };

  const projected = projectTrace(trace, runId);
  assert.ok(projected);
  assert.equal(projected.attempts, 1);
  assert.deepEqual(
    projected.root.children.map((item) => item.name),
    ["run-reproduction"],
  );
});

test("run_id 被脱敏时只回退到命名根而不是孤儿调用", () => {
  const runId = "60ab6f0e-4908-4881-98a1-0dadc0c04635";
  const trace = {
    id: "trace-masked-run-id",
    observations: [
      {
        id: "root",
        parentObservationId: "otel-parent-not-returned",
        name: "feedback-repair-run",
        type: "AGENT",
        metadata: { run_id: "60ab6f0e-[REDACTED_PHONE]a1-0dadc0c04635" },
        startTime: "2026-08-16T04:57:27.000Z",
        endTime: "2026-08-16T04:57:29.000Z",
      },
      {
        id: "tool",
        parentObservationId: "root",
        name: "run-reproduction",
        type: "TOOL",
        startTime: "2026-08-16T04:57:27.500Z",
        endTime: "2026-08-16T04:57:28.300Z",
      },
      {
        id: "orphan-child",
        parentObservationId: "another-root-not-indexed",
        name: "generate-test",
        type: "GENERATION",
        startTime: "2026-08-16T04:57:28.400Z",
        endTime: "2026-08-16T04:57:28.900Z",
      },
    ],
  };

  const projected = projectTrace(trace, runId);
  assert.ok(projected);
  assert.equal(projected.attempts, 1);
  assert.deepEqual(
    projected.root.children.map((item) => item.name),
    ["run-reproduction"],
  );
});
