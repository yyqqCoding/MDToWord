import re


INLINE_PARENS_PATTERN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
BLOCK_BRACKETS_PATTERN = re.compile(r"(?:[ \t]*\n)?[ \t]*\\\[(.+?)\\\][ \t]*(?:\n[ \t]*)?", re.DOTALL)
BARE_BLOCK_BRACKETS_PATTERN = re.compile(r"(?:[ \t]*\n)?[ \t]*\[\s*\n(.+?)\n[ \t]*\][ \t]*(?:\n[ \t]*)?", re.DOTALL)
DOLLAR_MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\$[^$\n]+\$)", re.DOTALL)


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
    return re.sub(r"(?<=[}|])\*([A-Za-z0-9])", r"_\1", repaired)
