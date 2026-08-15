import type { Observation, RunDetailData, RunPublic, RunTrace } from "@/lib/types";
import { heroDiff } from "@/lib/mock/diff";

/**
 * M2 阶段的构造数据，用于在接入真实数据库前确定详情页的视觉与交互。
 *
 * 结构、字段和 observation 名称严格对齐真实契约（视图列、领域模型、
 * observability.md 第 3 节的 Trace 结构），但所有数值、哈希与时间都是构造的。
 * 唯一取自真实运行的是案例主题与 PR 链接。
 *
 * 页面会显示 mock 横幅；M3 接入真实数据后本文件仅保留给本地开发使用。
 */

const RUN_ID = "9d3f7b12-4e88-4c05-9a61-2f0b8e5d7c34";
const SELECTOR = "test_feedback_4f2a9c31_mermaid_diagram_renders";

let seq = 0;
const nextId = () => `obs_${String(++seq).padStart(3, "0")}`;

function obs(init: Omit<Observation, "id" | "children"> & { children?: Observation[] }): Observation {
  return { id: nextId(), children: [], ...init };
}

const claim = obs({
  name: "claim-feedback",
  type: "span",
  startMs: 0,
  durationMs: 118,
  status: "success",
  output: { claimed: true, attempt_count: 1 },
});

const gate = obs({
  name: "gate-feedback",
  type: "agent",
  startMs: 130,
  durationMs: 3_320,
  status: "success",
  output: { route: "accepted_backend_bug", category: "docx_structure" },
  children: [
    obs({
      name: "classify-intent",
      type: "generation",
      startMs: 152,
      durationMs: 3_248,
      status: "success",
      model: "deepseek-ai/DeepSeek-V4-Flash",
      usage: { input: 1_842, output: 214, total: 2_056 },
      input: { feedback_hash: "sha256:2f9c…a41d", markdown_bytes: 1_284, feedback_type: "bug" },
      output: {
        intent: "bug_report",
        category: "docx_structure",
        relevance: 0.94,
        injection_suspected: false,
        requires_extension_change: false,
        reason: "[REDACTED_SUMMARY]",
      },
      metadata: { operation: "gate", prompt_version: "gate-v6", provider: "openai_compatible" },
    }),
  ],
});

const prepare = obs({
  name: "prepare-source",
  type: "span",
  startMs: 3_500,
  durationMs: 6_318,
  status: "success",
  output: {
    base_sha: "c41f8a7d92b6e0533ac18f4b7e2d9506a83c7f14",
    source_snapshot_sha256: "7b21e4c8f0a35d9e6412bc78d05f9a3e14762b8c0d5f39ae72c1846b0e93df5a",
    files: 218,
  },
});

const reproduce = obs({
  name: "reproduce",
  type: "agent",
  startMs: 9_900,
  durationMs: 128_600,
  status: "success",
  output: { disposition: "reproduced", round: 1 },
  children: [
    obs({
      name: "plan-reproduction",
      type: "generation",
      startMs: 9_950,
      durationMs: 11_350,
      status: "success",
      model: "deepseek-ai/DeepSeek-V4-Flash",
      usage: { input: 2_140, output: 486, total: 2_626 },
      output: {
        hypothesis: "[REDACTED_SUMMARY]",
        oracle: { kind: "docx_xpath", validator: "minimum_drawing_count", minimum: 1 },
        target_test_selector: SELECTOR,
        expected_failure_kind: "assertion",
        files_to_read: ["backend/app/pandoc_runner.py", "backend/app/normalizer.py"],
      },
      metadata: { operation: "plan_reproduction", prompt_version: "reproduction-v4", round: 1 },
    }),
    obs({
      name: "read-source-file",
      type: "tool",
      startMs: 21_400,
      durationMs: 356,
      status: "success",
      input: { path: "backend/app/pandoc_runner.py", start_line: 1, end_line: 240 },
      output: { lines: 240, bytes: 9_612 },
      metadata: { round: 1 },
    }),
    obs({
      name: "read-source-file",
      type: "tool",
      startMs: 21_800,
      durationMs: 291,
      status: "success",
      input: { path: "backend/app/normalizer.py", start_line: 1, end_line: 240 },
      output: { lines: 240, bytes: 8_845 },
      metadata: { round: 1 },
    }),
    obs({
      name: "generate-test",
      type: "generation",
      startMs: 22_200,
      durationMs: 25_700,
      status: "success",
      model: "deepseek-ai/DeepSeek-V4-Flash",
      usage: { input: 6_320, output: 1_284, total: 7_604 },
      output: {
        edits: [{ path: "backend/tests/test_feedback_regressions.py", mode: "append" }],
        target_test_selector: SELECTOR,
        oracle: { kind: "docx_xpath", validator: "minimum_drawing_count", minimum: 1 },
        reason: "[REDACTED_SUMMARY]",
        files_needed_for_fix: ["backend/app/pandoc_runner.py"],
      },
      metadata: { operation: "generate_test", prompt_version: "reproduction-v4", round: 1 },
    }),
    obs({
      name: "submit-test-edits",
      type: "tool",
      startMs: 48_000,
      durationMs: 262,
      status: "success",
      input: { edits: 1, paths: ["backend/tests/test_feedback_regressions.py"] },
      output: {
        test_patch_sha256: "e3a97c15d842b60f7e3c9018a5d24bf6710e83c95df2a4106bc8e7532904fd18",
        added_lines: 34,
        removed_lines: 0,
      },
      metadata: { round: 1 },
    }),
    obs({
      name: "run-reproduction",
      type: "tool",
      startMs: 48_400,
      durationMs: 89_980,
      status: "success",
      input: { selector: SELECTOR, image_digest: "sha256:9f4c…7b02" },
      output: {
        exit_code: 1,
        timed_out: false,
        target_outcome: "failed",
        target_failure_type: "AssertionError",
        tests: 1,
        failures: 1,
        disposition: "reproduced",
      },
      metadata: { round: 1, cpu_limit: "2", memory_limit: "2g" },
    }),
  ],
});

