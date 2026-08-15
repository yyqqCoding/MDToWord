import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any


_SECRET_KEYS = re.compile(
    r"(authorization|cookie|api[_-]?key|secret|token|password|contact)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
# 边界排除十六进制字符与连字符，而不是只排除数字：SHA-256、git SHA、镜像 digest 和
# UUID 里普遍存在 9 位以上连续数字段，`(?<!\d)` 挡不住相邻的 a-f 或 `-`，会把标识符
# 吃掉一段。实测 20 万个真实 SHA-256 中曾有 28.03% 被改写，UUID 22.25%，改后均为 0。
_PHONE = re.compile(r"(?<![0-9A-Fa-f-])(?:\+?\d[\d\s().-]{7,}\d)(?![0-9A-Fa-f-])")
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"\b([A-Za-z0-9_-]*(?:authorization|cookie|api[_-]?key|secret|token|"
    r"password|contact)[A-Za-z0-9_-]*)"
    r"\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
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
        return mask_text(data)
    return data


def mask_text(
    data: str,
    *,
    max_length: int = _MAX_TEXT,
    redact_phone: bool = True,
) -> str:
    """按调用边界需要的长度清理文本；日志与 Sandbox 复用同一脱敏规则。"""

    text = _BEARER.sub("Bearer [REDACTED]", data)
    text = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    if redact_phone:
        text = _PHONE.sub("[REDACTED_PHONE]", text)
    return text[:max_length]


def content_summary(content: str) -> dict[str, object]:
    encoded = content.encode("utf-8")
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
    }
