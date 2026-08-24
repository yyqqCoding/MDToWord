import type { StageKey } from "@/lib/run-graph";
import type { RunPublic } from "@/lib/types";

/**
 * 每个阶段的叙述与关键产物。
 *
 * 这是详情页此前缺失的一层。原先页面直接从「7 个阶段芯片」跳到「22 条调用瀑布」，
 * 中间没有回答访客真正关心的问题：这一步 Agent 做了什么判断、产出了什么。
 * 比如「选了 assert_minimum_drawing_count 这条断言」「读了 3 个文件」「第 2 轮才复现成功」
 * ——这些才是故事，而不是逐条调用的毫秒数。
 */

export interface StageFact {
  label: string;
  value: string;
  /** 长路径、哈希等需要等宽并允许换行。 */
  mono?: boolean;
}

export interface StageStory {
  narrative: string;
  facts: StageFact[];
}

const DISPOSITION_TEXT: Record<string, string> = {
  reproduced: "已复现",
  not_reproduced: "未复现",
  invalid_test: "测试无效",
  baseline_regression: "基线回归",
  security_rejected: "安全拒绝",
  target_passed: "目标测试通过",
  target_failed: "目标测试失败",
  invalid_result: "结果无效",
  needs_human: "转人工",
};

const INTENT_TEXT: Record<string, string> = {
  bug_report: "缺陷反馈",
  feature_request: "功能建议",
  unrelated: "无关内容",
  spam: "垃圾内容",
  unknown: "无法判断",
};

const ROUTE_TEXT: Record<string, string> = {
  accepted_backend_bug: "受理为后端缺陷",
  rejected_irrelevant: "判定为无关",
  quarantined_security: "安全隔离",
  issue_required: "创建人工处理 Issue",
  out_of_scope: "超出范围",
  needs_human: "转人工",
  duplicate: "重复反馈",
};

const FAILURE_KIND_TEXT: Record<string, string> = {
  assertion: "断言失败",
  unexpected_conversion_error: "转换异常",
};

export function stageStory(key: StageKey, run: RunPublic): StageStory {
  switch (key) {
    case "claim":
      return {
        narrative:
          "从待处理队列中原子领取这条反馈。同一条反馈不会被多个 Controller 实例重复处理，租约到期未完成会自动回收。",
        facts: [],
      };

    case "gate": {
      const c = run.classification;
      const inner = c?.classification ?? null;
      const facts: StageFact[] = [];
      if (inner) {
        facts.push(
          { label: "意图", value: INTENT_TEXT[inner.intent] ?? inner.intent },
          { label: "相关度", value: inner.relevance.toFixed(2) },
          { label: "信息是否充分", value: inner.sufficient_information ? "充分" : "不足" },
          { label: "疑似提示词注入", value: inner.injection_suspected ? "是" : "否" },
          {
            label: "需要扩展改动",
            value: inner.requires_extension_change ? "是（不可自动修复）" : "否",
          },
        );
      }
      if (c) {
        facts.push(
          { label: "路由", value: ROUTE_TEXT[c.route] ?? c.route },
          { label: "策略依据", value: c.policy_reason, mono: true },
        );
      }
      return {
        narrative:
          "一次无工具的模型调用，判定这条反馈的意图与类别，同时检查是否为提示词注入。只有被判定为可自动修复的后端缺陷才会继续，其余直接进入终态。",
        facts,
      };
    }

    case "prepare": {
      const facts: StageFact[] = [];
      if (run.base_sha) facts.push({ label: "基线 commit", value: run.base_sha, mono: true });
      if (run.validation?.source_snapshot_sha256) {
        facts.push({
          label: "源码快照",
          value: run.validation.source_snapshot_sha256,
          mono: true,
        });
      }
      return {
        narrative:
          "锁定 GitHub main 上的一个 commit 作为源码快照。此后复现、修复与验证全都基于这一份快照，期间 main 的任何变动都不会影响本次结果。",
        facts,
      };
    }

    case "reproduce": {
      const r = run.reproduction;
      const facts: StageFact[] = [];
      if (r) {
        facts.push(
          { label: "判定", value: DISPOSITION_TEXT[r.disposition] ?? r.disposition },
          { label: "轮次", value: `第 ${r.round} 轮成功` },
          {
            label: "期望失败方式",
            value: FAILURE_KIND_TEXT[r.expected_failure_kind] ?? r.expected_failure_kind,
          },
          { label: "目标测试", value: r.target_test_selector, mono: true },
        );
      }
      return {
        narrative:
          "在隔离沙箱里生成一个会失败的测试，用来证明缺陷真实存在。断言只能从已登记的 Oracle 列表中挑选，模型不能提交可执行表达式。测试在基线上必须以预期方式失败，否则不算复现。",
        facts,
      };
    }

    case "repair": {
      const r = run.repair;
      const changed = run.validation?.changed_files ?? [];
      const facts: StageFact[] = [];
      if (r) {
        facts.push(
          { label: "判定", value: DISPOSITION_TEXT[r.disposition] ?? r.disposition },
          { label: "轮次", value: `第 ${r.round} 轮` },
        );
      }
      for (const path of changed) {
        facts.push({ label: "改动文件", value: path, mono: true });
      }
      return {
        narrative:
          "读取相关源码后生成结构化编辑，只允许改动后端白名单文件。补丁必须让上一步那个失败的测试转为通过，否则本轮判定为失败。",
        facts,
      };
    }

    case "validate": {
      const v = run.validation;
      const facts: StageFact[] = [];
      if (v) {
        facts.push(
          {
            label: "基线复现",
            value: v.baseline_reproduction.expected_failure_observed
              ? "修复前确实失败"
              : "未观察到预期失败",
          },
          { label: "目标测试", value: v.target_validation.passed ? "通过" : "未通过" },
          {
            label: "全量后端测试",
            value: `${v.full_validation.tests} 项 · 失败 ${v.full_validation.failures} · 跳过 ${v.full_validation.skipped}`,
          },
          {
            label: "DOCX 结构",
            value: Object.keys(v.docx_validation.checks).join("、") || "—",
            mono: true,
          },
        );
      }
      return {
        narrative:
          "用彼此独立的沙箱作业重跑四道验证：先确认修复前确实失败，再确认修复后目标测试通过，然后跑全量后端测试防回归，最后检查产出的 DOCX 结构。结论由 Controller 自己计算，不采信模型的自评。",
        facts,
      };
    }

    case "publish": {
      const facts: StageFact[] = [];
      if (run.validated_patch_sha256) {
        facts.push({ label: "补丁哈希", value: run.validated_patch_sha256, mono: true });
      }
      if (run.pr_url) facts.push({ label: "Pull Request", value: run.pr_url, mono: true });
      return {
        narrative:
          "只有四道验证全部通过才会创建 Pull Request。Agent 到此为止 —— 绝不自动合并、绝不自动部署，是否采纳由维护者决定。",
        facts,
      };
    }
  }
}
