import clsx from "clsx";
import type { PatchDiff } from "@/lib/types";

/**
 * Diff 预览。数据来自 GitHub 公开 API 的已合并 PR，不是 Agent 的受控 artifact。
 * 公开仓库的 PR diff 本就是公开信息，这样既能展示真实改动，又不触碰脱敏边界。
 */

const LINE_STYLE: Record<string, string> = {
  add: "bg-good/10 text-ink",
  del: "bg-critical/10 text-ink",
  context: "text-ink-muted",
};

const LINE_MARK: Record<string, string> = { add: "+", del: "-", context: " " };

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
        <div key={file.path}>
          <div className="flex items-center gap-3 border-b border-line bg-raised/50 px-5 py-2.5">
            <span className="truncate font-mono text-sm text-ink-muted">{file.path}</span>
            <span className="ml-auto shrink-0 font-mono text-xs text-ink-faint">
              +{file.additions} −{file.deletions}
            </span>
          </div>

          {file.hunks.map((hunk) => (
            <div key={hunk.header}>
              <p className="bg-canvas px-5 py-1.5 font-mono text-xs text-ink-faint">
                {hunk.header}
              </p>
              <table className="w-full border-collapse">
                <tbody>
                  {hunk.lines.map((line, index) => (
                    <tr
                      key={index}
                      className={clsx(
                        "row-hover hover:brightness-125",
                        LINE_STYLE[line.kind],
                      )}
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
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