const repair = obs({
  name: "repair",
  type: "agent",
  startMs: 138_600,
  durationMs: 130_300,
  status: "success",
  output: { disposition: "target_passed", round: 1 },
  children: [
    obs({
      name: "generate-fix",
      type: "generation",
      startMs: 138_700,
      durationMs: 33_700,
      status: "success",
      model: "deepseek-ai/DeepSeek-V4-Flash",
      usage: { input: 7_980, output: 1_642, total: 9_622 },
      output: {
        edits: [{ path: "backend/app/pandoc_runner.py", mode: "replace" }],
        summary: "[REDACTED_SUMMARY]",
        risk_level: "low",
        extension_sync_required: false,
      },
      metadata: { operation: "generate_fix", prompt_version: "repair-v3", round: 1 },
    }),
    obs({
      name: "submit-fix-edits",
      type: "tool",
      startMs: 172_500,
      durationMs: 284,
      status: "success",
      input: { edits: 1, paths: ["backend/app/pandoc_runner.py"] },
      output: {
        fix_patch_sha256: "b58c2049e7d31af6928c40db15e73f8a26094cd7bf1e58230a6c94718df205e3",
        added_lines: 22,
        removed_lines: 6,
      },
      metadata: { round: 1 },
    }),
    obs({
      name: "run-target-validation",
      type: "tool",
      startMs: 172_900,
      durationMs: 95_900,
      status: "success",
      input: { selector: SELECTOR },
      output: { exit_code: 0, target_outcome: "passed", tests: 1, failures: 0 },
      metadata: { round: 1 },
    }),
  ],
});

const validate = obs({
  name: "validate-final",
  type: "span",
  startMs: 269_000,
  durationMs: 103_600,
  status: "success",
  output: { passed: true },
  children: [
    obs({
      name: "reproduce-baseline",
      type: "tool",
      startMs: 269_100,
      durationMs: 26_300,
      status: "success",
      output: { executed: true, expected_failure_observed: true },
    }),
    obs({
      name: "run-target-tests",
      type: "tool",
      startMs: 295_500,
      durationMs: 27_300,
      status: "success",
      output: { passed: true, tests: 1, failures: 0 },
    }),
    obs({
      name: "run-full-tests",
      type: "tool",
      startMs: 322_900,
      durationMs: 45_300,
      status: "success",
      output: { passed: true, tests: 142, failures: 0, errors: 0, skipped: 3, baseline_skipped: 3 },
    }),
    obs({
      name: "validate-docx",
      type: "tool",
      startMs: 368_300,
      durationMs: 4_200,
      status: "success",
      output: {
        passed: true,
        checks: { valid_zip: true, required_parts_present: true, xml_parseable: true, minimum_drawing_count: true },
      },
    }),
  ],
});

const publish = obs({
  name: "publish-pr",
  type: "tool",
  startMs: 372_700,
  durationMs: 3_190,
  status: "success",
  input: {
    base_sha: "c41f8a7d92b6e0533ac18f4b7e2d9506a83c7f14",
    branch: "agent/fix-mermaid-drawing-4f2a9c31",
    patch_sha256: "d70b16e5a4c839f2015be7d3948ca62710f85b3d92e04c7168a5df2be1470c93",
  },
  output: { pr_number: 1, reused: false },
});

const finalize = obs({
  name: "finalize",
  type: "span",
  startMs: 376_000,
  durationMs: 176,
  status: "success",
  output: { final_status: "completed", route: "accepted_backend_bug" },
});

