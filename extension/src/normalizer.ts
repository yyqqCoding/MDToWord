const INLINE_PARENS_PATTERN = /\\\(([\s\S]+?)\\\)/g;
const BLOCK_BRACKETS_PATTERN = /(?:[ \t]*\n)?[ \t]*\\\[([\s\S]+?)\\\][ \t]*(?:\n[ \t]*)?/g;
const BARE_BLOCK_BRACKETS_PATTERN = /(?:[ \t]*\n)?[ \t]*\[\s*\n([\s\S]+?)\n[ \t]*\][ \t]*(?:\n[ \t]*)?/g;
const DOLLAR_MATH_PATTERN = /(\$\$[\s\S]*?\$\$|\$[^$\n]+\$)/g;

export function normalizeMarkdown(value: string): string {
  let normalized = value
    .replace(BLOCK_BRACKETS_PATTERN, (_match, formula: string) => replaceBlockFormula(formula))
    .replace(BARE_BLOCK_BRACKETS_PATTERN, (_match, formula: string) => replaceBlockFormula(formula));

  normalized = normalizeFormulaSpacing(normalized);
  normalized = normalized.replace(INLINE_PARENS_PATTERN, (_match, formula: string) => `$${formula.trim()}$`);
  return normalizeAiParenthesizedMath(normalized);
}

function replaceBlockFormula(formula: string): string {
  return `\n\n$$\n${formula.trim()}\n$$\n\n`;
}

function normalizeFormulaSpacing(value: string): string {
  return value.replace(/\n{3,}(\$\$)/g, '\n\n$1').replace(/(\$\$)\n{3,}/g, '$1\n\n');
}

function normalizeAiParenthesizedMath(value: string): string {
  return value
    .split(DOLLAR_MATH_PATTERN)
    .map((part) => (isDollarMath(part) ? repairMath(part) : replaceMathParentheses(part)))
    .join('');
}

function isDollarMath(value: string): boolean {
  return (
    (value.startsWith('$$') && value.endsWith('$$')) ||
    (value.startsWith('$') && value.endsWith('$') && value.length > 1)
  );
}

function replaceMathParentheses(value: string): string {
  let result = '';
  let index = 0;

  while (index < value.length) {
    if (value[index] !== '(' || (index > 0 && value[index - 1] === '\\')) {
      result += value[index];
      index += 1;
      continue;
    }

    const closeIndex = findMatchingParenthesis(value, index);
    if (closeIndex === null) {
      result += value[index];
      index += 1;
      continue;
    }

    const content = value.slice(index + 1, closeIndex).trim();
    result += looksLikeMathFragment(content) ? `$${repairMathContent(content)}$` : value.slice(index, closeIndex + 1);
    index = closeIndex + 1;
  }

  return result;
}

function findMatchingParenthesis(value: string, openIndex: number): number | null {
  let depth = 0;
  for (let index = openIndex; index < value.length; index += 1) {
    const char = value[index];
    if (char === '\n') {
      return null;
    }
    if (char === '(') {
      depth += 1;
    } else if (char === ')') {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }
  return null;
}

function looksLikeMathFragment(content: string): boolean {
  if (!content || /[\u4e00-\u9fff]/.test(content)) {
    return false;
  }
  return /[\\_^=<>|{}]/.test(content) || /^[A-Za-z]$/.test(content);
}

function repairMath(value: string): string {
  if (value.startsWith('$$') && value.endsWith('$$')) {
    return `$$${repairMathContent(value.slice(2, -2))}$$`;
  }
  if (value.startsWith('$') && value.endsWith('$')) {
    return `$${repairMathContent(value.slice(1, -1))}$`;
  }
  return value;
}

function repairMathContent(content: string): string {
  const repaired = repairEnvironmentLineBreaks(
    content.replace(/\*\{([^}\n]+)\}/g, '_{$1}').replace(/(?<=[}|])\*([A-Za-z0-9])/g, '_$1'),
  );
  return escapeVisibleSetBraces(repaired);
}

function repairEnvironmentLineBreaks(content: string): string {
  const environments = ['cases', 'aligned', 'array', 'matrix', 'pmatrix', 'bmatrix', 'vmatrix'];
  return environments.reduce((result, environment) => repairEnvironment(result, environment), content);
}

function repairEnvironment(content: string, environment: string): string {
  const begin = `\\begin{${environment}}`;
  const end = `\\end{${environment}}`;
  let result = '';
  let index = 0;

  while (index < content.length) {
    const beginIndex = content.indexOf(begin, index);
    if (beginIndex === -1) {
      result += content.slice(index);
      break;
    }

    const endIndex = content.indexOf(end, beginIndex + begin.length);
    if (endIndex === -1) {
      result += content.slice(index);
      break;
    }

    const closeIndex = endIndex + end.length;
    result += content.slice(index, beginIndex);
    result += content.slice(beginIndex, closeIndex).replace(/(?<!\\)\\(?=\n)/g, '\\\\');
    index = closeIndex;
  }

  return result;
}

function escapeVisibleSetBraces(content: string): string {
  let result = '';
  let index = 0;

  while (index < content.length) {
    if (content[index] !== '{' || isTexGroupBrace(content, index)) {
      result += content[index];
      index += 1;
      continue;
    }

    const closeIndex = findMatchingBrace(content, index);
    if (closeIndex === null) {
      result += content[index];
      index += 1;
      continue;
    }

    const inner = content.slice(index + 1, closeIndex);
    result += inner.includes(',') ? `\\{${inner}\\}` : content.slice(index, closeIndex + 1);
    index = closeIndex + 1;
  }

  return result;
}

function isTexGroupBrace(content: string, index: number): boolean {
  if (index > 0 && ['_', '^', '\\'].includes(content[index - 1])) {
    return true;
  }
  return /\\[A-Za-z]+$/.test(content.slice(0, index));
}

function findMatchingBrace(content: string, openIndex: number): number | null {
  let depth = 0;
  for (let index = openIndex; index < content.length; index += 1) {
    const char = content[index];
    if (char === '{') {
      depth += 1;
    } else if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }
  return null;
}
