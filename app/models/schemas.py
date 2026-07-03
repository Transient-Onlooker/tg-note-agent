from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
