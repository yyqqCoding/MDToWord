"use client";

import clsx from "clsx";
import { useState } from "react";
import { ChevronRight, Sparkles, Wrench, CircleDot } from "lucide-react";
import { formatDuration, formatInteger, formatTokens } from "@/lib/format";
import { describeFailure } from "@/lib/failure-codes";
import { observationLabel, observationQualifier } from "@/lib/observation-labels";
import { stageStory } from "@/lib/stage-story";
import type { StageKey, StageView } from "@/lib/run-graph";
import type { Observation, RunPublic } from "@/lib/types";

/**
 * 阶段详情。
 *
 * 取代了原先「常驻元数据面板 + 全量瀑布」的组合:那两者对同一批调用重复呈现,
 * 而真正缺失的是「这一步做了什么判断」。这里左侧讲叙述与关键产物,
 * 右侧只列本阶段的调用,点开才展开脱敏摘要 —— 逐层深入,不一次性铺开。
 */

const TYPE_ICON = {
  generation: { icon: Sparkles, color: "text-accent" },
  tool: { icon: Wrench, color: "text-ink-muted" },
  agent: { icon: CircleDot, color: "text-accent" },
  span: { icon: CircleDot, color: "text-ink-faint" },
} as const;

function Summary({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value);
  if (entries.length === 0) return null;
  return (
    <dl className="mt-2 space-y-1.5 rounded-lg border border-line bg-canvas px-3 py-2.5">
      {entries.map(([key, item]) => (
        <div key={key} className="grid gap-0.5 sm:grid-cols-[10rem_1fr] sm:gap-3">
          <dt className="font-mono text-xs text-ink-faint">{key}</dt>
          <dd className="break-all font-mono text-xs leading-relaxed text-ink-muted">
            {typeof item === "object" && item !== null
              ? JSON.stringify(item)
              : String(item)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function CallRow({
  observation,
  index,
  spanStart,
  spanTotal,
}: {
  observation: Observation;
  index: number;
  spanStart: number;
  spanTotal: number;
}) {
  const [open, setOpen] = useState(false);
  const meta = TYPE_ICON[observation.type];
  const Icon = meta.icon;
  const qualifier = observationQualifier(observation);
  const failed = observation.status === "error";
  const left = ((observation.startMs - spanStart) / spanTotal) * 100;
  const width = Math.max((observation.durationMs / spanTotal) * 100, 1.5);

  return (
    <li
      className="anim-fade border-b border-line/50 last:border-b-0"
      style={{ animationDelay: `${index * 40}ms` }}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="row-hover group flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-raised/60"
      >
        <ChevronRight
          aria-hidden
          className={clsx(
            "size-3.5 shrink-0 text-ink-faint transition-transform duration-300",
            open && "rotate-90",
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
            failed ? "text-critical" : "text-ink-muted group-hover:text-ink",
          )}
        >
          {observationLabel(observation)}
          {qualifier && (
            <span className="ml-1.5 font-mono text-xs text-ink-faint">{qualifier}</span>
          )}
        </span>

        {/* 相对本阶段的微型时间条,替代整页瀑布 */}
        <span aria-hidden className="relative hidden h-1.5 w-24 shrink-0 rounded bg-raised sm:block">
          <span
            className={clsx(
              "anim-grow-x absolute inset-y-0 rounded",
              failed ? "bg-critical" : "bg-accent/70",
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
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="px-3 pb-3 pl-10">
            {failed && observation.errorCode && (
              <p className="rounded-lg border border-critical/35 bg-critical/10 px-3 py-2 text-sm leading-relaxed text-critical">
                {describeFailure(observation.errorCode)}
              </p>
            )}
            {observation.model && (
              <p className="mt-2 font-mono text-xs text-ink-faint">{observation.model}</p>
            )}
            {observation.usage && (
              <p className="mt-1 text-sm text-ink-muted">
                输入 {formatInteger(observation.usage.input)} · 输出{" "}
                {formatInteger(observation.usage.output)} tokens
              </p>
            )}
            {observation.input && <Summary value={observation.input} />}
            {observation.output && <Summary value={observation.output} />}
          </div>
        </div>
      </div>
    </li>
  );
}

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
  // 阶段是否确实执行过:done/failed 来自 Trace 或运行摘要字段,active 是进行中的当前阶段。
  // 只有「没执行过」的阶段(skipped/pending)才展示「从未执行」的提示,
  // 已执行但 Trace 恰好没有它的调用时,要明说是「未上报」而不是「从未执行」。
  const hasEvidence =
    stage.state === "done" || stage.state === "failed" || stage.state === "active";
  const spanStart = calls.length > 0 ? Math.min(...calls.map((c) => c.startMs)) : 0;
  const spanEnd =
    calls.length > 0 ? Math.max(...calls.map((c) => c.startMs + c.durationMs)) : 1;
  const spanTotal = Math.max(spanEnd - spanStart, 1);
  const enterClass =
    direction === "forward"
      ? "anim-slide-forward"
      : direction === "back"
        ? "anim-slide-back"
        : "anim-fade";

  return (
    <div key={stage.key} className={`${enterClass} grid gap-5 lg:grid-cols-[1fr_1.1fr]`}>
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
        <p className="mt-3 text-sm leading-relaxed text-ink-muted">{story.narrative}</p>

        {story.facts.length > 0 && (
          <dl className="mt-4 divide-y divide-line/60 rounded-xl border border-line bg-canvas">
            {story.facts.map((fact, index) => (
              <div
                key={`${fact.label}-${index}`}
                className="row-hover grid gap-1 px-4 py-2.5 hover:bg-raised/50 sm:grid-cols-[8rem_1fr] sm:gap-3"
              >
                <dt className="shrink-0 text-sm text-ink-faint">{fact.label}</dt>
                <dd
                  className={clsx(
                    "min-w-0 break-all text-sm text-ink-muted",
                    fact.mono && "font-mono text-xs leading-relaxed",
                  )}
                >
                  {fact.value}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      <div className="min-w-0">
        <p className="mb-2 text-sm text-ink-faint">
          本阶段调用
          {calls.length > 0 && <span className="ml-1.5 font-mono">{calls.length}</span>}
        </p>
        {calls.length > 0 ? (
          <ul className="overflow-hidden rounded-xl border border-line bg-surface">
            {calls.map((call, index) => (
              <CallRow
                key={call.id}
                observation={call}
                index={index}
                spanStart={spanStart}
                spanTotal={spanTotal}
              />
            ))}
          </ul>
        ) : hasEvidence ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm leading-relaxed text-ink-faint">
            该阶段已执行,但调用明细未随 Trace 上报。
            <br />
            观测上报是 fail-open 的,未成功上报不影响业务结果。
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
