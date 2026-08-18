import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { MarkdownPreview } from './preview';
import { loadFolders, saveFolders, subscribeToFolders } from './storage';
import type { MarkdownDialog, MarkdownFolder } from './types';

type ViewMode = 'preview' | 'split' | 'markdown';

interface TocEntry {
  level: number;
  text: string;
  index: number;
}

interface TocNode extends TocEntry {
  children: TocNode[];
}

/**
 * Standalone preview window opened via chrome.windows.create. It reads the
 * target folder + ordered dialog selection from the URL, then renders the
 * whole selection as one continuous document — just like the exported Word
 * file. A collapsible table-of-contents tree mirrors the document headings,
 * the left column is the full rendered preview, and the right column is the
 * editable Markdown (split per dialog so edits write straight back to
 * chrome.storage). A view toggle switches between all-preview / split /
 * all-markdown with a smooth transition.
 */
export function PreviewWindow() {
  const [folders, setFolders] = useState<MarkdownFolder[]>([]);
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState<ViewMode>('preview');

  const { folderId, dialogIds } = useMemo(() => parsePreviewParams(), []);

  useEffect(() => {
    void loadFolders().then((saved) => {
      setFolders(saved);
      setReady(true);
    });
    return subscribeToFolders(setFolders);
  }, []);

  const folder = useMemo(
    () => folders.find((item) => item.id === folderId),
    [folders, folderId],
  );

  // Ordered dialogs to preview: honour the selection order passed from the
  // side panel, falling back to every dialog in the folder ("preview all").
  const dialogs = useMemo<MarkdownDialog[]>(() => {
    if (!folder) {
      return [];
    }
    if (dialogIds.length === 0) {
      return folder.dialogs;
    }
    return dialogIds
      .map((id) => folder.dialogs.find((dialog) => dialog.id === id))
      .filter((dialog): dialog is MarkdownDialog => Boolean(dialog));
  }, [folder, dialogIds]);

  // The whole selection concatenated the same way export merges it.
  const mergedMarkdown = useMemo(
    () =>
      dialogs
        .map((dialog) => dialog.markdown.trim())
        .filter(Boolean)
        .join('\n\n'),
    [dialogs],
  );

  const toc = useMemo(() => buildTocTree(extractHeadings(mergedMarkdown)), [mergedMarkdown]);

  const renderRef = useRef<HTMLDivElement>(null);

  // Tag rendered headings with stable ids so the TOC can scroll to them.
  useLayoutEffect(() => {
    const root = renderRef.current;
    if (!root) {
      return;
    }
    const headings = root.querySelectorAll('h1, h2, h3, h4, h5, h6');
    headings.forEach((heading, index) => {
      heading.id = `toc-heading-${index}`;
    });
  }, [mergedMarkdown, mode]);

  async function updateDialog(id: string, patch: Partial<MarkdownDialog>) {
    if (!folder) {
      return;
    }
    const nextFolders = folders.map((item) =>
      item.id === folder.id
        ? {
            ...item,
            dialogs: item.dialogs.map((dialog) =>
              dialog.id === id ? { ...dialog, ...patch } : dialog,
            ),
          }
        : item,
    );
    setFolders(nextFolders);
    await saveFolders(nextFolders);
  }

  function scrollToHeading(index: number) {
    const target = renderRef.current?.querySelector(`#toc-heading-${index}`);
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  useEffect(() => {
    document.title = folder ? `预览 · ${folder.name}` : '预览';
  }, [folder]);

  if (ready && !folder) {
    return (
      <main className="preview-window">
        <div className="preview-window-empty">
          <p>找不到要预览的文件夹,它可能已被删除。</p>
        </div>
      </main>
    );
  }

  const showPreview = mode !== 'markdown';
  const showEditor = mode !== 'preview';

  return (
    <main className="preview-window preview-doc">
      <header className="preview-doc-header">
        <div className="preview-doc-heading">
          <h1>{folder?.name ?? '预览'}</h1>
          <span className="preview-doc-count">{dialogs.length} 个对话框</span>
        </div>
        <div className={`preview-doc-modes mode-${mode}`} role="group" aria-label="预览模式">
          <span className="preview-doc-modes-thumb" aria-hidden="true" />
          <button
            type="button"
            className={mode === 'preview' ? 'active' : ''}
            onClick={() => setMode('preview')}
          >
            全部预览
          </button>
          <button
            type="button"
            className={mode === 'split' ? 'active' : ''}
            onClick={() => setMode('split')}
          >
            分栏编辑
          </button>
          <button
            type="button"
            className={mode === 'markdown' ? 'active' : ''}
            onClick={() => setMode('markdown')}
          >
            全部 Markdown
          </button>
        </div>
      </header>

      {dialogs.length === 0 || !mergedMarkdown ? (
        <div className="preview-window-empty">
          <p>没有可预览的内容。</p>
        </div>
      ) : (
        <div className={`preview-doc-layout mode-${mode}`}>
          <nav className="preview-doc-toc" aria-label="目录">
            <p className="preview-doc-toc-title">目录</p>
            {toc.length === 0 ? (
              <p className="preview-doc-toc-empty">暂无标题</p>
            ) : (
              <TocTree nodes={toc} onSelect={scrollToHeading} />
            )}
          </nav>

          <div className="preview-doc-panes" key={mode}>
            {showPreview && (
              <div className="preview-doc-col preview-doc-render">
                <div className="preview-doc-col-label">Word 预览</div>
                <div className="preview-doc-render-scroll" ref={renderRef}>
                  <MarkdownPreview value={mergedMarkdown} />
                </div>
              </div>
            )}
            {showEditor && (
              <div className="preview-doc-col preview-doc-edit">
                <div className="preview-doc-col-label">Markdown 编辑</div>
                <div className="preview-doc-edit-scroll">
                  {dialogs.map((dialog, index) => (
                    <section className="preview-doc-block" key={dialog.id}>
                      <div className="preview-doc-block-label">
                        <span className="preview-doc-block-index">{index + 1}</span>
                        <input
                          className="preview-title-input"
                          aria-label="对话框标题"
                          value={dialog.title}
                          onChange={(event) =>
                            void updateDialog(dialog.id, { title: event.target.value })
                          }
                          placeholder="对话框标题"
                        />
                      </div>
                      <textarea
                        className="preview-source"
                        aria-label={`${dialog.title} Markdown source`}
                        value={dialog.markdown}
                        onChange={(event) =>
                          void updateDialog(dialog.id, { markdown: event.target.value })
                        }
                        placeholder="在这里编辑 Markdown"
                      />
                    </section>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}

/**
 * Recursive, collapsible TOC tree. Nodes with children get a chevron that
 * toggles their subtree (Word-style); clicking the label scrolls the preview
 * to that heading. Everything starts expanded and can be collapsed.
 */
function TocTree({
  nodes,
  onSelect,
}: {
  nodes: TocNode[];
  onSelect: (index: number) => void;
}) {
  return (
    <ul className="preview-doc-toc-list">
      {nodes.map((node) => (
        <TocItem key={node.index} node={node} onSelect={onSelect} />
      ))}
    </ul>
  );
}

function TocItem({ node, onSelect }: { node: TocNode; onSelect: (index: number) => void }) {
  const [open, setOpen] = useState(true);
  const hasChildren = node.children.length > 0;

  return (
    <li className={`toc-node toc-level-${node.level}`}>
      <div className="toc-node-row">
        {hasChildren ? (
          <button
            type="button"
            className={`toc-node-toggle ${open ? 'open' : ''}`}
            aria-label={open ? '折叠' : '展开'}
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
              <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        ) : (
          <span className="toc-node-toggle toc-node-toggle-empty" aria-hidden="true" />
        )}
        <button type="button" className="toc-node-label" onClick={() => onSelect(node.index)}>
          {node.text}
        </button>
      </div>
      {hasChildren && open && <TocTree nodes={node.children} onSelect={onSelect} />}
    </li>
  );
}

/**
 * Assemble flat headings into a nested tree by level, so the TOC can render
 * collapsible parent/child branches. A heading nests under the most recent
 * ancestor with a smaller level.
 */
function buildTocTree(entries: TocEntry[]): TocNode[] {
  const root: TocNode[] = [];
  const stack: TocNode[] = [];

  for (const entry of entries) {
    const node: TocNode = { ...entry, children: [] };
    while (stack.length > 0 && stack[stack.length - 1].level >= entry.level) {
      stack.pop();
    }
    if (stack.length === 0) {
      root.push(node);
    } else {
      stack[stack.length - 1].children.push(node);
    }
    stack.push(node);
  }

  return root;
}

/**
 * Pull ATX headings out of the merged markdown in document order, skipping
 * fenced code blocks so `# comments` inside code don't pollute the TOC. The
 * index lines up with the rendered heading order for scroll targeting.
 */
function extractHeadings(markdown: string): TocEntry[] {
  const entries: TocEntry[] = [];
  const lines = markdown.split('\n');
  let inFence = false;
  let headingIndex = 0;

  for (const line of lines) {
    const fenceMatch = /^\s*(```|~~~)/.exec(line);
    if (fenceMatch) {
      inFence = !inFence;
      continue;
    }
    if (inFence) {
      continue;
    }
    const headingMatch = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (headingMatch) {
      entries.push({
        level: headingMatch[1].length,
        text: headingMatch[2].trim(),
        index: headingIndex,
      });
      headingIndex += 1;
    }
  }

  return entries;
}

function parsePreviewParams(): { folderId: string; dialogIds: string[] } {
  const params = new URLSearchParams(window.location.search);
  const folderId = params.get('folder') ?? '';
  const rawDialogs = params.get('dialogs') ?? '';
  const dialogIds = rawDialogs
    .split(',')
    .map((id) => id.trim())
    .filter(Boolean);
  return { folderId, dialogIds };
}