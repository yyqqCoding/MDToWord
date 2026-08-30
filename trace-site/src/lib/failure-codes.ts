/**
 * error_code / failure_code → 面向外行读者的中文解释。
 *
 * 站点不下发任何后端 failure_summary（来源含未脱敏的 JUnit 失败原文，
 * 见 agent/domain/repair.py:180,284），失败原因一律由此表渲染。
 * 未命中时回退显示原始 code，绝不显示后端文本。
 */

const FAILURE_TEXT: Record<string, string> = {
  // ---- 复现阶段 ----
  target_passed:
    "基线上目标测试直接通过，说明该缺陷已被此前的改动修复，无需再提交补丁。",
  target_not_collected: "生成的目标测试未被 pytest 收集到，判定为无效测试。",
  target_skipped: "目标测试被跳过，没有产生可信的复现证据。",
  non_target_failure:
    "目标测试之外的用例失败了，属于基线回归，不能算作本次缺陷的复现。",
  invalid_test_infrastructure:
    "目标测试因自身的导入或夹具问题失败，不是被测缺陷导致的，判定为无效测试。",
  unexpected_target_error:
    "目标测试确实失败了，但失败方式与复现计划预期的不一致。",
  target_assertion_failure: "目标测试产生了计划中的断言失败，缺陷复现成功。",
  target_conversion_error: "目标测试产生了计划中的转换错误，缺陷复现成功。",
  missing_junit: "沙箱没有返回可信的 JUnit 结果。",
  sandbox_timeout: "沙箱执行超时。",
  sandbox_security_rejected: "沙箱拒绝了本次执行，判定为安全风险。",

  // ---- Gate 阶段 ----
  description_blank: "反馈描述为空，需要人工介入。",
  description_too_long: "反馈描述超出长度上限，需要人工介入。",
  bug_markdown_blank: "缺陷反馈没有附带 Markdown 内容，无法复现。",
  bug_markdown_too_large: "附带的 Markdown 内容超出处理上限。",
  open_duplicate_found: "已存在处理中的同内容反馈，本次判定为重复。",
  feature_feedback_type: "这是功能建议而非缺陷，不在自动修复范围内。",

  // ---- 修复与验证阶段 ----
  claim_attempts_exhausted: "领取重试次数耗尽，转人工处理。",
  provider_unavailable: "模型服务在有限重试后仍不可用（传输或上游故障）。",
  timeout: "模型请求在三次有界尝试后仍然超时。",
  rate_limit: "模型服务持续限流，已停止本次自动处理。",
  auth_error: "模型凭据不可用，反馈已转入人工处理队列。",
  source_auth_error: "GitHub 源码读取凭据不可用，反馈已转入人工处理队列。",
  source_revision_error: "GitHub main 版本无法通过源码版本契约校验。",
  repository_unavailable: "GitHub 或数据库等源码依赖在三次有界尝试后仍不可用。",
  invalid_response: "已收到模型响应，但未通过严格 Schema 或本地策略校验。",
  sandbox_unavailable: "沙箱服务在有界重试和总时限内仍不可用。",
  sandbox_invalid_response: "沙箱返回了无法通过严格契约校验的响应。",
  sandbox_auth_error: "沙箱凭据不可用，反馈已转入人工处理队列。",
  sandbox_job_conflict: "沙箱任务标识与既有请求冲突，已停止执行。",
  unexpected_error: "运行遇到未登记异常，已安全终结并保留失败位置。",
};

export function describeFailure(code: string | null | undefined): string | null {
  if (!code) return null;
  return FAILURE_TEXT[code] ?? code;
}

/** 该 code 是否已有中文解释；用于决定是否以等宽字体显示原始 code。 */
export function hasFailureText(code: string | null | undefined): boolean {
  return Boolean(code && code in FAILURE_TEXT);
}
