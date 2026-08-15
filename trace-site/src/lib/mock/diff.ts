import type { PatchDiff } from "@/lib/types";

/**
 * M2 构造的 diff，形状对齐 GitHub 公开 API 的 pull request files 响应。
 * M3 会换成真实取数：GET /repos/{owner}/{repo}/pulls/{n}/files，
 * 公开仓库无需 token，且完全绕开 Agent 受控 artifact 的脱敏边界。
 */

export const heroDiff: PatchDiff = {
  prNumber: 1,
  prUrl: "https://github.com/yyqqCoding/MDToWord/pull/1",
  merged: true,
  files: [
    {
      path: "backend/app/pandoc_runner.py",
      additions: 22,
      deletions: 6,
      hunks: [
        {
          header: "@@ -142,12 +142,28 @@ def _prepare_markdown(source: str) -> str:",
          lines: [
            { kind: "context", oldNumber: 142, newNumber: 142, text: "    blocks = _split_fenced_blocks(source)" },
            { kind: "context", oldNumber: 143, newNumber: 143, text: "    rendered: list[str] = []" },
            { kind: "context", oldNumber: 144, newNumber: 144, text: "" },
            { kind: "context", oldNumber: 145, newNumber: 145, text: "    for block in blocks:" },
            { kind: "del", oldNumber: 146, newNumber: null, text: "        # Mermaid 暂按普通代码块处理" },
            { kind: "del", oldNumber: 147, newNumber: null, text: "        if block.language == \"mermaid\":" },
            { kind: "del", oldNumber: 148, newNumber: null, text: "            rendered.append(block.raw)" },
            { kind: "del", oldNumber: 149, newNumber: null, text: "            continue" },
            { kind: "add", oldNumber: null, newNumber: 146, text: "        # Mermaid 必须渲染成 PNG 再嵌入，否则 Word 里只会留下源码文本。" },
            { kind: "add", oldNumber: null, newNumber: 147, text: "        if block.language == \"mermaid\":" },
            { kind: "add", oldNumber: null, newNumber: 148, text: "            image_path = render_mermaid_to_png(block.content, workdir)" },
            { kind: "add", oldNumber: null, newNumber: 149, text: "            if image_path is None:" },
            { kind: "add", oldNumber: null, newNumber: 150, text: "                # 渲染失败时保留源码，至少不丢内容。" },
            { kind: "add", oldNumber: null, newNumber: 151, text: "                rendered.append(block.raw)" },
            { kind: "add", oldNumber: null, newNumber: 152, text: "                continue" },
            { kind: "add", oldNumber: null, newNumber: 153, text: "            rendered.append(f\"![]({image_path.as_posix()})\")" },
            { kind: "add", oldNumber: null, newNumber: 154, text: "            continue" },
            { kind: "context", oldNumber: 150, newNumber: 155, text: "" },
            { kind: "context", oldNumber: 151, newNumber: 156, text: "        rendered.append(block.raw)" },
            { kind: "context", oldNumber: 152, newNumber: 157, text: "" },
            { kind: "context", oldNumber: 153, newNumber: 158, text: "    return \"\\n\".join(rendered)" },
          ],
        },
      ],
    },
  ],
};
