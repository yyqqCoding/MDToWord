"use client";

import clsx from "clsx";
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { DiffHunk, PatchDiff, PatchDiffFile } from "@/lib/types";

/**
 * Diff 预览。数据来自 GitHub 公开 API 的已合并 PR，不是 Agent 的受控 artifact。
 * 公开仓库的 PR diff 本就是公开信息，这样既能展示真实改动，又完全绕开脱敏边界。
 *
 * 两层展开：
 *   - 文件层：默认只显示文件列表（路径 + 增删统计），点击展开该文件的 diff；
 *   - 行数层：展开后单文件超过 COLLAPSED_LINES 行的部分收进"展开其余 N 行"，
 *     先给足判断改动性质的上下文，再按需看全文。
 */

/** 展开一个文件后默认展示的行数上限；超出部分二次折叠。 */
const COLLAPSED_LINES = 24;

const LINE_STYLE: Record<string, string> = {
  add: "bg-good/10 text-ink",
  del: "bg-critical/10 text-ink",
  context: "text-ink-muted",
};

const LINE_MARK: Record<string, string> = { add: "+", del: "-", context: " " };

function HunkHeader({ header }: { header: string }) {
  return (
    <p className="bg-canvas px-5 py-1.5 font-mono text-xs text-ink-faint">{header}</p>
  );
}

/** 一个 hunk 中从 skip 行起渲染 take 行（折叠会把同一个 hunk 切成首尾两段）。 */
function HunkRows({ hunk, skip, take }: { hunk: DiffHunk; skip: number; take: number }) {
  return (
    <table className="w-full border-collapse">
      <tbody>
        {hunk.lines.slice(skip, skip + take).map((line, index) => (
          <tr
            key={skip + index}
            className={clsx("row-hover hover:brightness-125", LINE_STYLE[line.kind])}
          >
            <td className="w-12 select-none border-r border-line/60 px-2 text-right font-mono text-xs text-ink-faint">
              {line.oldNumber ?? ""}
            </td>
            <td className="w-12 select-none border-r border-line/60 px-2 text-right font-mono text-xs text-ink-faint">
              {line.newNumber ?? ""}
            </td>
            <td className="whitespace-pre px-3 py-0.5 font-mono text-sm leading-relaxed">
              <span className="select-none text-ink-faint">{LINE_MARK[line.kind]}</span>
              {line.text}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface HunkPart {
  hunk: DiffHunk;
  skip: number;
  take: number;
}

/** 把行数预算依次分给各 hunk：头部拿前 COLLAPSED_LINES 行（允许切断最后的
    hunk），其余进折叠区。展开后同一 hunk 的首尾两段在页面上是连续的。 */
function splitHunks(file: PatchDiffFile, fullyExpanded: boolean) {
  if (!fullyExpanded) {
    let budget = COLLAPSED_LINES;
    const headParts: HunkPart[] = [];
    const tailParts: HunkPart[] = [];
    for (const hunk of file.hunks) {
      if (budget <= 0) {
        tailParts.push({ hunk, skip: 0, take: hunk.lines.length });
        continue;
      }
      const take = Math.min(budget, hunk.lines.length);
      budget -= take;
      headParts.push({ hunk, skip: 0, take });
      if (take < hunk.lines.length) {
        tailParts.push({ hunk, skip: take, take: hunk.lines.length - take });
      }
    }
    return { headParts, tailParts };
  }
  return {
    headParts: file.hunks.map((hunk) => ({ hunk, skip: 0, take: hunk.lines.length })),
    tailParts: [] as HunkPart[],
  };
}

function RenderedHunks({ parts }: { parts: HunkPart[] }) {
  return (
    <>
      {parts.map(
        (part, index) =>
          part.take > 0 && (
            <div key={`${part.hunk.header}-${index}`}>
              {part.skip === 0 && <HunkHeader header={part.hunk.header} />}
              <HunkRows {...part} />
            </div>
          ),
      )}
    </>
  );
}

function FileBlock({ file }: { file: PatchDiffFile }) {
  // open：文件层的点击展开；fullyExpanded：长文件内部的二次折叠
  const [open, setOpen] = useState(false);
  const [fullyExpanded, setFullyExpanded] = useState(false);
  const totalLines = file.hunks.reduce((acc, hunk) => acc + hunk.lines.length, 0);
  const collapsible = totalLines > COLLAPSED_LINES;
  const { headParts, tailParts } = splitHunks(file, fullyExpanded);
  const hiddenLines = tailParts.reduce((acc, part) => acc + part.take, 0);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="row-hover flex w-full items-center gap-3 border-b border-line bg-raised/50 px-5 py-2.5 text-left hover:bg-raised"
      >
        <ChevronRight
          aria-hidden
          className={clsx(
            "size-4 shrink-0 text-ink-faint transition-transform duration-300",
            open && "rotate-90",
          )}
        />
        <span className="truncate font-mono text-sm text-ink-muted">{file.path}</span>
        <span className="ml-auto shrink-0 font-mono text-xs text-ink-faint">
          +{file.additions} −{file.deletions}
          {collapsible && ` · ${totalLines} 行`}
        </span>
      </button>

      {/* 文件内容：grid-rows 过渡与站内其余折叠区同一模式 */}
      <div
        className={clsx(
          "grid transition-[grid-template-rows] duration-300 ease-out",
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className={clsx("overflow-hidden", open && "border-b border-line")}>
          <RenderedHunks parts={headParts} />

          {collapsible && (
            <>
              <div
                className={clsx(
                  "grid transition-[grid-template-rows] duration-300 ease-out",
                  fullyExpanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
                )}
              >
                <div className="overflow-hidden">
                  <RenderedHunks parts={tailParts} />
                </div>
              </div>
              <button
                type="button"
                onClick={() => setFullyExpanded((value) => !value)}
                aria-expanded={fullyExpanded}
                className="row-hover flex w-full items-center justify-center gap-1.5 bg-canvas px-5 py-2 text-sm text-ink-muted hover:text-ink"
              >
                <ChevronDown
                  aria-hidden
                  className={clsx(
                    "size-4 transition-transform duration-300",
                    fullyExpanded && "rotate-180",
                  )}
                />
                {fullyExpanded ? "收起" : `展开其余 ${hiddenLines} 行`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function DiffPreview({ diff }: { diff: PatchDiff }) {
  const totals = diff.files.reduce(
    (acc, file) => ({
      additions: acc.additions + file.additions,
      deletions: acc.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 },
  );

  return (
    <div>
      <div className="flex flex-wrap items-center gap-4 border-b border-line px-5 py-3">
        <span className="font-mono text-sm text-good">+{totals.additions}</span>
        <span className="font-mono text-sm text-critical">−{totals.deletions}</span>
        <span className="text-sm text-ink-faint">
          来自已{diff.merged ? "合并" : "开放"}的 PR #{diff.prNumber}
        </span>
        <a
          href={diff.prUrl}
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-sm text-accent transition-colors hover:text-ink"
        >
          在 GitHub 查看完整改动
        </a>
      </div>

      {diff.files.map((file) => (
        <FileBlock key={file.path} file={file} />
      ))}
    </div>
  );
}
