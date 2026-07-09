import os

from pydantic import BaseModel


class Settings(BaseModel):
    pandoc_binary: str = "pandoc"
    pandoc_timeout_seconds: int = 30
    allowed_origins: list[str] = ["*"]
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_key: str = os.environ.get("SUPABASE_KEY", "")


settings = Settings()
