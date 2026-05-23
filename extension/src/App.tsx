import { Activity, Download, FileText, RotateCw, Server } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { checkHealth, convertToDocx, downloadDocx } from './api';
import { MarkdownPreview } from './preview';
import { loadDraft, loadServiceUrl, saveDraft, saveServiceUrl } from './storage';
import type { ServiceStatus } from './types';

const DEFAULT_FILENAME = 'md-to-word.docx';
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
  const [markdown, setMarkdown] = useState('');
  const [serviceUrl, setServiceUrl] = useState('http://127.0.0.1:8000');
  const [status, setStatus] = useState<ServiceStatus>('unknown');
  const [message, setMessage] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const canExport = useMemo(() => markdown.trim().length > 0 && !isExporting, [isExporting, markdown]);

  useEffect(() => {
    void Promise.all([loadDraft(), loadServiceUrl()]).then(([savedDraft, savedServiceUrl]) => {
      setMarkdown(savedDraft || STARTER_MARKDOWN);
      setServiceUrl(savedServiceUrl);
    });
  }, []);

  async function handleMarkdownChange(value: string) {
    setMarkdown(value);
    await saveDraft(value);
  }

  async function handleServiceUrlChange(value: string) {
    setServiceUrl(value);
    await saveServiceUrl(value);
  }

  async function handleHealthCheck() {
    setMessage('');
    const available = await checkHealth(serviceUrl);
    setStatus(available ? 'available' : 'unavailable');
    setMessage(available ? 'Conversion service is available.' : 'Conversion service is unavailable.');
  }

  async function handleExport() {
    if (!markdown.trim()) {
      setMessage('Paste Markdown before exporting.');
      return;
    }

    setIsExporting(true);
    setMessage('Exporting DOCX...');
    try {
      const blob = await convertToDocx(serviceUrl, {
        title: 'md-to-word',
        markdown,
        options: {
          filename: DEFAULT_FILENAME,
        },
      });
      downloadDocx(blob, DEFAULT_FILENAME);
      setMessage('DOCX download started.');
    } catch (error) {
      setStatus('unavailable');
      setMessage(error instanceof Error ? error.message : 'Export failed.');
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <main className="app">
      <header className="toolbar">
        <div className="brand">
          <FileText aria-hidden="true" size={22} />
          <div>
            <h1>MD To Word</h1>
            <p>Text, formulas, and tables to DOCX</p>
          </div>
        </div>
        <div className="actions">
          <label className="service-url">
            <Server aria-hidden="true" size={16} />
            <input
              aria-label="Conversion service URL"
              value={serviceUrl}
              onChange={(event) => void handleServiceUrlChange(event.target.value)}
            />
          </label>
          <button type="button" className="secondary" onClick={() => void handleHealthCheck()} title="Check service">
            <Activity aria-hidden="true" size={16} />
            Check
          </button>
          <button type="button" disabled={!canExport} onClick={() => void handleExport()} title="Export DOCX">
            {isExporting ? <RotateCw aria-hidden="true" size={16} className="spin" /> : <Download aria-hidden="true" size={16} />}
            Export
          </button>
        </div>
      </header>
      <section className="status-row" aria-live="polite">
        <span className={`status-dot ${status}`} />
        <span>{message || 'Paste Markdown content and export through the configured conversion service.'}</span>
      </section>
      <section className="workspace">
        <textarea
          aria-label="Markdown input"
          placeholder="Paste Markdown here"
          value={markdown}
          onChange={(event) => void handleMarkdownChange(event.target.value)}
        />
        <MarkdownPreview value={markdown} />
      </section>
    </main>
  );
}
