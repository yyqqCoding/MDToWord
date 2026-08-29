"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Check, ChevronDown, ChevronRight, ExternalLink, Minus } from "lucide-react";
import clsx from "clsx";
import { Card, CardHeader } from "@/components/ui/card";
import { MockBanner } from "@/components/ui/mock-banner";
import { StatCard } from "@/components/ui/stat-card";
import { MetaBadge, StatusBadge } from "@/components/ui/badge";
import { StageChips } from "@/components/run/StageChips";
import { StageDetail } from "@/components/run/StageDetail";
import { DiffPreview } from "@/components/run/DiffPreview";
import { TimelineStrip } from "@/components/run/TimelineStrip";
import { ValidationPipeline } from "@/components/run/ValidationPipeline";
import { StickyRunHeader } from "@/components/run/StickyRunHeader";
import {
  deriveStages,
  describeOutcome,
  flattenObservations,
  outcomeInputOf,
  stageOf,
} from "@/lib/run-graph";
import { derivePolicyChecks, PATCH_POLICY_VERSION } from "@/lib/policy";
import { describeFailure } from "@/lib/failure-codes";
import { formatDateTime, formatDateTimeFull, formatDuration, formatInteger, runDurationMs } from "@/lib/format";
import type { Observation, RunDetailData } from "@/lib/types";

/**
 * 运行详情。
 *
 * 渐进式披露：
 *   ① 结论：一句话讲清发生了什么，配四个关键指标
 *   ② 时序与流程：全流程耗时分布条 + 7 阶段流光芯片，点击切换
 *   ③ 阶段详情：这一步做了什么判断、产出了什么、调用了什么（含类型过滤与参数卡片）
 *   ④ 证据：代码改动 (Diff 与文件药丸) + 独立验证四道门禁流水线 + 补丁策略检查
 */

function CheckLine({
  label,
  detail,
  passed,
}: {
  label: string;
  detail: string;
  passed: boolean;
}) {
  const Icon = passed ? Check : Minus;
  return (
    <li className="group row-hover flex items-start gap-2.5 px-4 py-2.5 hover:bg-raised/50">
      <Icon
        aria-hidden
        className={clsx(
          "mt-0.5 size-4 shrink-0 transition-transform duration-200 group-hover:scale-110",
          passed ? "text-good" : "text-critical",
        )}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm text-ink">{label}</p>
        <p className="mt-0.5 break-all text-sm leading-relaxed text-ink-faint">{detail}</p>
      </div>
      <span className={clsx("shrink-0 text-sm font-medium", passed ? "text-good" : "text-critical")}>
        {passed ? "通过" : "未通过"}
      </span>
    </li>
  );
}

