import type {
  GateRoute,
  Observation,
  RunPublic,
  RunStatus,
  RunTrace,
} from "@/lib/types";

/* ------------------------------------------------------------------ */
/* 固定流水线阶段                                                       */
/* ------------------------------------------------------------------ */

/**
 * 阶段序列与 observation 名称对应关系来自
 * docs/AgentRequirements/observability.md 第 3 节，名称是稳定契约。
 * 因为序列固定，布局手工排定，不使用自动布局算法 —— 每次运行的图长得一样，
 * 便于横向对比与讲解。
 */
export const RUN_STAGES = [
  {
    key: "claim",
    label: "领取反馈",
    hint: "原子领取，避免多实例重复处理",
    observationName: "claim-feedback",
    runStatus: "created" as RunStatus,
  },
  {
    key: "gate",
    label: "分类与安全",
    hint: "判断是否为可自动修复的后端缺陷，并拦截注入",
    observationName: "gate-feedback",
    runStatus: "gating" as RunStatus,
  },
  {
    key: "prepare",
    label: "固定源码快照",
    hint: "锁定 GitHub main 的一个 commit，全程不再变动",
    observationName: "prepare-source",
    runStatus: "preparing_source" as RunStatus,
  },
  {
    key: "reproduce",
    label: "沙箱复现",
    hint: "在隔离容器中生成测试并证明缺陷真实存在",
    observationName: "reproduce",
    runStatus: "reproducing" as RunStatus,
  },
  {
    key: "repair",
    label: "生成修复",
    hint: "只允许修改后端白名单文件",
    observationName: "repair",
    runStatus: "repairing" as RunStatus,
  },
  {
    key: "validate",
    label: "独立验证",
    hint: "重跑基线、目标测试、全量测试与 DOCX 结构检查",
    observationName: "validate-final",
    runStatus: "validating" as RunStatus,
  },
  {
    key: "publish",
    label: "创建 PR",
    hint: "验证通过才创建；绝不自动合并或部署",
    observationName: "publish-pr",
    runStatus: "publishing" as RunStatus,
  },
] as const;

export type StageKey = (typeof RUN_STAGES)[number]["key"];

/**
 * observation 名称 → 所属阶段。
 *
 * 真实 Trace 是根节点下的一层扁平调用，文档 §3 描述的 gate-feedback / reproduce /
 * repair / validate-final 等分组节点并不存在。因此阶段归属由名称映射决定，
 * 不依赖树的层级 —— 将来 Agent 若补上分组节点，这里同样能正确归组。
 */
const OBSERVATION_STAGE: Record<string, StageKey> = {
  "claim-feedback": "claim",

  "gate-feedback": "gate",
  "classify-intent": "gate",

  "prepare-source": "prepare",
  "prepare-source-snapshot": "prepare",

  reproduce: "reproduce",
  "plan-reproduction": "reproduce",
  "read-source-file": "reproduce",
  "generate-test": "reproduce",
  "submit-test-edits": "reproduce",
  "run-reproduction": "reproduce",

  repair: "repair",
  "read-fix-source-file": "repair",
  "generate-fix": "repair",
  "submit-fix-edits": "repair",
  "run-target-validation": "repair",

  "validate-final": "validate",
  "reproduce-baseline": "validate",
  "run-target-tests": "validate",
  "run-full-tests": "validate",
  "validate-docx": "validate",

  "publish-pr": "publish",
  "publish-issue": "publish",
  finalize: "publish",
};

export function stageOf(observation: Observation): StageKey | null {
  return OBSERVATION_STAGE[observation.name] ?? null;
}

/**
 * 阶段是否以失败告终 —— 只看每种调用的**最后一次**尝试。
 *
 * 真实运行里重试很常见：hero run 的 publish-pr 连续失败 3 次后第 4 次成功建出了 PR，
 * submit-test-edits 也是失败一次后重试成功。若按"任一失败即整段失败"判定，
 * 会把明明产出了 PR 的运行标成失败，这是错的。
 */
export function stageFailed(items: Observation[]): boolean {
  const lastByName = new Map<string, Observation>();
  for (const item of items) {
    const previous = lastByName.get(item.name);
    if (!previous || item.startMs > previous.startMs) lastByName.set(item.name, item);
  }
  return [...lastByName.values()].some((item) => item.status === "error");
}

/** 该阶段中失败后被重试的次数；用于展示"重试 N 次后成功"。 */
export function stageRetries(items: Observation[]): number {
  return items.filter((item) => item.status === "error").length;
}

export type StageState = "done" | "failed" | "skipped" | "active" | "pending";

export interface StageView {
  key: StageKey;
  label: string;
  hint: string;
  observationName: string;
  state: StageState;
  durationMs: number | null;
  /** 该阶段失败后被重试的次数；>0 时展示"重试 N 次"。 */
  retries: number;
}

