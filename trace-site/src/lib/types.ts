/**
 * DTO 定义与 `public.agent_run_public` 视图逐列对应。
 *
 * 这里出现的字段就是浏览器能拿到的全部字段。视图已在数据库侧做过字段级白名单，
 * 本文件是第二道防线：任何新增字段都必须同时改视图和这里，才可能到达页面。
 *
 * 明确不存在于本类型中、且不得加入的字段：
 *   error_message / *.failure_summary / classification.classification.reason
 *   feedback_id / claim_token / trace_id / langfuse_trace_id
 *   artifact_path / task_artifact_ref / validation.validated_patch_ref
 */

export type RunStatus =
  | "created"
  | "gating"
  | "preparing_source"
  | "reproducing"
  | "repairing"
  | "validating"
  | "publishing"
  | "completed"
  | "failed"
  | "cancelled"
  | "budget_exhausted"
  | "security_rejected"
  | "stale_base";

export type GateRoute =
  | "accepted_backend_bug"
  | "rejected_irrelevant"
  | "quarantined_security"
  | "out_of_scope"
  | "needs_human"
  | "duplicate";

export type GateCategory =
  | "conversion_crash"
  | "formula_parsing"
  | "table_parsing"
  | "heading_parsing"
  | "list_parsing"
  | "docx_structure"
  | "backend_normalization"
  | "extension_ui"
  | "visual_quality"
  | "unknown";

export type GateIntent =
  | "bug_report"
  | "feature_request"
  | "unrelated"
  | "spam"
  | "unknown";

export type RiskLevel = "unknown" | "low" | "medium" | "high";

export type ReproductionDisposition =
  | "reproduced"
  | "not_reproduced"
  | "invalid_test"
  | "baseline_regression"
  | "security_rejected";

export type RepairDisposition =
  | "target_passed"
  | "target_failed"
  | "invalid_result"
  | "needs_human"
  | "security_rejected";

export type ExpectedFailureKind = "assertion" | "unexpected_conversion_error";

export interface GateClassificationPublic {
  intent: GateIntent;
  category: GateCategory;
  relevance: number;
  sufficient_information: boolean;
  injection_suspected: boolean;
  requires_extension_change: boolean;
  // 注意：reason 被视图排除，此处刻意没有该字段。
}

export interface GateResultPublic {
  route: GateRoute;
  category: GateCategory;
  risk: RiskLevel;
  /** 代码字面量，如 description_blank / open_duplicate_found。 */
  policy_reason: string;
  classification: GateClassificationPublic | null;
  model_calls: number;
  tool_calls: number;
}

export interface ReproductionPublic {
  disposition: ReproductionDisposition;
  round: number;
  target_test_selector: string;
  expected_failure_kind: ExpectedFailureKind;
  failure_code: string | null;
}

export interface RepairPublic {
  disposition: RepairDisposition;
  round: number;
  failure_code: string | null;
}

export interface ValidationPublic {
  passed: boolean;
  base_sha: string;
  source_snapshot_sha256: string;
  test_patch_sha256: string;
  fix_patch_sha256: string;
  target_test_selector: string;
  baseline_reproduction: { executed: boolean; expected_failure_observed: boolean };
  target_validation: { passed: boolean };
  full_validation: {
    passed: boolean;
    tests: number;
    failures: number;
    errors: number;
    skipped: number;
    baseline_skipped: number;
  };
  docx_validation: { passed: boolean; checks: Record<string, boolean> };
  changed_files: string[];
  validated_patch_sha256: string;
  failure_code: string | null;
}

export interface RunPublic {
  id: string;
  /** left(md5(feedback_id), 12)，不可逆，仅供展示与检索。 */
  run_ref: string;
  status: RunStatus;
  route: GateRoute | null;
  category: GateCategory | null;
  dry_run: boolean;
  base_sha: string | null;
  extension_version: string;
  provider: string | null;
  model: string | null;
  graph_version: string | null;
  prompt_versions: Record<string, string>;
  policy_version: string | null;
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  /**
   * 单价未配置时为 "0"，只代表未估算，不代表上游 API 免费。
   * 展示层必须区分这两种含义，见 formatCost()。
   */
  estimated_cost: string;
  validated_patch_sha256: string | null;
  pr_url: string | null;
  error_code: string | null;
  started_at: string;
  finished_at: string | null;
  classification: GateResultPublic | null;
  reproduction: ReproductionPublic | null;
  repair: RepairPublic | null;
  validation: ValidationPublic | null;
}

/* ------------------------------------------------------------------ */
/* Trace 快照                                                          */
/* ------------------------------------------------------------------ */

export type ObservationType = "agent" | "span" | "generation" | "tool";

export interface ObservationUsage {
  input: number;
  output: number;
  total: number;
}

export interface Observation {
  id: string;
  name: string;
  type: ObservationType;
  /** 相对 Trace 起点的毫秒偏移。 */
  startMs: number;
  durationMs: number;
  status: "success" | "error";
  /** 失败时的稳定错误码，不是自由文本。 */
  errorCode?: string;
  model?: string;
  usage?: ObservationUsage;
  /** 已脱敏的结构化摘要，只含路径、大小、哈希、计数与分类结果。 */
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  children: Observation[];
}

export interface RunTrace {
  runId: string;
  totalDurationMs: number;
  /**
   * 该运行在 Langfuse 中的根节点数量。
   * Controller 用确定性 Trace ID，同一次运行被 checkpoint 恢复多次时会产生多个根，
   * 站点把它们合并成一条时间轴，这个数字用于说明「被恢复过几次」。
   */
  attempts: number;
  root: Observation;
}

/** 详情页的完整数据包。 */
export interface RunDetailData {
  run: RunPublic;
  trace: RunTrace | null;
  /** 人工撰写的案例说明，与数据库解耦，不含用户原文。 */
  narrative: { title: string; summary: string } | null;
  /**
   * 已合并/开放 PR 的代码改动。
   * 数据源是 GitHub 公开 API，不是 Agent 的受控 artifact —— 公开仓库的 PR diff
   * 本来就是公开信息，这样既能展示真实改动，又完全绕开脱敏边界。
   * 未产出 PR 的运行此处为 null，语义上也本就不该有 diff。
   */
  diff: PatchDiff | null;
  /** M2/M3 过渡期为 true：页面顶部会显示醒目的 mock 横幅。 */
  isMock: boolean;
}

export type DiffLineKind = "context" | "add" | "del";

export interface DiffLine {
  kind: DiffLineKind;
  oldNumber: number | null;
  newNumber: number | null;
  text: string;
}

export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

export interface PatchDiffFile {
  path: string;
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
}

export interface PatchDiff {
  prNumber: number;
  prUrl: string;
  merged: boolean;
  files: PatchDiffFile[];
}

/** 列表页与概览页使用的精简行。 */
export interface RunListItem {
  id: string;
  run_ref: string;
  title: string;
  route: GateRoute | null;
  category: GateCategory | null;
  status: RunStatus;
  durationMs: number | null;
  total_tokens: number;
  pr_url: string | null;
  started_at: string;
}

/** 概览页 KPI。只包含真实可算的量，不做趋势与环比。 */
export interface OverviewStats {
  totalRuns: number;
  pullRequests: number;
  averageDurationMs: number;
  totalTokens: number;
}

