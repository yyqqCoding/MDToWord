import re


INLINE_PARENS_PATTERN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
BLOCK_BRACKETS_PATTERN = re.compile(r"(?:[ \t]*\n)?[ \t]*\\\[(.+?)\\\][ \t]*(?:\n[ \t]*)?", re.DOTALL)


def normalize_markdown(markdown: str) -> str:
    """Normalize supported formula delimiters into Pandoc-friendly Markdown."""
    normalized = INLINE_PARENS_PATTERN.sub(lambda match: f"${match.group(1).strip()}$", markdown)
    return BLOCK_BRACKETS_PATTERN.sub(_replace_block_formula, normalized)


def _replace_block_formula(match: re.Match[str]) -> str:
    formula = match.group(1).strip()
    return f"\n\n$$\n{formula}\n$$\n\n"
