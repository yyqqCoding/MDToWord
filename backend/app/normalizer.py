import re


INLINE_PARENS_PATTERN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
BLOCK_BRACKETS_PATTERN = re.compile(r"(?:[ \t]*\n)?[ \t]*\\\[(.+?)\\\][ \t]*(?:\n[ \t]*)?", re.DOTALL)
BARE_BLOCK_BRACKETS_PATTERN = re.compile(r"(?:[ \t]*\n)?[ \t]*\[\s*\n(.+?)\n[ \t]*\][ \t]*(?:\n[ \t]*)?", re.DOTALL)
DOLLAR_MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\$[^$\n]+\$)", re.DOTALL)
GROUP_ARGUMENT_COMMANDS = {
    "begin",
    "end",
    "frac",
    "sqrt",
    "left",
    "right",
    "mathbb",
    "mathcal",
    "mathbf",
    "mathrm",
    "mathit",
    "mathsf",
    "mathtt",
    "operatorname",
    "text",
    "overline",
    "underline",
    "hat",
    "tilde",
    "bar",
    "vec",
    "underbrace",
    "overbrace",
    "underset",
    "overset",
}


def normalize_markdown(markdown: str) -> str:
    """Normalize supported formula delimiters into Pandoc-friendly Markdown."""
    normalized = BLOCK_BRACKETS_PATTERN.sub(_replace_block_formula, markdown)
    normalized = BARE_BLOCK_BRACKETS_PATTERN.sub(_replace_block_formula, normalized)
    normalized = _normalize_formula_spacing(normalized)
    normalized = INLINE_PARENS_PATTERN.sub(lambda match: f"${match.group(1).strip()}$", normalized)
    return _normalize_ai_parenthesized_math(normalized)


def _replace_block_formula(match: re.Match[str]) -> str:
    formula = match.group(1).strip()
    return f"\n\n$$\n{formula}\n$$\n\n"


def _normalize_formula_spacing(markdown: str) -> str:
    normalized = re.sub(r"\n{3,}(\$\$)", r"\n\n\1", markdown)
    return re.sub(r"(\$\$)\n{3,}", r"\1\n\n", normalized)


def _normalize_ai_parenthesized_math(markdown: str) -> str:
    parts = DOLLAR_MATH_PATTERN.split(markdown)
    return "".join(_repair_math(part) if _is_dollar_math(part) else _replace_math_parentheses(part) for part in parts)


def _is_dollar_math(value: str) -> bool:
    return (value.startswith("$$") and value.endswith("$$")) or (
        value.startswith("$") and value.endswith("$") and len(value) > 1
    )


def _replace_math_parentheses(text: str) -> str:
    result: list[str] = []
    index = 0

    while index < len(text):
        if text[index] != "(" or (index > 0 and text[index - 1] == "\\"):
            result.append(text[index])
            index += 1
            continue

        close_index = _find_matching_parenthesis(text, index)
        if close_index is None:
            result.append(text[index])
            index += 1
            continue

        content = text[index + 1 : close_index].strip()
        if _looks_like_math_fragment(content):
            result.append(f"${_repair_math_content(content)}$")
        else:
            result.append(text[index : close_index + 1])
        index = close_index + 1

    return "".join(result)


