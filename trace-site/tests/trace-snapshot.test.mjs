import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");

async function loadSnapshotRules() {
  const source = await readFile(
    new URL("../src/lib/trace-snapshot.ts", import.meta.url),
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
  return module.exports;
}

const { isTraceSnapshotUsable } = await loadSnapshotRules();

function snapshotWithChildren(children) {
  return {
    runId: "run-id",
    totalDurationMs: 1,
    attempts: 1,
    root: {
      id: "root",
      name: "feedback-repair-run",
      type: "agent",
      startMs: 0,
      durationMs: 1,
      status: "success",
      children,
    },
  };
}

test("有调用计数但零 observation 的旧快照需要重抓", () => {
  assert.equal(isTraceSnapshotUsable(snapshotWithChildren([]), 3), false);
});

test("有 observation 的调用快照可直接使用", () => {
  const child = {
    id: "tool",
    name: "run-reproduction",
    type: "tool",
    startMs: 0,
    durationMs: 1,
    status: "success",
    children: [],
  };
  assert.equal(isTraceSnapshotUsable(snapshotWithChildren([child]), 3), true);
});

test("真实零调用运行允许只有合成根", () => {
  assert.equal(isTraceSnapshotUsable(snapshotWithChildren([]), 0), true);
});
