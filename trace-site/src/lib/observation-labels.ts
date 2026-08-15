import type { Observation } from "@/lib/types";

/**
 * observation 名称 → 中文标签。
 *
 * 名称清单以真实 Trace 为准，不以设计文档为准：文档 §3 描述的
 * gate-feedback / reproduce / repair / validate-final 等分组节点，Agent 实际并未产生，
 * 真实结构是根节点下的一层扁平调用。这里登记的是实际出现过的名称，
 * 同时保留文档里的名称以兼容将来可能补上的分组节点。
 */
const LABELS: Record<string, string> = {
  "feedback-repair-run": "整次运行",
  "claim-feedback": "领取反馈",
  "gate-feedback": "分类与安全检查",
  "classify-intent": "意图分类",
  "prepare-source": "固定源码快照",
  "prepare-source-snapshot": "固定源码快照",
  reproduce: "沙箱复现",
  "plan-reproduction": "规划复现方案",
  "read-source-file": "读取源码",
  "generate-test": "生成复现测试",
  "submit-test-edits": "提交测试改动",
  "run-reproduction": "执行复现",
  repair: "生成修复",
  "read-fix-source-file": "读取待修文件",
  "generate-fix": "生成补丁",
  "submit-fix-edits": "提交补丁改动",
  "run-target-validation": "目标测试验证",
  "validate-final": "独立验证",
  "reproduce-baseline": "基线复现校验",
  "run-target-tests": "目标测试",
  "run-full-tests": "全量后端测试",
  "validate-docx": "DOCX 结构校验",
  "publish-pr": "创建 Pull Request",
  finalize: "收尾",
};

export function observationLabel(observation: Observation): string {
  return LABELS[observation.name] ?? observation.name;
}

/**
 * 同名节点的区分后缀。
 * 一次复现里会出现 8 次 read-source-file，只有文件名能把它们区分开。
 */
export function observationQualifier(observation: Observation): string | null {
  const path = observation.input?.path;
  if (typeof path === "string") {
    return path.split("/").pop() ?? path;
  }
  const selector = observation.input?.selector;
  if (typeof selector === "string") {
    return selector.replace(/^test_feedback_[0-9a-f]{8}_/, "");
  }
  return null;
}
