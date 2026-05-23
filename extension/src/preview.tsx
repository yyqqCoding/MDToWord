import MarkdownIt from 'markdown-it';
import katex from 'katex';
import 'katex/dist/katex.min.css';

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

export function MarkdownPreview({ value }: { value: string }) {
  const html = renderMarkdownWithMath(value);
  return <article className="preview" aria-label="Preview" dangerouslySetInnerHTML={{ __html: html }} />;
}

function renderMarkdownWithMath(value: string): string {
  const protectedMath: string[] = [];
  const protectedValue = value
    .replace(/\\\[([\s\S]+?)\\\]/g, (_match, formula: string) => {
      const index = protectedMath.push(renderFormula(formula, true)) - 1;
      return `@@MATH_${index}@@`;
    })
    .replace(/\\\(([\s\S]+?)\\\)/g, (_match, formula: string) => {
      const index = protectedMath.push(renderFormula(formula, false)) - 1;
      return `@@MATH_${index}@@`;
    })
    .replace(/\$\$([\s\S]+?)\$\$/g, (_match, formula: string) => {
      const index = protectedMath.push(renderFormula(formula, true)) - 1;
      return `@@MATH_${index}@@`;
    })
    .replace(/\$([^$\n]+?)\$/g, (_match, formula: string) => {
      const index = protectedMath.push(renderFormula(formula, false)) - 1;
      return `@@MATH_${index}@@`;
    });

  let html = markdown.render(protectedValue);
  protectedMath.forEach((formulaHtml, index) => {
    html = html.replace(`@@MATH_${index}@@`, formulaHtml);
  });
  return html;
}

function renderFormula(formula: string, displayMode: boolean): string {
  try {
    return katex.renderToString(formula.trim(), {
      displayMode,
      throwOnError: false,
    });
  } catch {
    return `<code>${escapeHtml(formula)}</code>`;
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
