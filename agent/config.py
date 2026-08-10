import os
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agent.domain.errors import ConfigurationError


class AgentConfig(BaseModel):
    """Controller 启动配置；Secret 使用专门类型避免在 repr 和校验日志中泄露。"""

    model_config = ConfigDict(extra="forbid")

    supabase_url: str = Field(min_length=1)
    supabase_agent_key: SecretStr
    artifact_root: Path
    extension_manifest_path: Path
    claim_lease_seconds: int = Field(default=300, ge=1)
    max_claim_attempts: int = Field(default=3, ge=1)
    artifact_retention_days: int = Field(default=14, ge=1)
    min_gate_confidence: float = Field(default=0.80, ge=0, le=1)
    database_url: SecretStr | None = None
    checkpoint_schema: str = Field(
        default="agent_runtime",
        pattern=r"^[a-z_][a-z0-9_]{0,62}$",
    )
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    model_provider: str = "openai_compatible"
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    model_base_url: str | None = None
    model_input_cost_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    model_output_cost_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    agent_environment: str = Field(
        default="development",
        pattern=r"^[a-z0-9_-]{1,40}$",
    )
    trace_content: bool = False

    @field_validator("supabase_url")
    @classmethod
    def require_http_supabase_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("SUPABASE_URL must be an HTTP(S) URL")
        return value

    @field_validator("agent_environment")
    @classmethod
    def reject_reserved_environment_prefix(cls, value: str) -> str:
        if value.startswith("langfuse"):
            raise ValueError("AGENT_ENVIRONMENT cannot start with langfuse")
        return value

    @field_validator("model_base_url", "langfuse_host")
    @classmethod
    def require_http_external_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("external service URL must use HTTP(S)")
        return value

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: Path | None = None,
    ) -> "AgentConfig":
        values = environ if environ is not None else os.environ
        required = ("SUPABASE_URL", "SUPABASE_AGENT_KEY")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            # 错误只列配置名，绝不拼接环境变量值。
            raise ConfigurationError(
                "missing required configuration: " + ", ".join(missing)
            )

        root = (project_root or Path(__file__).resolve().parents[1]).resolve()
        artifact_root = _rooted_path(
            root,
            values.get("ARTIFACT_ROOT", str(root / "var" / "agent-artifacts")),
        )
        manifest_path = _rooted_path(
            root,
            values.get(
                "EXTENSION_MANIFEST_PATH",
                str(root / "extension" / "dist" / "manifest.json"),
            )
        )
        return cls(
            supabase_url=values["SUPABASE_URL"].strip().rstrip("/"),
            supabase_agent_key=SecretStr(values["SUPABASE_AGENT_KEY"]),
            artifact_root=artifact_root,
            extension_manifest_path=manifest_path,
            claim_lease_seconds=_int_value(values, "CLAIM_LEASE_SECONDS", 300),
            max_claim_attempts=_int_value(values, "MAX_CLAIM_ATTEMPTS", 3),
            artifact_retention_days=_int_value(
                values,
                "ARTIFACT_RETENTION_DAYS",
                14,
            ),
            min_gate_confidence=_float_value(
                values,
                "MIN_GATE_CONFIDENCE",
                0.80,
            ),
            database_url=(
                SecretStr(values["AGENT_DATABASE_URL"])
                if values.get("AGENT_DATABASE_URL", "").strip()
                else None
            ),
            checkpoint_schema=values.get(
                "AGENT_CHECKPOINT_SCHEMA",
                "agent_runtime",
            ).strip(),
            poll_interval_seconds=_float_value(
                values,
                "POLL_INTERVAL_SECONDS",
                5.0,
            ),
            model_provider=values.get(
                "MODEL_PROVIDER",
                "openai_compatible",
            ).strip(),
            model_name=_optional_text(values, "MODEL_NAME"),
            model_api_key=_optional_secret(values, "MODEL_API_KEY"),
            model_base_url=_optional_text(values, "MODEL_BASE_URL"),
            model_input_cost_per_million=_decimal_value(
                values,
                "MODEL_INPUT_COST_PER_MILLION",
            ),
            model_output_cost_per_million=_decimal_value(
                values,
                "MODEL_OUTPUT_COST_PER_MILLION",
            ),
            langfuse_host=values.get(
                "LANGFUSE_HOST",
                "https://cloud.langfuse.com",
            ).strip().rstrip("/"),
            langfuse_public_key=_optional_secret(values, "LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=_optional_secret(values, "LANGFUSE_SECRET_KEY"),
            agent_environment=values.get(
                "AGENT_ENVIRONMENT",
                "development",
            ).strip(),
            trace_content=_bool_value(values, "TRACE_CONTENT", False),
        )

    def require_database_url(self) -> str:
        if self.database_url is None:
            raise ConfigurationError("missing required configuration: AGENT_DATABASE_URL")
        return self.database_url.get_secret_value()

    def require_model_settings(self) -> tuple[str, str, str]:
        if self.model_provider != "openai_compatible":
            raise ConfigurationError(
                "MODEL_PROVIDER must be openai_compatible for Stage B3"
            )
        missing = [
            name
            for name, value in (
                ("MODEL_NAME", self.model_name),
                ("MODEL_API_KEY", self.model_api_key),
                ("MODEL_BASE_URL", self.model_base_url),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                "missing required configuration: " + ", ".join(missing)
            )
        assert self.model_name is not None
        assert self.model_api_key is not None
        assert self.model_base_url is not None
        return (
            self.model_name,
            self.model_api_key.get_secret_value(),
            self.model_base_url,
        )

    def require_langfuse_settings(self) -> tuple[str, str, str]:
        if self.trace_content:
            raise ConfigurationError(
                "TRACE_CONTENT=true is not supported by the Stage B3 CLI"
            )
        missing = [
            name
            for name, value in (
                ("LANGFUSE_PUBLIC_KEY", self.langfuse_public_key),
                ("LANGFUSE_SECRET_KEY", self.langfuse_secret_key),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                "missing required configuration: " + ", ".join(missing)
            )
        assert self.langfuse_public_key is not None
        assert self.langfuse_secret_key is not None
        return (
            self.langfuse_host,
            self.langfuse_public_key.get_secret_value(),
            self.langfuse_secret_key.get_secret_value(),
        )


def _int_value(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"configuration {name} must be an integer") from exc


def _float_value(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"configuration {name} must be a number") from exc


def _decimal_value(
    values: Mapping[str, str],
    name: str,
    default: Decimal = Decimal("0"),
) -> Decimal:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigurationError(f"configuration {name} must be a number") from exc


def _bool_value(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"configuration {name} must be true or false")


def _optional_text(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name, "").strip()
    return value or None


def _optional_secret(values: Mapping[str, str], name: str) -> SecretStr | None:
    value = values.get(name, "").strip()
    return SecretStr(value) if value else None


def _rooted_path(root: Path, value: str) -> Path:
    """相对路径统一以项目根目录解析，避免受 Controller 启动目录影响。"""

    path = Path(value)
    return path if path.is_absolute() else root / path
