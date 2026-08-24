"use client";

import clsx from "clsx";
import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Copy, FileCode, Layers } from "lucide-react";
import type { DiffHunk, PatchDiff, PatchDiffFile } from "@/lib/types";

/**
 * Diff 预览。
 *
 * 包含：
 * 1. 顶部文件直达药丸 (File Pills) - 点击仅展开该文件并平滑滚动到该文件；
 * 2. 独立受控的文件折叠状态 - 每个文件折叠/展开互不干扰；
 * 3. 全局“全部展开 / 全部收起”与一键复制 Patch。
 */

/** 展开一个文件后默认展示的行数上限；超出部分二次折叠。 */
const COLLAPSED_LINES = 24;

const LINE_STYLE: Record<string, string> = {
  add: "bg-good/12 text-ink hover:bg-good/20",
  del: "bg-critical/12 text-ink hover:bg-critical/20",
  context: "text-ink-muted hover:bg-raised/40",
};

const LINE_MARK: Record<string, string> = { add: "+", del: "-", context: " " };

function HunkHeader({ header }: { header: string }) {
  return (
    <p className="bg-canvas/80 px-5 py-1.5 font-mono text-xs text-ink-faint border-y border-line/40">
      {header}
    </p>
  );
}

/** 一个 hunk 中从 skip 行起渲染 take 行 */
function HunkRows({ hunk, skip, take }: { hunk: DiffHunk; skip: number; take: number }) {
  return (
    <table className="w-full border-collapse font-mono">
      <tbody>
        {hunk.lines.slice(skip, skip + take).map((line, index) => (
          <tr
            key={skip + index}
            className={clsx("transition-colors", LINE_STYLE[line.kind])}
          >
            <td className="w-12 select-none border-r border-line/60 px-2 py-0.5 text-right text-xs text-ink-faint">
              {line.oldNumber ?? ""}
            </td>
            <td className="w-12 select-none border-r border-line/60 px-2 py-0.5 text-right text-xs text-ink-faint">
              {line.newNumber ?? ""}
            </td>
            <td className="whitespace-pre px-3 py-0.5 text-xs leading-relaxed">
              <span
                className={clsx(
                  "select-none font-bold mr-1.5",
                  line.kind === "add"
                    ? "text-good"
                    : line.kind === "del"
                      ? "text-critical"
                      : "text-ink-faint",
                )}
              >
                {LINE_MARK[line.kind]}
              </span>
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

function FileBlock({
  file,
  isOpen,
  onToggle,
}: {
  file: PatchDiffFile;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const [fullyExpanded, setFullyExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const totalLines = file.hunks.reduce((acc, hunk) => acc + hunk.lines.length, 0);
  const collapsible = totalLines > COLLAPSED_LINES;
  const { headParts, tailParts } = splitHunks(file, fullyExpanded);
  const hiddenLines = tailParts.reduce((acc, part) => acc + part.take, 0);

  const copyPath = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(file.path);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const fileId = `diff-file-${file.path.replace(/[^a-zA-Z0-9_-]/g, "_")}`;

  return (
    <div id={fileId} className="border-b border-line last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="row-hover flex w-full items-center gap-3 bg-raised/40 px-5 py-2.5 text-left hover:bg-raised/70 cursor-pointer"
      >
        <ChevronRight
          aria-hidden
          className={clsx(
            "size-4 shrink-0 text-ink-faint transition-transform duration-300",
            isOpen && "rotate-90",
          )}
        />
        <FileCode className="size-4 text-accent shrink-0" />
        <span className="truncate font-mono text-xs text-ink font-medium">
          {file.path}
        </span>

        <button
          type="button"
          onClick={copyPath}
          title="复制文件路径"
          className="ml-1 inline-flex items-center gap-1 rounded p-1 text-ink-faint hover:bg-surface hover:text-ink transition-colors cursor-pointer"
        >
          {copied ? (
            <Check className="size-3 text-good" />
          ) : (
            <Copy className="size-3" />
          )}
        </button>

        <span className="ml-auto shrink-0 font-mono text-xs text-ink-faint">
          <span className="text-good">+{file.additions}</span>{" "}
          <span className="text-critical">−{file.deletions}</span>
          {collapsible && ` · ${totalLines} 行`}
        </span>
      </button>

      {/* 文件内容折叠区 */}
      <div
        className={clsx(
          "grid transition-[grid-template-rows] duration-300 ease-out bg-canvas",
          isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
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
                className="row-hover flex w-full items-center justify-center gap-1.5 bg-raised/30 px-5 py-2 text-xs text-ink-muted hover:text-ink border-t border-line/60 cursor-pointer"
              >
                <ChevronDown
                  aria-hidden
                  className={clsx(
                    "size-3.5 transition-transform duration-300",
                    fullyExpanded && "rotate-180",
                  )}
                />
                {fullyExpanded ? "收起剩余代码" : `展开其余 ${hiddenLines} 行`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function DiffPreview({ diff }: { diff: PatchDiff }) {
  // 维护所有已展开文件的路径集合
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [patchCopied, setPatchCopied] = useState(false);

  const allFilesAreExpanded =
    diff.files.length > 0 && diff.files.every((f) => expandedFiles.has(f.path));

  const toggleFile = (path: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const toggleAll = () => {
    if (allFilesAreExpanded) {
      setExpandedFiles(new Set());
    } else {
      setExpandedFiles(new Set(diff.files.map((f) => f.path)));
    }
  };

  const jumpToFile = (filePath: string) => {
    const isCurrentlyExpanded = expandedFiles.has(filePath);

    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filePath)) {
        next.delete(filePath);
      } else {
        next.add(filePath);
      }
      return next;
    });

    // 若当前为展开操作，则平滑滚动定位到该文件
    if (!isCurrentlyExpanded) {
      setTimeout(() => {
        const fileId = `diff-file-${filePath.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
        const el = document.getElementById(fileId);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }, 50);
    }
  };

  const totals = diff.files.reduce(
    (acc, file) => ({
      additions: acc.additions + file.additions,
      deletions: acc.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 },
  );

  const copyAllPatch = () => {
    const lines = diff.files.map(
      (f) =>
        `diff --git a/${f.path} b/${f.path}\n` +
        f.hunks
          .map(
            (h) =>
              h.header +
              "\n" +
              h.lines.map((l) => `${LINE_MARK[l.kind]}${l.text}`).join("\n"),
          )
          .join("\n"),
    );
    navigator.clipboard.writeText(lines.join("\n\n"));
    setPatchCopied(true);
    setTimeout(() => setPatchCopied(false), 2000);
  };

  return (
    <div>
      {/* 顶部统计与操作栏 */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="font-mono font-medium text-good">+{totals.additions}</span>
          <span className="font-mono font-medium text-critical">−{totals.deletions}</span>
          <span className="text-ink-faint">
            共 <strong className="text-ink">{diff.files.length}</strong> 个改动文件
          </span>
          <span className="text-ink-faint">
            来自已{diff.merged ? "合并" : "开放"}的 PR #{diff.prNumber}
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={copyAllPatch}
            className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2.5 py-1 text-ink-muted transition-colors hover:bg-raised hover:text-ink cursor-pointer"
          >
            {patchCopied ? (
              <>
                <Check className="size-3 text-good" />
                <span className="text-good">已复制 Patch</span>
              </>
            ) : (
              <>
                <Copy className="size-3" />
                <span>复制 Patch</span>
              </>
            )}
          </button>

          <button
            type="button"
            onClick={toggleAll}
            className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2.5 py-1 text-ink-muted transition-colors hover:bg-raised hover:text-ink cursor-pointer"
          >
            <Layers className="size-3" />
            <span>{allFilesAreExpanded ? "全部收起" : "全部展开"}</span>
          </button>

          <a
            href={diff.prUrl}
            target="_blank"
            rel="noreferrer"
            className="text-accent transition-colors hover:text-ink ml-1 font-medium"
          >
            GitHub 详情 ↗
          </a>
        </div>
      </div>

      {/* 文件快捷药丸导航栏 (File Pills) */}
      {diff.files.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line/60 bg-surface/50 px-5 py-2">
          <span className="text-xs text-ink-faint mr-1">文件直达:</span>
          {diff.files.map((file) => {
            const isExpanded = expandedFiles.has(file.path);
            return (
              <button
                key={file.path}
                type="button"
                onClick={() => jumpToFile(file.path)}
                className={clsx(
                  "group inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-xs transition-colors cursor-pointer",
                  isExpanded
                    ? "border-accent/60 bg-accent/15 text-accent"
                    : "border-line/80 bg-raised/60 text-ink-muted hover:border-accent/50 hover:bg-raised hover:text-ink",
                )}
              >
                <FileCode className="size-3 text-accent" />
                <span className="truncate max-w-[14rem]">
                  {file.path.split("/").pop()}
                </span>
                <span className="text-[10px] text-ink-faint">
                  <span className="text-good">+{file.additions}</span>{" "}
                  <span className="text-critical">−{file.deletions}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* 文件列表与 Diff Hunks */}
      <div>
        {diff.files.map((file) => (
          <FileBlock
            key={file.path}
            file={file}
            isOpen={expandedFiles.has(file.path)}
            onToggle={() => toggleFile(file.path)}
          />
        ))}
      </div>
    </div>
  );
}
