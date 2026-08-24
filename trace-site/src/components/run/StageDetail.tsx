"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import {
  ChevronRight,
  Sparkles,
  Wrench,
  CircleDot,
  AlertTriangle,
  ChevronsUpDown,
} from "lucide-react";
import { formatDuration, formatInteger, formatTokens } from "@/lib/format";
import { describeFailure } from "@/lib/failure-codes";
import { observationLabel, observationQualifier } from "@/lib/observation-labels";
import { stageStory } from "@/lib/stage-story";
import { PayloadVisualizer } from "@/components/run/PayloadVisualizer";
import type { StageKey, StageView } from "@/lib/run-graph";
import type { Observation, RunPublic } from "@/lib/types";

/**
 * 阶段详情。
 *
 * 左侧讲叙述与关键产物 (含 sticky 响应式悬停)，
 * 右侧提供调用明细分类筛选 (模型/工具/异常) 与结构化 Payload 可视化。
 */

const TYPE_ICON = {
  generation: { icon: Sparkles, color: "text-accent" },
  tool: { icon: Wrench, color: "text-ink-muted" },
  agent: { icon: CircleDot, color: "text-accent" },
  span: { icon: CircleDot, color: "text-ink-faint" },
} as const;

function CallRow({
  observation,
  index,
  spanStart,
  spanTotal,
  isOpen,
  onToggle,
}: {
  observation: Observation;
  index: number;
  spanStart: number;
  spanTotal: number;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const meta = TYPE_ICON[observation.type];
  const Icon = meta.icon;
  const qualifier = observationQualifier(observation);
  const failed = observation.status === "error";
  const left = ((observation.startMs - spanStart) / spanTotal) * 100;
  const width = Math.max((observation.durationMs / spanTotal) * 100, 1.5);

  return (
    <li
      className="anim-fade border-b border-line/50 last:border-b-0"
      style={{ animationDelay: `${index * 35}ms` }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="row-hover group flex w-full items-center gap-2.5 px-3.5 py-3 text-left hover:bg-raised/60 cursor-pointer"
      >
        <ChevronRight
          aria-hidden
          className={clsx(
            "size-3.5 shrink-0 text-ink-faint transition-transform duration-300",
            isOpen && "rotate-90",
          )}
        />
        <Icon
          aria-hidden
          className={clsx(
            "size-4 shrink-0 transition-transform duration-200 group-hover:scale-110",
            failed ? "text-critical" : meta.color,
          )}
        />
        <span
          className={clsx(
            "min-w-0 flex-1 truncate text-sm",
            failed ? "text-critical font-medium" : "text-ink-muted group-hover:text-ink",
          )}
        >
          {observationLabel(observation)}
          {qualifier && (
            <span className="ml-1.5 font-mono text-xs text-ink-faint">{qualifier}</span>
          )}
        </span>

        {/* 相对本阶段的微型时间条 */}
        <span
          aria-hidden
          className="relative hidden h-1.5 w-24 shrink-0 rounded bg-raised sm:block overflow-hidden"
        >
          <span
            className={clsx(
              "anim-grow-x absolute inset-y-0 rounded",
              failed ? "bg-critical" : "bg-accent/80",
            )}
            style={{ left: `${left}%`, width: `${width}%` }}
          />
        </span>

        <span className="shrink-0 font-mono text-xs text-ink-faint">
          {formatDuration(observation.durationMs)}
        </span>
        {observation.usage && (
          <span className="hidden shrink-0 font-mono text-xs text-ink-faint sm:inline">
            {formatTokens(observation.usage.total)} tok
          </span>
        )}
      </button>

      <div
        className={clsx(
          "grid transition-[grid-template-rows] duration-300 ease-out",
          isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="px-4 pb-4 pl-10 space-y-3">
            {failed && observation.errorCode && (
              <div className="flex items-start gap-2 rounded-lg border border-critical/35 bg-critical/10 px-3 py-2 text-xs leading-relaxed text-critical">
                <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                <span>{describeFailure(observation.errorCode)}</span>
              </div>
            )}

            {observation.model && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line/60 pb-1.5">
                <span className="font-mono text-xs text-accent">
                  模型: {observation.model}
                </span>
                {observation.usage && (
                  <span className="font-mono text-xs text-ink-faint">
                    输入 {formatInteger(observation.usage.input)} · 输出{" "}
                    {formatInteger(observation.usage.output)} tokens
                  </span>
                )}
              </div>
            )}

            {observation.input && (
              <PayloadVisualizer value={observation.input} title="输入参数 (Input Payload)" />
            )}
            {observation.output && (
              <PayloadVisualizer value={observation.output} title="执行产物 (Output Result)" />
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

type CallFilter = "all" | "generation" | "tool" | "error";

export function StageDetail({
  stage,
  stageIndex,
  stageCount,
  direction = "none",
  run,
  calls,
}: {
  stage: StageView;
  stageIndex: number;
  stageCount: number;
  /** 切换方向：往后的阶段从右滑入，往前的从左滑入；首次渲染原地淡入。 */
  direction?: "none" | "forward" | "back";
  run: RunPublic;
  calls: Observation[];
}) {
  const story = stageStory(stage.key as StageKey, run);
  const [filter, setFilter] = useState<CallFilter>("all");
  const [expandedCallIds, setExpandedCallIds] = useState<Set<string>>(new Set());

  const hasEvidence =
    stage.state === "done" || stage.state === "failed" || stage.state === "active";

  const spanStart = calls.length > 0 ? Math.min(...calls.map((c) => c.startMs)) : 0;
  const spanEnd =
    calls.length > 0 ? Math.max(...calls.map((c) => c.startMs + c.durationMs)) : 1;
  const spanTotal = Math.max(spanEnd - spanStart, 1);

  const counts = useMemo(() => {
    let generations = 0;
    let tools = 0;
    let errors = 0;
    for (const c of calls) {
      if (c.type === "generation") generations++;
      if (c.type === "tool") tools++;
      if (c.status === "error") errors++;
    }
    return { all: calls.length, generation: generations, tool: tools, error: errors };
  }, [calls]);

  const filteredCalls = useMemo(() => {
    if (filter === "generation") return calls.filter((c) => c.type === "generation");
    if (filter === "tool") return calls.filter((c) => c.type === "tool");
    if (filter === "error") return calls.filter((c) => c.status === "error");
    return calls;
  }, [calls, filter]);

  const allCallsExpanded =
    filteredCalls.length > 0 &&
    filteredCalls.every((c) => expandedCallIds.has(c.id));

  const toggleCall = (id: string) => {
    setExpandedCallIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleAllCalls = () => {
    if (allCallsExpanded) {
      setExpandedCallIds((prev) => {
        const next = new Set(prev);
        for (const c of filteredCalls) next.delete(c.id);
        return next;
      });
    } else {
      setExpandedCallIds((prev) => {
        const next = new Set(prev);
        for (const c of filteredCalls) next.add(c.id);
        return next;
      });
    }
  };

  const enterClass =
    direction === "forward"
      ? "anim-slide-forward"
      : direction === "back"
        ? "anim-slide-back"
        : "anim-fade";

  return (
    <div key={stage.key} className={`${enterClass} grid gap-6 lg:grid-cols-[1fr_1.2fr]`}>
      {/* 左侧：阶段故事与事实 (sticky 悬停，避免右侧过长时长篇空白) */}
      <div className="lg:sticky lg:top-20 lg:self-start space-y-4">
        <div>
          <p className="text-xs tracking-wide text-ink-faint">
            阶段 {stageIndex + 1} / {stageCount}
          </p>
          <h3 className="mt-1 flex flex-wrap items-center gap-3 text-lg font-semibold text-ink">
            {stage.label}
            {stage.durationMs !== null && (
              <span className="font-mono text-sm font-normal text-ink-muted">
                {formatDuration(stage.durationMs)}
              </span>
            )}
            {stage.retries > 0 && (
              <span className="text-sm font-normal text-warn">重试 {stage.retries} 次</span>
            )}
          </h3>
          <p className="mt-2.5 text-sm leading-relaxed text-ink-muted">{story.narrative}</p>
        </div>

        {story.facts.length > 0 && (
          <dl className="divide-y divide-line/60 rounded-xl border border-line bg-canvas">
            {story.facts.map((fact, index) => (
              <div
                key={`${fact.label}-${index}`}
                className="row-hover grid gap-1 px-4 py-2.5 hover:bg-raised/50 sm:grid-cols-[7.5rem_1fr] sm:gap-3"
              >
                <dt className="shrink-0 text-xs text-ink-faint">{fact.label}</dt>
                <dd
                  className={clsx(
                    "min-w-0 break-all text-xs text-ink-muted",
                    fact.mono && "font-mono leading-relaxed",
                  )}
                >
                  {fact.value}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {/* 右侧：调用明细与过滤 */}
      <div className="min-w-0">
        <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
          {/* 类型筛选 Tabs */}
          <div className="flex flex-wrap items-center gap-1">
            <button
              type="button"
              onClick={() => setFilter("all")}
              className={clsx(
                "rounded-full px-2.5 py-1 text-xs transition-colors cursor-pointer",
                filter === "all"
                  ? "bg-accent/15 font-medium text-accent border border-accent/40"
                  : "text-ink-faint hover:text-ink hover:bg-raised",
              )}
            >
              全部 ({counts.all})
            </button>
            {counts.generation > 0 && (
              <button
                type="button"
                onClick={() => setFilter("generation")}
                className={clsx(
                  "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors cursor-pointer",
                  filter === "generation"
                    ? "bg-accent/15 font-medium text-accent border border-accent/40"
                    : "text-ink-faint hover:text-ink hover:bg-raised",
                )}
              >
                <Sparkles className="size-3" />
                模型 ({counts.generation})
              </button>
            )}
            {counts.tool > 0 && (
              <button
                type="button"
                onClick={() => setFilter("tool")}
                className={clsx(
                  "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors cursor-pointer",
                  filter === "tool"
                    ? "bg-accent/15 font-medium text-accent border border-accent/40"
                    : "text-ink-faint hover:text-ink hover:bg-raised",
                )}
              >
                <Wrench className="size-3" />
                工具 ({counts.tool})
              </button>
            )}
            {counts.error > 0 && (
              <button
                type="button"
                onClick={() => setFilter("error")}
                className={clsx(
                  "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors cursor-pointer",
                  filter === "error"
                    ? "bg-critical/15 font-medium text-critical border border-critical/40"
                    : "text-critical/80 hover:text-critical hover:bg-critical/10",
                )}
              >
                <AlertTriangle className="size-3" />
                异常 ({counts.error})
              </button>
            )}
          </div>

          {/* 全部展开/收起 */}
          {filteredCalls.length > 1 && (
            <button
              type="button"
              onClick={toggleAllCalls}
              className="inline-flex items-center gap-1 text-xs text-ink-faint transition-colors hover:text-ink cursor-pointer"
            >
              <ChevronsUpDown className="size-3" />
              {allCallsExpanded ? "全部收起" : "全部展开"}
            </button>
          )}
        </div>

        {filteredCalls.length > 0 ? (
          <ul className="overflow-hidden rounded-xl border border-line bg-surface">
            {filteredCalls.map((call, index) => (
              <CallRow
                key={call.id}
                observation={call}
                index={index}
                spanStart={spanStart}
                spanTotal={spanTotal}
                isOpen={expandedCallIds.has(call.id)}
                onToggle={() => toggleCall(call.id)}
              />
            ))}
          </ul>
        ) : hasEvidence ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm leading-relaxed text-ink-faint">
            该分类下无调用明细。
          </p>
        ) : (
          <p className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm leading-relaxed text-ink-faint">
            该阶段没有可用的调用明细。
            <br />
            观测上报是 fail-open 的,未成功上报不影响业务结果。
          </p>
        )}
      </div>
    </div>
  );
}
