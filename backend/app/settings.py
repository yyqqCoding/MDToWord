from pydantic import BaseModel


class Settings(BaseModel):
    pandoc_binary: str = "pandoc"
    pandoc_timeout_seconds: int = 30
    allowed_origins: list[str] = ["*"]


settings = Settings()
