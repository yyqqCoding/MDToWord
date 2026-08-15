import logging
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse, propagate_attributes

from agent.providers.base import StructuredModelResponse
from agent.telemetry.base import (
    GenerationTrace,
    NoopTelemetry,
    RunTrace,
    ToolTrace,
    cost_details,
    exclusive_usage_buckets,
)
from agent.telemetry.masking import mask_sensitive


_LOGGER = logging.getLogger(__name__)


class LangfuseTelemetry:
    """Langfuse v4 适配器；任何观测故障都降级为空实现。"""

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str,
        environment: str,
        client: Any | None = None,
    ) -> None:
        self._client = client or Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=host,
            environment=environment,
            mask=mask_sensitive,
        )

    @contextmanager
    def start_run(self, trace: RunTrace):
        metadata = mask_sensitive(
            {
                "run_id": trace.run_id,
                "session_id": trace.session_id,
                "feedback_hash": trace.feedback_hash,
                "provider": trace.provider,
                "model": trace.model,
                "graph_version": trace.graph_version,
                "policy_version": trace.policy_version,
                "environment": trace.environment,
            }
        )
        context = self._safe_context(
            name="feedback-repair-run",
            as_type="agent",
            trace_context={"trace_id": trace.trace_id},
            input={"feedback_hash": trace.feedback_hash},
            metadata=metadata,
        )
        if context is None:
            with NoopTelemetry().start_run(trace) as observation:
                yield observation
            return
        try:
            raw = context.__enter__()
        except Exception as exc:  # pragma: no cover - SDK-specific failure
            _warn(exc)
            with NoopTelemetry().start_run(trace) as observation:
                yield observation
            return
        attributes = None
        try:
            attributes = propagate_attributes(
                session_id=trace.session_id,
                trace_name="feedback-repair-run",
                environment=trace.environment,
                metadata=metadata,
            )
            attributes.__enter__()
        except Exception as exc:  # pragma: no cover - SDK-specific failure
            _warn(exc)
            attributes = None
        wrapped = _LangfuseRunObservation(raw)
        try:
            yield wrapped
        except BaseException as exc:
            if attributes is not None:
                _safe_exit(attributes, type(exc), exc, exc.__traceback__)
            _safe_exit(context, type(exc), exc, exc.__traceback__)
            raise
        else:
            if attributes is not None:
                _safe_exit(attributes, None, None, None)
            _safe_exit(context, None, None, None)

    @contextmanager
    def start_generation(self, trace: GenerationTrace):
        context = self._safe_context(
            name=(
                "classify-intent"
                if trace.operation == "gate"
                else trace.operation.replace("_", "-")
            ),
            as_type="generation",
            model=trace.model,
            input=mask_sensitive(trace.input_summary),
            metadata=mask_sensitive(
                {
                    "operation": trace.operation,
                    "prompt_version": trace.prompt_version,
                    "provider": trace.provider,
                }
            ),
        )
        if context is None:
            with NoopTelemetry().start_generation(trace) as observation:
                yield observation
            return
        try:
            raw = context.__enter__()
        except Exception as exc:  # pragma: no cover - SDK-specific failure
            _warn(exc)
            with NoopTelemetry().start_generation(trace) as observation:
                yield observation
            return
        wrapped = _LangfuseGenerationObservation(raw)
        try:
            yield wrapped
        except BaseException as exc:
            _safe_exit(context, type(exc), exc, exc.__traceback__)
            raise
        else:
            _safe_exit(context, None, None, None)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as exc:  # pragma: no cover - SDK-specific failure
            _warn(exc)

    @contextmanager
    def start_tool(self, trace: ToolTrace):
        context = self._safe_context(
            name=trace.operation,
            as_type="tool",
            input=mask_sensitive(trace.input_summary),
            metadata={"round": trace.round},
        )
        if context is None:
            with NoopTelemetry().start_tool(trace) as observation:
                yield observation
            return
        try:
            raw = context.__enter__()
        except Exception as exc:  # pragma: no cover - SDK-specific failure
            _warn(exc)
            with NoopTelemetry().start_tool(trace) as observation:
                yield observation
            return
        wrapped = _LangfuseToolObservation(raw)
        try:
            yield wrapped
        except BaseException as exc:
            _safe_exit(context, type(exc), exc, exc.__traceback__)
            raise
        else:
            _safe_exit(context, None, None, None)

    def _safe_context(self, **kwargs: object):
        try:
            return self._client.start_as_current_observation(**kwargs)
        except Exception as exc:
            _warn(exc)
            return None


