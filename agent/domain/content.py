"""反馈 Markdown 的确定性内容特征，供 Gate 与复现规则共享。"""

import re


_MERMAID_DIAGRAM_PATTERN = re.compile(
    r"(?im)^\s*(?:graph|flowchart)\s+(?:tb|td|bt|rl|lr)\b"
)


def contains_mermaid_diagram(markdown: str) -> bool:
    """只识别行首完整 Mermaid 声明，避免普通正文中的 graph/flowchart 误触发。"""

    return _MERMAID_DIAGRAM_PATTERN.search(markdown) is not None
