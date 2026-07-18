from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator


_HAN_IDEOGRAPH_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]"
)


def remove_han_ideographs(value: str) -> str:
    """Remove Chinese/Hanja ideographs while preserving Korean, English, and punctuation."""
    cleaned = _HAN_IDEOGRAPH_RE.sub("", value)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


class TelegramUser(BaseModel):
    id: int


class TelegramChat(BaseModel):
    id: int


class TelegramPhotoSize(BaseModel):
    file_id: str
    file_unique_id: str
    width: int
    height: int
    file_size: int | None = None


class TelegramMessage(BaseModel):
    message_id: int
    date: int | None = None
    chat: TelegramChat
    from_user: TelegramUser = Field(alias="from")
    text: str | None = None
    caption: str | None = None
    photo: list[TelegramPhotoSize] | None = None

    model_config = {"populate_by_name": True}


class TelegramUpdate(BaseModel):
    update_id: int | None = None
    message: TelegramMessage | None = None


class TextAnalysisResult(BaseModel):
    title: str
    summary: str
    tags: list[str]
    category: str = "note"
    confidence: float
    raw_response: str
    ocr_text: str | None = None
    is_note: bool | None = None
    needs_user_clarification: bool = False
    action: str = "create"
    target_note_id: str | None = None
    tool_name: str | None = None
    tool_query: str | None = None
    tool_tag: str | None = None
    tool_limit: int | None = None

    model_config = {"validate_assignment": True}

    @field_validator("title", "summary", mode="before")
    @classmethod
    def keep_generated_note_text_hanja_free(cls, value: Any, info: ValidationInfo) -> str:
        cleaned = remove_han_ideographs(str(value or ""))
        if cleaned:
            return cleaned
        return "메모" if info.field_name == "title" else "원문을 저장한 메모."

    @field_validator("tags", mode="before")
    @classmethod
    def keep_generated_tags_hanja_free(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned_tags: list[str] = []
        for tag in value:
            cleaned = remove_han_ideographs(str(tag))
            if cleaned and cleaned not in cleaned_tags:
                cleaned_tags.append(cleaned)
        return cleaned_tags


class RouteDecision(BaseModel):
    route: str
    confidence: float
    target_note_id: str | None = None
    reason: str = ""
    tool_name: str | None = None
    tool_query: str | None = None
    tool_tag: str | None = None
    tool_limit: int | None = None


class WebhookResult(BaseModel):
    status: str
    detail: str | None = None


class OpenAIMessage(BaseModel):
    role: str
    content: str


class ChatCompletionChoice(BaseModel):
    message: OpenAIMessage


class ChatCompletionResponse(BaseModel):
    choices: list[ChatCompletionChoice]
    model: str | None = None
    raw: dict[str, Any] | None = None
