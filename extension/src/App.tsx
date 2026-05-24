import { Activity, CloudUpload, Code2, Download, Eye, Plus, RotateCw, Trash2 } from 'lucide-react';
import type { DragEvent } from 'react';
import { useEffect, useMemo, useState } from 'react';

import { checkHealth, convertToDocx, downloadDocx } from './api';
import { MarkdownPreview } from './preview';
import { loadDialogs, saveDialogs } from './storage';
import type { MarkdownDialog, ServiceStatus } from './types';

const DEFAULT_FILENAME = 'md-to-word.docx';
const MAX_FILES_PER_UPLOAD = 9;
const SERVICE_URL = 'https://mdtoword.onrender.com';
const STARTER_MARKDOWN = `# 物理公式示例

质能方程描述质量和能量之间的关系：$E = mc^2$。

下面是一个定积分：

$$
\\int_0^1 x^2 dx = \\frac{1}{3}
$$

| 名称 | 公式 | 说明 |
|---|---|---|
| 动能 | $E_k = \\frac{1}{2}mv^2$ | 物体因为运动而具有的能量 |
| 勾股定理 | $a^2 + b^2 = c^2$ | 直角三角形边长关系 |
`;

export function App() {
  const [dialogs, setDialogs] = useState<MarkdownDialog[]>([]);
  const [exportSelectionIds, setExportSelectionIds] = useState<string[]>([]);
  const [expandedSourceDialogId, setExpandedSourceDialogId] = useState<string>('');
  const [previewDialogId, setPreviewDialogId] = useState<string>('');
  const [status, setStatus] = useState<ServiceStatus>('unknown');
  const [message, setMessage] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const previewDialog = useMemo(() => dialogs.find((dialog) => dialog.id === previewDialogId), [dialogs, previewDialogId]);
  const mergedMarkdown = useMemo(
    () =>
      exportSelectionIds
        .map((id) => dialogs.find((dialog) => dialog.id === id)?.markdown.trim() ?? '')
        .filter(Boolean)
        .join('\n\n'),
    [dialogs, exportSelectionIds],
  );
  const canExport = useMemo(() => mergedMarkdown.length > 0 && !isExporting, [isExporting, mergedMarkdown]);
  const statusLabel = status === 'available' ? '插件已就绪' : status === 'unavailable' ? '服务不可用' : '插件待检查';
  const serviceCardHint = status === 'available' ? '插件已就绪' : '确认插件已就绪';

  useEffect(() => {
    void loadDialogs().then((savedDialogs) => {
      const initialDialogs = savedDialogs.length > 0 ? savedDialogs : [createDialog('示例', STARTER_MARKDOWN)];
      setDialogs(initialDialogs);
    });
  }, []);

  async function updateDialogs(nextDialogs: MarkdownDialog[]) {
    setDialogs(nextDialogs);
    await saveDialogs(nextDialogs);
  }

  async function handleMarkdownChange(id: string, value: string) {
    const nextDialogs = dialogs.map((dialog) => (dialog.id === id ? { ...dialog, markdown: value } : dialog));
    if (previewDialogId === id) {
      setPreviewDialogId('');
    }
    await updateDialogs(nextDialogs);
  }

  async function handleTitleChange(id: string, value: string) {
    const nextDialogs = dialogs.map((dialog) => (dialog.id === id ? { ...dialog, title: value } : dialog));
    await updateDialogs(nextDialogs);
  }

  async function handleHealthCheck() {
    setMessage('');
    const available = await checkHealth(SERVICE_URL);
    setStatus(available ? 'available' : 'unavailable');
    setMessage(available ? '' : '转换服务不可用。');
  }

  async function handleAddDialog() {
    const nextDialog = createDialog(`对话 ${dialogs.length + 1}`, '');
    await updateDialogs([...dialogs, nextDialog]);
    setExpandedSourceDialogId(nextDialog.id);
    setPreviewDialogId('');
  }

  async function handleDeleteDialog(id: string) {
    const dialog = dialogs.find((item) => item.id === id);
    if (!window.confirm(`确认删除${dialog ? `「${dialog.title}」` : '该对话框'}吗？`)) {
      return;
    }

    const nextDialogs = dialogs.filter((dialog) => dialog.id !== id);
    const fallbackDialog = nextDialogs[0] ?? createDialog('对话 1', '');
    const normalizedDialogs = nextDialogs.length > 0 ? nextDialogs : [fallbackDialog];
    await updateDialogs(normalizedDialogs);
    setExportSelectionIds((selectionIds) => selectionIds.filter((selectionId) => selectionId !== id));
    if (expandedSourceDialogId === id) {
      setExpandedSourceDialogId('');
    }
    if (previewDialogId === id) {
      setPreviewDialogId('');
    }
  }

  function handleToggleExportSelection(id: string) {
    setExportSelectionIds((selectionIds) => {
      if (selectionIds.includes(id)) {
        return selectionIds.filter((selectionId) => selectionId !== id);
      }

      return [...selectionIds, id];
    });
  }

  function handleMdPreview(id: string) {
    if (expandedSourceDialogId === id) {
      setExpandedSourceDialogId('');
      setMessage('MD 预览已关闭。');
      return;
    }

    setPreviewDialogId('');
    setExpandedSourceDialogId(id);
    setMessage('正在预览 MD 源文本。');
  }

  function handleDialogDoubleClick(id: string) {
    if (expandedSourceDialogId === id || previewDialogId === id) {
      setExpandedSourceDialogId('');
      setPreviewDialogId('');
      setMessage('预览已关闭。');
    }
  }

  function handleWordPreview(id: string) {
    const dialog = dialogs.find((item) => item.id === id);
    if (!dialog?.markdown.trim()) {
      setMessage('请选择一个有 Markdown 内容的对话框再预览。');
      return;
    }

    if (previewDialogId === id) {
      setPreviewDialogId('');
      setMessage('预览已关闭。');
      return;
    }

    setExpandedSourceDialogId('');
    setPreviewDialogId(id);
    setMessage(`正在预览 Word 格式：${dialog.title}`);
  }

  async function handleFileUpload(files: FileList | File[]) {
    if (!files || files.length === 0) {
      return;
    }

    const selectedFiles = Array.from(files);
    if (selectedFiles.length > MAX_FILES_PER_UPLOAD) {
      setMessage(`单次最多上传 ${MAX_FILES_PER_UPLOAD} 个 Markdown 文件。`);
      return;
    }

    const invalidFile = selectedFiles.find((file) => !file.name.toLowerCase().endsWith('.md'));
    if (invalidFile) {
      setMessage(`只支持 .md 文件：${invalidFile.name}`);
      return;
    }

    const importedDialogs = await Promise.all(
      selectedFiles.map(async (file) => createDialog(file.name, await file.text())),
    );
    const nextDialogs = [...dialogs, ...importedDialogs];
    await updateDialogs(nextDialogs);
    setExpandedSourceDialogId(importedDialogs[0].id);
    setPreviewDialogId('');
    setMessage(`已导入 ${importedDialogs.length} 个 Markdown 文件。点击圆圈设置导出顺序。`);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    void handleFileUpload(Array.from(event.dataTransfer.files));
  }

  async function handleExport() {
    if (!mergedMarkdown) {
      setMessage('请选择一个或多个对话框后再导出。');
      return;
    }

    setIsExporting(true);
    setMessage('正在导出 DOCX...');
    try {
      const blob = await convertToDocx(SERVICE_URL, {
        title: 'md-to-word',
        markdown: mergedMarkdown,
        options: {
          filename: DEFAULT_FILENAME,
        },
      });
      downloadDocx(blob, DEFAULT_FILENAME);
      setMessage('DOCX 已开始下载。');
    } catch (error) {
      setStatus('unavailable');
      setMessage(error instanceof Error ? error.message : '导出失败。');
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <main className="app">
      <header className="toolbar">
        <div className="brand">
          <img className="brand-icon" src="icon.svg" alt="" />
          <div>
            <div className="brand-title-row">
              <h1>MD To Word</h1>
              <span className={`header-status ${status}`}>
                <span />
                {statusLabel}
              </span>
            </div>
            <p>Markdown 一键转 Word</p>
          </div>
        </div>
      </header>
      {message ? (
        <section className="status-row" aria-live="polite">
          <span className={`status-dot ${status}`} />
          <span>{message}</span>
        </section>
      ) : null}
      <section className="workspace">
        <section className="dialog-list" aria-label="Markdown dialogs">
          <div className="dialog-list-header">
            <span>对话框</span>
          </div>
          <div className="action-card-grid operation-card-grid">
            <button type="button" className="action-card service-card" onClick={() => void handleHealthCheck()} title="检查服务">
              <span className={`action-card-icon ${status === 'available' ? 'ready' : ''}`}>
                <Activity aria-hidden="true" size={22} />
              </span>
              <strong>检查服务</strong>
              <small>{serviceCardHint}</small>
            </button>
            <button type="button" className="action-card export-card" disabled={!canExport} onClick={() => void handleExport()} title="导出 DOCX">
              <span className="action-card-icon">
                {isExporting ? <RotateCw aria-hidden="true" size={22} className="spin" /> : <Download aria-hidden="true" size={22} />}
              </span>
              <strong>导出 Word</strong>
              <small>按圆圈编号合并导出</small>
            </button>
          </div>
          <div className="action-card-grid">
            <button type="button" className="action-card add-dialog-button" onClick={() => void handleAddDialog()} title="新增对话框">
              <span className="action-card-icon">
                <Plus aria-hidden="true" size={22} />
              </span>
              <strong>新建对话框</strong>
              <small>创建空白 Markdown 输入框</small>
            </button>
            <label
              className="action-card upload-dropzone"
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleDrop}
            >
              <span className="action-card-icon">
                <CloudUpload aria-hidden="true" size={24} />
              </span>
              <strong>拖拽或上传 .md 文件</strong>
              <small>支持单个或批量上传</small>
              <input
                type="file"
                accept=".md,text/markdown"
                multiple
                onChange={(event) => {
                  if (event.currentTarget.files) {
                    void handleFileUpload(event.currentTarget.files);
                  }
                  event.currentTarget.value = '';
                }}
              />
            </label>
          </div>
          <div className="dialog-items">
            {dialogs.map((dialog) => {
              const exportOrder = exportSelectionIds.indexOf(dialog.id) + 1;
              const isSourceOpen = expandedSourceDialogId === dialog.id;
              const isPreviewOpen = previewDialogId === dialog.id;
              return (
              <article
                className={`dialog-item ${isSourceOpen || isPreviewOpen ? 'expanded' : ''}`}
                key={dialog.id}
                onDoubleClick={() => handleDialogDoubleClick(dialog.id)}
              >
                <div className="dialog-row">
                  <button
                    type="button"
                    className={`export-order-button ${exportOrder ? 'selected' : ''}`}
                    onClick={() => handleToggleExportSelection(dialog.id)}
                    onDoubleClick={(event) => event.stopPropagation()}
                    title={exportOrder ? `导出顺序 ${exportOrder}` : '加入导出'}
                  >
                    {exportOrder || ''}
                  </button>
                  <input
                    className="dialog-title-input"
                    aria-label="对话框标题"
                    value={dialog.title}
                    onChange={(event) => void handleTitleChange(dialog.id, event.target.value)}
                    onDoubleClick={(event) => event.stopPropagation()}
                    placeholder="对话框标题"
                  />
                  <div className="dialog-controls">
                    <button
                      type="button"
                      className={`secondary preview-toggle ${isSourceOpen ? 'active' : ''}`}
                      onClick={() => handleMdPreview(dialog.id)}
                      onDoubleClick={(event) => event.stopPropagation()}
                      title="MD 格式预览"
                    >
                      <Code2 aria-hidden="true" size={16} />
                      MD格式预览
                    </button>
                    <button
                      type="button"
                      className={`secondary preview-toggle ${isPreviewOpen ? 'active' : ''}`}
                      disabled={!dialog.markdown.trim()}
                      onClick={() => handleWordPreview(dialog.id)}
                      onDoubleClick={(event) => event.stopPropagation()}
                      title="Word 格式预览"
                    >
                      <Eye aria-hidden="true" size={16} />
                      Word格式预览
                    </button>
                  <button
                    type="button"
                    className="icon-button danger"
                    onClick={() => void handleDeleteDialog(dialog.id)}
                    onDoubleClick={(event) => event.stopPropagation()}
                    title="删除对话框"
                  >
                    <Trash2 aria-hidden="true" size={14} />
                  </button>
                  </div>
                </div>
                {isSourceOpen ? (
                  <textarea
                    className="dialog-source"
                    aria-label={`${dialog.title} Markdown source`}
                    placeholder="在这里粘贴 Markdown"
                    value={dialog.markdown}
                    onChange={(event) => void handleMarkdownChange(dialog.id, event.target.value)}
                  />
                ) : null}
                {isPreviewOpen && previewDialog ? (
                  <div className="dialog-preview">
                    <MarkdownPreview value={previewDialog.markdown} />
                  </div>
                ) : null}
              </article>
              );
            })}
          </div>
        </section>
      </section>
    </main>
  );
}

function createDialog(title: string, markdown: string): MarkdownDialog {
  return {
    id: createDialogId(),
    title,
    markdown,
  };
}

function createDialogId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }

  return `dialog-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
