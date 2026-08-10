import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any


_SECRET_KEYS = re.compile(
    r"(authorization|cookie|api[_-]?key|secret|token|password|contact)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_MAX_TEXT = 300


def mask_sensitive(data: Any) -> Any:
    """统一清理发送到 Trace 的结构化值，不修改业务数据。"""

    # Langfuse v4 通过 mask(data=...) 调用；参数名必须保持为 data。
    if isinstance(data, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEYS.search(str(key)) else mask_sensitive(item)
            for key, item in data.items()
        }
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return [mask_sensitive(item) for item in data]
    if isinstance(data, str):
        text = _BEARER.sub("Bearer [REDACTED]", data)
        text = _EMAIL.sub("[REDACTED_EMAIL]", text)
        text = _PHONE.sub("[REDACTED_PHONE]", text)
        return text[:_MAX_TEXT]
    return data


def content_summary(content: str) -> dict[str, object]:
    encoded = content.encode("utf-8")
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
    }
