import os

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(validate_default=True)

    pandoc_binary: str = "pandoc"
    pandoc_timeout_seconds: int = 30
    allowed_origins: list[str] = ["*"]
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_key: str = os.environ.get("SUPABASE_KEY", "")
    feedback_rate_per_minute: int = Field(
        default=os.environ.get("FEEDBACK_RATE_PER_MINUTE", "1"),
        ge=1,
    )
    feedback_rate_per_hour: int = Field(
        default=os.environ.get("FEEDBACK_RATE_PER_HOUR", "5"),
        ge=1,
    )
    feedback_rate_per_day: int = Field(
        default=os.environ.get("FEEDBACK_RATE_PER_DAY", "10"),
        ge=1,
    )
    feedback_global_rate_per_hour: int = Field(
        default=os.environ.get("FEEDBACK_GLOBAL_RATE_PER_HOUR", "30"),
        ge=1,
    )


settings = Settings()
