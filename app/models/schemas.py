from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TelegramUser(BaseModel):
    id: int


class TelegramChat(BaseModel):
    id: int


class TelegramMessage(BaseModel):
    message_id: int
    date: int | None = None
    chat: TelegramChat
    from_user: TelegramUser = Field(alias="from")
    text: str | None = None

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
