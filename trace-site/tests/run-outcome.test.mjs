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
const { describeOutcome, deriveStages, stageOf } = module.exports;

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

test("后续安全策略拒绝不伪装成零工具的提示词注入", () => {
  const outcome = describeOutcome({
    status: "security_rejected",
    route: "accepted_backend_bug",
    pr_url: null,
    issue_url: null,
  });

  assert.equal(outcome.label, "安全拦截");
  assert.match(outcome.detail, /本地安全策略/);
  assert.doesNotMatch(outcome.detail, /工具调用为 0|检测到提示词注入/);
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

test("验证通过的 dry-run 优先显示候选修复而不是无法复现", () => {
  const outcome = describeOutcome({
    status: "completed",
    route: "accepted_backend_bug",
    pr_url: null,
    issue_url: null,
    reproductionDisposition: "reproduced",
    validationPassed: true,
    hasValidatedPatch: true,
    dry_run: true,
  });

  assert.equal(outcome.label, "候选修复已验证");
  assert.equal(outcome.tone, "good");
  assert.match(outcome.detail, /演练运行/);
});

test("独立验证失败不显示成无法复现", () => {
  assert.equal(
    describeOutcome({
      status: "completed",
      route: "accepted_backend_bug",
      pr_url: null,
      issue_url: null,
      reproductionDisposition: "reproduced",
      validationPassed: false,
      hasValidatedPatch: false,
    }).label,
    "独立验证未通过",
  );
});

test("Repair Agent 的复用工具按受信 phase 归入复现或修复", () => {
  const observation = {
    id: "tool-1",
    name: "read-source-file",
    type: "tool",
    startMs: 0,
    durationMs: 1,
    status: "success",
    children: [],
  };

  assert.equal(
    stageOf({ ...observation, input: { phase: "reproducing" } }),
    "reproduce",
  );
  assert.equal(
    stageOf({ ...observation, input: { phase: "repairing" } }),
    "repair",
  );
  assert.equal(
    stageOf({ ...observation, name: "repair-agent-model", input: { phase: "repairing" } }),
    "repair",
  );
});
