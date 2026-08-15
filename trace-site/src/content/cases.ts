import type { GateCategory } from "@/lib/types";

/**
 * 人工撰写的案例说明。
 *
 * 站点不渲染 feedback.description 或 markdown_content —— 那是用户内容，
 * 访问范围又定为完全公开。因此每条需要叙事的运行在此登记一段人工文案，
 * 与数据库解耦，措辞也可以针对外行读者优化。
 *
 * 键是 agent_runs.id（UUID）。接入真实数据后，可在运行记录页取到真实 ID 再补录；
 * 未登记的运行会走 fallbackTitle()，不影响页面可用。
 */
export const CASE_NARRATIVES: Record<string, { title: string; summary: string }> = {
  // 首个走完全流程并产出已合并 PR 的运行。
  "f11032d7-ce6b-4412-b0aa-14671b64e6f4": {
    title: "导出 Word 后只显示 Mermaid 源码，未生成流程图",
    summary:
      "用户在 AI 对话里得到一段含 Mermaid 流程图的 Markdown，导出成 Word 后图变成了一段代码文本。" +
      "Agent 判定这是后端转换缺陷，在隔离沙箱里选用「DOCX 中至少出现 1 个图形」这条已登记断言写出复现测试，" +
      "第二轮复现成功，随后一轮修复即通过目标测试。" +
      "独立验证重跑了基线、目标测试、53 项全量后端测试与 DOCX 结构检查，全部通过后创建了 Pull Request。" +
      "过程中 PR 创建连续失败三次，第四次成功——这类重试在真实运行里很常见，站点如实展示而不做修饰。",
  },
};

const CATEGORY_TITLES: Record<GateCategory, string> = {
  conversion_crash: "转换过程崩溃",
  formula_parsing: "公式解析问题",
  table_parsing: "表格解析问题",
  heading_parsing: "标题解析问题",
  list_parsing: "列表解析问题",
  docx_structure: "DOCX 结构问题",
  backend_normalization: "后端归一化问题",
  extension_ui: "扩展界面问题",
  visual_quality: "排版观感问题",
  unknown: "未分类反馈",
};

/** 未登记案例说明时的标题，只用分类推导，不触碰用户内容。 */
export function fallbackTitle(category: GateCategory | null): string {
  return CATEGORY_TITLES[category ?? "unknown"];
}
