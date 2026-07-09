from pydantic import BaseModel, Field, field_validator


class ConvertOptions(BaseModel):
    filename: str = "document.docx"

    @field_validator("filename")
    @classmethod
    def ensure_docx_filename(cls, value: str) -> str:
        cleaned = value.strip() or "document.docx"
        if not cleaned.endswith(".docx"):
            cleaned = f"{cleaned}.docx"
        return cleaned


class ConvertRequest(BaseModel):
    title: str = "document"
    markdown: str = Field(min_length=1)
    options: ConvertOptions = Field(default_factory=ConvertOptions)

    @field_validator("markdown")
    @classmethod
    def markdown_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("markdown must not be blank")
        return value


class ConversionErrorResponse(BaseModel):
    error: str
    message: str
    details: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    feedback_type: str = Field(default="bug", pattern=r"^(bug|feature)$")
    markdown_content: str = Field(default="", max_length=50000)
    description: str = Field(min_length=1, max_length=1000)
    contact: str = Field(default="", max_length=200)

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value


class FeedbackResponse(BaseModel):
    success: bool
    id: str
