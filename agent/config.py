import os
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agent.domain.errors import ConfigurationError


class AgentConfig(BaseModel):
    """Controller 启动配置；Secret 使用专门类型避免在 repr 和校验日志中泄露。"""

    model_config = ConfigDict(extra="forbid")

    supabase_url: str = Field(min_length=1)
    supabase_agent_key: SecretStr
    artifact_root: Path
    source_workspace_root: Path
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
    production_scheduler_enabled: bool = False
    model_provider: str = "openai_compatible"
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    model_base_url: str | None = None
    model_context_window: int | None = Field(default=None, ge=1)
    model_input_cost_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    model_output_cost_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    fallback_model_enabled: bool = False
    fallback_model_name: str | None = None
    fallback_model_api_key: SecretStr | None = None
    fallback_model_base_url: str | None = None
    fallback_model_context_window: int | None = Field(default=None, ge=1)
    fallback_model_input_cost_per_million: Decimal = Field(
        default=Decimal("0"),
        ge=0,
    )
    fallback_model_output_cost_per_million: Decimal = Field(
        default=Decimal("0"),
        ge=0,
    )
    gate_model_timeout_seconds: float = Field(default=30.0, ge=30.0, le=120.0)
    reproduction_model_timeout_seconds: float = Field(
        default=180.0,
        ge=30.0,
        le=300.0,
    )
    max_model_calls_per_run: int = Field(default=12, ge=1, le=100)
    max_tool_calls_per_run: int = Field(default=30, ge=1, le=1000)
    max_sandbox_seconds_per_run: int = Field(default=900, ge=1, le=3600)
    backend_baseline_skipped: int = Field(default=0, ge=0)
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    agent_environment: str = Field(
        default="development",
        pattern=r"^[a-z0-9_-]{1,40}$",
    )
    trace_content: bool = False
    # GitHub/Sandbox 只在 --reproduce 时强制，Gate-only 运行保持最小配置。
    github_repository: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    )
    github_read_token: SecretStr | None = None
    github_app_id: str | None = Field(default=None, pattern=r"^\d+$")
    github_app_private_key: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_main_branch: str = Field(
        default="main",
        pattern=r"^[A-Za-z0-9._/-]{1,100}$",
    )
    langfuse_trace_url_template: str | None = None
    sandbox_worker_url: str | None = None
    sandbox_worker_credential: SecretStr | None = None
    # 公开展示站点的完成回调。两项都留空时完全不推送，Agent 行为与之前一致。
    trace_site_webhook_url: str | None = None
    trace_site_webhook_secret: SecretStr | None = None

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

    @field_validator(
        "model_base_url",
        "fallback_model_base_url",
        "langfuse_host",
        "sandbox_worker_url",
        "github_api_url",
        "trace_site_webhook_url",
    )
    @classmethod
    def require_http_external_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("external service URL must use HTTP(S)")
        return value

    @field_validator("langfuse_trace_url_template")
    @classmethod
    def require_trace_url_placeholder(cls, value: str | None) -> str | None:
        parsed = urlsplit(value) if value is not None else None
        if value is not None and (
            parsed is None
            or parsed.scheme not in {"https", "http"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
            or "{trace_id}" not in value
        ):
            raise ValueError(
                "LANGFUSE_TRACE_URL_TEMPLATE must be HTTP(S) and contain {trace_id}"
            )
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
        source_workspace_root = _rooted_path(
            root,
            values.get(
                "SOURCE_WORKSPACE_ROOT",
                str(root / "var" / "source-snapshots"),
            ),
        )
        return cls(
            supabase_url=values["SUPABASE_URL"].strip().rstrip("/"),
            supabase_agent_key=SecretStr(values["SUPABASE_AGENT_KEY"]),
            artifact_root=artifact_root,
            source_workspace_root=source_workspace_root,
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
            production_scheduler_enabled=_bool_value(
                values,
                "PRODUCTION_SCHEDULER_ENABLED",
                False,
            ),
            model_provider=values.get(
                "MODEL_PROVIDER",
                "openai_compatible",
            ).strip(),
            model_name=_optional_text(values, "MODEL_NAME"),
            model_api_key=_optional_secret(values, "MODEL_API_KEY"),
            model_base_url=_optional_text(values, "MODEL_BASE_URL"),
            model_context_window=_optional_int_value(
                values,
                "MODEL_CONTEXT_WINDOW",
            ),
            model_input_cost_per_million=_decimal_value(
                values,
                "MODEL_INPUT_COST_PER_MILLION",
            ),
            model_output_cost_per_million=_decimal_value(
                values,
                "MODEL_OUTPUT_COST_PER_MILLION",
            ),
            fallback_model_enabled=_bool_value(
                values,
                "FALLBACK_MODEL_ENABLED",
                False,
            ),
            fallback_model_name=_optional_text(values, "FALLBACK_MODEL_NAME"),
            fallback_model_api_key=_optional_secret(
                values,
                "FALLBACK_MODEL_API_KEY",
            ),
            fallback_model_base_url=_optional_text(
                values,
                "FALLBACK_MODEL_BASE_URL",
            ),
            fallback_model_context_window=_optional_int_value(
                values,
                "FALLBACK_MODEL_CONTEXT_WINDOW",
            ),
            fallback_model_input_cost_per_million=_decimal_value(
                values,
                "FALLBACK_MODEL_INPUT_COST_PER_MILLION",
            ),
            fallback_model_output_cost_per_million=_decimal_value(
                values,
                "FALLBACK_MODEL_OUTPUT_COST_PER_MILLION",
            ),
            gate_model_timeout_seconds=_float_value(
                values,
                "GATE_MODEL_TIMEOUT_SECONDS",
                30.0,
            ),
            reproduction_model_timeout_seconds=_float_value(
                values,
                "REPRODUCTION_MODEL_TIMEOUT_SECONDS",
                180.0,
            ),
            max_model_calls_per_run=_int_value(
                values,
                "MAX_MODEL_CALLS_PER_RUN",
                12,
            ),
            max_tool_calls_per_run=_int_value(
                values,
                "MAX_TOOL_CALLS_PER_RUN",
                30,
            ),
            max_sandbox_seconds_per_run=_int_value(
                values,
                "MAX_SANDBOX_SECONDS_PER_RUN",
                900,
            ),
            backend_baseline_skipped=_int_value(
                values,
                "BACKEND_BASELINE_SKIPPED",
                0,
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
            github_repository=_optional_text(values, "GITHUB_REPOSITORY"),
            github_read_token=_optional_secret(values, "GITHUB_READ_TOKEN"),
            github_app_id=_optional_text(values, "GITHUB_APP_ID"),
            github_app_private_key=_private_key_secret(
                values,
                "GITHUB_APP_PRIVATE_KEY",
            ),
            github_api_url=values.get(
                "GITHUB_API_URL",
                "https://api.github.com",
            ).strip().rstrip("/"),
            github_main_branch=values.get("GITHUB_MAIN_BRANCH", "main").strip(),
            langfuse_trace_url_template=_optional_text(
                values,
                "LANGFUSE_TRACE_URL_TEMPLATE",
            ),
            sandbox_worker_url=_optional_text(values, "SANDBOX_WORKER_URL"),
            sandbox_worker_credential=_optional_secret(
                values,
                "SANDBOX_WORKER_CREDENTIAL",
            ),
            trace_site_webhook_url=_optional_text(
                values,
                "TRACE_SITE_WEBHOOK_URL",
            ),
            trace_site_webhook_secret=_optional_secret(
                values,
                "TRACE_SITE_WEBHOOK_SECRET",
            ),
        )

    def require_database_url(self) -> str:
        if self.database_url is None:
            raise ConfigurationError("missing required configuration: AGENT_DATABASE_URL")
        return self.database_url.get_secret_value()

    def require_production_scheduler_enabled(self) -> None:
        if not self.production_scheduler_enabled:
            raise ConfigurationError(
                "PRODUCTION_SCHEDULER_ENABLED must be true before claiming production feedback"
            )

    def require_model_settings(self) -> tuple[str, str, str]:
        if self.model_provider != "openai_compatible":
            raise ConfigurationError(
                "MODEL_PROVIDER must be openai_compatible for the configured runtime"
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

    def fallback_model_settings(self) -> tuple[str, str, str] | None:
        """返回启用的备用接口；未启用时不读取或暴露其 Secret。"""

        if not self.fallback_model_enabled:
            return None
        missing = [
            name
            for name, value in (
                ("FALLBACK_MODEL_NAME", self.fallback_model_name),
                ("FALLBACK_MODEL_API_KEY", self.fallback_model_api_key),
                ("FALLBACK_MODEL_BASE_URL", self.fallback_model_base_url),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                "missing required configuration: " + ", ".join(missing)
            )
        assert self.fallback_model_name is not None
        assert self.fallback_model_api_key is not None
        assert self.fallback_model_base_url is not None
        return (
            self.fallback_model_name,
            self.fallback_model_api_key.get_secret_value(),
            self.fallback_model_base_url,
        )

    def require_langfuse_settings(self) -> tuple[str, str, str]:
        if self.trace_content:
            raise ConfigurationError(
                "TRACE_CONTENT=true is not supported by the Agent CLI"
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

    def trace_site_webhook_settings(self) -> tuple[str, str] | None:
        """展示站点回调是可选能力：只有 URL 与密钥都配齐才启用，缺一律视为关闭。

        刻意不抛错。回调纯粹服务于对外展示，不配置时 Agent 必须照常修复。
        """

        if self.trace_site_webhook_url is None or self.trace_site_webhook_secret is None:
            return None
        return (
            self.trace_site_webhook_url,
            self.trace_site_webhook_secret.get_secret_value(),
        )

    def require_stage_c_controller_settings(self) -> tuple[str, str, str, str]:
        """在阶段 D 接入源码与沙箱节点时一次性收紧阶段 C 必需配置。"""

        missing = [
            name
            for name, value in (
                ("GITHUB_REPOSITORY", self.github_repository),
                ("GITHUB_READ_TOKEN", self.github_read_token),
                ("SANDBOX_WORKER_URL", self.sandbox_worker_url),
                ("SANDBOX_WORKER_CREDENTIAL", self.sandbox_worker_credential),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                "missing required configuration: " + ", ".join(missing)
            )
        assert self.github_repository is not None
        assert self.github_read_token is not None
        assert self.sandbox_worker_url is not None
        assert self.sandbox_worker_credential is not None
        return (
            self.github_repository,
            self.github_read_token.get_secret_value(),
            self.sandbox_worker_url,
            self.sandbox_worker_credential.get_secret_value(),
        )

    def require_stage_f_publisher_settings(self) -> tuple[str, str, str, str, str]:
        """发布前一次性校验 GitHub App 与可审查 Trace URL 配置。"""

        missing = [
            name
            for name, value in (
                ("GITHUB_APP_ID", self.github_app_id),
                ("GITHUB_APP_PRIVATE_KEY", self.github_app_private_key),
                ("LANGFUSE_TRACE_URL_TEMPLATE", self.langfuse_trace_url_template),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                "missing required configuration: " + ", ".join(missing)
            )
        assert self.github_app_id is not None
        assert self.github_app_private_key is not None
        assert self.langfuse_trace_url_template is not None
        return (
            self.github_app_id,
            self.github_app_private_key.get_secret_value(),
            self.github_api_url,
            self.github_main_branch,
            self.langfuse_trace_url_template,
        )


def _int_value(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"configuration {name} must be an integer") from exc


def _optional_int_value(values: Mapping[str, str], name: str) -> int | None:
    raw = values.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"configuration {name} must be an integer"
        ) from exc


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


def _private_key_secret(
    values: Mapping[str, str],
    name: str,
) -> SecretStr | None:
    value = values.get(name, "").strip()
    if not value:
        return None
    # 同时支持 dotenv 多行值和以字面量 \n 保存的 Secret 注入方式。
    return SecretStr(value.replace("\\n", "\n"))


def _rooted_path(root: Path, value: str) -> Path:
    """相对路径统一以项目根目录解析，避免受 Controller 启动目录影响。"""

    path = Path(value)
    return path if path.is_absolute() else root / path
