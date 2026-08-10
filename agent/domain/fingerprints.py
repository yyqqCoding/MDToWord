import hashlib
import json
import unicodedata

from agent.domain.enums import FeedbackType


def _canonicalize_text(value: str) -> str:
    # 只消除 Unicode、跨平台换行和首尾空白差异，不改写 Markdown 语义。
    normalized = unicodedata.normalize("NFC", value)
    return normalized.replace("\r\n", "\n").replace("\r", "\n").strip()


def feedback_fingerprint(
    feedback_type: FeedbackType,
    markdown_content: str,
    description: str,
) -> str:
    """对无语义差异的规范化反馈计算稳定 SHA-256。"""

    canonical = json.dumps(
        {
            "description": _canonicalize_text(description),
            "feedback_type": feedback_type.value,
            "markdown_content": _canonicalize_text(markdown_content),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
