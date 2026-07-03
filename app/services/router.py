from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

from fastapi import BackgroundTasks

from app.integrations.telegram import TelegramClient
from app.models.db import StoredMessage
from app.models.schemas import TelegramUpdate, WebhookResult
from app.services.nim_provider import NIMProviderError, NvidiaNIMProvider
from app.services.note_manager import NoteManager

logger = logging.getLogger(__name__)


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

    def handle_update(
        self,
        update: TelegramUpdate,
        background_tasks: BackgroundTasks | None = None,
    ) -> WebhookResult:
        message = update.message
        if message is None or not message.text:
            logger.info("Ignored non-text Telegram update")
            return WebhookResult(status="ignored", detail="non_text_update")

        user_id = message.from_user.id
        if user_id not in self.config.allowed_user_ids:
            logger.warning("Ignored unauthorized Telegram user_id=%s", user_id)
            return WebhookResult(status="ignored", detail="unauthorized_user")

        logger.info(
            "Received Telegram text message_id=%s chat_id=%s sender_id=%s",
            message.message_id,
            message.chat.id,
            user_id,
        )
        existing_message = self.note_manager.find_existing_message(
            chat_id=str(message.chat.id),
            telegram_message_id=str(message.message_id),
        )
        if existing_message is not None:
            logger.info(
                "Ignored duplicate Telegram message message_id=%s chat_id=%s db_message_id=%s status=%s",
                message.message_id,
                message.chat.id,
                existing_message["id"],
                existing_message["status"],
            )
            return WebhookResult(status="ignored", detail="duplicate_message")

        stored_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(user_id),
            raw_text=message.text,
        )
        message_id = self.note_manager.store_message(stored_message)
        logger.info("Stored raw message message_id=%s db_message_id=%s", message.message_id, message_id)
        self.telegram_client.send_message(message.chat.id, "수신 완료.")
        logger.info("Sent receive acknowledgement chat_id=%s", message.chat.id)

        if background_tasks is not None:
            background_tasks.add_task(
                self.process_message,
                message_id,
                message.chat.id,
                message.text,
            )
            return WebhookResult(status="accepted")

        self.process_message(message_id, message.chat.id, message.text)
        return WebhookResult(status="processed")

    def process_message(self, message_id: str, chat_id: int | str, text: str) -> None:
        try:
            logger.info("Starting NIM analysis db_message_id=%s model=%s", message_id, self.nim_provider.model)
            analysis = self.nim_provider.analyze_text(text)
            logger.info(
                "Finished NIM analysis db_message_id=%s title=%r confidence=%.2f",
                message_id,
                analysis.title,
                analysis.confidence,
            )
            self.note_manager.store_analysis_and_note(
                message_id=message_id,
                provider_name="nvidia_nim",
                model_name=self.nim_provider.model,
                source_text=text,
                analysis=analysis,
            )
            logger.info("Stored note and AI analysis db_message_id=%s", message_id)
            response_text = self._build_success_message(analysis.title, analysis.summary, analysis.tags)
            self.telegram_client.send_message(chat_id, response_text)
            logger.info("Sent completion message chat_id=%s db_message_id=%s", chat_id, message_id)
        except NIMProviderError as exc:
            logger.exception("Failed to process Telegram message db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                f"AI 분석이 너무 오래 걸리거나 실패했어. ({exc})",
            )
        except Exception:
            logger.exception("Failed to process Telegram message db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                "메시지는 저장했지만 AI 분석에는 실패했어. 나중에 다시 시도해줘.",
            )

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