def _find_matching_parenthesis(text: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "\n":
            return None
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _looks_like_math_fragment(content: str) -> bool:
    if not content or re.search(r"[\u4e00-\u9fff]", content):
        return False
    if re.search(r"[\\_^=<>|{}]", content):
        return True
    return bool(re.fullmatch(r"[A-Za-z]", content))


def _repair_math(math: str) -> str:
    if math.startswith("$$") and math.endswith("$$"):
        return f"$${_repair_math_content(math[2:-2])}$$"
    if math.startswith("$") and math.endswith("$"):
        return f"${_repair_math_content(math[1:-1])}$"
    return math


def _repair_math_content(content: str) -> str:
    repaired = re.sub(r"\*\{([^}\n]+)\}", r"_{\1}", content)
    repaired = re.sub(r"(?<=[}|])\*([A-Za-z0-9])", r"_\1", repaired)
    repaired = _repair_environment_line_breaks(repaired)
    repaired = _expand_tall_parentheses(repaired)
    repaired = _wrap_underbrace_parentheses_for_word(repaired)
    return _escape_visible_set_braces(repaired)


def _repair_environment_line_breaks(content: str) -> str:
    environments = ("cases", "aligned", "array", "matrix", "pmatrix", "bmatrix", "vmatrix")
    for environment in environments:
        content = re.sub(
            rf"(\\begin\{{{environment}\}}.*?\\end\{{{environment}\}})",
            _repair_single_environment_line_breaks,
            content,
            flags=re.DOTALL,
        )
    return content


def _repair_single_environment_line_breaks(match: re.Match[str]) -> str:
    return re.sub(r"(?<!\\)\\(?=\n)", r"\\\\", match.group(1))


def _expand_tall_parentheses(content: str) -> str:
    result: list[str] = []
    index = 0

    while index < len(content):
        if content[index] != "(" or _is_already_sized_delimiter(content, index):
            result.append(content[index])
            index += 1
            continue

        close_index = _find_matching_parenthesis(content, index)
        if close_index is None:
            result.append(content[index])
            index += 1
            continue

        inner = content[index + 1 : close_index]
        if _has_tall_math(inner):
            result.append(f"\\left({inner}\\right)")
        else:
            result.append(content[index : close_index + 1])
        index = close_index + 1

    return "".join(result)


def _has_tall_math(content: str) -> bool:
    return bool(re.search(r"\\(?:underbrace|overbrace|frac|sqrt|sum|prod|int|begin)\b", content))


def _is_already_sized_delimiter(content: str, index: int) -> bool:
    if index > 0 and content[index - 1] == "\\":
        return True

    prefix = content[:index]
    return bool(re.search(r"\\(?:left|right|big|Big|bigg|Bigg)$", prefix))


def _wrap_underbrace_parentheses_for_word(content: str) -> str:
    result: list[str] = []
    index = 0

    while index < len(content):
        if not content.startswith("\\left(", index):
            result.append(content[index])
            index += 1
            continue

        match = _find_matching_right_delimiter(content, index)
        if match is None:
            result.append(content[index])
            index += 1
            continue

        right_index, delimiter_end = match
        inner_start = index + len("\\left(")
        inner = content[inner_start:right_index]
        if _needs_word_tall_height_wrap(inner):
            result.append(f"\\left(\\begin{{matrix}}{inner}\\end{{matrix}}\\right)")
        else:
            result.append(content[index:delimiter_end])
        index = delimiter_end

    return "".join(result)


def _find_matching_right_delimiter(content: str, left_index: int) -> tuple[int, int] | None:
    depth = 1
    index = left_index + len("\\left(")

    while index < len(content):
        if content.startswith("\\left", index):
            depth += 1
            index += len("\\left")
            continue

        if content.startswith("\\right", index):
            delimiter_index = index + len("\\right")
            if delimiter_index >= len(content):
                return None
            depth -= 1
            delimiter_end = delimiter_index + 1
            if depth == 0:
                return index, delimiter_end
            index = delimiter_end
            continue

        index += 1

    return None


def _needs_word_tall_height_wrap(inner: str) -> bool:
    if re.match(r"\s*\\begin\{(?:matrix|array)\}", inner):
        return False

    if "\\underbrace" in inner and re.search(r"\\underbrace\b[\s\S]*?_\{", inner) is not None:
        return True

    return bool(re.search(r"\\frac\b", inner))


def _escape_visible_set_braces(content: str) -> str:
    result: list[str] = []
    index = 0

    while index < len(content):
        if content[index] != "{" or _is_tex_group_brace(content, index):
            result.append(content[index])
            index += 1
            continue

        close_index = _find_matching_brace(content, index)
        if close_index is None:
            result.append(content[index])
            index += 1
            continue

        inner = content[index + 1 : close_index]
        if "," in inner:
            result.append(f"\\{{{inner}\\}}")
        else:
            result.append(content[index : close_index + 1])
        index = close_index + 1

    return "".join(result)


def _is_tex_group_brace(content: str, index: int) -> bool:
    if index > 0 and content[index - 1] in "_^\\":
        return True

    command_match = re.search(r"\\[A-Za-z]+$", content[:index])
    if command_match and command_match.group(0)[1:] in GROUP_ARGUMENT_COMMANDS:
        return True

    return _continues_group_argument_command(content, index)


def _continues_group_argument_command(content: str, index: int) -> bool:
    previous_index = index - 1
    while previous_index >= 0 and content[previous_index].isspace():
        previous_index -= 1

    if previous_index < 0 or content[previous_index] != "}":
        return False

    previous_open = _find_matching_open_brace(content, previous_index)
    if previous_open is None:
        return False

    command_match = re.search(r"\\[A-Za-z]+$", content[:previous_open])
    return bool(command_match and command_match.group(0)[1:] in GROUP_ARGUMENT_COMMANDS)


def _find_matching_open_brace(content: str, close_index: int) -> int | None:
    depth = 0
    for index in range(close_index, -1, -1):
        char = content[index]
        if char == "}":
            depth += 1
        elif char == "{":
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_matching_brace(content: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(content)):
        char = content[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None