const TERMINAL_STATUSES = new Set<RunStatus>([
  "completed",
  "failed",
  "cancelled",
  "budget_exhausted",
  "security_rejected",
  "stale_base",
]);

/** 运行是否已落终态；与 Agent 侧 TERMINAL_RUN_STATUSES 对应。 */
export function isTerminalStatus(status: RunStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

/**
 * Trace 缺失时的兜底证据：直接看运行摘要里各阶段是否留下了产物。
 *
 * 快照回填是异步的，绝大多数运行在被访问时还没有 Trace。此时不能把阶段一律判成
 * 「未执行」—— 一次产出了 PR 的运行显然走完了全部七步，摘要里的 classification /
 * reproduction / repair / validation / pr_url 就是证据。
 */
function fieldEvidence(run: RunPublic): Record<StageKey, boolean> {
  return {
    claim: true,
    gate: run.classification !== null,
    prepare: run.base_sha !== null,
    reproduce: run.reproduction !== null,
    repair: run.repair !== null,
    validate: run.validation !== null,
    publish: run.pr_url !== null || run.issue_url !== null,
  };
}

/** 各阶段是否以失败告终；正常的「未复现」不算失败，是合法结论。 */
function fieldFailures(run: RunPublic): Partial<Record<StageKey, boolean>> {
  return {
    reproduce: run.reproduction?.disposition === "security_rejected",
    repair:
      run.repair?.disposition === "target_failed" ||
      run.repair?.disposition === "invalid_result" ||
      run.repair?.disposition === "security_rejected",
    validate: run.validation !== null && !run.validation.passed,
    publish: run.status === "stale_base",
  };
}

/** 按阶段汇总 Trace 证据：耗时取该阶段首末调用的跨度。 */
function traceEvidence(
  trace: RunTrace | null,
): Partial<
  Record<StageKey, { durationMs: number; failed: boolean; name: string; retries: number }>
> {
  if (!trace) return {};
  const buckets = new Map<StageKey, Observation[]>();

  for (const { observation } of flattenObservations(trace.root).slice(1)) {
    const key = stageOf(observation);
    if (!key) continue;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(observation);
    else buckets.set(key, [observation]);
  }

  const out: Partial<
    Record<StageKey, { durationMs: number; failed: boolean; name: string; retries: number }>
  > = {};
  for (const [key, items] of buckets) {
    const start = Math.min(...items.map((item) => item.startMs));
    const end = Math.max(...items.map((item) => item.startMs + item.durationMs));
    out[key] = {
      durationMs: Math.max(end - start, 0),
      failed: stageFailed(items),
      retries: stageRetries(items),
      name: items[0].name,
    };
  }
  return out;
}

/**
 * 阶段状态优先由 Trace 推导（有逐次调用的执行证据），
 * Trace 缺失时回落到运行摘要字段，两者都没有才判为未执行。
 */
export function deriveStages(run: RunPublic, trace: RunTrace | null): StageView[] {
  const observed = traceEvidence(trace);
  const evidence = fieldEvidence(run);
  const failures = fieldFailures(run);
  const isTerminal = TERMINAL_STATUSES.has(run.status);
  const activeIndex = RUN_STAGES.findIndex(
    (stage) =>
      stage.runStatus === run.status ||
      (run.status === "publishing_issue" && stage.key === "publish"),
  );

  // run 整体失败但没有具体阶段信号时，把断点标在最后一个有证据的阶段之后
  const lastWithEvidence = RUN_STAGES.reduce(
    (acc, stage, index) => (evidence[stage.key] ? index : acc),
    -1,
  );
  const genericFailureIndex =
    run.status === "failed" || run.status === "budget_exhausted"
      ? lastWithEvidence + 1
      : -1;

  return RUN_STAGES.map((stage, index) => {
    const seen = observed[stage.key];
    const label =
      stage.key === "publish" && run.route === "issue_required"
        ? "创建 Issue"
        : stage.label;
    const hint =
      stage.key === "publish" && run.route === "issue_required"
        ? "只发布脱敏摘要，交由维护者人工处理"
        : stage.hint;

    if (seen) {
      return {
        key: stage.key,
        label,
        hint,
        observationName: seen.name,
        state: seen.failed ? "failed" : "done",
        durationMs: seen.durationMs,
        retries: seen.retries,
      };
    }

    let state: StageState;
    if (evidence[stage.key]) {
      state = failures[stage.key] ? "failed" : "done";
    } else if (index === genericFailureIndex) {
      state = "failed";
    } else if (activeIndex >= 0 && index === activeIndex) {
      state = "active";
    } else if (isTerminal) {
      state = "skipped";
    } else if (activeIndex >= 0 && index < activeIndex) {
      state = "done";
    } else {
      state = "pending";
    }

    return {
      key: stage.key,
      label,
      hint,
      observationName: stage.observationName,
      state,
      durationMs: null,
      retries: 0,
    };
  });
}

/* ------------------------------------------------------------------ */
/* 终态呈现                                                             */
/* ------------------------------------------------------------------ */

export type Tone = "good" | "warn" | "serious" | "critical" | "accent" | "neutral";

export interface OutcomeView {
  label: string;
  detail: string;
  tone: Tone;
}

/** describeOutcome 只需要这几个字段，列表行与完整运行都能满足。 */
export interface OutcomeInput {
  status: RunStatus;
  route: GateRoute | null;
  pr_url: string | null;
  issue_url: string | null;
  reproductionDisposition?: string | null;
}

export function outcomeInputOf(run: RunPublic): OutcomeInput {
  return {
    status: run.status,
    route: run.route,
    pr_url: run.pr_url,
    issue_url: run.issue_url,
    reproductionDisposition: run.reproduction?.disposition ?? null,
  };
}

/**
 * 终态文案面向外行读者，因此不直接暴露 status 枚举值，
 * 而是结合 route 与 pr_url 讲清楚"发生了什么、为什么"。
 */
export function describeOutcome(run: OutcomeInput): OutcomeView {
  // 业务 route 比通用 completed 更具体，必须先判定，避免安全拦截显示成“已结束”。
  if (run.route === "quarantined_security") {
    return {
      label: "安全拦截",
      detail: "检测到提示词注入或越权内容，未调用任何工具或 Publisher。",
      tone: "serious",
    };
  }
  if (run.route === "rejected_irrelevant") {
    return {
      label: "已忽略",
      detail: "Gate 判定为无关内容或垃圾信息，未进入后续流程。",
      tone: "neutral",
    };
  }
  switch (run.status) {
    case "completed":
      if (run.pr_url) {
        return {
          label: "已创建 PR",
          detail: "修复通过独立验证，已提交 Pull Request 等待人工审核。",
          tone: "good",
        };
      }
      if (run.issue_url) {
        return {
          label: "已创建 Issue",
          detail: "需求或前端缺陷已脱敏提交，等待维护者人工处理。",
          tone: "good",
        };
      }
      if (run.route === "issue_required") {
        return {
          label: "需要创建 Issue",
          detail: "分类已经完成；当前运行未获得真实 GitHub 写入授权。",
          tone: "neutral",
        };
      }
      if (run.route === "out_of_scope") {
        return {
          label: "超出自动修复范围",
          detail: "属于功能建议或需要扩展改动，交由人工处理。",
          tone: "neutral",
        };
      }
      if (run.route === "duplicate") {
        return {
          label: "重复反馈",
          detail: "已存在处理中的同内容反馈。",
          tone: "neutral",
        };
      }
      if (
        run.reproductionDisposition &&
        run.reproductionDisposition !== "reproduced"
      ) {
        return {
          label: "无法复现",
          detail: "沙箱中未能稳定复现该缺陷，主动放弃而不是提交空修复。",
          tone: "warn",
        };
      }
      if (run.route === "accepted_backend_bug") {
        return {
          label: "无法复现",
          detail: "沙箱中未能稳定复现该缺陷，主动放弃而不是提交空修复。",
          tone: "warn",
        };
      }
      return {
        label: "已结束",
        detail: "运行正常结束，未产出补丁。",
        tone: "neutral",
      };
    case "security_rejected":
      return {
        label: "安全拦截",
        detail: "检测到提示词注入或越权操作，运行被隔离终止，工具调用为 0。",
        tone: "serious",
      };
    case "failed":
      return {
        label: "运行失败",
        detail: "流程中断，未产出补丁。",
        tone: "critical",
      };
    case "budget_exhausted":
      return {
        label: "预算耗尽",
        detail: "达到单次运行的 token 或轮次上限，主动停止。",
        tone: "warn",
      };
    case "stale_base":
      return {
        label: "基线已过期",
        detail: "验证期间 main 已前进，补丁需要基于新快照重做。",
        tone: "warn",
      };
    case "cancelled":
      return { label: "已取消", detail: "运行被取消。", tone: "neutral" };
    default:
      return {
        label: "进行中",
        detail: "该运行尚未结束。",
        tone: "accent",
      };
  }
}

/** 扁平化 observation 树，供瀑布图按时间顺序渲染。 */
export interface FlatObservation {
  observation: Observation;
  depth: number;
}

export function flattenObservations(root: Observation, depth = 0): FlatObservation[] {
  const rows: FlatObservation[] = [{ observation: root, depth }];
  for (const child of root.children) {
    rows.push(...flattenObservations(child, depth + 1));
  }
  return rows;
}
