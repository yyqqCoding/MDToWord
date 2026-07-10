import {
  ChevronLeft,
  ChevronRight,
  CloudUpload,
  Code2,
  Download,
  Eye,
  FileText,
  Folder,
  FolderPlus,
  ListChecks,
  MessageSquare,
  Move,
  Pencil,
  Plus,
  RotateCw,
  Trash2,
} from 'lucide-react';
import type { CSSProperties, DragEvent } from 'react';
import { useEffect, useMemo, useState } from 'react';

import confetti from 'canvas-confetti';

import { checkHealth, convertToDocx, downloadDocx, submitFeedback } from './api';
import { createDialog, createFolder, moveDialogBetweenFolders } from './folders';
import { MarkdownPreview } from './preview';
import { loadFolders, loadOnboardingCompleted, saveFolders, saveOnboardingCompleted } from './storage';
import type { MarkdownDialog, MarkdownFolder, ServiceStatus } from './types';

const CONVERSION_REQUEST_FILENAME = 'md-to-word.docx';
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

function sanitizeFilenamePart(value: string, fallback: string): string {
  const safe = value
    .trim()
    .replace(/[<>:"\\|?*]/g, '')
    .replace(/\//g, '')
    .replace(/\s+/g, ' ')
    .replace(/\.+$/g, '');
  return safe || fallback;
}

function createExportFilename(folder: MarkdownFolder, selectionIds: string[]): string {
  const selectedDialogs = selectionIds
    .map((id) => folder.dialogs.find((dialog) => dialog.id === id))
    .filter((dialog): dialog is MarkdownDialog => Boolean(dialog));
  const folderName = sanitizeFilenamePart(folder.name, '未命名文件夹');

  if (selectedDialogs.length === 1) {
    const dialogName = sanitizeFilenamePart(selectedDialogs[0].title, '未命名对话框');
    return `${folderName}/${dialogName}.docx`;
  }

  return `${folderName}.docx`;
}

type OnboardingView = 'folder-list' | 'folder-detail';

interface OnboardingExample {
  input: string;
  output: string;
  outputType?: 'text' | 'table' | 'headings';
}

interface OnboardingStep {
  title: string;
  body: string;
  target?: string;
  view?: OnboardingView;
  example?: OnboardingExample;
}

interface SpotlightRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const ONBOARDING_STEPS: OnboardingStep[] = [
  {
    title: '公式自动转换',
    body: '复制 AI 对话中的公式，导出后自动转为 Word 可编辑公式，无需截图。',
    example: {
      input: '$$E = mc^2$$',
      output: 'E = mc²  可编辑公式',
    },
  },
  {
    title: '表格转三线表',
    body: '复制 AI 生成的表格，导出时自动转为规范的三线表格式。',
    example: {
      input: '| 方法 | 准确率 |\n|------|--------|\n| CNN  | 95.2%  |',
      output: '',
      outputType: 'table',
    },
  },
  {
    title: '标题自动识别',
    body: '带 # 的行自动转为对应级别标题。你也可以手动添加多个 # 来控制段落层级。',
    example: {
      input: '# 一级标题\n## 二级标题\n### 三级标题',
      output: '',
      outputType: 'headings',
    },
  },
  {
    title: '新建文件夹',
    body: '开始一个主题或章节前，先新建文件夹。每个文件夹里的对话互相隔离，后续导出也只会处理当前文件夹。',
    target: 'add-folder',
    view: 'folder-list',
  },
  {
    title: '进入文件夹',
    body: '点击文件夹进入内容区。你可以把不同课程、论文章节或项目分别放进不同文件夹。',
    target: 'open-folder',
    view: 'folder-list',
  },
  {
    title: '在文件夹内创建对话框',
    body: '进入文件夹后，点击“新建对话框”。一个对话框可以保存一段 AI 输出、一个章节或一份 Markdown 内容。',
    target: 'add-dialog',
    view: 'folder-detail',
  },
  {
    title: '粘贴 AI 网页内容',
    body: '把网页端 AI 生成的内容直接粘贴到这里。即使公式不是标准 Markdown 写法，也会在预览和导出时做规范化处理。',
    target: 'dialog-source',
    view: 'folder-detail',
  },
  {
    title: '预览 Word 效果',
    body: '点击眼睛按钮可以直接预览接近 Word 的排版效果，适合在导出前检查标题、表格和公式。',
    target: 'word-preview',
    view: 'folder-detail',
  },
  {
    title: '选择导出内容和顺序',
    body: '点击左侧圆圈加入导出队列。圆圈里的数字就是合并导出时的顺序。',
    target: 'export-order',
    view: 'folder-detail',
  },
  {
    title: '批量选择',
    body: '文件夹里有多个对话框时，可以一键全选或取消全选，然后按顺序合并成一个 Word 文档。',
    target: 'select-all',
    view: 'folder-detail',
  },
  {
    title: '导出 Word',
    body: '确认选择后点击“导出 Word”。单选时文件名会包含文件夹和对话框，多选时使用文件夹名。',
    target: 'export-docx',
    view: 'folder-detail',
  },
  {
    title: '重命名内容',
    body: '这里可以修改对话框标题。标题会帮助你整理内容，也会用于单个对话框导出的文件名。',
    target: 'dialog-title',
    view: 'folder-detail',
  },
  {
    title: '移动或删除对话框',
    body: '右侧按钮可以查看源码预览、Word 预览、移动到其它文件夹或删除对话框，用来整理长期积累的内容。',
    target: 'dialog-management',
    view: 'folder-detail',
  },
];

export function App() {
  const [folders, setFolders] = useState<MarkdownFolder[]>([]);
  const [openFolderId, setOpenFolderId] = useState<string>('');
  const [renamingFolderId, setRenamingFolderId] = useState<string>('');
  const [exportSelectionIds, setExportSelectionIds] = useState<string[]>([]);
  const [expandedSourceDialogId, setExpandedSourceDialogId] = useState<string>('');
  const [previewDialogId, setPreviewDialogId] = useState<string>('');
  const [moveMenuDialogId, setMoveMenuDialogId] = useState<string>('');
  const [status, setStatus] = useState<ServiceStatus>('unknown');
  const [message, setMessage] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const [onboardingActive, setOnboardingActive] = useState(false);
  const [onboardingStepIndex, setOnboardingStepIndex] = useState(0);
  const [spotlightRect, setSpotlightRect] = useState<SpotlightRect | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackTab, setFeedbackTab] = useState<'bug' | 'feature'>('bug');
  const [feedbackMd, setFeedbackMd] = useState('');
  const [feedbackDesc, setFeedbackDesc] = useState('');
  const [feedbackContact, setFeedbackContact] = useState('');
  const [feedbackFeature, setFeedbackFeature] = useState('');
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);

  const openFolder = useMemo(
    () => folders.find((folder) => folder.id === openFolderId),
    [folders, openFolderId],
  );
  const previewDialog = useMemo(
    () => openFolder?.dialogs.find((dialog) => dialog.id === previewDialogId),
    [openFolder, previewDialogId],
  );
  const mergedMarkdown = useMemo(
    () =>
      exportSelectionIds
        .map((id) => openFolder?.dialogs.find((dialog) => dialog.id === id)?.markdown.trim() ?? '')
        .filter(Boolean)
        .join('\n\n'),
    [openFolder, exportSelectionIds],
  );
  const canExport = useMemo(() => mergedMarkdown.length > 0 && !isExporting, [isExporting, mergedMarkdown]);
  const allDialogsSelected =
    !!openFolder &&
    openFolder.dialogs.length > 0 &&
    openFolder.dialogs.every((dialog) => exportSelectionIds.includes(dialog.id));
  const statusLabel = status === 'available' ? '插件已就绪' : status === 'unavailable' ? '服务不可用' : '插件待检查';
  const onboardingStep = onboardingActive ? ONBOARDING_STEPS[onboardingStepIndex] : undefined;
  const isLastOnboardingStep = onboardingStepIndex === ONBOARDING_STEPS.length - 1;

  useEffect(() => {
    void Promise.all([loadFolders(), loadOnboardingCompleted()]).then(([savedFolders, onboardingCompleted]) => {
      const initialFolders =
        savedFolders.length > 0
          ? savedFolders
          : [createFolder('默认', [createDialog('示例', STARTER_MARKDOWN)])];
      setFolders(initialFolders);
      if (!onboardingCompleted) {
        setOnboardingActive(true);
      }
    });
  }, []);

  useEffect(() => {
    if (!onboardingStep?.view) {
      return;
    }
    if (onboardingStep.view === 'folder-list') {
      setOpenFolderId('');
      resetFolderScopedState();
      return;
    }
    if (!openFolder && folders.length > 0) {
      setOpenFolderId(folders[0].id);
      return;
    }
    if (openFolder && onboardingStep.target === 'dialog-source') {
      const firstDialog = openFolder.dialogs[0];
      if (firstDialog) {
        setExpandedSourceDialogId(firstDialog.id);
        setPreviewDialogId('');
      }
    }
  }, [folders, onboardingStep, openFolder]);

  useEffect(() => {
    if (!onboardingActive || !onboardingStep?.target) {
      setSpotlightRect(null);
      return;
    }

    const targetName = onboardingStep.target;

    function updateSpotlight() {
      const target = document.querySelector<HTMLElement>(`[data-onboarding-target="${targetName}"]`);
      if (!target) {
        setSpotlightRect(null);
        return;
      }
      target.scrollIntoView({ block: 'center', inline: 'nearest' });
      const rect = target.getBoundingClientRect();
      setSpotlightRect({
        top: rect.top,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      });
    }

    const timeoutId = window.setTimeout(updateSpotlight, 50);
    window.addEventListener('resize', updateSpotlight);
    window.addEventListener('scroll', updateSpotlight, true);

    return () => {
      window.clearTimeout(timeoutId);
      window.removeEventListener('resize', updateSpotlight);
      window.removeEventListener('scroll', updateSpotlight, true);
    };
  }, [expandedSourceDialogId, folders, onboardingActive, onboardingStep, openFolderId, previewDialogId]);

  async function updateFolders(nextFolders: MarkdownFolder[]) {
    setFolders(nextFolders);
    await saveFolders(nextFolders);
  }

  function startOnboarding() {
    setOnboardingStepIndex(0);
    setOnboardingActive(true);
    setMessage('');
  }

  async function completeOnboarding() {
    setOnboardingActive(false);
    setOnboardingStepIndex(0);
    setSpotlightRect(null);
    await saveOnboardingCompleted(true);
  }

  function handlePreviousOnboardingStep() {
    setOnboardingStepIndex((index) => Math.max(index - 1, 0));
  }

  function handleNextOnboardingStep() {
    if (isLastOnboardingStep) {
      void completeOnboarding();
      return;
    }
    setOnboardingStepIndex((index) => index + 1);
  }

  function resetFolderScopedState() {
    setExportSelectionIds([]);
    setExpandedSourceDialogId('');
    setPreviewDialogId('');
    setMoveMenuDialogId('');
  }

  async function updateCurrentDialogs(updater: (dialogs: MarkdownDialog[]) => MarkdownDialog[]) {
    if (!openFolder) {
      return;
    }
    const folderId = openFolder.id;
    const nextFolders = folders.map((folder) =>
      folder.id === folderId ? { ...folder, dialogs: updater(folder.dialogs) } : folder,
    );
    await updateFolders(nextFolders);
  }

  async function handleAddFolder() {
    const folder = createFolder(`文件夹 ${folders.length + 1}`, []);
    await updateFolders([...folders, folder]);
    setRenamingFolderId(folder.id);
  }

  function handleOpenFolder(id: string) {
    setOpenFolderId(id);
    resetFolderScopedState();
    setRenamingFolderId('');
    setMessage('');
  }

  function handleBackToFolders() {
    setOpenFolderId('');
    resetFolderScopedState();
    setMessage('');
  }

  async function handleRenameFolder(id: string, name: string) {
    await updateFolders(folders.map((folder) => (folder.id === id ? { ...folder, name } : folder)));
  }

  async function handleDeleteFolder(id: string) {
    const folder = folders.find((item) => item.id === id);
    const count = folder?.dialogs.length ?? 0;
    const base = `确认删除文件夹${folder ? `「${folder.name}」` : ''}吗？`;
    const confirmText = count > 0 ? `${base}将同时删除其中 ${count} 个对话框。` : base;
    if (!window.confirm(confirmText)) {
      return;
    }
    await updateFolders(folders.filter((item) => item.id !== id));
    if (openFolderId === id) {
      handleBackToFolders();
    }
  }

  async function handleAddDialog() {
    if (!openFolder) {
      return;
    }
    const dialog = createDialog(`对话 ${openFolder.dialogs.length + 1}`, '');
    await updateCurrentDialogs((dialogs) => [...dialogs, dialog]);
    setExpandedSourceDialogId(dialog.id);
    setPreviewDialogId('');
  }

  async function handleMarkdownChange(id: string, value: string) {
    if (previewDialogId === id) {
      setPreviewDialogId('');
    }
    await updateCurrentDialogs((dialogs) =>
      dialogs.map((dialog) => (dialog.id === id ? { ...dialog, markdown: value } : dialog)),
    );
  }

  async function handleTitleChange(id: string, value: string) {
    await updateCurrentDialogs((dialogs) =>
      dialogs.map((dialog) => (dialog.id === id ? { ...dialog, title: value } : dialog)),
    );
  }

  async function handleDeleteDialog(id: string) {
    const dialog = openFolder?.dialogs.find((item) => item.id === id);
    if (!window.confirm(`确认删除${dialog ? `「${dialog.title}」` : '该对话框'}吗？`)) {
      return;
    }
    await updateCurrentDialogs((dialogs) => dialogs.filter((item) => item.id !== id));
    setExportSelectionIds((ids) => ids.filter((selectionId) => selectionId !== id));
    if (expandedSourceDialogId === id) {
      setExpandedSourceDialogId('');
    }
    if (previewDialogId === id) {
      setPreviewDialogId('');
    }
    if (moveMenuDialogId === id) {
      setMoveMenuDialogId('');
    }
  }

  async function handleMoveDialog(dialogId: string, targetFolderId: string) {
    await updateFolders(moveDialogBetweenFolders(folders, dialogId, targetFolderId));
    setExportSelectionIds((ids) => ids.filter((selectionId) => selectionId !== dialogId));
    if (expandedSourceDialogId === dialogId) {
      setExpandedSourceDialogId('');
    }
    if (previewDialogId === dialogId) {
      setPreviewDialogId('');
    }
    setMoveMenuDialogId('');
    setMessage('已移动对话框到其它文件夹。');
  }

  function handleToggleExportSelection(id: string) {
    setExportSelectionIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }

  function handleToggleSelectAll() {
    if (!openFolder) {
      return;
    }
    if (allDialogsSelected) {
      setExportSelectionIds([]);
      return;
    }
    setExportSelectionIds(openFolder.dialogs.map((dialog) => dialog.id));
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
    const dialog = openFolder?.dialogs.find((item) => item.id === id);
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

  async function handleHealthCheck() {
    setMessage('');
    const available = await checkHealth(SERVICE_URL);
    setStatus(available ? 'available' : 'unavailable');
    setMessage(available ? '' : '转换服务不可用。');
  }

  async function handleFileUpload(files: FileList | File[]) {
    if (!openFolder) {
      setMessage('请先进入一个文件夹再上传。');
      return;
    }
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
    await updateCurrentDialogs((dialogs) => [...dialogs, ...importedDialogs]);
    setExpandedSourceDialogId(importedDialogs[0].id);
    setPreviewDialogId('');
    setMessage(`已导入 ${importedDialogs.length} 个 Markdown 文件。点击圆圈设置导出顺序。`);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    void handleFileUpload(Array.from(event.dataTransfer.files));
  }

  async function handleExport() {
    if (!openFolder) {
      setMessage('请先进入一个文件夹再导出。');
      return;
    }
    if (!mergedMarkdown) {
      setMessage('请选择一个或多个对话框后再导出。');
      return;
    }
    const filename = createExportFilename(openFolder, exportSelectionIds);
    setIsExporting(true);
    setMessage('正在导出 DOCX...');
    try {
      const blob = await convertToDocx(SERVICE_URL, {
        title: openFolder.name.trim() || 'md-to-word',
        markdown: mergedMarkdown,
        options: { filename: CONVERSION_REQUEST_FILENAME },
      });
      downloadDocx(blob, filename);
      setMessage('DOCX 已开始下载。');
    } catch (error) {
      setStatus('unavailable');
      setMessage(error instanceof Error ? error.message : '导出失败。');
    } finally {
      setIsExporting(false);
    }
  }

  function handleFeedbackSubmit() {
    if (feedbackTab === 'bug') {
      if (!feedbackMd.trim() || !feedbackDesc.trim()) return;
    } else {
      if (!feedbackFeature.trim()) return;
    }

    const payload = {
      feedback_type: feedbackTab,
      markdown_content: feedbackTab === 'bug' ? feedbackMd : '',
      description: feedbackTab === 'bug' ? feedbackDesc : feedbackFeature,
      contact: feedbackContact,
    };

    setFeedbackOpen(false);
    setFeedbackMd('');
    setFeedbackDesc('');
    setFeedbackFeature('');
    setFeedbackContact('');
    confetti({ particleCount: 80, spread: 70, origin: { y: 0.6 } });
    setTimeout(() => confetti({ particleCount: 50, spread: 100, origin: { y: 0.5 } }), 200);

    (async () => {
      const maxRetries = 3;
      for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
          await submitFeedback(SERVICE_URL, payload);
          return;
        } catch {
          if (attempt < maxRetries - 1) {
            await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
          }
        }
      }
      setMessage('反馈提交失败，请稍后重试');
    })();
  }

  function renderFeedbackModal() {
    if (!feedbackOpen) return null;

    const bugFormValid = feedbackMd.trim() && feedbackDesc.trim();
    const featureFormValid = feedbackFeature.trim();
    const canSubmit = feedbackTab === 'bug' ? bugFormValid : featureFormValid;

    return (
      <div className="onboarding-layer" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
        <div className="onboarding-backdrop" onClick={() => setFeedbackOpen(false)} />
        <section className="feedback-modal">
          <div className="feedback-tabs">
            <button
              type="button"
              className={`feedback-tab ${feedbackTab === 'bug' ? 'active' : ''}`}
              onClick={() => setFeedbackTab('bug')}
            >
              问题反馈
            </button>
            <button
              type="button"
              className={`feedback-tab ${feedbackTab === 'feature' ? 'active' : ''}`}
              onClick={() => setFeedbackTab('feature')}
            >
              功能建议
            </button>
          </div>
          {feedbackTab === 'bug' ? (
            <>
              <p className="feedback-hint">粘贴解析失败的 Markdown 内容，帮助我们改进转换质量。</p>
              <label>
                Markdown 内容 <span className="required">*</span>
                <textarea
                  value={feedbackMd}
                  onChange={(e) => setFeedbackMd(e.target.value)}
                  placeholder="粘贴解析失败的 Markdown 原文..."
                  rows={4}
                />
              </label>
              <label>
                问题描述 <span className="required">*</span>
                <input
                  type="text"
                  value={feedbackDesc}
                  onChange={(e) => setFeedbackDesc(e.target.value)}
                  placeholder="例如：表格没对齐、公式丢失..."
                />
              </label>
            </>
          ) : (
            <>
              <p className="feedback-hint">告诉我们你希望增加的功能。</p>
              <label>
                功能描述 <span className="required">*</span>
                <textarea
                  value={feedbackFeature}
                  onChange={(e) => setFeedbackFeature(e.target.value)}
                  placeholder="描述你希望增加的功能..."
                  rows={4}
                />
              </label>
            </>
          )}
          <label>
            联系方式
            <input
              type="text"
              value={feedbackContact}
              onChange={(e) => setFeedbackContact(e.target.value)}
              placeholder="邮箱或微信（选填，方便回复）"
            />
          </label>
          <div className="feedback-actions">
            <button type="button" className="secondary" onClick={() => setFeedbackOpen(false)}>
              取消
            </button>
            <button type="button" disabled={!canSubmit} onClick={handleFeedbackSubmit}>
              提交
            </button>
          </div>
        </section>
      </div>
    );
  }

  function renderOnboardingGuide() {
    if (!onboardingActive || !onboardingStep) {
      return null;
    }

    const spotlightStyle = spotlightRect
      ? ({
          top: spotlightRect.top - 8,
          left: spotlightRect.left - 8,
          width: spotlightRect.width + 16,
          height: spotlightRect.height + 16,
        } satisfies CSSProperties)
      : undefined;

    return (
      <div className="onboarding-layer" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
        <div className={`onboarding-backdrop ${spotlightStyle ? 'spotlight-mode' : ''}`} />
        {spotlightStyle ? <div className="onboarding-spotlight" style={spotlightStyle} /> : null}
        <section className={`onboarding-card ${spotlightStyle ? 'anchored' : 'intro'}`}>
          <div className="onboarding-step-count">
            {onboardingStepIndex + 1} / {ONBOARDING_STEPS.length}
          </div>
          <h2 id="onboarding-title">{onboardingStep.title}</h2>
          {onboardingStep.example && (
            <div className="onboarding-example">
              <div className="onboarding-example-input">
                <span className="onboarding-example-label">复制内容</span>
                <code>{onboardingStep.example.input}</code>
              </div>
              <div className="onboarding-example-arrow">↓</div>
              <div className="onboarding-example-output">
                <span className="onboarding-example-label">Word 效果</span>
                {onboardingStep.example.outputType === 'table' ? (
                  <table className="onboarding-mini-table">
                    <thead><tr><th>方法</th><th>准确率</th></tr></thead>
                    <tbody><tr><td>CNN</td><td>95.2%</td></tr></tbody>
                  </table>
                ) : onboardingStep.example.outputType === 'headings' ? (
                  <div className="onboarding-headings-demo">
                    <div className="demo-h1">一级标题</div>
                    <div className="demo-h2">二级标题</div>
                    <div className="demo-h3">三级标题</div>
                  </div>
                ) : (
                  <span>{onboardingStep.example.output}</span>
                )}
              </div>
            </div>
          )}
          <p>{onboardingStep.body}</p>
          <div className="onboarding-actions">
            <button type="button" className="secondary" onClick={() => void completeOnboarding()}>
              跳过
            </button>
            <div className="onboarding-nav-actions">
              <button
                type="button"
                className="secondary"
                disabled={onboardingStepIndex === 0}
                onClick={handlePreviousOnboardingStep}
              >
                上一步
              </button>
              <button type="button" onClick={handleNextOnboardingStep}>
                {isLastOnboardingStep ? '完成' : '下一步'}
              </button>
            </div>
          </div>
        </section>
      </div>
    );
  }

  function renderFolderList() {
    return (
      <section className="dialog-list" aria-label="Folder list">
        <div className="dialog-list-header">
          <span>文件夹</span>
        </div>
        <div className="action-card-grid single">
          <button
            type="button"
            className="action-card add-dialog-button"
            data-onboarding-target="add-folder"
            onClick={() => void handleAddFolder()}
            title="新建文件夹"
          >
            <span className="action-card-icon">
              <FolderPlus aria-hidden="true" size={22} />
            </span>
            <strong>新建文件夹</strong>
            <small>创建一个空文件夹</small>
          </button>
        </div>
        <div className="folder-items">
          {folders.length === 0 ? (
            <div className="empty-state">
              <Folder aria-hidden="true" size={28} />
              <p>还没有文件夹，点击上方「新建文件夹」开始。</p>
            </div>
          ) : (
            folders.map((folder) => (
              <div className="folder-row" key={folder.id}>
                {renamingFolderId === folder.id ? (
                  <input
                    className="folder-name-input"
                    aria-label="文件夹名称"
                    autoFocus
                    value={folder.name}
                    onChange={(event) => void handleRenameFolder(folder.id, event.target.value)}
                    onBlur={() => setRenamingFolderId('')}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        setRenamingFolderId('');
                      }
                    }}
                  />
                ) : (
                  <button
                    type="button"
                    className="folder-open-button"
                    data-onboarding-target={folder.id === folders[0]?.id ? 'open-folder' : undefined}
                    onClick={() => handleOpenFolder(folder.id)}
                    title="进入文件夹"
                  >
                    <Folder aria-hidden="true" size={18} />
                    <span className="folder-name">{folder.name}</span>
                    <span className="folder-count">{folder.dialogs.length}</span>
                    <ChevronRight aria-hidden="true" size={18} />
                  </button>
                )}
                <button
                  type="button"
                  className="secondary icon-button"
                  onClick={() => setRenamingFolderId(folder.id)}
                  title="重命名"
                >
                  <Pencil aria-hidden="true" size={14} />
                </button>
                <button
                  type="button"
                  className="icon-button danger"
                  onClick={() => void handleDeleteFolder(folder.id)}
                  title="删除文件夹"
                >
                  <Trash2 aria-hidden="true" size={14} />
                </button>
              </div>
            ))
          )}
        </div>
      </section>
    );
  }

  function renderFolderDetail() {
    if (!openFolder) {
      return null;
    }
    const otherFolders = folders.filter((folder) => folder.id !== openFolder.id);
    return (
      <section className="dialog-list" aria-label="Folder detail">
        <div className="folder-detail-header">
          <button type="button" className="secondary back-button" onClick={handleBackToFolders} title="返回文件夹列表">
            <ChevronLeft aria-hidden="true" size={16} />
            返回
          </button>
          <Folder aria-hidden="true" size={18} />
          <input
            className="folder-title-input"
            aria-label="文件夹名称"
            value={openFolder.name}
            onChange={(event) => void handleRenameFolder(openFolder.id, event.target.value)}
            placeholder="文件夹名称"
          />
        </div>
        <div className="action-card-grid folder-action-grid">
          <button
            type="button"
            className="action-card export-card compact"
            data-onboarding-target="export-docx"
            disabled={!canExport}
            onClick={() => void handleExport()}
            title="导出 DOCX"
          >
            <span className="action-card-icon">
              {isExporting ? (
                <RotateCw aria-hidden="true" size={22} className="spin" />
              ) : (
                <Download aria-hidden="true" size={22} />
              )}
            </span>
            <strong>导出 Word</strong>
            <small>按圆圈编号合并本文件夹</small>
          </button>
          <button
            type="button"
            className="action-card add-dialog-button compact"
            data-onboarding-target="add-dialog"
            onClick={() => void handleAddDialog()}
            title="新增对话框"
          >
            <span className="action-card-icon">
              <Plus aria-hidden="true" size={22} />
            </span>
            <strong>新建对话框</strong>
            <small>创建空白 Markdown 输入框</small>
          </button>
          <label
            className="action-card upload-dropzone compact"
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleDrop}
          >
            <span className="action-card-icon">
              <CloudUpload aria-hidden="true" size={24} />
            </span>
            <strong>上传 .md</strong>
            <small>导入到当前文件夹</small>
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
        {openFolder.dialogs.length > 1 || onboardingActive ? (
          <div className="dialog-items-toolbar">
            <button
              type="button"
              className="secondary select-all-button"
              data-onboarding-target="select-all"
              disabled={openFolder.dialogs.length <= 1}
              onClick={handleToggleSelectAll}
              title={openFolder.dialogs.length <= 1 ? '有多个对话框时可全选' : allDialogsSelected ? '取消全选' : '全选'}
            >
              <ListChecks aria-hidden="true" size={15} />
              {allDialogsSelected ? '取消全选' : '全选'}
            </button>
            {exportSelectionIds.length > 0 ? (
              <span className="selection-count">已选 {exportSelectionIds.length} 项</span>
            ) : null}
          </div>
        ) : null}
        <div className="dialog-items">
          {openFolder.dialogs.length === 0 ? (
            <div className="empty-state">
              <FileText aria-hidden="true" size={28} />
              <p>这个文件夹还没有对话框，点击「新建对话框」开始。</p>
            </div>
          ) : (
            openFolder.dialogs.map((dialog) => {
              const exportOrder = exportSelectionIds.indexOf(dialog.id) + 1;
              const isSourceOpen = expandedSourceDialogId === dialog.id;
              const isPreviewOpen = previewDialogId === dialog.id;
              const isMoveOpen = moveMenuDialogId === dialog.id;
              return (
                <article
                  className={`dialog-item ${isSourceOpen || isPreviewOpen ? 'expanded' : ''} ${exportOrder ? 'selected' : ''}`}
                  key={dialog.id}
                  onDoubleClick={() => handleDialogDoubleClick(dialog.id)}
                >
                  <div className="dialog-row">
                    <button
                      type="button"
                      className={`export-order-button ${exportOrder ? 'selected' : ''}`}
                      data-onboarding-target={dialog.id === openFolder.dialogs[0]?.id ? 'export-order' : undefined}
                      onClick={() => handleToggleExportSelection(dialog.id)}
                      onDoubleClick={(event) => event.stopPropagation()}
                      title={exportOrder ? `导出顺序 ${exportOrder}` : '加入导出'}
                    >
                      {exportOrder || ''}
                    </button>
                    <input
                      className="dialog-title-input"
                      data-onboarding-target={dialog.id === openFolder.dialogs[0]?.id ? 'dialog-title' : undefined}
                      aria-label="对话框标题"
                      value={dialog.title}
                      onChange={(event) => void handleTitleChange(dialog.id, event.target.value)}
                      onDoubleClick={(event) => event.stopPropagation()}
                      placeholder="对话框标题"
                    />
                    <div
                      className="dialog-controls"
                      data-onboarding-target={dialog.id === openFolder.dialogs[0]?.id ? 'dialog-management' : undefined}
                    >
                      <button
                        type="button"
                        className={`secondary preview-toggle icon-button ${isSourceOpen ? 'active' : ''}`}
                        onClick={() => handleMdPreview(dialog.id)}
                        onDoubleClick={(event) => event.stopPropagation()}
                        title="MD 格式预览"
                      >
                        <Code2 aria-hidden="true" size={16} />
                      </button>
                      <button
                        type="button"
                        className={`secondary preview-toggle icon-button ${isPreviewOpen ? 'active' : ''}`}
                        data-onboarding-target={dialog.id === openFolder.dialogs[0]?.id ? 'word-preview' : undefined}
                        disabled={!dialog.markdown.trim()}
                        onClick={() => handleWordPreview(dialog.id)}
                        onDoubleClick={(event) => event.stopPropagation()}
                        title="Word 格式预览"
                      >
                        <Eye aria-hidden="true" size={16} />
                      </button>
                      <div className="move-menu-wrap">
                        <button
                          type="button"
                          className="secondary icon-button"
                          disabled={otherFolders.length === 0}
                          onClick={() => setMoveMenuDialogId(isMoveOpen ? '' : dialog.id)}
                          onDoubleClick={(event) => event.stopPropagation()}
                          title={otherFolders.length === 0 ? '没有其它文件夹' : '移动到…'}
                        >
                          <Move aria-hidden="true" size={14} />
                        </button>
                        {isMoveOpen ? (
                          <ul className="move-menu">
                            {otherFolders.map((folder) => (
                              <li key={folder.id}>
                                <button type="button" onClick={() => void handleMoveDialog(dialog.id, folder.id)}>
                                  <Folder aria-hidden="true" size={14} />
                                  {folder.name}
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
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
                      data-onboarding-target={dialog.id === openFolder.dialogs[0]?.id ? 'dialog-source' : undefined}
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
            })
          )}
        </div>
      </section>
    );
  }

  return (
    <main className="app">
      <header className="toolbar">
        <div className="brand">
          <img className="brand-icon" src="icon-48.png" alt="" />
          <div>
            <div className="brand-title-row">
              <h1>MD To Word</h1>
              <button
                type="button"
                className={`header-status ${status}`}
                onClick={() => void handleHealthCheck()}
                title="点击重新检查服务"
              >
                <span />
                {statusLabel}
              </button>
              <button type="button" className="guide-button" onClick={startOnboarding}>
                使用指南
              </button>
              <button type="button" className="guide-button feedback-button" onClick={() => setFeedbackOpen(true)}>
                <MessageSquare size={12} />
                问题反馈
              </button>
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
      <section className="workspace">{openFolder ? renderFolderDetail() : renderFolderList()}</section>
      {renderOnboardingGuide()}
      {renderFeedbackModal()}
    </main>
  );
}
