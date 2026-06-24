const INLINE_PARENS_PATTERN = /\\\(([\s\S]+?)\\\)/g;
const BLOCK_BRACKETS_PATTERN = /(?:[ \t]*\n)?[ \t]*\\\[([\s\S]+?)\\\][ \t]*(?:\n[ \t]*)?/g;
const BARE_BLOCK_BRACKETS_PATTERN = /(?:[ \t]*\n)?[ \t]*\[\s*\n([\s\S]+?)\n[ \t]*\][ \t]*(?:\n[ \t]*)?/g;
const DOLLAR_MATH_PATTERN = /(\$\$[\s\S]*?\$\$|\$[^$\n]+\$)/g;
const FENCE_PATTERN = /^[ \t]{0,3}(```|~~~)/;
const ATX_NO_SPACE_PATTERN = /^(#{1,6})([A-Za-z一-鿿])/;
const TABLE_DELIMITER_PATTERN = /^[ \t]*\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)+\|?[ \t]*$/;
const FULLWIDTH_PIPE = '｜';
const TABLE_DASH_PATTERN = /[-—–−－]/g;
const GROUP_ARGUMENT_COMMANDS = new Set([
  'begin',
  'end',
  'frac',
  'sqrt',
  'left',
  'right',
  'mathbb',
  'mathcal',
  'mathbf',
  'mathrm',
  'mathit',
  'mathsf',
  'mathtt',
  'operatorname',
  'text',
  'overline',
  'underline',
  'hat',
  'tilde',
  'bar',
  'vec',
  'underbrace',
  'overbrace',
  'underset',
  'overset',
]);

export function normalizeMarkdown(value: string): string {
  let normalized = normalizeTables(normalizeHeadings(value))
    .replace(BLOCK_BRACKETS_PATTERN, (_match, formula: string) => replaceBlockFormula(formula))
    .replace(BARE_BLOCK_BRACKETS_PATTERN, (_match, formula: string) => replaceBlockFormula(formula));

  normalized = normalizeFormulaSpacing(normalized);
  normalized = normalized.replace(INLINE_PARENS_PATTERN, (_match, formula: string) => `$${formula.trim()}$`);
  return normalizeAiParenthesizedMath(normalized);
}

function normalizeHeadings(value: string): string {
  let inFence = false;
  return value
    .split('\n')
    .map((line) => {
      if (FENCE_PATTERN.test(line)) {
        inFence = !inFence;
        return line;
      }
      return inFence ? line : line.replace(ATX_NO_SPACE_PATTERN, '$1 $2');
    })
    .join('\n');
}

function normalizeTables(value: string): string {
  const lines = value.split('\n');
  const result: string[] = [];
  let inFence = false;
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (FENCE_PATTERN.test(line)) {
      inFence = !inFence;
      result.push(line);
      index += 1;
      continue;
    }

    const isTableStart =
      !inFence && isTableRow(line) && index + 1 < lines.length && isTableDelimiter(lines[index + 1]);
    if (!isTableStart) {
      result.push(line);
      index += 1;
      continue;
    }

    if (result.length > 0 && result[result.length - 1].trim() !== '') {
      result.push('');
    }

    result.push(repairTablePipes(line));
    result.push(repairDelimiterRow(lines[index + 1]));
    index += 2;
    while (index < lines.length && isTableRow(lines[index])) {
      result.push(repairTablePipes(lines[index]));
      index += 1;
    }

    if (index < lines.length && lines[index].trim() !== '') {
      result.push('');
    }
  }

  return result.join('\n');
}

function isTableRow(line: string): boolean {
  return line.trim() !== '' && (line.includes('|') || line.includes(FULLWIDTH_PIPE));
}

function isTableDelimiter(line: string): boolean {
  return TABLE_DELIMITER_PATTERN.test(repairDelimiterRow(line));
}

function repairTablePipes(line: string): string {
  return line.split(FULLWIDTH_PIPE).join('|');
}

function repairDelimiterRow(line: string): string {
  return repairTablePipes(line).replace(TABLE_DASH_PATTERN, '-');
}

function replaceBlockFormula(formula: string): string {
  return `\n\n$$\n${normalizeBlockFormulaContent(formula)}\n$$\n\n`;
}

function normalizeBlockFormulaContent(value: string): string {
  return value
    .trim()
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.trim())
    .join('\n');
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
  let repaired = content.replace(/\*\{([^}\n]+)\}/g, '_{$1}').replace(/(?<=[}|])\*([A-Za-z0-9])/g, '_$1');
  repaired = escapeLiteralPercentSigns(repaired);
  repaired = repairEnvironmentLineBreaks(repaired);
  repaired = expandTallParentheses(repaired);
  repaired = wrapUnderbraceParenthesesForWord(repaired);
  repaired = expandTallSquareBrackets(repaired);
  repaired = expandTallNormDelimiters(repaired);
  return escapeVisibleSetBraces(repaired);
}

function escapeLiteralPercentSigns(content: string): string {
  return content.replace(/(^|[^\\])%/g, '$1\\%');
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

function expandTallParentheses(content: string): string {
  let result = '';
  let index = 0;

  while (index < content.length) {
    if (content[index] !== '(' || isAlreadySizedDelimiter(content, index)) {
      result += content[index];
      index += 1;
      continue;
    }

    const closeIndex = findMatchingParenthesis(content, index);
    if (closeIndex === null) {
      result += content[index];
      index += 1;
      continue;
    }

    const inner = content.slice(index + 1, closeIndex);
    result += hasTallMath(inner) ? `\\left(${inner}\\right)` : content.slice(index, closeIndex + 1);
    index = closeIndex + 1;
  }

  return result;
}

function hasTallMath(content: string): boolean {
  return /\\(?:underbrace|overbrace|frac|sqrt|sum|prod|int|begin)\b/.test(content);
}

function isAlreadySizedDelimiter(content: string, index: number): boolean {
  if (index > 0 && content[index - 1] === '\\') {
    return true;
  }

  return /\\(?:left|right|big|Big|bigg|Bigg)$/.test(content.slice(0, index));
}

function wrapUnderbraceParenthesesForWord(content: string): string {
  let result = '';
  let index = 0;

  while (index < content.length) {
    if (!content.startsWith('\\left(', index)) {
      result += content[index];
      index += 1;
      continue;
    }

    const match = findMatchingRightDelimiter(content, index);
    if (match === null) {
      result += content[index];
      index += 1;
      continue;
    }

    const [rightIndex, delimiterEnd] = match;
    const inner = content.slice(index + '\\left('.length, rightIndex);
    result += needsWordTallHeightWrap(inner)
      ? `\\left(\\begin{matrix}${inner}\\end{matrix}\\right)`
      : content.slice(index, delimiterEnd);
    index = delimiterEnd;
  }

  return result;
}

function findMatchingRightDelimiter(content: string, leftIndex: number): [number, number] | null {
  let depth = 1;
  let index = leftIndex + '\\left('.length;

  while (index < content.length) {
    if (content.startsWith('\\left', index)) {
      depth += 1;
      index += '\\left'.length;
      continue;
    }

    if (content.startsWith('\\right', index)) {
      const delimiterIndex = index + '\\right'.length;
      if (delimiterIndex >= content.length) {
        return null;
      }

      depth -= 1;
      const delimiterEnd = delimiterIndex + 1;
      if (depth === 0) {
        return [index, delimiterEnd];
      }
      index = delimiterEnd;
      continue;
    }

    index += 1;
  }

  return null;
}

function needsWordTallHeightWrap(inner: string): boolean {
  if (/^\s*\\begin\{(?:matrix|array)\}/.test(inner)) {
    return false;
  }

  if (inner.includes('\\underbrace') && /\\underbrace\b[\s\S]*?_\{/.test(inner)) {
    return true;
  }

  return /\\frac\b/.test(inner);
}

function expandTallSquareBrackets(content: string): string {
  let result = '';
  let index = 0;

  while (index < content.length) {
    const sizeCommand = sizedSquareOpenCommand(content, index);
    if (sizeCommand) {
      const openIndex = index + sizeCommand.length - 1;
      const closeIndex = findMatchingSquareBracket(content, openIndex);
      if (closeIndex === null) {
        result += content[index];
        index += 1;
        continue;
      }

      const closeStart = sizedSquareCloseStart(content, closeIndex);
      const inner = content.slice(openIndex + 1, closeStart);
      result += hasTallMath(inner) ? `\\left[${inner}\\right]` : content.slice(index, closeIndex + 1);
      index = closeIndex + 1;
      continue;
    }

    if (content[index] !== '[' || isAlreadySizedDelimiter(content, index)) {
      result += content[index];
      index += 1;
      continue;
    }

    const closeIndex = findMatchingSquareBracket(content, index);
    if (closeIndex === null) {
      result += content[index];
      index += 1;
      continue;
    }

    const inner = content.slice(index + 1, closeIndex);
    result += hasTallMath(inner) ? `\\left[${inner}\\right]` : content.slice(index, closeIndex + 1);
    index = closeIndex + 1;
  }

  return result;
}

function sizedSquareOpenCommand(content: string, index: number): string | null {
  for (const command of ['\\big[', '\\Big[', '\\bigg[', '\\Bigg[']) {
    if (content.startsWith(command, index)) {
      return command;
    }
  }
  return null;
}

function sizedSquareCloseStart(content: string, closeIndex: number): number {
  const prefix = content.slice(0, closeIndex);
  for (const command of ['\\big', '\\Big', '\\bigg', '\\Bigg']) {
    if (prefix.endsWith(command)) {
      return closeIndex - command.length;
    }
  }
  return closeIndex;
}

function findMatchingSquareBracket(content: string, openIndex: number): number | null {
  let depth = 0;
  for (let index = openIndex; index < content.length; index += 1) {
    const char = content[index];
    if (char === '[' && !isEscapedDelimiter(content, index)) {
      depth += 1;
    } else if (char === ']' && !isEscapedDelimiter(content, index)) {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }
  return null;
}

function expandTallNormDelimiters(content: string): string {
  let result = '';
  let index = 0;

  while (index < content.length) {
    if (!content.startsWith('\\|', index) || isAlreadySizedNorm(content, index)) {
      result += content[index];
      index += 1;
      continue;
    }

    const closeIndex = findNextUnsizedNormDelimiter(content, index + 2);
    if (closeIndex === null) {
      result += content[index];
      index += 1;
      continue;
    }

    const inner = content.slice(index + 2, closeIndex);
    result += hasTallMath(inner) ? `\\left\\|${inner}\\right\\|` : content.slice(index, closeIndex + 2);
    index = closeIndex + 2;
  }

  return result;
}

function findNextUnsizedNormDelimiter(content: string, startIndex: number): number | null {
  let index = startIndex;
  while (index < content.length) {
    if (content.startsWith('\\|', index) && !isAlreadySizedNorm(content, index)) {
      return index;
    }
    index += 1;
  }
  return null;
}

function isAlreadySizedNorm(content: string, index: number): boolean {
  return /\\(?:left|right)$/.test(content.slice(0, index));
}

function isEscapedDelimiter(content: string, index: number): boolean {
  return index > 0 && content[index - 1] === '\\';
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

  const command = content.slice(0, index).match(/\\[A-Za-z]+$/)?.[0].slice(1);
  if (command && GROUP_ARGUMENT_COMMANDS.has(command)) {
    return true;
  }

  return continuesGroupArgumentCommand(content, index);
}

function continuesGroupArgumentCommand(content: string, index: number): boolean {
  let previousIndex = index - 1;
  while (previousIndex >= 0 && /\s/.test(content[previousIndex])) {
    previousIndex -= 1;
  }

  if (previousIndex < 0 || content[previousIndex] !== '}') {
    return false;
  }

  const previousOpen = findMatchingOpenBrace(content, previousIndex);
  if (previousOpen === null) {
    return false;
  }

  const command = content.slice(0, previousOpen).match(/\\[A-Za-z]+$/)?.[0].slice(1);
  return Boolean(command && GROUP_ARGUMENT_COMMANDS.has(command));
}

function findMatchingOpenBrace(content: string, closeIndex: number): number | null {
  let depth = 0;
  for (let index = closeIndex; index >= 0; index -= 1) {
    const char = content[index];
    if (char === '}') {
      depth += 1;
    } else if (char === '{') {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }
  return null;
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
