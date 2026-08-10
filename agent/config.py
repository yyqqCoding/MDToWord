import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agent.domain.errors import ConfigurationError


class AgentConfig(BaseModel):
    """阶段 A 启动配置；Secret 使用专门类型避免在 repr 和校验日志中泄露。"""

    model_config = ConfigDict(extra="forbid")

    supabase_url: str = Field(min_length=1)
    supabase_agent_key: SecretStr
    artifact_root: Path
    extension_manifest_path: Path
    claim_lease_seconds: int = Field(default=300, ge=1)
    max_claim_attempts: int = Field(default=3, ge=1)
    artifact_retention_days: int = Field(default=14, ge=1)

    @field_validator("supabase_url")
    @classmethod
    def require_http_supabase_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("SUPABASE_URL must be an HTTP(S) URL")
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
        )


def _int_value(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"configuration {name} must be an integer") from exc


def _rooted_path(root: Path, value: str) -> Path:
    """相对路径统一以项目根目录解析，避免受 Controller 启动目录影响。"""

    path = Path(value)
    return path if path.is_absolute() else root / path