class _LangfuseRunObservation:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def finish(self, *, route: str | None, status: str) -> None:
        try:
            self._raw.update(
                output=mask_sensitive({"route": route, "status": status}),
                metadata=mask_sensitive({"final_status": status, "route": route}),
            )
        except Exception as exc:
            _warn(exc)


class _LangfuseGenerationObservation:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def succeed(self, response: StructuredModelResponse[object]) -> None:
        output = _safe_model_output(response.output.model_dump(mode="json"))
        update: dict[str, object] = {
            "output": mask_sensitive(output),
            "usage_details": exclusive_usage_buckets(response),
            "metadata": {
                "provider_request_id": response.provider_request_id,
                "retry_count": response.retry_count,
                "status": "success",
            },
        }
        costs = cost_details(response)
        if costs is not None:
            update["cost_details"] = costs
        try:
            self._raw.update(**update)
        except Exception as exc:
            _warn(exc)

    def fail(
        self,
        *,
        error_code: str,
        error_type: str,
        schema_errors: str | None = None,
    ) -> None:
        output: dict[str, object] = {
            "error_code": error_code,
            "error_type": error_type,
        }
        if schema_errors:
            # 只有字段路径与 Pydantic 规则名，不经 mask_sensitive 也不含模型原文；
            # 见 providers/openai_compatible.py:_schema_error_paths。
            output["schema_errors"] = schema_errors
        try:
            self._raw.update(
                level="ERROR",
                status_message=error_code,
                output=output,
            )
        except Exception as exc:
            _warn(exc)


class _LangfuseToolObservation:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def succeed(self, output_summary: dict[str, object]) -> None:
        try:
            self._raw.update(
                output=mask_sensitive(output_summary),
                metadata={"status": "success"},
            )
        except Exception as exc:
            _warn(exc)

    def fail(self, *, error_code: str, error_type: str) -> None:
        try:
            self._raw.update(
                level="ERROR",
                status_message=error_code,
                output={"error_code": error_code, "error_type": error_type},
            )
        except Exception as exc:
            _warn(exc)

def _safe_exit(context: Any, *args: object) -> None:
    try:
        context.__exit__(*args)
    except Exception as exc:  # pragma: no cover - SDK-specific failure
        _warn(exc)


def _safe_model_output(output: dict[str, object]) -> dict[str, object]:
    """Stage D 输出可能带测试源码或反馈复述，Trace 只保存可审计结构摘要。"""

    safe: dict[str, object] = {}
    for key, value in output.items():
        if key in {"reason", "hypothesis"}:
            safe[key] = "[REDACTED_SUMMARY]"
        elif key == "edits" and isinstance(value, list):
            safe[key] = [
                {
                    "path": item.get("path"),
                    "mode": item.get("mode"),
                }
                for item in value
                if isinstance(item, dict)
            ]
        elif key == "parameters" and isinstance(value, dict):
            safe[key] = {"parameter_names": sorted(value)}
        elif isinstance(value, dict):
            safe[key] = _safe_model_output(value)
        else:
            safe[key] = value
    return safe


def _warn(exc: Exception) -> None:
    # 只记录类型，SDK 异常文本可能包含 Host、Header 或观测内容。
    _LOGGER.warning("Langfuse telemetry failed: %s", type(exc).__name__)