const root: Observation = obs({
  name: "feedback-repair-run",
  type: "agent",
  startMs: 0,
  durationMs: 376_176,
  status: "success",
  input: { feedback_hash: "sha256:2f9c…a41d" },
  output: { route: "accepted_backend_bug", status: "completed" },
  metadata: {
    run_id: RUN_ID,
    graph_version: "graph-v5",
    policy_version: "publication-policy-v3",
    sandbox_image_digest: "sha256:9f4c…7b02",
    environment: "production",
  },
  children: [claim, gate, prepare, reproduce, repair, validate, publish, finalize],
});

const trace: RunTrace = {
  runId: RUN_ID,
  totalDurationMs: root.durationMs,
  attempts: 1,
  root,
};

const run: RunPublic = {
  id: RUN_ID,
  run_ref: "a7c41e93b520",
  status: "completed",
  route: "accepted_backend_bug",
  category: "docx_structure",
  dry_run: false,
  base_sha: "c41f8a7d92b6e0533ac18f4b7e2d9506a83c7f14",
  extension_version: "0.1.1",
  provider: "openai_compatible",
  model: "deepseek-ai/DeepSeek-V4-Flash",
  graph_version: "graph-v5",
  prompt_versions: {
    gate: "gate-v6",
    plan_reproduction: "reproduction-v4",
    generate_test: "reproduction-v4",
    generate_fix: "repair-v3",
  },
  policy_version: "publication-policy-v3",
  model_calls: 4,
  tool_calls: 11,
  input_tokens: 18_282,
  output_tokens: 3_626,
  total_tokens: 21_908,
  // 维护者尚未配置模型单价，因此为 0；页面必须显示"未配置单价"。
  estimated_cost: "0",
  validated_patch_sha256:
    "d70b16e5a4c839f2015be7d3948ca62710f85b3d92e04c7168a5df2be1470c93",
  pr_url: "https://github.com/yyqqCoding/MDToWord/pull/1",
  error_code: null,
  started_at: "2026-08-11T09:14:22.000Z",
  finished_at: "2026-08-11T09:20:38.000Z",
  classification: {
    route: "accepted_backend_bug",
    category: "docx_structure",
    risk: "low",
    policy_reason: "automatable_backend_bug",
    classification: {
      intent: "bug_report",
      category: "docx_structure",
      relevance: 0.94,
      sufficient_information: true,
      injection_suspected: false,
      requires_extension_change: false,
    },
    model_calls: 1,
    tool_calls: 0,
  },
  reproduction: {
    disposition: "reproduced",
    round: 1,
    target_test_selector: SELECTOR,
    expected_failure_kind: "assertion",
    failure_code: "target_assertion_failure",
  },
  repair: { disposition: "target_passed", round: 1, failure_code: null },
  validation: {
    passed: true,
    base_sha: "c41f8a7d92b6e0533ac18f4b7e2d9506a83c7f14",
    source_snapshot_sha256:
      "7b21e4c8f0a35d9e6412bc78d05f9a3e14762b8c0d5f39ae72c1846b0e93df5a",
    test_patch_sha256:
      "e3a97c15d842b60f7e3c9018a5d24bf6710e83c95df2a4106bc8e7532904fd18",
    fix_patch_sha256:
      "b58c2049e7d31af6928c40db15e73f8a26094cd7bf1e58230a6c94718df205e3",
    target_test_selector: SELECTOR,
    baseline_reproduction: { executed: true, expected_failure_observed: true },
    target_validation: { passed: true },
    full_validation: {
      passed: true,
      tests: 142,
      failures: 0,
      errors: 0,
      skipped: 3,
      baseline_skipped: 3,
    },
    docx_validation: {
      passed: true,
      checks: {
        valid_zip: true,
        required_parts_present: true,
        xml_parseable: true,
        minimum_drawing_count: true,
      },
    },
    changed_files: ["backend/app/pandoc_runner.py"],
    validated_patch_sha256:
      "d70b16e5a4c839f2015be7d3948ca62710f85b3d92e04c7168a5df2be1470c93",
    failure_code: null,
  },
};

export const HERO_RUN_ID = RUN_ID;

export const heroRunDetail: RunDetailData = {
  run,
  trace,
  narrative: {
    title: "导出 Word 后只显示 Mermaid 源码，未生成流程图",
    summary:
      "用户在 AI 对话里得到一段含 Mermaid 流程图的 Markdown，导出成 Word 后图变成了一段代码文本。" +
      "Agent 判定这是后端转换缺陷，在隔离沙箱里写了一个「DOCX 中至少应出现 1 个图形」的断言测试并复现成功，" +
      "随后修改后端渲染逻辑，通过基线、目标、全量与 DOCX 结构四道独立验证后提交了 PR。",
  },
  diff: heroDiff,
  isMock: true,
};

export function getMockRun(id: string): RunDetailData | null {
  return id === RUN_ID ? heroRunDetail : null;
}

export { run as heroRun };
