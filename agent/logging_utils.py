"""结构化 JSON 日志。

每条日志带 feedback_id / agent_run_id / workflow_run_id(天然 trace id,阶段 09)。
敏感键(contact / key / token / authorization ...)在序列化前被丢弃,
兜底 security-policy §8 的禁止记录项。默认不记录完整用户 Markdown。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

SENSITIVE_KEY_PATTERN = re.compile(
    r"(contact|api[_-]?key|apikey|authorization|token|secret|password|credential)",
    re.IGNORECASE,
)


def sanitize(value: Any) -> Any:
    """递归丢弃敏感键。"""
    if isinstance(value, Mapping):
        return {
            k: sanitize(v)
            for k, v in value.items()
            if not SENSITIVE_KEY_PATTERN.search(str(k))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    return value


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload.update(sanitize(context))
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ContextLogger(logging.LoggerAdapter):
    """把 feedback_id 等上下文注入每条日志。额外字段经 kwargs['extra'] 合并。"""

    def process(self, msg, kwargs):
        extra_context = kwargs.pop("context", {})
        merged = {**(self.extra or {}), **extra_context}
        kwargs["extra"] = {"context": sanitize(merged)}
        return msg, kwargs


def get_logger(name: str = "agent", **context: Any) -> ContextLogger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return ContextLogger(logger, context)
