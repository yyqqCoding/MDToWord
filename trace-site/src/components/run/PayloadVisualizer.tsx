"use client";

import { useState } from "react";
import { Check, Copy, FileCode, Layers, ShieldCheck, TestTube } from "lucide-react";
import clsx from "clsx";

/**
 * 复制按钮辅助组件
 */
function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const onCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <button
      type="button"
      onClick={onCopy}
      title={label ?? "复制"}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-ink-faint transition-colors hover:bg-raised hover:text-ink cursor-pointer"
    >
      {copied ? (
        <>
          <Check className="size-3 text-good" />
          <span className="text-[11px] text-good">已复制</span>
        </>
      ) : (
        <>
          <Copy className="size-3" />
          {label && <span className="text-[11px]">{label}</span>}
        </>
      )}
    </button>
  );
}

/**
 * Oracle 判定规则专用卡片
 */
function OracleCard({ oracle }: { oracle: Record<string, unknown> }) {
  return (
    <div className="mt-1.5 rounded-lg border border-accent/30 bg-accent/5 p-2.5">
      <div className="flex items-center gap-2 text-xs font-medium text-accent">
        <ShieldCheck className="size-4" />
        <span>Oracle 判定规则 ({String(oracle.kind ?? "断言")})</span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
        {Object.entries(oracle).map(([k, v]) => (
          <div key={k} className="flex flex-col">
            <span className="text-[11px] text-ink-faint">{k}</span>
            <span className="font-mono text-xs text-ink">
              {typeof v === "object" ? JSON.stringify(v) : String(v)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 文件列表专用展示
 */
function FileListPill({ files }: { files: string[] }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {files.map((file) => (
        <span
          key={file}
          className="group inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2 py-1 font-mono text-xs text-ink-muted"
        >
          <FileCode className="size-3 text-accent" />
          <span className="truncate max-w-[20rem]">{file}</span>
          <CopyButton text={file} />
        </span>
      ))}
    </div>
  );
}

/**
 * 结构化 Payload 可视化组件 (Semantic Payload Visualizer)。
 *
 * 识别 observation 的输入输出，并按语义排版：
 * - Oracle 规则以保护盾卡片呈现；
 * - 文件路径以文件标签药丸呈现并支持一键复制；
 * - 测试 Selector 等长哈希自带快速复制；
 * - 其余常规参数提供整齐的键值网格与语法着色。
 */
export function PayloadVisualizer({
  value,
  title,
}: {
  value: Record<string, unknown>;
  title?: string;
}) {
  const entries = Object.entries(value);
  if (entries.length === 0) return null;

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-line bg-canvas/90 p-3">
      {title && (
        <div className="flex items-center justify-between border-b border-line/60 pb-1.5 text-xs font-medium text-ink-muted">
          <span className="flex items-center gap-1.5">
            <Layers className="size-3.5 text-accent" />
            {title}
          </span>
          <CopyButton text={JSON.stringify(value, null, 2)} label="复制 JSON" />
        </div>
      )}

      <div className="space-y-2">
        {entries.map(([key, item]) => {
          // 1. Oracle 规则特殊渲染
          if (key === "oracle" && typeof item === "object" && item !== null) {
            return (
              <OracleCard
                key={key}
                oracle={item as Record<string, unknown>}
              />
            );
          }

          // 2. 文件列表特殊渲染
          if (
            (key === "files_to_read" ||
              key === "changed_files" ||
              key === "target_files") &&
            Array.isArray(item)
          ) {
            return (
              <div key={key}>
                <span className="font-mono text-xs text-ink-faint">{key}</span>
                <FileListPill files={item.map(String)} />
              </div>
            );
          }

          // 3. 测试 Selector 特殊渲染
          if (key === "target_test_selector" && typeof item === "string") {
            return (
              <div
                key={key}
                className="flex items-center justify-between rounded bg-surface px-2.5 py-1.5 border border-line/80"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <TestTube className="size-3.5 text-accent shrink-0" />
                  <span className="font-mono text-xs text-ink truncate">
                    {item}
                  </span>
                </div>
                <CopyButton text={item} />
              </div>
            );
          }

          // 4. 布尔值与常规标量
          const isBool = typeof item === "boolean";
          const isObject = typeof item === "object" && item !== null;
          const displayStr = isObject ? JSON.stringify(item) : String(item);

          return (
            <div
              key={key}
              className="group flex flex-wrap items-baseline justify-between gap-2 py-0.5 text-xs"
            >
              <dt className="shrink-0 font-mono text-ink-faint">{key}</dt>
              <dd className="flex items-center gap-1.5 min-w-0 max-w-full">
                {isBool ? (
                  <span
                    className={clsx(
                      "rounded px-1.5 py-0.2 font-mono text-[11px] font-medium",
                      item
                        ? "bg-good/15 text-good"
                        : "bg-ink-faint/15 text-ink-muted",
                    )}
                  >
                    {item ? "true" : "false"}
                  </span>
                ) : (
                  <span
                    className={clsx(
                      "break-all font-mono leading-relaxed",
                      isObject ? "text-ink-muted" : "text-ink",
                    )}
                  >
                    {displayStr}
                  </span>
                )}
                {displayStr.length > 8 && !isObject && (
                  <span className="opacity-0 group-hover:opacity-100 transition-opacity">
                    <CopyButton text={displayStr} />
                  </span>
                )}
              </dd>
            </div>
          );
        })}
      </div>
    </div>
  );
}
