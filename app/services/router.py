from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from app.integrations.telegram import TelegramClient
from app.models.db import StoredMessage
from app.models.schemas import TelegramUpdate, WebhookResult
from app.services.nim_provider import NvidiaNIMProvider
from app.services.note_manager import NoteManager


@dataclass(slots=True)
class RouterConfig:
    allowed_user_ids: set[int]


class UpdateRouter:
    def __init__(
        self,
        config: RouterConfig,
        note_manager: NoteManager,
        nim_provider: NvidiaNIMProvider,
        telegram_client: TelegramClient,
    ) -> None:
        self.config = config
        self.note_manager = note_manager
        self.nim_provider = nim_provider
        self.telegram_client = telegram_client

    def handle_update(self, update: TelegramUpdate) -> WebhookResult:
        message = update.message
        if message is None or not message.text:
            return WebhookResult(status="ignored", detail="non_text_update")

        user_id = message.from_user.id
        if user_id not in self.config.allowed_user_ids:
            return WebhookResult(status="ignored", detail="unauthorized_user")

        stored_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(user_id),
            raw_text=message.text,
        )
        message_id = self.note_manager.store_message(stored_message)

        try:
            analysis = self.nim_provider.analyze_text(message.text)
            self.note_manager.store_analysis_and_note(
                message_id=message_id,
                provider_name="nvidia_nim",
                model_name=self.nim_provider.model,
                source_text=message.text,
                analysis=analysis,
            )
            response_text = self._build_success_message(analysis.title, analysis.summary, analysis.tags)
            self.telegram_client.send_message(message.chat.id, response_text)
            return WebhookResult(status="processed")
        except Exception:
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                message.chat.id,
                "메시지는 저장했지만 AI 분석에는 실패했어. 나중에 다시 시도해줘.",
            )
            return WebhookResult(status="accepted_with_ai_failure")

    @staticmethod
    def _build_success_message(title: str, summary: str, tags: list[str]) -> str:
        tags_text = ", ".join(tags) if tags else "-"
        return (
            "저장했어.\n\n"
            f"제목: {title}\n"
            f"태그: {tags_text}\n"
            f"요약: {summary}"
        )


def parse_allowed_user_ids(raw_value: str | None) -> set[int]:
    if not raw_value:
        return set()

    values = set()
    for token in raw_value.split(","):
        token = token.strip()
        if token:
            values.add(int(token))
    return values


def build_router(
    note_manager: NoteManager,
    nim_provider: NvidiaNIMProvider,
    telegram_client: TelegramClient,
) -> UpdateRouter:
    config = RouterConfig(
        allowed_user_ids=parse_allowed_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS")),
    )
    return UpdateRouter(
        config=config,
        note_manager=note_manager,
        nim_provider=nim_provider,
        telegram_client=telegram_client,
    )
