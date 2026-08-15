import "server-only";

import { githubConfig } from "@/lib/server/env";
import type { DiffHunk, DiffLine, PatchDiff, PatchDiffFile } from "@/lib/types";

/**
 * 从 GitHub 公开 API 取已合并 PR 的 diff。
 *
 * 数据源刻意不是 Agent 的受控 artifact —— 那些文件含未脱敏的 JUnit 输出与完整
 * patch，是明确的展示禁区。公开仓库的 PR diff 本就是公开信息，取它既能展示真实
 * 改动，又完全绕开脱敏边界。
 *
 * 只有产出了 PR 的运行才有 diff，未发 PR 的运行返回 null，语义上本就不该有。
 */

const MAX_FILES = 5;
const MAX_LINES_PER_FILE = 120;

interface GithubPullFile {
  filename?: string;
  additions?: number;
  deletions?: number;
  patch?: string;
}

interface GithubPull {
  number?: number;
  html_url?: string;
  merged_at?: string | null;
}

/** 从 PR URL 里取编号；非本仓库的链接一律拒绝。 */
export function parsePullNumber(prUrl: string | null): number | null {
  if (!prUrl) return null;
  const expected = `https://github.com/${githubConfig.owner}/${githubConfig.repo}/pull/`;
  if (!prUrl.startsWith(expected)) return null;
  const value = Number.parseInt(prUrl.slice(expected.length), 10);
  return Number.isInteger(value) && value > 0 ? value : null;
}

/** 解析统一 diff 的 hunk 头，拿到起始行号。 */
function parseHunkHeader(header: string): { oldStart: number; newStart: number } {
  const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(header);
  return match
    ? { oldStart: Number(match[1]), newStart: Number(match[2]) }
    : { oldStart: 1, newStart: 1 };
}

function parsePatch(patch: string): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  let current: DiffHunk | null = null;
  let oldNumber = 0;
  let newNumber = 0;
  let emitted = 0;

  for (const raw of patch.split("\n")) {
    if (raw.startsWith("@@")) {
      const { oldStart, newStart } = parseHunkHeader(raw);
      oldNumber = oldStart;
      newNumber = newStart;
      current = { header: raw, lines: [] };
      hunks.push(current);
      continue;
    }
    if (!current || emitted >= MAX_LINES_PER_FILE) continue;
    if (raw.startsWith("\\")) continue; // "\ No newline at end of file"

    const marker = raw[0];
    const text = raw.slice(1);
    let line: DiffLine;
    if (marker === "+") {
      line = { kind: "add", oldNumber: null, newNumber: newNumber++, text };
    } else if (marker === "-") {
      line = { kind: "del", oldNumber: oldNumber++, newNumber: null, text };
    } else {
      line = { kind: "context", oldNumber: oldNumber++, newNumber: newNumber++, text };
    }
    current.lines.push(line);
    emitted += 1;
  }

  return hunks.filter((hunk) => hunk.lines.length > 0);
}

export async function fetchPullDiff(prUrl: string | null): Promise<PatchDiff | null> {
  const number = parsePullNumber(prUrl);
  if (number === null) return null;

  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  if (githubConfig.token) headers.Authorization = `Bearer ${githubConfig.token}`;

  const base = `https://api.github.com/repos/${githubConfig.owner}/${githubConfig.repo}/pulls/${number}`;
  // PR 一旦合并就不再变化，缓存一天足够；未合并的 PR 也不需要秒级新鲜度。
  const options = { headers, next: { revalidate: 86_400 } } as const;

  const [pullResponse, filesResponse] = await Promise.all([
    fetch(base, options),
    fetch(`${base}/files?per_page=${MAX_FILES}`, options),
  ]);

  if (!pullResponse.ok || !filesResponse.ok) return null;

  const pull = (await pullResponse.json()) as GithubPull;
  const rawFiles = (await filesResponse.json()) as GithubPullFile[];

  const files: PatchDiffFile[] = rawFiles
    .filter((file) => file.filename && file.patch)
    .slice(0, MAX_FILES)
    .map((file) => ({
      path: file.filename!,
      additions: file.additions ?? 0,
      deletions: file.deletions ?? 0,
      hunks: parsePatch(file.patch!),
    }));

  if (files.length === 0) return null;

  return {
    prNumber: pull.number ?? number,
    prUrl: pull.html_url ?? prUrl!,
    merged: Boolean(pull.merged_at),
    files,
  };
}
