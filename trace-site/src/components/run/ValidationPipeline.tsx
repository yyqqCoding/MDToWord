"use client";

import { Check, Flame, Layers, ShieldCheck, Sparkles, X } from "lucide-react";
import clsx from "clsx";
import type { ValidationPublic } from "@/lib/types";

interface GateCardProps {
  step: number;
  title: string;
  subtitle: string;
  passed: boolean;
  icon: React.ComponentType<{ className?: string }>;
  metrics?: React.ReactNode;
}

function GateCard({
  step,
  title,
  subtitle,
  passed,
  icon: Icon,
  metrics,
}: GateCardProps) {
  return (
    <div
      className={clsx(
        "group relative rounded-xl border p-4 transition-all duration-200",
        passed
          ? "border-good/30 bg-good/5 hover:border-good/60 hover:bg-good/8"
          : "border-critical/30 bg-critical/5 hover:border-critical/60",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={clsx(
              "flex size-7 items-center justify-center rounded-lg border text-xs font-mono font-medium",
              passed
                ? "border-good/40 bg-good/15 text-good"
                : "border-critical/40 bg-critical/15 text-critical",
            )}
          >
            <Icon className="size-4" />
          </span>
          <div>
            <span className="font-mono text-[11px] uppercase tracking-wider text-ink-faint">
              GATE 0{step}
            </span>
            <h4 className="text-sm font-semibold text-ink">{title}</h4>
          </div>
        </div>

        <span
          className={clsx(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
            passed
              ? "bg-good/15 text-good"
              : "bg-critical/15 text-critical",
          )}
        >
          {passed ? (
            <>
              <Check className="size-3" />
              通过
            </>
          ) : (
            <>
              <X className="size-3" />
              未通过
            </>
          )}
        </span>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-ink-muted">
        {subtitle}
      </p>

      {metrics && <div className="mt-3 pt-2.5 border-t border-line/60">{metrics}</div>}
    </div>
  );
}

/**
 * 独立验证四道门禁流水线 (4-Gate Defense Pipeline)。
 *
 * 将 Agent 最核心的可信度证明可视化为阶梯门禁卡片：
 * 1. 基线复现确认 (确保问题真实存在)
 * 2. 目标测试转绿 (确保补丁修复缺陷)
 * 3. 全量测试回归 (确保已有功能零破坏)
 * 4. DOCX 结构校验 (确保物理产物结构完好)
 */
export function ValidationPipeline({
  validation,
}: {
  validation: ValidationPublic;
}) {
  const {
    baseline_reproduction,
    target_validation,
    full_validation,
    docx_validation,
  } = validation;

  const docxCheckList = Object.keys(docx_validation.checks || {});

  return (
    <div className="space-y-3 p-5">
      <div className="grid gap-3.5 sm:grid-cols-2">
        {/* Gate 1: 基线复现 */}
        <GateCard
          step={1}
          title="基线复现确认"
          subtitle="在未修改源码的干净基线上运行目标测试，确认确实观察到预期的失败。"
          passed={baseline_reproduction.expected_failure_observed}
          icon={Flame}
          metrics={
            <div className="flex items-center gap-2 text-xs">
              <span className="text-ink-faint">预期失败观察：</span>
              <span className="font-medium text-ink">
                {baseline_reproduction.expected_failure_observed ? "确切触发" : "未触发"}
              </span>
            </div>
          }
        />

        {/* Gate 2: 目标测试验证 */}
        <GateCard
          step={2}
          title="目标测试验证"
          subtitle="应用修复补丁后重新执行目标用例，确认断言转为全部通过。"
          passed={target_validation.passed}
          icon={Sparkles}
          metrics={
            <div className="flex items-center gap-2 text-xs">
              <span className="text-ink-faint">目标修复判定：</span>
              <span className="font-medium text-good">
                {target_validation.passed ? "测试转为全部通过" : "仍存在失败"}
              </span>
            </div>
          }
        />

        {/* Gate 3: 全量回归防御 */}
        <GateCard
          step={3}
          title="全量测试回归"
          subtitle={`执行后端全部 ${full_validation.tests} 项测试用例，确保修复无副作用、零回归。`}
          passed={full_validation.passed}
          icon={ShieldCheck}
          metrics={
            <div className="flex flex-wrap items-center gap-1.5 text-xs font-mono">
              <span className="rounded bg-surface px-1.5 py-0.5 text-ink">
                总计 {full_validation.tests} 项
              </span>
              <span className="rounded bg-good/15 px-1.5 py-0.5 text-good">
                通过 {full_validation.tests - full_validation.failures - full_validation.errors - full_validation.skipped}
              </span>
              {full_validation.failures > 0 && (
                <span className="rounded bg-critical/15 px-1.5 py-0.5 text-critical">
                  失败 {full_validation.failures}
                </span>
              )}
              {full_validation.skipped > 0 && (
                <span className="rounded bg-raised px-1.5 py-0.5 text-ink-faint">
                  跳过 {full_validation.skipped}
                </span>
              )}
            </div>
          }
        />

        {/* Gate 4: DOCX 结构校验 */}
        <GateCard
          step={4}
          title="DOCX 结构校验"
          subtitle="解析生成的 Word 文档 XML 树，核验 XPath 路径、绘图对象与段落完整性。"
          passed={docx_validation.passed}
          icon={Layers}
          metrics={
            <div className="flex flex-wrap gap-1">
              {docxCheckList.length > 0 ? (
                docxCheckList.map((check) => (
                  <span
                    key={check}
                    className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-muted"
                  >
                    {check}
                  </span>
                ))
              ) : (
                <span className="text-xs text-ink-faint">标准 XML 结构检查</span>
              )}
            </div>
          }
        />
      </div>
    </div>
  );
}
