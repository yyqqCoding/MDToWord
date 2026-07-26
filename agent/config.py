"""Agent 配置。

不在 import 时读取任何 Secret;CLI 按子命令调用 `require()` 声明必需项
(如 fetch 需要 Supabase,classify 需要模型 Key)。
阈值默认值以 docs/AgentRequirements/00-overview/security-policy.md §3 为准。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr

from agent.exceptions import ConfigError


class AgentConfig(BaseModel):
    feedback_id: UUID | None = None
    dry_run: bool = True

    supabase_url: str | None = None
    supabase_key: SecretStr | None = None

    model_provider: str = "openai_compatible"
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    model_base_url: str | None = None
    model_timeout_seconds: float = 120.0
    max_output_tokens: int = 4096
    temperature: float = 0.0

    # security-policy §3 阈值
    max_repair_rounds: int = 2
    max_changed_files: int = 5
    max_added_lines: int = 300
    max_deleted_lines: int = 150
    max_patch_bytes: int = 200_000
    min_classification_confidence: float = 0.75

    max_claim_attempts: int = 3
    workflow_run_id: str | None = None
    repo_root: Path = Field(default_factory=Path.cwd)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides) -> "AgentConfig":
        env = os.environ if env is None else env

        def read(name: str) -> str | None:
            value = env.get(name)
            return value if value not in (None, "") else None

        values: dict = {}
        for field, var in {
            "supabase_url": "SUPABASE_URL",
            "supabase_key": "SUPABASE_KEY",
            "model_provider": "MODEL_PROVIDER",
            "model_name": "MODEL_NAME",
            "model_api_key": "MODEL_API_KEY",
            "model_base_url": "MODEL_BASE_URL",
            "workflow_run_id": "GITHUB_RUN_ID",
        }.items():
            value = read(var)
            if value is not None:
                values[field] = value
        for field, var in {
            "model_timeout_seconds": "MODEL_TIMEOUT_SECONDS",
            "max_output_tokens": "MODEL_MAX_OUTPUT_TOKENS",
            "temperature": "MODEL_TEMPERATURE",
            "max_repair_rounds": "MAX_REPAIR_ROUNDS",
            "max_claim_attempts": "MAX_CLAIM_ATTEMPTS",
        }.items():
            value = read(var)
            if value is not None:
                values[field] = value
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def require(self, *fields: str) -> "AgentConfig":
        """校验必需字段已配置,缺失时给出明确的环境变量名。"""
        env_names = {
            "supabase_url": "SUPABASE_URL",
            "supabase_key": "SUPABASE_KEY",
            "model_name": "MODEL_NAME",
            "model_api_key": "MODEL_API_KEY",
            "model_base_url": "MODEL_BASE_URL",
            "feedback_id": "--feedback-id",
        }
        missing = [f for f in fields if getattr(self, f, None) is None]
        if missing:
            hints = ", ".join(f"{f} ({env_names.get(f, f)})" for f in missing)
            raise ConfigError(f"缺少必需配置: {hints}")
        return self