export function RunDetail({ data }: { data: RunDetailData }) {
  const { run, trace, narrative, diff, isMock } = data;
  const stages = useMemo(() => deriveStages(run, trace), [run, trace]);

  // 默认落在最有信息量的阶段：优先第一个失败的，否则最后一个已执行的
  const initialIndex = useMemo(() => {
    const failed = stages.findIndex((s) => s.state === "failed");
    if (failed >= 0) return failed;
    const done = stages.map((s) => s.state === "done").lastIndexOf(true);
    return done >= 0 ? done : 0;
  }, [stages]);

  const [active, setActive] = useState<{
    index: number;
    dir: "none" | "forward" | "back";
  }>({ index: initialIndex, dir: "none" });
  const activeIndex = active.index;
  const [showEnv, setShowEnv] = useState(false);

  const selectStage = (index: number) => {
    setActive((prev) =>
      index === prev.index
        ? prev
        : { index, dir: index > prev.index ? "forward" : "back" },
    );
  };

  const outcome = describeOutcome(outcomeInputOf(run));
  const wallClock = runDurationMs(run.started_at, run.finished_at);
  const policyChecks = useMemo(() => derivePolicyChecks(run, diff), [run, diff]);
  const validation = run.validation;
  const failureCode =
    run.error_code ?? run.repair?.failure_code ?? run.reproduction?.failure_code ?? null;

  const callsByStage = useMemo(() => {
    const map = new Map<string, Observation[]>();
    if (!trace) return map;
    for (const { observation } of flattenObservations(trace.root).slice(1)) {
      const key = stageOf(observation);
      if (!key) continue;
      const bucket = map.get(key);
      if (bucket) bucket.push(observation);
      else map.set(key, [observation]);
    }
    for (const items of map.values()) items.sort((a, b) => a.startMs - b.startMs);
    return map;
  }, [trace]);

  const diffLines = diff
    ? diff.files.reduce(
        (acc, file) => ({
          added: acc.added + file.additions,
          removed: acc.removed + file.deletions,
        }),
        { added: 0, removed: 0 },
      )
    : null;

  const activeStage = stages[activeIndex] ?? stages[0];

  return (
    <div className="px-5 py-7 lg:px-8">
      {/* 滚动吸顶状态栏 */}
      <StickyRunHeader
        run={run}
        outcome={outcome}
        activeStage={activeStage}
        hasDiff={Boolean(diff)}
      />

      {isMock && (
        <MockBanner>
          构造数据：结构与字段对齐真实契约，数值、哈希和时间均为构造值。
        </MockBanner>
      )}

      <nav className="anim-fade mb-4 flex items-center gap-2 text-sm text-ink-faint">
        <Link href="/runs" className="transition-colors hover:text-ink">
          运行记录
        </Link>
        <ChevronRight aria-hidden className="size-3.5" />
        <span className="font-mono">{run.run_ref}</span>
      </nav>

      {/* ① 结论与核心标题 */}
      <div className="anim-rise mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-3xl font-semibold leading-snug tracking-tight text-ink">
            {narrative?.title ?? "反馈自动修复运行"}
          </h1>
          <p className="mt-2.5 max-w-3xl text-base leading-relaxed text-ink-muted">
            {narrative?.summary ?? outcome.detail}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {run.dry_run && <MetaBadge>演练运行</MetaBadge>}
          <StatusBadge tone={outcome.tone}>{outcome.label}</StatusBadge>
        </div>
      </div>

      {/* 指标卡片行 */}
      <div
        className="anim-rise mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4"
        style={{ animationDelay: "60ms" }}
      >
        <StatCard
          label="总耗时"
          value={wallClock !== null ? formatDuration(wallClock) : "进行中"}
          note={formatDateTime(run.started_at)}
          noteTitle={formatDateTimeFull(run.started_at)}
          delay={0}
        />
        <StatCard
          label="Token"
          count={run.total_tokens}
          note={`${run.model_calls} 次模型调用`}
          delay={50}
        />
        <StatCard
          label="代码改动"
          value={diffLines ? `+${diffLines.added} −${diffLines.removed}` : "无补丁"}
          note={validation ? `${validation.changed_files.length} 个文件` : undefined}
          delay={100}
        />
        <StatCard
          label="工具调用"
          count={run.tool_calls}
          note={trace && trace.attempts > 1 ? `恢复 ${trace.attempts} 次` : "沙箱与外部操作"}
          delay={150}
        />
      </div>

      {/* 运行环境与版本折叠栏 */}
      <div className="anim-fade mb-6">
        <button
          type="button"
          onClick={() => setShowEnv((value) => !value)}
          aria-expanded={showEnv}
          className="flex items-center gap-1.5 rounded px-2 py-1 text-sm text-ink-faint transition-colors hover:text-ink hover:bg-raised/50 cursor-pointer"
        >
          <ChevronDown
            aria-hidden
            className={clsx("size-4 transition-transform duration-300", showEnv && "rotate-180")}
          />
          运行环境与版本
        </button>
        <div
          className={clsx(
            "grid transition-[grid-template-rows] duration-300 ease-out",
            showEnv ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
          )}
        >
          <div className="overflow-hidden">
            <dl className="mt-2 grid gap-x-8 rounded-xl border border-line bg-surface/90 px-5 py-3 sm:grid-cols-2 lg:grid-cols-3">
              {(
                [
                  ["RUN REF", run.run_ref],
                  ["类别", run.category ?? "—"],
                  ["Provider", run.provider ?? "—"],
                  ["模型", run.model ?? "—"],
                  ["Graph", run.graph_version ?? "—"],
                  ["Policy", run.policy_version ?? "—"],
                  ["扩展版本", run.extension_version],
                  ["基线 commit", run.base_sha?.slice(0, 12) ?? "—"],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="flex items-baseline justify-between gap-3 py-1.5">
                  <dt className="shrink-0 text-sm text-ink-faint">{label}</dt>
                  <dd className="min-w-0 truncate font-mono text-sm text-ink-muted" title={value}>
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </div>

      {/* ② 流程与全流程时序分布 */}
      <div id="section-stages" className="mb-6">
        {/* 全流程耗时比例分布条 */}
        <TimelineStrip
          stages={stages}
          activeKey={activeStage?.key ?? null}
          onSelect={(key) => {
            const index = stages.findIndex((s) => s.key === key);
            if (index >= 0) selectStage(index);
          }}
        />

        {/* 7 阶段流光芯片条 */}
        <StageChips
          stages={stages}
          selectedKey={activeStage?.key ?? null}
          onSelect={(key) => {
            const index = stages.findIndex((s) => s.key === key);
            if (index >= 0) selectStage(index);
          }}
        />
      </div>

      {/* ③ 阶段详情 */}
      {activeStage && (
        <Card className="mb-6" delay={120}>
          <div className="p-5">
            <StageDetail
              stage={activeStage}
              stageIndex={activeIndex}
              stageCount={stages.length}
              direction={active.dir}
              run={run}
              calls={callsByStage.get(activeStage.key) ?? []}
            />
          </div>
        </Card>
      )}

      {/* ④ 证据：代码改动 */}
      {diff && (
        <div id="section-diff">
          <Card className="mb-6 overflow-hidden" delay={180}>
            <CardHeader
              title="代码改动"
              description="取自公开仓库中已合并的 Pull Request，不是 Agent 的受控产物"
              aside={
                run.pr_url ? (
                  <a
                    href={run.pr_url}
                    target="_blank"
                    rel="noreferrer"
                    className="group inline-flex items-center gap-1.5 text-sm text-accent transition-colors hover:text-ink font-medium"
                  >
                    PR #{diff.prNumber}
                    <ExternalLink
                      aria-hidden
                      className="size-3.5 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
                    />
                  </a>
                ) : undefined
              }
            />
            <div className="overflow-x-auto">
              <DiffPreview diff={diff} />
            </div>
          </Card>
        </div>
      )}

      {run.issue_url && (
        <Card className="mb-6" delay={180}>
          <CardHeader
            title="人工处理 Issue"
            description="Agent 已发布脱敏摘要；前端/扩展代码不会被自动修改"
            aside={
              <a
                href={run.issue_url}
                target="_blank"
                rel="noreferrer"
                className="group inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:text-ink"
              >
                查看公开 Issue
                <ExternalLink aria-hidden className="size-3.5" />
              </a>
            }
          />
        </Card>
      )}

      {/* 证据：独立验证与策略检查 */}
      <div id="section-validation" className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <Card delay={220}>
          <CardHeader
            title="独立验证门禁"
            description={
              validation
                ? `四道严密防线 · 全量后端测试 ${validation.full_validation.tests} 项`
                : undefined
            }
          />
          {validation ? (
            <ValidationPipeline validation={validation} />
          ) : (
            <div className="px-5 py-10 text-center">
              <p className="text-sm text-ink-faint">该运行未进入验证阶段。</p>
              {failureCode && (
                <p className="mt-2 text-sm leading-relaxed text-ink-muted">
                  {describeFailure(failureCode)}
                </p>
              )}
            </div>
          )}
          {run.failure && (
            <div className="border-t border-line/70 px-5 py-5">
              <p className="text-sm leading-relaxed text-ink-muted">
                {describeFailure(run.failure.code)}
              </p>
              <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-2 rounded-xl border border-line/70 bg-raised/40 p-4 text-left text-xs">
                <dt className="text-ink-faint">失败码</dt>
                <dd className="font-mono text-ink-muted">{run.failure.code}</dd>
                <dt className="text-ink-faint">阶段 / 节点</dt>
                <dd className="font-mono text-ink-muted">
                  {run.failure.phase} / {run.failure.node}
                </dd>
                <dt className="text-ink-faint">组件 / 类别</dt>
                <dd className="font-mono text-ink-muted">
                  {run.failure.component} / {run.failure.kind}
                </dd>
                <dt className="text-ink-faint">尝试次数</dt>
                <dd className="font-mono text-ink-muted">
                  {run.failure.attempt} / {run.failure.max_attempts}
                </dd>
                <dt className="text-ink-faint">最终处理</dt>
                <dd className="font-mono text-ink-muted">{run.failure.handling}</dd>
              </dl>
            </div>
          )}
        </Card>

        <Card delay={260}>
          <CardHeader title="补丁策略检查" description={PATCH_POLICY_VERSION} />
          {policyChecks.length > 0 ? (
            <ul className="divide-y divide-line/60 py-2">
              {policyChecks.map((check) => (
                <CheckLine key={check.label} {...check} />
              ))}
            </ul>
          ) : (
            <p className="px-5 py-10 text-center text-sm text-ink-faint">该运行未产生补丁。</p>
          )}
        </Card>
      </div>
    </div>
  );
}
