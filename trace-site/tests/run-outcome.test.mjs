import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = await readFile(
  new URL("../src/lib/run-graph.ts", import.meta.url),
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
const { describeOutcome, deriveStages } = module.exports;

test("安全和无关 route 优先于 completed 通用终态", () => {
  assert.equal(
    describeOutcome({
      status: "completed",
      route: "quarantined_security",
      pr_url: null,
      issue_url: null,
    }).label,
    "安全拦截",
  );
  assert.equal(
    describeOutcome({
      status: "completed",
      route: "rejected_irrelevant",
      pr_url: null,
      issue_url: null,
    }).label,
    "已忽略",
  );
});

test("Issue 结果与 PR 分开显示且不生成代码阶段证据", () => {
  const run = {
    status: "completed",
    route: "issue_required",
    pr_url: null,
    issue_url: "https://github.test/issues/1",
    classification: {},
    base_sha: null,
    reproduction: null,
    repair: null,
    validation: null,
  };

  assert.equal(describeOutcome(run).label, "已创建 Issue");
  const stages = deriveStages(run, null);
  assert.equal(stages.find((stage) => stage.key === "publish").label, "创建 Issue");
  assert.equal(stages.find((stage) => stage.key === "publish").state, "done");
  assert.equal(stages.find((stage) => stage.key === "prepare").state, "skipped");
});
