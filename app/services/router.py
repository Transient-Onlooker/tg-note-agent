from __future__ import annotations

import logging
import os
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import BackgroundTasks

from app.integrations.telegram import TelegramClient
from app.models.db import DuplicateMessageError, StoredMessage
from app.models.schemas import TelegramMessage, TelegramPhotoSize, TelegramUpdate, TextAnalysisResult, WebhookResult
from app.services.image_archive import ImageArchive
from app.services.nim_provider import NIMProviderError, NvidiaNIMProvider
from app.services.note_manager import NoteManager

logger = logging.getLogger(__name__)
_CURRENT_REPLY_MESSAGE_ID: ContextVar[str | None] = ContextVar(
    "current_reply_message_id",
    default=None,
)

PHOTO_ONLY_KEYWORDS = {"사진", "일반 사진", "보관", "보관용", "그냥 사진"}
NOTE_SCOPE_HINTS = (
    "메모중",
    "메모 중",
    "내 메모",
    "메모에서",
    "메모에",
    "메모 검색",
)
NOTE_SEARCH_ACTION_HINTS = (
    "뭐있",
    "뭐 있",
    "찾아",
    "검색",
)
NOTE_COUNT_HINTS = (
    "메모 개수",
    "메모 갯수",
    "몇개",
    "몇 개",
    "총 몇",
)

"""
FAST_READ_REFERENCE_HINTS = (
    "저 메모",
    "이 메모",
    "그 메모",
    "방금 메모",
    "방금 저장",
    "방금 시킨",
    "아까 메모",
    "아까 사진",
    "저 사진",
    "이 사진",
    "그 사진",
    "사진 메모",
    "ocr",
    "그거",
)
FAST_READ_CONTENT_HINTS = (
    "전체 내용",
    "전체 메모",
    "원문",
    "뭐라고 저장",
    "어떻게 저장",
    "요약 말고",
    "풀어서",
    "전문",
    "본문",
)


"""
FAST_READ_REFERENCE_HINTS = (
    "\uc800 \uba54\ubaa8",
    "\uc774 \uba54\ubaa8",
    "\uadf8 \uba54\ubaa8",
    "\ubc29\uae08 \uba54\ubaa8",
    "\ubc29\uae08 \uc800\uc7a5",
    "\ubc29\uae08 \uc2dc\ud0a8",
    "\uc544\uae4c \uba54\ubaa8",
    "\uc544\uae4c \uc0ac\uc9c4",
    "\uc800 \uc0ac\uc9c4",
    "\uc774 \uc0ac\uc9c4",
    "\uadf8 \uc0ac\uc9c4",
    "\uc0ac\uc9c4 \uba54\ubaa8",
    "ocr",
    "\uadf8\uac70",
)
FAST_READ_CONTENT_HINTS = (
    "\uc804\uccb4 \ub0b4\uc6a9",
    "\uc804\uccb4 \uba54\ubaa8",
    "\uc6d0\ubb38",
    "\ubb50\ub77c\uace0 \uc800\uc7a5",
    "\uc5b4\ub5bb\uac8c \uc800\uc7a5",
    "\uc694\uc57d \ub9d0\uace0",
    "\ud480\uc5b4\uc11c",
    "\uc804\ubb38",
    "\ubcf8\ubb38",
)
DELETE_HINTS = ("\uc0ad\uc81c", "\uc9c0\uc6cc", "\uc5c6\uc560")
DUPLICATE_HINTS = ("\uc911\ubcf5", "\uac19\uc740 \uba54\ubaa8", "\uac19\uc740 \ub178\ud2b8", "\uacb9\uce5c")
DELETE_CONFIRM_HINTS = (
    "\uc0ad\uc81c \ud655\uc778",
    "\uc815\ub9d0 \uc0ad\uc81c",
    "\uc751 \uc0ad\uc81c",
    "\uc9c0\uc6cc",
)
CORRECTION_HINTS = (
    "\uc218\uc815\ud574\uc918",
    "\uc218\uc815\ud558\ub77c\uace0",
    "\uc218\uc815",
    "\uc815\uc815\ud574\uc918",
    "\uace0\uccd0\uc918",
    "\ubc14\uafd4\uc918",
)
SEARCH_VERBS = (
    "\uc54c\ub824\uc918",
    "\ubcf4\uc5ec\uc918",
    "\ucc3e\uc544\uc918",
    "\uac80\uc0c9",
    "\ubb50 \uc788",
    "\ubb50\uc788",
)
NUMBERED_DETAIL_HINTS = (
    "\uc880\ub354",
    "\uc880 \ub354",
    "\uc790\uc138\ud788",
    "\uc0c1\uc138",
    "\ub354 \uc54c\ub824",
    "\uc54c\ub824\ub2ec",
    "\uc124\uba85",
)
SLASH_COMMAND_NAMES = {
    "new",
    "add",
    "list",
    "show",
    "raw",
    "delete",
    "fix",
    "dedupe",
    "help",
}
NOTE_COMMAND_HINTS = (
    "\uba54\ubaa8",
    "\ub178\ud2b8",
    "\uc800\uc7a5\ud55c",
    "\uc800\uc7a5\ub41c",
    "\uc0ac\uc9c4",
)
LIST_HINTS = (
    "\uc804\uccb4",
    "\uc804\ubd80",
    "\ubaa9\ub85d",
    "\ucd5c\uadfc",
)
LIST_ALL_PHRASES = (
    "\uc804\uccb4 \uba54\ubaa8 \ubaa9\ub85d",
    "\ubaa8\ub4e0 \uba54\ubaa8",
    "\uc5ec\ud0dc\uae4c\uc9c0 \uc800\uc7a5\ub41c \uba54\ubaa8",
    "\uc800\uc7a5\ub41c \uba54\ubaa8\ub4e4",
    "\uc804\uccb4 \ubaa9\ub85d",
    "\uba54\ubaa8 \ubaa9\ub85d",
    "\uc804\uccb4 \uc800\uc7a5 \ud56d\ubaa9",
)
LIST_RECENT_PHRASES = (
    "\ucd5c\uadfc \uc800\uc7a5\ub41c \ud56d\ubaa9",
)
LIST_DISCOURSE_PREFIXES = (
    "\uc544\ub2c8 \uadf8\uac70 \ub9d0\uace0",
    "\uadf8\uac70 \ub9d0\uace0",
    "\uc544\ub2c8",
)
TECHNICAL_NOTE_KEYWORDS = (
    "fastapi",
    "sqlite",
    "image_file",
    "message",
    "note",
    "ai_analysis",
    "tag",
    "note_tag",
    "conversation_state",
    "note_revision",
    "webhook",
    "ocr",
    "nim",
    "ocr_text",
    "summary",
    "image_type",
    "confidence",
    "schema",
    "pipeline",
    "table",
    "column",
    "컬럼",
    "테이블",
    "파이프라인",
    "구조",
)


@dataclass(slots=True)
class RouterConfig:
    allowed_user_ids: set[int]


@dataclass(slots=True)
class CommandIntent:
    name: str
    query: str | None = None
    note_id: str | None = None
    old_text: str | None = None
    new_text: str | None = None


class SafeTelegramSender:
    def __init__(self, telegram_client: TelegramClient, reply_failure_callback=None) -> None:
        self.telegram_client = telegram_client
        self.reply_failure_callback = reply_failure_callback

    def send_message(self, chat_id: int | str, text: str) -> bool:
        try:
            sent = self.telegram_client.send_message(chat_id, text)
        except Exception:
            logger.warning(
                "Telegram send_message raised chat_id=%s",
                chat_id,
                exc_info=True,
            )
            self._mark_reply_failed_if_processing()
            return False
        if sent is False:
            logger.warning("Telegram send_message failed chat_id=%s", chat_id)
            self._mark_reply_failed_if_processing()
            return False
        return True

    def _mark_reply_failed_if_processing(self) -> None:
        if self.reply_failure_callback is None:
            return
        message_id = _CURRENT_REPLY_MESSAGE_ID.get()
        if message_id is not None:
            self.reply_failure_callback(message_id)


class UpdateRouter:
    def __init__(
        self,
        config: RouterConfig,
        note_manager: NoteManager,
        nim_provider: NvidiaNIMProvider,
        telegram_client: TelegramClient,
        image_archive: ImageArchive,
    ) -> None:
        self.config = config
        self.note_manager = note_manager
        self.nim_provider = nim_provider
        self.telegram_client = SafeTelegramSender(telegram_client, self._mark_reply_failed)
        self.image_archive = image_archive

    def _mark_reply_failed(self, message_id: str) -> None:
        self.note_manager.mark_reply_failed(message_id)

    def handle_update(
        self,
        update: TelegramUpdate,
        background_tasks: BackgroundTasks | None = None,
    ) -> WebhookResult:
        message = update.message
        if message is None:
            logger.info("Ignored Telegram update without message body")
            return WebhookResult(status="ignored", detail="empty_update")

        user_id = message.from_user.id
        if user_id not in self.config.allowed_user_ids:
            logger.warning("Ignored unauthorized Telegram user_id=%s", user_id)
            return WebhookResult(status="ignored", detail="unauthorized_user")

        logger.info(
            "Received Telegram message_id=%s chat_id=%s sender_id=%s",
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

        if message.photo:
            return self._handle_photo_message(message, background_tasks)

        if message.text:
            pending_photo_review = self.note_manager.find_pending_photo_review(str(message.chat.id))
            if pending_photo_review is not None:
                return self._handle_photo_review_reply(
                    message,
                    pending_photo_review,
                    background_tasks,
                )
            pending_merge_proposal = self.note_manager.find_pending_merge_proposal(str(message.chat.id))
            merge_reply_action = self._classify_merge_reply(message.text)
            if pending_merge_proposal is not None and merge_reply_action is not None:
                return self._handle_merge_proposal_reply(
                    message,
                    pending_merge_proposal,
                    merge_reply_action,
                    background_tasks,
                )
            return self._handle_text_message(message, background_tasks)

        logger.info("Ignored unsupported Telegram message message_id=%s", message.message_id)
        return WebhookResult(status="ignored", detail="unsupported_message")

    def _handle_text_message(
        self,
        message: TelegramMessage,
        background_tasks: BackgroundTasks | None,
    ) -> WebhookResult:
        stored_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(message.from_user.id),
            raw_text=message.text or "",
        )
        message_id = self._store_message_or_ignore(stored_message)
        if message_id is None:
            return WebhookResult(status="ignored", detail="duplicate_message")
        logger.info("Stored raw text message message_id=%s db_message_id=%s", message.message_id, message_id)
        self._send_message_safely(message.chat.id, "수신 완료.", purpose="text_ack")

        if background_tasks is not None:
            background_tasks.add_task(
                self.process_message,
                message_id,
                message.chat.id,
                message.text or "",
                message.from_user.id,
            )
            return WebhookResult(status="accepted")

        self.process_message(message_id, message.chat.id, message.text or "", message.from_user.id)
        return WebhookResult(status="processed")

    def _handle_photo_message(
        self,
        message: TelegramMessage,
        background_tasks: BackgroundTasks | None,
    ) -> WebhookResult:
        largest_photo = max(
            message.photo or [],
            key=lambda item: (item.file_size or 0, item.width * item.height),
        )
        stored_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(message.from_user.id),
            raw_text=message.caption or "",
            content_type="photo",
        )
        message_id = self._store_message_or_ignore(stored_message)
        if message_id is None:
            return WebhookResult(status="ignored", detail="duplicate_message")
        logger.info("Stored raw photo message message_id=%s db_message_id=%s", message.message_id, message_id)
        self._send_message_safely(message.chat.id, "사진 수신 완료.", purpose="photo_ack")

        if background_tasks is not None:
            background_tasks.add_task(
                self.process_photo_message,
                message_id,
                message.chat.id,
                message.from_user.id,
                message.message_id,
                largest_photo,
                message.caption,
            )
            return WebhookResult(status="accepted")

        self.process_photo_message(
            message_id,
            message.chat.id,
            message.from_user.id,
            message.message_id,
            largest_photo,
            message.caption,
        )
        return WebhookResult(status="processed")

    def _handle_photo_review_reply(
        self,
        message: TelegramMessage,
        pending_photo_review: dict,
        background_tasks: BackgroundTasks | None,
    ) -> WebhookResult:
        reply_text = (message.text or "").strip()
        clarification_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(message.from_user.id),
            raw_text=reply_text,
        )
        clarification_message_id = self._store_message_or_ignore(clarification_message)
        if clarification_message_id is None:
            return WebhookResult(status="ignored", detail="duplicate_message")
        self.note_manager.mark_processed(clarification_message_id)
        logger.info(
            "Stored photo clarification reply message_id=%s db_message_id=%s target_photo_db_message_id=%s",
            message.message_id,
            clarification_message_id,
            pending_photo_review["id"],
        )

        normalized_reply = self._normalize_text(reply_text)
        if normalized_reply in PHOTO_ONLY_KEYWORDS:
            self.note_manager.mark_processed(pending_photo_review["id"])
            self.telegram_client.send_message(message.chat.id, "일반 사진으로만 보관할게.")
            logger.info("Marked pending photo as general photo db_message_id=%s", pending_photo_review["id"])
            return WebhookResult(status="processed", detail="photo_review_completed")

        self.telegram_client.send_message(message.chat.id, "메모로 다시 처리해볼게.")
        if background_tasks is not None:
            background_tasks.add_task(
                self.process_message,
                pending_photo_review["id"],
                message.chat.id,
                reply_text,
                message.from_user.id,
            )
            return WebhookResult(status="accepted", detail="photo_review_retry")

        self.process_message(
            pending_photo_review["id"],
            message.chat.id,
            reply_text,
            message.from_user.id,
        )
        return WebhookResult(status="processed", detail="photo_review_retry")

    def _handle_merge_proposal_reply(
        self,
        message: TelegramMessage,
        pending_merge_proposal: dict,
        reply_action: str,
        background_tasks: BackgroundTasks | None,
    ) -> WebhookResult:
        reply_text = (message.text or "").strip()
        stored_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(message.from_user.id),
            raw_text=reply_text,
        )
        message_id = self._store_message_or_ignore(stored_message)
        if message_id is None:
            return WebhookResult(status="ignored", detail="duplicate_message")

        logger.info(
            "Stored merge proposal reply message_id=%s db_message_id=%s proposal_id=%s action=%s",
            message.message_id,
            message_id,
            pending_merge_proposal["id"],
            reply_action,
        )
        self.telegram_client.send_message(message.chat.id, "수신 완료.")

        if background_tasks is not None:
            background_tasks.add_task(
                self.process_merge_proposal_reply,
                message_id,
                message.chat.id,
                pending_merge_proposal,
                reply_action,
            )
            return WebhookResult(status="accepted", detail="merge_proposal_reply")

        self.process_merge_proposal_reply(
            message_id,
            message.chat.id,
            pending_merge_proposal,
            reply_action,
        )
        return WebhookResult(status="processed", detail="merge_proposal_reply")

    def _handle_note_search_message(
        self,
        message: TelegramMessage,
        background_tasks: BackgroundTasks | None,
    ) -> WebhookResult:
        stored_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(message.from_user.id),
            raw_text=message.text or "",
            content_type="search",
        )
        message_id = self._store_message_or_ignore(stored_message)
        if message_id is None:
            return WebhookResult(status="ignored", detail="duplicate_message")

        self.telegram_client.send_message(message.chat.id, "수신 완료.")
        if background_tasks is not None:
            background_tasks.add_task(
                self.process_note_search,
                message_id,
                message.chat.id,
                message.text or "",
            )
            return WebhookResult(status="accepted", detail="note_search")

        self.process_note_search(message_id, message.chat.id, message.text or "")
        return WebhookResult(status="processed", detail="note_search")

    def _handle_note_count_message(self, message: TelegramMessage) -> WebhookResult:
        stored_message = StoredMessage(
            id=str(uuid.uuid4()),
            telegram_message_id=str(message.message_id),
            chat_id=str(message.chat.id),
            sender_id=str(message.from_user.id),
            raw_text=message.text or "",
            content_type="query",
        )
        message_id = self._store_message_or_ignore(stored_message)
        if message_id is None:
            return WebhookResult(status="ignored", detail="duplicate_message")

        self.telegram_client.send_message(message.chat.id, "수신 완료.")
        note_count = self.note_manager.count_notes()
        self.note_manager.mark_processed(message_id)
        suffix = "개야." if note_count != 1 else "개야."
        self.telegram_client.send_message(
            message.chat.id,
            f"지금 저장된 메모는 {note_count}{suffix}",
        )
        return WebhookResult(status="processed", detail="note_count")

    def process_message(
        self,
        message_id: str,
        chat_id: int | str,
        text: str,
        sender_id: int | str | None = None,
    ) -> None:
        reply_token = _CURRENT_REPLY_MESSAGE_ID.set(message_id)
        try:
            resolved_sender_id = self._resolve_sender_id(message_id, sender_id)
            explicit_save = self._has_explicit_note_save_request(text)
            command = self._detect_direct_command(text)
            command_takes_priority = (
                command is not None
                and (
                    not explicit_save
                    or self._is_explicit_save_prefix_delete_command(command)
                )
            )
            if command_takes_priority and self._handle_direct_command(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=resolved_sender_id,
                text=text,
                command=command,
            ):
                return

            if not explicit_save and self._looks_like_technical_note_statement(text):
                logger.info(
                    "Technical note statement matched before AI route db_message_id=%s llm_called=false",
                    message_id,
                )
                self._handle_technical_note_statement(
                    message_id=message_id,
                    chat_id=chat_id,
                    sender_id=resolved_sender_id,
                    text=text,
                )
                return

            if not explicit_save and self._looks_like_meta_command(text):
                self.note_manager.mark_processed(message_id)
                self._send_result_message(
                    message_id,
                    chat_id,
                    "\uba54\ubaa8\ub85c \uc800\uc7a5\ud558\uc9c4 \uc54a\uc558\uc5b4.",
                    purpose="meta_command_ignored",
                )
                logger.info(
                    "Blocked meta command before AI route db_message_id=%s llm_called=false",
                    message_id,
                )
                return

            prepared_text = self._prepare_text_for_note_save(text)
            if explicit_save:
                duplicate_note = self.note_manager.find_recent_note_by_body(
                    chat_id=str(chat_id),
                    sender_id=resolved_sender_id,
                    body=prepared_text,
                    within_minutes=10,
                )
                if duplicate_note is not None:
                    self.note_manager.mark_processed(message_id)
                    self._remember_note_reference(
                        chat_id=str(chat_id),
                        sender_id=resolved_sender_id,
                        note_id=str(duplicate_note.get("id")),
                    )
                    self._send_result_message(
                        message_id,
                        chat_id,
                        "\uc774\ubbf8 \uac19\uc740 \uba54\ubaa8\uac00 \ucd5c\uadfc\uc5d0 \uc800\uc7a5\ub3fc \uc788\uc5b4. \uc0c8\ub85c \ucd94\uac00\ud558\uc9c4 \uc54a\uc558\uc5b4.",
                        purpose="duplicate_note_body",
                    )
                    logger.info(
                        "Ignored duplicate explicit note body db_message_id=%s note_id=%s",
                        message_id,
                        duplicate_note.get("id"),
                    )
                    return

            logger.info(
                "Starting text route db_message_id=%s router_model=%s",
                message_id,
                self.nim_provider.router_model,
            )
            existing_tags = self.note_manager.list_tags()
            candidate_notes = self.note_manager.search_notes(prepared_text, limit=5)
            conversation_context = self.note_manager.recent_chat_messages(
                str(chat_id),
                limit=8,
                max_age_minutes=30,
                exclude_message_id=message_id,
            )
            route = self.nim_provider.route_text(
                prepared_text,
                candidate_notes=candidate_notes,
                conversation_context=conversation_context,
            )
            logger.info(
                "Finished text route db_message_id=%s route=%s confidence=%.2f reason=%s",
                message_id,
                route.route,
                route.confidence,
                route.reason,
            )

            effective_route = "create" if explicit_save else route.route

            if route.tool_name and not explicit_save:
                self._execute_tool_request(
                    message_id=message_id,
                    chat_id=chat_id,
                    original_text=text,
                    analysis=route,
                )
                return

            if not explicit_save and effective_route in {"create", "append"} and self._looks_like_meta_command(text):
                self.note_manager.mark_processed(message_id)
                self._send_result_message(
                    message_id,
                    chat_id,
                    "\uba54\ubaa8\ub85c \uc800\uc7a5\ud558\uc9c4 \uc54a\uc558\uc5b4.",
                    purpose="meta_command_ai_route_blocked",
                )
                logger.info(
                    "Blocked meta command after AI route db_message_id=%s route=%s",
                    message_id,
                    route.route,
                )
                return

            if (
                effective_route == "ignore"
            ) and not self._should_retry_as_contextual_query(
                text=text,
                conversation_context=conversation_context,
            ):
                self.note_manager.mark_processed(message_id)
                logger.info(
                    "Ignored text after AI route db_message_id=%s reason=%s",
                    message_id,
                    route.reason,
                )
                self.telegram_client.send_message(
                    chat_id,
                    "메모로 저장하진 않았어.",
                )
                logger.info("Ignored non-note text db_message_id=%s", message_id)
                return

            if (
                effective_route == "ignore"
            ) and self._should_retry_as_contextual_query(
                text=text,
                conversation_context=conversation_context,
            ):
                logger.info(
                    "Retrying ignored text as contextual agent query db_message_id=%s text=%r",
                    message_id,
                    text,
                )
                self.process_agent_query(message_id, chat_id, text)
                return

            existing_note_id = None
            action = "append" if effective_route == "append" else "create"
            if action == "append" and route.target_note_id:
                existing_note = self.note_manager.get_note(route.target_note_id)
                if existing_note is not None:
                    existing_note_id = route.target_note_id

            try:
                analysis = self.nim_provider.analyze_text(
                    prepared_text,
                    existing_tags=existing_tags,
                    candidate_notes=candidate_notes,
                    conversation_context=conversation_context,
                    action=action,
                    target_note_id=existing_note_id,
                )
                if explicit_save and (analysis.is_note is False or analysis.action == "ignore"):
                    analysis = self._build_explicit_save_fallback_analysis(
                        prepared_text,
                        action=action,
                        target_note_id=existing_note_id,
                    )
                elif explicit_save:
                    analysis = self._normalize_explicit_save_analysis(analysis, prepared_text)
            except NIMProviderError:
                logger.exception(
                    "Failed to generate note metadata; saving degraded note db_message_id=%s",
                    message_id,
                )
                analysis = self.nim_provider.build_fallback_note_analysis(
                    prepared_text,
                    action=action,
                    target_note_id=existing_note_id,
                )

            saved_note = self.note_manager.store_analysis_and_note(
                message_id=message_id,
                provider_name="nvidia_nim",
                model_name=self.nim_provider.text_model,
                source_text=prepared_text,
                analysis=analysis,
                existing_note_id=existing_note_id,
            )
            logger.info("Stored note and AI analysis db_message_id=%s", message_id)
            self._remember_note_reference(
                chat_id=str(chat_id),
                sender_id=resolved_sender_id,
                note_id=saved_note.note_id,
            )
            response_text = self._build_note_saved_message(
                analysis.title,
                analysis.summary,
                saved_note.action,
                saved_note.notion_status,
            )
            self._send_result_message(message_id, chat_id, response_text, purpose="text_completion")
            logger.info("Sent text completion message chat_id=%s db_message_id=%s", chat_id, message_id)
        except NIMProviderError as exc:
            logger.exception("Failed to process Telegram text db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                f"AI 분석이 너무 오래 걸리거나 실패했어. ({exc})",
                purpose="text_ai_failed",
            )
        except Exception:
            logger.exception("Failed to process Telegram text db_message_id=%s", message_id)
            self.note_manager.mark_action_failed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "메시지는 저장했지만 AI 분석에는 실패했어. 나중에 다시 시도해줘.",
                purpose="text_action_failed",
            )
        finally:
            _CURRENT_REPLY_MESSAGE_ID.reset(reply_token)

    def _resolve_sender_id(self, message_id: str, sender_id: int | str | None) -> str:
        if sender_id is not None:
            return str(sender_id)
        message = self.note_manager.get_message(message_id)
        if message is None:
            return "0"
        return str(message.get("sender_id") or "0")

    def _detect_direct_command(self, text: str | None) -> CommandIntent | None:
        if not text:
            return None
        normalized = self._normalize_text(text)
        if not normalized:
            return None

        slash_command = self._extract_slash_command(text)
        if slash_command is not None:
            command_name, argument = slash_command
            if command_name in SLASH_COMMAND_NAMES:
                return CommandIntent(name=f"slash_{command_name}", query=argument)

        if any(hint == normalized for hint in DELETE_CONFIRM_HINTS):
            return CommandIntent(name="delete_confirm")

        list_mode = self._list_command_mode(normalized)
        if list_mode is not None:
            return CommandIntent(name="recent_notes", query=list_mode)

        if self._looks_like_numbered_select_request(normalized):
            return CommandIntent(name="select_note")

        if self._looks_like_numbered_read_request(normalized):
            return CommandIntent(name="read_last_note")

        if self._detect_fast_read_intent(text) == "read_last_note":
            return CommandIntent(name="read_last_note")

        if self._is_note_count_request(text):
            return CommandIntent(name="count_notes")

        if self._looks_like_duplicate_delete_request(normalized):
            return CommandIntent(name="delete_duplicates_request")

        if self._extract_selection_index(normalized) is not None and "\uc218\uc815" in normalized:
            return CommandIntent(name="correct_last_note")

        correction = self._extract_correction_intent(text)
        if correction is not None:
            return correction

        if self._looks_like_delete_request(normalized):
            return CommandIntent(name="delete_request")

        if self._looks_like_recent_list_request(normalized):
            return CommandIntent(name="recent_notes")

        if self._looks_like_search_request(normalized):
            return CommandIntent(
                name="search_notes",
                query=self._clean_search_query(text),
            )
        return None

    def _handle_direct_command(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
        command: CommandIntent,
    ) -> bool:
        logger.info(
            "Direct command gate db_message_id=%s intent=%s llm_called=false",
            message_id,
            command.name,
        )
        if command.name.startswith("slash_"):
            return self._handle_slash_command(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                command_name=command.name.removeprefix("slash_"),
                argument=command.query or "",
            )

        if command.name == "read_last_note":
            self._clear_correction_state(chat_id=str(chat_id), sender_id=sender_id)
            self._handle_fast_read_last_note(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                text=text,
            )
            return True

        if command.name == "select_note":
            return self._handle_note_selection(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                text=text,
            )

        if command.name == "count_notes":
            self._clear_correction_state(chat_id=str(chat_id), sender_id=sender_id)
            note_count = self.note_manager.count_notes()
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                f"\uc9c0\uae08 \uc800\uc7a5\ub41c \uba54\ubaa8\ub294 {note_count}\uac1c\uc57c.",
                purpose="count_notes",
            )
            return True

        if command.name == "recent_notes":
            self._clear_correction_state(chat_id=str(chat_id), sender_id=sender_id)
            note_limit = 5
            if command.query == "all":
                note_limit = max(self.note_manager.count_notes(), 1)
            notes = self.note_manager.recent_notes(limit=note_limit)
            self._remember_list_results(
                chat_id=str(chat_id),
                sender_id=sender_id,
                note_ids=[str(note.get("id")) for note in notes if note.get("id")],
            )
            self._remember_search_results(
                chat_id=str(chat_id),
                sender_id=sender_id,
                note_ids=[str(note.get("id")) for note in notes if note.get("id")],
                query="recent_notes",
            )
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                self._build_recent_notes_plain_message(notes),
                purpose="recent_notes",
            )
            return True

        if command.name == "search_notes":
            self._clear_correction_state(chat_id=str(chat_id), sender_id=sender_id)
            self.process_note_search(
                message_id,
                chat_id,
                command.query or text,
                sender_id,
            )
            return True

        if command.name == "delete_request":
            return self._handle_delete_request(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                text=text,
            )

        if command.name == "delete_duplicates_request":
            return self._handle_duplicate_delete_request(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
            )

        if command.name == "delete_confirm":
            return self._handle_delete_confirm(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
            )

        if command.name == "correct_last_note":
            return self._handle_note_correction(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                text=text,
                old_text=command.old_text or "",
                new_text=command.new_text or "",
            )

        return False

    def _handle_slash_command(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        command_name: str,
        argument: str,
    ) -> bool:
        argument = argument.strip()
        if command_name == "new":
            return self._handle_slash_new_note(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                text=argument,
            )
        if command_name == "list":
            return self._handle_slash_list(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                query=argument,
            )
        if command_name in {"show", "raw"}:
            return self._handle_slash_show(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                text=argument,
            )
        if command_name == "delete":
            return self._handle_delete_request(
                message_id=message_id,
                chat_id=chat_id,
                sender_id=sender_id,
                text=argument,
            )
        if command_name in {"add", "fix", "dedupe"}:
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                self._build_pending_action_not_ready_message(command_name),
                purpose=f"slash_{command_name}_not_ready",
            )
            return True
        if command_name == "help":
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                self._build_slash_help_message(argument),
                purpose="slash_help",
            )
            return True
        return False

    def _handle_slash_new_note(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
    ) -> bool:
        if not text:
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "새 메모 내용을 같이 보내줘. 예: /new 오늘 회의에서 v1 테스트를 먼저 하기로 함",
                purpose="slash_new_missing_body",
            )
            return True

        existing_tags = self.note_manager.list_tags()
        candidate_notes = self.note_manager.search_notes(text, limit=5)
        conversation_context = self.note_manager.recent_chat_messages(
            str(chat_id),
            limit=8,
            max_age_minutes=30,
            exclude_message_id=message_id,
        )
        try:
            analysis = self.nim_provider.analyze_text(
                text,
                existing_tags=existing_tags,
                candidate_notes=candidate_notes,
                conversation_context=conversation_context,
                action="create",
                target_note_id=None,
            )
            if analysis.is_note is False or analysis.action == "ignore":
                analysis = self._build_explicit_save_fallback_analysis(
                    text,
                    action="create",
                    target_note_id=None,
                )
            else:
                analysis = self._normalize_explicit_save_analysis(analysis, text)
        except NIMProviderError:
            logger.exception("Failed to generate slash /new note metadata db_message_id=%s", message_id)
            analysis = self.nim_provider.build_fallback_note_analysis(
                text,
                action="create",
                target_note_id=None,
            )

        saved_note = self.note_manager.store_analysis_and_note(
            message_id=message_id,
            provider_name="nvidia_nim",
            model_name=self.nim_provider.text_model,
            source_text=text,
            analysis=analysis,
            existing_note_id=None,
        )
        self._remember_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            note_id=saved_note.note_id,
        )
        self.note_manager.set_conversation_state(
            chat_id=str(chat_id),
            sender_id=sender_id,
            key="last_selected_note_id",
            value={"note_id": saved_note.note_id},
        )
        self._send_result_message(
            message_id,
            chat_id,
            self._build_note_saved_message(
                analysis.title,
                analysis.summary,
                saved_note.action,
                saved_note.notion_status,
            ),
            purpose="slash_new_saved",
        )
        return True

    def _handle_slash_list(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        query: str,
    ) -> bool:
        if query:
            notes = self.note_manager.search_notes(query, limit=10)
            self._remember_search_results(
                chat_id=str(chat_id),
                sender_id=sender_id,
                note_ids=[str(note.get("id")) for note in notes if note.get("id")],
                query=query,
            )
            message = self._build_search_results_message(notes) if notes else "관련 메모를 찾지 못했어."
            purpose = "slash_list_search"
        else:
            notes = self.note_manager.recent_notes(limit=10)
            self._remember_list_results(
                chat_id=str(chat_id),
                sender_id=sender_id,
                note_ids=[str(note.get("id")) for note in notes if note.get("id")],
            )
            message = self._build_recent_notes_plain_message(notes)
            purpose = "slash_list_recent"
        self.note_manager.mark_processed(message_id)
        self._send_result_message(message_id, chat_id, message, purpose=purpose)
        return True

    def _handle_slash_show(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
    ) -> bool:
        read_text = text or "방금 메모 원문 보여줘"
        self._handle_fast_read_last_note(
            message_id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            text=read_text,
        )
        return True

    def _handle_delete_request(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
    ) -> bool:
        resolved = self._resolve_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            text=text,
            prefer_image=False,
        )
        note = resolved.get("note")
        candidates = resolved.get("candidates") or []

        if candidates:
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                self._build_reference_choice_message(
                    action_label="\uc0ad\uc81c",
                    notes=candidates,
                ),
                purpose="delete_reference_choice",
            )
            return True

        if not isinstance(note, dict):
            self.note_manager.mark_action_failed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "\uc0ad\uc81c\ud560 \uba54\ubaa8\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc5b4. \uc81c\ubaa9\uc774\ub098 \uac80\uc0c9 \uacb0\uacfc \ubc88\ud638\ub97c \ud568\uaed8 \ubcf4\ub0b4\uc918.",
                purpose="delete_target_missing",
            )
            return True

        self.note_manager.set_conversation_state(
            chat_id=str(chat_id),
            sender_id=sender_id,
            key="pending_delete_note_id",
            value={"note_id": str(note.get("id"))},
        )
        self.note_manager.mark_processed(message_id)
        self._send_result_message(
            message_id,
            chat_id,
            self._build_delete_confirmation_message(note),
            purpose="delete_confirmation",
        )
        return True

    def _handle_duplicate_delete_request(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
    ) -> bool:
        duplicate_groups = self.note_manager.find_duplicate_notes_by_body(
            chat_id=str(chat_id),
            sender_id=sender_id,
        )
        if not duplicate_groups:
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "\uc911\ubcf5\ub41c \uba54\ubaa8\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc5b4. \uc9c0\uae08\uc740 \ubcf8\ubb38\uc774 \uc815\ud655\ud788 \uac19\uc740 \uba54\ubaa8\ub9cc \uc911\ubcf5\uc73c\ub85c \ubcf4\uace0 \uc0ad\uc81c\ud574.",
                purpose="duplicate_delete_none",
            )
            return True

        deleted_count = 0
        kept_titles: list[str] = []
        for group in duplicate_groups:
            keep_note = group.get("keep_note") or {}
            keep_title = str(keep_note.get("title") or "\uc81c\ubaa9 \uc5c6\uc74c").strip()
            if keep_title:
                kept_titles.append(keep_title)
            for duplicate_note in group.get("duplicate_notes") or []:
                note_id = duplicate_note.get("id")
                if not note_id:
                    continue
                self.note_manager.delete_note(str(note_id), reason="duplicate_body_cleanup")
                deleted_count += 1

        self.note_manager.mark_processed(message_id)
        self._clear_correction_state(chat_id=str(chat_id), sender_id=sender_id)
        lines = [
            f"\uc911\ubcf5 \uba54\ubaa8 {deleted_count}\uac1c\ub97c \uc0ad\uc81c\ud588\uc5b4.",
            "\ubcf8\ubb38\uc774 \uc815\ud655\ud788 \uac19\uc740 \uba54\ubaa8\ub9cc \ub300\uc0c1\uc73c\ub85c \ud588\uace0, \uac01 \uadf8\ub8f9\uc758 \ucd5c\uc2e0 \uba54\ubaa8\ub294 \ub0a8\uacbc\uc5b4.",
        ]
        if kept_titles:
            lines.append("\ub0a8\uae34 \uba54\ubaa8: " + ", ".join(kept_titles[:3]))
        self._send_result_message(
            message_id,
            chat_id,
            "\n".join(lines),
            purpose="duplicate_delete_completed",
        )
        return True

    def _handle_note_selection(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
    ) -> bool:
        resolved = self._resolve_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            text=text,
            prefer_image=True,
        )
        note = resolved.get("note")
        if not isinstance(note, dict):
            self.note_manager.mark_action_failed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "\ud574\ub2f9 \ubc88\ud638\uc758 \uba54\ubaa8\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc5b4. \ucd5c\uadfc \uc800\uc7a5\ub41c \ud56d\ubaa9\uc744 \ub2e4\uc2dc \ubcf4\uc5ec\uc904\uac8c.",
                purpose="select_note_missing",
            )
            notes = self.note_manager.recent_notes(limit=5)
            self._remember_list_results(
                chat_id=str(chat_id),
                sender_id=sender_id,
                note_ids=[str(item.get("id")) for item in notes if item.get("id")],
            )
            self._send_result_message(
                message_id,
                chat_id,
                self._build_recent_notes_plain_message(notes),
                purpose="select_note_recent_fallback",
            )
            return True

        self.note_manager.set_conversation_state(
            chat_id=str(chat_id),
            sender_id=sender_id,
            key="last_selected_note_id",
            value={"note_id": str(note.get("id"))},
        )
        self._remember_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            note_id=str(note.get("id")),
        )
        self.note_manager.mark_processed(message_id)
        title = str(note.get("title") or "\uc81c\ubaa9 \uc5c6\uc74c").strip()
        self._send_result_message(
            message_id,
            chat_id,
            f"1\ubc88 \uba54\ubaa8\ub97c \uc120\ud0dd\ud588\uc5b4.\n\n\uc81c\ubaa9: {title}\n\n\uc6d0\ubb38 \ubcf4\uae30, \uc218\uc815, \uc0ad\uc81c \uc911 \ubb50\ub97c \ud560\uae4c?",
            purpose="select_note",
        )
        return True

    def _handle_technical_note_statement(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
    ) -> None:
        analysis = self._build_technical_note_analysis(text)
        saved_note = self.note_manager.store_analysis_and_note(
            message_id=message_id,
            provider_name="deterministic_gate",
            model_name="technical_note_statement",
            source_text=text,
            analysis=analysis,
        )
        self._remember_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            note_id=saved_note.note_id,
        )
        self.note_manager.set_conversation_state(
            chat_id=str(chat_id),
            sender_id=sender_id,
            key="last_selected_note_id",
            value={"note_id": saved_note.note_id},
        )
        response_text = self._build_note_saved_message(
            analysis.title,
            analysis.summary,
            saved_note.action,
            saved_note.notion_status,
        )
        self._send_result_message(
            message_id,
            chat_id,
            response_text,
            purpose="technical_note_saved",
        )

    def _handle_delete_confirm(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
    ) -> bool:
        pending = self.note_manager.get_conversation_state(
            chat_id=str(chat_id),
            sender_id=sender_id,
            key="pending_delete_note_id",
        )
        note_id = pending.get("note_id") if isinstance(pending, dict) else None
        if not note_id:
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "\uc0ad\uc81c \ub300\uae30 \uc911\uc778 \uba54\ubaa8\uac00 \uc5c6\uc5b4.",
                purpose="delete_confirm_no_pending",
            )
            return True
        note = self.note_manager.get_note_with_source(str(note_id)) if note_id else None
        if note is None:
            deleted_note = self.note_manager.get_note_any_status(str(note_id))
            self.note_manager.clear_conversation_state(
                chat_id=str(chat_id),
                sender_id=sender_id,
                key="pending_delete_note_id",
            )
            self.note_manager.mark_processed(message_id)
            if deleted_note is not None and deleted_note.get("deleted_at"):
                self._send_result_message(
                    message_id,
                    chat_id,
                    "\uc774 \uba54\ubaa8\ub294 \uc774\ubbf8 \uc0ad\uc81c\ub418\uc5b4 \uc788\uc5b4.",
                    purpose="delete_confirm_already_deleted",
                )
                return True
            self._send_result_message(
                message_id,
                chat_id,
                "\uc9c0\uae08 \ud655\uc778 \ub300\uae30 \uc911\uc778 \uc0ad\uc81c \ub300\uc0c1\uc744 \ucc3e\uc9c0 \ubabb\ud588\uc5b4.",
                purpose="delete_confirm_missing_target",
            )
            return True

        self.note_manager.delete_note(str(note.get("id")), reason="user_confirmed_delete")
        self.note_manager.clear_conversation_state(
            chat_id=str(chat_id),
            sender_id=sender_id,
            key="pending_delete_note_id",
        )
        self.note_manager.mark_processed(message_id)
        self._send_result_message(
            message_id,
            chat_id,
            self._build_delete_completed_message(note),
            purpose="delete_completed",
        )
        return True

    def _handle_note_correction(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
        old_text: str,
        new_text: str,
    ) -> bool:
        if not old_text:
            self.note_manager.mark_action_failed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "\uc218\uc815\ud560 \ubb38\uad6c\ub97c \uc815\ud655\ud788 \uc54c \uc218 \uc5c6\uc5b4. 'A\uac00 \uc544\ub2c8\ub77c B\uc57c. \uc218\uc815\ud574\uc918.' \ud615\uc2dd\uc73c\ub85c \ubcf4\ub0b4\uc918.",
                purpose="correction_parse_failed",
            )
            return True

        resolved = self._resolve_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            text=text,
            prefer_image=True,
        )
        note = resolved.get("note")
        candidates = resolved.get("candidates") or []
        if candidates:
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                self._build_reference_choice_message(
                    action_label="\uc218\uc815",
                    notes=candidates,
                ),
                purpose="correction_reference_choice",
            )
            return True

        if not isinstance(note, dict):
            self.note_manager.mark_action_failed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                "\uc218\uc815\ud560 \uba54\ubaa8\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc5b4.",
                purpose="correction_target_missing",
            )
            return True

        current_title = str(note.get("title") or "").strip()
        current_summary = str(note.get("summary") or "").strip()
        current_body = str(note.get("image_ocr_text") or note.get("body") or "").strip()
        if old_text not in current_title and old_text not in current_summary and old_text not in current_body:
            self.note_manager.mark_action_failed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                self._build_correction_miss_message(current_body),
                purpose="correction_old_text_missing",
            )
            return True

        updated_title = self._replace_note_fragment(current_title, old_text, new_text)
        updated_summary = self._replace_note_fragment(current_summary, old_text, new_text)
        updated_body = self._replace_note_fragment(current_body, old_text, new_text)
        updated_note = self.note_manager.replace_note_text_fields(
            note_id=str(note.get("id")),
            new_title=updated_title or current_title,
            new_summary=updated_summary or current_summary,
            new_body=updated_body,
            reason="user_correction",
        )
        self.note_manager.mark_processed(message_id)
        self._remember_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            note_id=str(note.get("id")),
        )
        self.note_manager.set_conversation_state(
            chat_id=str(chat_id),
            sender_id=sender_id,
            key="last_selected_note_id",
            value={"note_id": str(note.get("id"))},
        )
        self._send_result_message(
            message_id,
            chat_id,
            self._build_correction_success_message(
                title=str((updated_note or note).get("title") or ""),
                old_text=old_text,
                new_text=new_text,
                current_body=updated_body,
            ),
            purpose="correction_success",
        )
        return True

    def _resolve_note_reference(
        self,
        *,
        chat_id: str,
        sender_id: str,
        text: str,
        prefer_image: bool,
    ) -> dict:
        normalized = self._normalize_text(text)
        selection_index = self._extract_selection_index(normalized)
        list_note_ids = self._state_note_ids(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_list_results",
        )
        search_note_ids = self._state_note_ids(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_search_results",
        )

        if selection_index is not None:
            for note_ids in (list_note_ids, search_note_ids):
                if 0 <= selection_index < len(note_ids):
                    note = self.note_manager.get_note_with_source(note_ids[selection_index])
                    if note is not None:
                        return {"note": note}
            return {"missing_number": True}

        last_selected = self.note_manager.get_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_selected_note_id",
        )
        selected_note_id = None
        if isinstance(last_selected, dict):
            selected_note_id = last_selected.get("note_id")
        elif isinstance(last_selected, str):
            selected_note_id = last_selected
        if selected_note_id:
            note = self.note_manager.get_note_with_source(str(selected_note_id))
            if note is not None:
                return {"note": note}

        note_ids = search_note_ids or list_note_ids
        if note_ids:
            if len(note_ids) == 1:
                note = self.note_manager.get_note_with_source(note_ids[0])
                if note is not None:
                    return {"note": note}
            if any(hint in normalized for hint in FAST_READ_REFERENCE_HINTS):
                candidates = []
                for note_id in note_ids[:5]:
                    candidate = self.note_manager.get_note_with_source(note_id)
                    if candidate is not None:
                        candidates.append(candidate)
                if candidates:
                    return {"candidates": candidates}

        last_artifact = self.note_manager.get_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_artifact_note_id",
        )
        artifact_note_id = None
        if isinstance(last_artifact, dict):
            artifact_note_id = last_artifact.get("note_id")
        elif isinstance(last_artifact, str):
            artifact_note_id = last_artifact
        if artifact_note_id:
            note = self.note_manager.get_note_with_source(str(artifact_note_id))
            if note is not None:
                return {"note": note}

        last_image = self.note_manager.get_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_image_note_id",
        )
        image_note_id = None
        if isinstance(last_image, dict):
            image_note_id = last_image.get("note_id")
        elif isinstance(last_image, str):
            image_note_id = last_image
        if image_note_id:
            note = self.note_manager.get_note_with_source(str(image_note_id))
            if note is not None:
                return {"note": note}

        fallback = self.note_manager.get_last_note_for_chat(
            chat_id,
            prefer_image=prefer_image,
            within_minutes=30,
        )
        if fallback is not None:
            return {"note": fallback}
        return {}

    def _resolve_correction_reference(
        self,
        *,
        chat_id: str,
        sender_id: str,
        text: str,
        old_text: str,
        prefer_image: bool,
    ) -> dict:
        normalized = self._normalize_text(text)
        selection_index = self._extract_selection_index(normalized)
        if selection_index is not None:
            return self._resolve_note_reference(
                chat_id=chat_id,
                sender_id=sender_id,
                text=text,
                prefer_image=prefer_image,
            )

        selected = self.note_manager.get_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_selected_note_id",
        )
        selected_note_id = selected.get("note_id") if isinstance(selected, dict) else selected
        if selected_note_id:
            note = self.note_manager.get_note_with_source(str(selected_note_id))
            if note is not None:
                return {"note": note}

        for key in ("last_list_results", "last_search_results"):
            matches = []
            for note_id in self._state_note_ids(chat_id=chat_id, sender_id=sender_id, key=key):
                note = self.note_manager.get_note_with_source(note_id)
                body = str((note or {}).get("image_ocr_text") or (note or {}).get("body") or "")
                if note is not None and old_text and old_text in body:
                    matches.append(note)
            if len(matches) == 1:
                return {"note": matches[0]}
            if len(matches) > 1:
                return {"candidates": matches[:5]}

        return self._resolve_note_reference(
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            prefer_image=prefer_image,
        )

    def _state_note_ids(self, *, chat_id: str, sender_id: str, key: str) -> list[str]:
        state = self.note_manager.get_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key=key,
        )
        if not isinstance(state, dict):
            return []
        raw_ids = state.get("note_ids") or []
        if not isinstance(raw_ids, list):
            return []
        return [str(item) for item in raw_ids if item]

    def _remember_note_reference(self, *, chat_id: str, sender_id: str, note_id: str) -> None:
        self.note_manager.set_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_artifact_note_id",
            value={"note_id": note_id},
        )
        self.note_manager.set_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_selected_note_id",
            value={"note_id": note_id},
        )

    def _remember_search_results(
        self,
        *,
        chat_id: str,
        sender_id: str,
        note_ids: list[str],
        query: str,
    ) -> None:
        self.note_manager.set_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_search_results",
            value={"note_ids": note_ids, "query": query},
        )

    def _remember_list_results(
        self,
        *,
        chat_id: str,
        sender_id: str,
        note_ids: list[str],
    ) -> None:
        self.note_manager.set_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key="last_list_results",
            value={"note_ids": note_ids},
        )

    def _clear_correction_state(self, *, chat_id: str, sender_id: str) -> None:
        for key in ("pending_correction", "pending_correction_note_id", "pending_correction_payload"):
            self.note_manager.clear_conversation_state(
                chat_id=chat_id,
                sender_id=sender_id,
                key=key,
            )

    @staticmethod
    def _looks_like_delete_request(normalized: str) -> bool:
        has_delete = any(hint in normalized for hint in DELETE_HINTS)
        has_reference = any(hint in normalized for hint in FAST_READ_REFERENCE_HINTS) or re.search(r"\d+\s*\ubc88", normalized)
        return has_delete and has_reference

    @staticmethod
    def _looks_like_duplicate_delete_request(normalized: str) -> bool:
        has_delete = any(hint in normalized for hint in DELETE_HINTS)
        has_duplicate = any(hint in normalized for hint in DUPLICATE_HINTS)
        has_note = any(hint in normalized for hint in NOTE_COMMAND_HINTS)
        return has_delete and has_duplicate and has_note

    @staticmethod
    def _looks_like_numbered_read_request(normalized: str) -> bool:
        if not re.search(r"\d+\s*\ubc88", normalized):
            return False
        has_note = "\uba54\ubaa8" in normalized or "\ub178\ud2b8" in normalized
        has_read = any(hint in normalized for hint in FAST_READ_CONTENT_HINTS + SEARCH_VERBS)
        has_detail = any(hint in normalized for hint in NUMBERED_DETAIL_HINTS)
        return has_detail or (has_note and has_read)

    @staticmethod
    def _looks_like_numbered_select_request(normalized: str) -> bool:
        if not re.fullmatch(r"\d+\s*\ubc88\s*(?:\uba54\ubaa8|\ub178\ud2b8)?", normalized):
            return False
        return True

    @classmethod
    def _looks_like_meta_command(cls, text: str | None) -> bool:
        if not text:
            return False
        normalized = cls._normalize_text(text)
        if not normalized:
            return False
        if normalized.startswith("/"):
            return True
        if cls._list_command_mode(normalized) is not None:
            return True
        if cls._looks_like_duplicate_delete_request(normalized):
            return True
        if cls._looks_like_delete_request(normalized):
            return True
        if cls._looks_like_numbered_read_request(normalized):
            return True
        if any(hint in normalized for hint in DELETE_CONFIRM_HINTS):
            return True
        if cls._extract_selection_index(normalized) is not None and "\uc218\uc815" in normalized:
            return True
        if any(hint in normalized for hint in CORRECTION_HINTS):
            return True
        if cls._extract_correction_intent(text) is not None:
            return True
        has_read_content = any(hint in normalized for hint in FAST_READ_CONTENT_HINTS)
        has_read_verb = any(verb in normalized for verb in SEARCH_VERBS)
        has_note_reference = (
            any(hint in normalized for hint in FAST_READ_REFERENCE_HINTS)
            or any(hint in normalized for hint in NOTE_COMMAND_HINTS)
            or re.search(r"\d+\s*\ubc88", normalized)
        )
        return bool(has_note_reference and has_read_content and has_read_verb)

    @staticmethod
    def _looks_like_recent_list_request(normalized: str) -> bool:
        if UpdateRouter._list_command_mode(normalized) is not None:
            return True
        return (
            any(hint in normalized for hint in NOTE_COMMAND_HINTS)
            and any(hint in normalized for hint in LIST_HINTS)
            and any(verb in normalized for verb in SEARCH_VERBS)
        )

    @classmethod
    def _list_command_mode(cls, normalized: str) -> str | None:
        stripped = normalized
        for prefix in LIST_DISCOURSE_PREFIXES:
            if stripped.startswith(prefix + " "):
                stripped = stripped[len(prefix):].strip()
                break
            if stripped == prefix:
                stripped = ""
                break

        if any(phrase in stripped for phrase in LIST_ALL_PHRASES):
            return "all"
        if any(phrase in stripped for phrase in LIST_RECENT_PHRASES):
            return "recent"
        return None

    @staticmethod
    def _looks_like_search_request(normalized: str) -> bool:
        if any(hint in normalized for hint in DELETE_HINTS + CORRECTION_HINTS):
            return False
        if "\ud0dc\uadf8" in normalized:
            return False
        has_note_context = any(hint in normalized for hint in NOTE_COMMAND_HINTS)
        has_search_verb = any(hint in normalized for hint in SEARCH_VERBS)
        has_related = "\uad00\ub828" in normalized or "\ubb50\uc788" in normalized or "\ubb50 \uc788" in normalized
        return has_note_context and (has_search_verb or has_related)

    @classmethod
    def _looks_like_technical_note_statement(cls, text: str | None) -> bool:
        if not text:
            return False
        normalized = cls._normalize_text(text)
        if not normalized:
            return False
        if cls._detect_fast_read_intent(text) == "read_last_note":
            return False
        if cls._looks_like_search_request(normalized):
            return False
        if cls._looks_like_recent_list_request(normalized):
            return False
        if cls._looks_like_delete_request(normalized):
            return False
        if cls._extract_correction_intent(text) is not None:
            return False
        if cls._is_note_count_request(text):
            return False

        structural_patterns = (
            r".+\ub294 .+\uc5d0 .+\uc800\uc7a5\ud55c\ub2e4",
            r".+\ub97c \ub530\ub85c \uc800\uc7a5\ud55c\ub2e4",
            r".+\ud558\ub3c4\ub85d \uc124\uacc4\ud588\ub2e4",
            r".+\uad6c\uc870\ub2e4",
            r".+\ud30c\uc774\ud504\ub77c\uc778\uc740 .+",
            r".+\ud14c\uc774\ube14\uc740 .+",
            r".+\uceec\ub7fc\uc740 .+",
        )
        if any(re.search(pattern, text) for pattern in structural_patterns):
            return True

        keyword_hits = sum(1 for keyword in TECHNICAL_NOTE_KEYWORDS if keyword in normalized)
        explanatory_verbs = (
            "\uc800\uc7a5\ud55c\ub2e4",
            "\uc124\uacc4\ud588\ub2e4",
            "\uad6c\uc870\ub2e4",
            "\uad00\ub9ac\ud55c\ub2e4",
            "\uae30\ub85d\ud55c\ub2e4",
            "\uc5c5\ub370\uc774\ud2b8\ud55c\ub2e4",
        )
        return keyword_hits >= 2 and any(verb in normalized for verb in explanatory_verbs)

    @staticmethod
    def _build_technical_note_analysis(text: str) -> TextAnalysisResult:
        normalized = UpdateRouter._normalize_text(text)
        tags: list[str] = []
        tag_map = (
            ("ocr", "ocr"),
            ("image_file", "image_file"),
            ("파이프라인", "pipeline"),
            ("pipeline", "pipeline"),
            ("sqlite", "sqlite"),
            ("schema", "schema"),
            ("webhook", "webhook"),
            ("nim", "nim"),
            ("message", "message"),
            ("note", "note"),
            ("conversation_state", "conversation_state"),
            ("note_revision", "note_revision"),
        )
        for keyword, tag in tag_map:
            if keyword in normalized and tag not in tags:
                tags.append(tag)
        if not tags:
            tags = ["technical", "schema"]

        title = "\uae30\uc220 \uba54\ubaa8"
        if "ocr" in normalized and "image_file" in normalized and (
            "\ud30c\uc774\ud504\ub77c\uc778" in normalized or "pipeline" in normalized
        ):
            title = "OCR \ud30c\uc774\ud504\ub77c\uc778 \uc800\uc7a5 \uad6c\uc870"
        elif "webhook" in normalized and "fastapi" in normalized:
            title = "FastAPI webhook \uad6c\uc870"
        elif "conversation_state" in normalized:
            title = "CONVERSATION_STATE \uad6c\uc870"

        return TextAnalysisResult(
            title=title,
            summary=text.strip(),
            tags=tags[:5],
            category="note",
            confidence=0.96,
            raw_response='{"source":"deterministic_technical_note_statement"}',
            is_note=True,
            action="create",
        )

    @classmethod
    def _extract_correction_intent(cls, text: str) -> CommandIntent | None:
        normalized = cls._normalize_text(text)
        if cls._list_command_mode(normalized) is not None:
            return None
        if "\ub9d0\uace0" in normalized and (
            "\ubb50\uc788" in normalized
            or "\ubb50 \uc788" in normalized
            or "\ubb50\uc9c0" in normalized
        ):
            return None
        patterns = (
            r".*?\ub294\s+(?P<old>.+?)\s*(?:\uc774|\uac00)?\s*\uc544\ub2c8\ub77c\s+(?P<new>.+?)(?:\uc57c|\uc774\uc57c|\uc785\ub2c8\ub2e4)?(?:[.!?])?\s*(?:\uc218\uc815\ud574\uc918|\uc815\uc815\ud574\uc918|\uace0\uccd0\uc918|\ubc14\uafd4\uc918)?\s*$",
            r"(?P<old>.+?)(?:\uc774|\uac00)?\s*\uc544\ub2c8\ub77c[,\s]+(?P<new>.+?)(?:\uc57c|\uc774\uc57c|\uc785\ub2c8\ub2e4)?(?:[.!?])?\s*(?:\uc218\uc815\ud574\uc918|\uc815\uc815\ud574\uc918|\uace0\uccd0\uc918|\ubc14\uafd4\uc918)?\s*$",
            r"(?P<old>.+?)\s+(?:\uc774\s+\uc544\ub2c8\ub77c|\uac00\s+\uc544\ub2c8\ub77c|\uc544\ub2c8\ub77c|\uc544\ub2c8\uace0)\s+(?P<new>.+?)(?:\uc57c|\uc774\uc57c|\uc785\ub2c8\ub2e4)?(?:[.!?])?\s*(?:\uc218\uc815\ud574\uc918|\uc815\uc815\ud574\uc918|\uace0\uccd0\uc918|\ubc14\uafd4\uc918)?\s*$",
            r"(?P<old>.+?)\s*(?:->|\u2192)\s*(?P<new>.+?)\s*(?:\uc218\uc815\ud574\uc918|\uc815\uc815\ud574\uc918|\uace0\uccd0\uc918|\ubc14\uafd4\uc918)?(?:[.!?])?\s*$",
            r"(?P<old>.+?)\s+\ub9d0\uace0\s+(?P<new>.+?)(?:[.!?])?\s*(?:\uc218\uc815\ud574\uc918|\uc815\uc815\ud574\uc918|\uace0\uccd0\uc918|\ubc14\uafd4\uc918)?\s*$",
            r"(?P<old>.+?)(?:\uc744|\ub97c)\s+(?P<new>.+?)\ub85c\s*(?:[.!?])?\s*(?:\uc218\uc815\ud574\uc918|\uc815\uc815\ud574\uc918|\uace0\uccd0\uc918|\ubc14\uafd4\uc918)?\s*$",
            r"(?P<old>.+?)(?:\uc744|\ub97c)\s*(?:\uc0ad\uc81c|\uc9c0\uc6cc|\uc5c6\uc560)(?:\ud574\uc918|\ud574|\uc918)?\s*$",
        )
        for pattern in patterns:
            match = re.search(pattern, text.strip(), re.IGNORECASE)
            if match:
                old_text = match.group("old").strip(" \"'")
                new_text = cls._strip_correction_suffix(match.groupdict().get("new", ""))
                if not new_text and cls._looks_like_reference_only_text(old_text):
                    continue
                return CommandIntent(
                    name="correct_last_note",
                    old_text=old_text,
                    new_text=new_text,
                )
        if not any(hint in normalized for hint in CORRECTION_HINTS):
            return None
        return CommandIntent(name="correct_last_note")

    @staticmethod
    def _strip_correction_suffix(value: str) -> str:
        cleaned = value.strip(" \"'")
        cleaned = re.sub(
            r"\s*(?:\uc218\uc815\ud574\uc918|\uc815\uc815\ud574\uc918|\uace0\uccd0\uc918|\ubc14\uafd4\uc918|\uc218\uc815\ud574|\uc815\uc815\ud574)\s*$",
            "",
            cleaned,
        )
        cleaned = re.sub(r"(?:\uc57c|\uc774\uc57c|\uc785\ub2c8\ub2e4)\s*$", "", cleaned)
        return cleaned.strip(" .!?\"'")

    @staticmethod
    def _extract_selection_index(normalized: str) -> int | None:
        match = re.search(r"(\d+)\s*\ubc88", normalized)
        if match:
            return max(int(match.group(1)) - 1, 0)
        return None

    @classmethod
    def _looks_like_reference_only_text(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        if not normalized:
            return True
        if re.fullmatch(r"\d+\s*\ubc88\s*(?:\uba54\ubaa8|\ub178\ud2b8)?", normalized):
            return True
        return normalized in FAST_READ_REFERENCE_HINTS or normalized in {"\uba54\ubaa8", "\ub178\ud2b8"}

    @staticmethod
    def _replace_note_fragment(text: str, old_text: str, new_text: str) -> str:
        if not text or old_text not in text:
            return text
        updated = text.replace(old_text, new_text, 1)
        updated = re.sub(r"^[\s:,-]+", "", updated)
        updated = re.sub(r"\s{2,}", " ", updated)
        return updated.strip()

    @staticmethod
    def _normalize_explicit_save_analysis(analysis: TextAnalysisResult, source_text: str) -> TextAnalysisResult:
        title = (analysis.title or "").strip()
        summary = (analysis.summary or "").strip()
        source = source_text.strip()
        title = UpdateRouter._prepare_text_for_note_save(title) if title else ""
        summary = UpdateRouter._prepare_text_for_note_save(summary) if summary else ""
        if not summary:
            summary = source
        if not title:
            title = source[:60] or "Untitled note"
        analysis.title = title
        analysis.summary = summary
        analysis.is_note = True
        analysis.action = "create"
        analysis.target_note_id = None
        analysis.tool_name = None
        analysis.tool_query = None
        analysis.tool_tag = None
        analysis.tool_limit = None
        return analysis

    @staticmethod
    def _extract_explicit_save_anchor(source_text: str) -> str | None:
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_-]{3,})(?:[.\s:),\]]|$)", source_text.strip())
        if not match:
            return None
        anchor = match.group(1)
        has_identifier_shape = "_" in anchor or "-" in anchor or any(char.isdigit() for char in anchor)
        if has_identifier_shape:
            return anchor
        return None

    @staticmethod
    def _is_explicit_save_prefix_delete_command(command: CommandIntent) -> bool:
        if command.name != "correct_last_note" or not command.old_text or command.new_text:
            return False
        return command.old_text.strip() in {
            "\uba54\ubaa8\ub85c \uc800\uc7a5\ud574\uc918:",
            "\uba54\ubaa8\ub85c \uc800\uc7a5\ud574\uc918",
            "\uc800\uc7a5\ud574\uc918:",
            "\uc800\uc7a5\ud574\uc918",
        }

    @staticmethod
    def _build_explicit_save_fallback_analysis(
        text: str,
        *,
        action: str,
        target_note_id: str | None,
    ) -> TextAnalysisResult:
        title = text[:60].strip() or "Untitled note"
        return TextAnalysisResult(
            title=title,
            summary=text,
            tags=[],
            category="note",
            confidence=0.5,
            raw_response='{"fallback": "explicit_save"}',
            is_note=True,
            action=action,
            target_note_id=target_note_id,
        )

    @staticmethod
    def _has_explicit_note_save_request(text: str) -> bool:
        normalized = text.strip()
        patterns = (
            r"^(?:\uba54\ubaa8\ub85c\s*)?\uc800\uc7a5\ud574(?:줘|\uc8fc\uc138\uc694|\uc8fc\ub77c)?\s*[:,-]?\s*",
            r"^\uba54\ubaa8\s*(?:\ub85c\s*)?\ub0a8\uaca8(?:줘|\uc8fc\uc138\uc694|\uc8fc\ub77c)?\s*[:,-]?\s*",
            r"^\uae30\ub85d\ud574(?:줘|\uc8fc\uc138\uc694|\uc8fc\ub77c)?\s*[:,-]?\s*",
        )
        return any(re.match(pattern, normalized) for pattern in patterns)

    @staticmethod
    def _extract_slash_command(text: str) -> tuple[str, str] | None:
        match = re.match(r"^/([A-Za-z0-9_]{1,32})(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", text.strip(), flags=re.DOTALL)
        if not match:
            return None
        return match.group(1).lower(), (match.group(2) or "").strip()

    @staticmethod
    def _prepare_text_for_note_save(text: str) -> str:
        cleaned = text.strip()
        patterns = (
            r"^(?:\uba54\ubaa8\ub85c\s*)?\uc800\uc7a5\ud574(?:줘|\uc8fc\uc138\uc694|\uc8fc\ub77c)?\s*[:,-]?\s*",
            r"^\uba54\ubaa8\s*(?:\ub85c\s*)?\ub0a8\uaca8(?:줘|\uc8fc\uc138\uc694|\uc8fc\ub77c)?\s*[:,-]?\s*",
            r"^\uae30\ub85d\ud574(?:줘|\uc8fc\uc138\uc694|\uc8fc\ub77c)?\s*[:,-]?\s*",
        )
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, count=1)
        compact = cleaned.strip()
        return compact or text.strip()

    @staticmethod
    def _clean_search_query(text: str) -> str:
        cleaned = text
        for phrase in (
            "\ub0b4 \uba54\ubaa8\uc911\uc5d0",
            "\uba54\ubaa8\uc911\uc5d0",
            "\uc800\uc7a5\ud55c",
            "\uc800\uc7a5\ub41c",
            "\uad00\ub828 \uba54\ubaa8",
            "\uad00\ub828\ub41c \uac70",
            "\ubb50\uc788\ub354\ub77c",
            "\ubb50 \uc788\ub354\ub77c",
            "\ubb50 \uc788\uc9c0",
            "\uc54c\ub824\uc918",
            "\ubcf4\uc5ec\uc918",
            "\ucc3e\uc544\uc918",
            "\uac80\uc0c9",
            "\uba54\ubaa8",
            "\ub178\ud2b8",
        ):
            cleaned = cleaned.replace(phrase, " ")
        compact = " ".join(cleaned.split()).strip()
        return compact or text.strip()

    @staticmethod
    def _build_note_saved_message(
        title: str,
        summary: str,
        action: str,
        notion_status: str = "disabled",
    ) -> str:
        prefix = (
            "\uae30\uc874 \uba54\ubaa8\uc5d0 \ub367\ubd99\uc600\uc5b4."
            if action == "append"
            else "\uba54\ubaa8\ub85c \uc800\uc7a5\ud588\uc5b4."
        )
        message = prefix
        if summary:
            message += f"\n\n\uc694\uc57d: {summary}"
        if notion_status == "exported":
            message += "\nNotion: \uc800\uc7a5\ud568"
        elif notion_status == "failed":
            message += "\nNotion: \uc800\uc7a5 \uc2e4\ud328"
        return message

    @staticmethod
    def _build_search_results_message(notes: list[dict]) -> str:
        lines = [f"\uad00\ub828 \uba54\ubaa8 {min(len(notes), 5)}\uac1c\ub97c \ucc3e\uc558\uc5b4."]
        for index, note in enumerate(notes[:5], start=1):
            title = str(note.get("title") or "\uc81c\ubaa9 \uc5c6\uc74c").strip()
            summary = str(note.get("summary") or "").strip()
            if summary:
                lines.append(f"{index}. {title}\n{summary}")
            else:
                lines.append(f"{index}. {title}")
        return "\n".join(lines)

    @staticmethod
    def _build_recent_notes_plain_message(notes: list[dict]) -> str:
        if not notes:
            return "\uc800\uc7a5\ub41c \ud56d\ubaa9\uc774 \uc544\uc9c1 \uc5c6\uc5b4."
        lines = [f"\ucd5c\uadfc \uc800\uc7a5\ub41c \ud56d\ubaa9 {len(notes)}\uac1c\uc57c."]
        for index, note in enumerate(notes, start=1):
            title = str(note.get("title") or "\uc81c\ubaa9 \uc5c6\uc74c").strip()
            summary = str(note.get("summary") or "").strip()
            if summary:
                lines.append(f"{index}. {title}\n{summary}")
            else:
                lines.append(f"{index}. {title}")
        return "\n".join(lines)

    @staticmethod
    def _build_delete_confirmation_message(note: dict) -> str:
        title = str(note.get("title") or "\uc81c\ubaa9 \uc5c6\uc74c").strip()
        summary = str(note.get("summary") or "").strip()
        message = "\uc774 \uba54\ubaa8\ub97c \uc0ad\uc81c\ud560\uae4c?"
        message += f"\n\n\uc81c\ubaa9: {title}"
        if summary:
            message += f"\n\uc694\uc57d: {summary}"
        message += "\n\n'\uc0ad\uc81c \ud655\uc778'\uc774\ub77c\uace0 \ubcf4\ub0b4\uba74 \uc0ad\uc81c\ud560\uac8c."
        return message

    @staticmethod
    def _build_delete_completed_message(note: dict) -> str:
        title = str(note.get("title") or "\uc81c\ubaa9 \uc5c6\uc74c").strip()
        return f"\uba54\ubaa8\ub97c \uc0ad\uc81c\ud588\uc5b4.\n\n\uc81c\ubaa9: {title}"

    @staticmethod
    def _build_reference_choice_message(action_label: str, notes: list[dict]) -> str:
        lines = [f"{action_label}\ud560 \uba54\ubaa8\uac00 \uc5ec\ub7ec \uac1c\uc57c. \ubc88\ud638\ub97c \ud3ec\ud568\ud574 \ub2e4\uc2dc \ubcf4\ub0b4\uc918."]
        for index, note in enumerate(notes[:5], start=1):
            title = str(note.get("title") or "\uc81c\ubaa9 \uc5c6\uc74c").strip()
            summary = str(note.get("summary") or "").strip()
            if summary:
                lines.append(f"{index}\ubc88. {title}\n{summary}")
            else:
                lines.append(f"{index}\ubc88. {title}")
        return "\n".join(lines)

    @staticmethod
    def _build_pending_action_not_ready_message(command_name: str) -> str:
        examples = {
            "add": "/add 5번 메모에 후속 작업 추가",
            "fix": "/fix 5번 메모의 대수를 확률과 통계로 수정",
            "dedupe": "/dedupe 대수 관련 메모",
        }
        example = examples.get(command_name, f"/{command_name}")
        return (
            f"/{command_name} 명령은 기존 메모를 바꾸는 작업이라 승인 단계가 필요해.\n\n"
            "지금은 안전하게 실행하지 않았고, 메모로 저장하지도 않았어.\n"
            f"예상 형식: {example}\n\n"
            "다음 구현 단계에서 후보/변경 preview를 보여준 뒤 '승인'을 받아 실행하게 만들 거야."
        )

    @staticmethod
    def _build_slash_help_message(topic: str = "") -> str:
        topic = topic.strip().lower()
        if topic in {"new", "add", "list", "show", "raw", "delete", "fix", "dedupe"}:
            details = {
                "new": "/new 새 메모 내용\n새 NOTE를 바로 생성해. 예: /new 오늘 회의에서 v1 테스트를 먼저 하기로 함",
                "add": "/add 대상 + 추가 내용\n기존 NOTE append용. 승인 preview 구현 후 실행 예정.",
                "list": "/list [검색어]\n최근 메모 또는 검색 결과를 보여줘. 예: /list 대수",
                "show": "/show 대상\n메모 상세를 보여줘. 예: /show 5번 메모, /show 대수 보고서 관련 메모",
                "raw": "/raw 대상\n본문 또는 OCR 원문을 보여줘. 예: /raw 5번",
                "delete": "/delete 대상\n삭제 대상을 찾고 확인을 받은 뒤 soft delete해.",
                "fix": "/fix 대상 + 수정 지시\n기존 NOTE 수정용. 승인 preview 구현 후 실행 예정.",
                "dedupe": "/dedupe [범위]\n중복 후보를 찾고 승인 후 정리하는 명령. 승인 preview 구현 후 실행 예정.",
            }
            return details[topic]
        return "\n".join(
            [
                "사용 가능한 명령:",
                "/new 새 메모 내용",
                "/add 기존 메모에 추가할 내용",
                "/list [검색어]",
                "/show 대상",
                "/raw 대상",
                "/delete 대상",
                "/fix 대상 + 수정 지시",
                "/dedupe [범위]",
                "/help [명령어]",
            ]
        )

    @staticmethod
    def _build_correction_success_message(
        *,
        title: str,
        old_text: str,
        new_text: str,
        current_body: str,
    ) -> str:
        message = "\uc218\uc815\ud588\uc5b4."
        if title:
            message += f"\n\n\ub300\uc0c1: {title}"
        message += f"\n\n\ubcc0\uacbd \uc804:\n{old_text}"
        message += f"\n\n\ubcc0\uacbd \ud6c4:\n{new_text}"
        message += f"\n\n\ud604\uc7ac \uc800\uc7a5\ub41c \uc804\uccb4 \ub0b4\uc6a9:\n{current_body}"
        return message

    @staticmethod
    def _build_correction_miss_message(current_body: str) -> str:
        message = "\uc218\uc815\ud560 \ubb38\uad6c\ub97c \ud604\uc7ac \uba54\ubaa8\uc5d0\uc11c \ucc3e\uc9c0 \ubabb\ud588\uc5b4."
        if current_body:
            message += f"\n\n\ud604\uc7ac \uc800\uc7a5\ub41c \ub0b4\uc6a9:\n{current_body}"
        return message

    def process_note_search(
        self,
        message_id: str,
        chat_id: int | str,
        query: str,
        sender_id: int | str | None = None,
    ) -> None:
        try:
            logger.info("Starting note search db_message_id=%s query=%r", message_id, query)
            notes = self.note_manager.search_notes(query, limit=10)
            resolved_sender_id = self._resolve_sender_id(message_id, sender_id)
            if not notes:
                self.note_manager.mark_processed(message_id)
                self._remember_search_results(
                    chat_id=str(chat_id),
                    sender_id=resolved_sender_id,
                    note_ids=[],
                    query=query,
                )
                self._send_result_message(
                    message_id,
                    chat_id,
                    "\uad00\ub828 \uba54\ubaa8\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc5b4.",
                    purpose="search_no_results",
                )
                return
                self.telegram_client.send_message(
                    chat_id,
                    "관련 메모를 못 찾았어.",
                )
                return

            self._remember_search_results(
                chat_id=str(chat_id),
                sender_id=resolved_sender_id,
                note_ids=[str(note.get("id")) for note in notes if note.get("id")],
                query=query,
            )
            self.note_manager.mark_processed(message_id)
            self._send_result_message(
                message_id,
                chat_id,
                self._build_search_results_message(notes),
                purpose="search_results",
            )
            logger.info("Finished note search db_message_id=%s matches=%s", message_id, len(notes))
        except Exception:
            logger.exception("Failed to process note search db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                "메모 검색에 실패했어. 나중에 다시 시도해줘.",
            )

    def process_merge_proposal_reply(
        self,
        message_id: str,
        chat_id: int | str,
        proposal: dict,
        reply_action: str,
    ) -> None:
        try:
            if reply_action == "cancel":
                self.note_manager.update_merge_proposal_status(proposal["id"], "canceled")
                self.note_manager.mark_processed(message_id)
                self.telegram_client.send_message(chat_id, "병합 제안은 취소했어.")
                return

            keep_note = self.note_manager.get_note(proposal["keep_note_id"])
            merge_note = self.note_manager.get_note(proposal["merge_note_id"])
            if keep_note is None or merge_note is None:
                self.note_manager.update_merge_proposal_status(proposal["id"], "failed")
                self.note_manager.mark_processed(message_id)
                self.telegram_client.send_message(chat_id, "병합하려던 메모를 찾지 못했어.")
                return

            merged_analysis = None
            try:
                merged_analysis = self.nim_provider.summarize_merged_note(
                    keep_note=keep_note,
                    merge_note=merge_note,
                    existing_tags=self.note_manager.list_tags(),
                )
            except NIMProviderError:
                logger.exception(
                    "Failed to summarize merged notes proposal_id=%s keep_note_id=%s merge_note_id=%s",
                    proposal["id"],
                    proposal["keep_note_id"],
                    proposal["merge_note_id"],
                )

            merged_note = self.note_manager.merge_notes(
                keep_note_id=proposal["keep_note_id"],
                merge_note_id=proposal["merge_note_id"],
                merged_analysis=merged_analysis,
            )
            self.note_manager.update_merge_proposal_status(proposal["id"], "approved")
            self.note_manager.mark_processed(message_id)
            self.telegram_client.send_message(
                chat_id,
                self._build_merge_completed_message(merged_note),
            )
        except Exception:
            logger.exception(
                "Failed to process merge proposal reply db_message_id=%s proposal_id=%s",
                message_id,
                proposal.get("id"),
            )
            self.note_manager.update_merge_proposal_status(proposal["id"], "failed")
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                "메모 병합 처리 중에 문제가 생겼어. 나중에 다시 시도해줘.",
            )

    def process_agent_query(self, message_id: str, chat_id: int | str, query: str) -> None:
        try:
            logger.info("Starting agent fallback query db_message_id=%s query=%r", message_id, query)
            conversation_context = self.note_manager.recent_chat_messages(
                str(chat_id),
                limit=8,
                max_age_minutes=30,
                exclude_message_id=message_id,
            )
            tool_history: list[dict] = []
            for _ in range(4):
                step = self.nim_provider.plan_agent_step(
                    query=query,
                    tool_history=tool_history,
                    conversation_context=conversation_context,
                )
                action = str(step.get("action", "")).strip().lower()
                if action == "respond":
                    response_text = self._sanitize_agent_response(
                        str(step.get("response", "")).strip(),
                        tool_history,
                    )
                    self.note_manager.mark_processed(message_id)
                    self.telegram_client.send_message(chat_id, response_text)
                    return

                if action != "tool":
                    break

                tool_name = str(step.get("tool_name", "")).strip()
                arguments = step.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_result = self._execute_agent_tool(tool_name, arguments)
                tool_history.append(
                    {
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "result": tool_result,
                    }
                )

            self.note_manager.mark_processed(message_id)
            self.telegram_client.send_message(
                chat_id,
                self._build_agent_fallback_message(tool_history),
            )
        except Exception:
            logger.exception("Failed to process agent fallback query db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                "硫붾え瑜?뜑 ?좎뿰?섍쾶 ?꾪룷蹂려젮 ?뚮뒗??以묎컙??臾몄젣媛 ?앷꼈?? ?ㅼ떆 ?쒕룄?댁쨾.",
            )

    def _execute_tool_request(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        original_text: str,
        analysis,
    ) -> None:
        tool_name = analysis.tool_name
        if tool_name == "agent_fallback":
            query = analysis.tool_query or original_text
            self.process_agent_query(message_id, chat_id, query)
            return

        if tool_name == "count_notes":
            note_count = self.note_manager.count_notes()
            self.note_manager.mark_processed(message_id)
            self.telegram_client.send_message(chat_id, f"지금 저장된 메모는 {note_count}개야.")
            return

        if tool_name == "recent_notes":
            limit = analysis.tool_limit or 5
            notes = self.note_manager.recent_notes(limit=limit)
            self.note_manager.mark_processed(message_id)
            self.telegram_client.send_message(chat_id, self._build_recent_notes_message(notes))
            return

        if tool_name == "list_tags":
            tags = self.note_manager.list_tags()
            self.note_manager.mark_processed(message_id)
            self.telegram_client.send_message(chat_id, self._build_tag_list_message(tags))
            return

        if tool_name == "count_notes_by_tag":
            tag_name = analysis.tool_tag or self._extract_tag_from_text(original_text)
            count = self.note_manager.count_notes_by_tag(tag_name or "")
            self.note_manager.mark_processed(message_id)
            if not tag_name:
                self.telegram_client.send_message(chat_id, "어떤 태그를 볼지 못 정했어.")
            else:
                self.telegram_client.send_message(chat_id, f"'{tag_name}' 태그 메모는 {count}개야.")
            return

        if tool_name == "notes_by_tag":
            tag_name = analysis.tool_tag or self._extract_tag_from_text(original_text)
            limit = analysis.tool_limit or 5
            notes = self.note_manager.notes_by_tag(tag_name or "", limit=limit) if tag_name else []
            self.note_manager.mark_processed(message_id)
            if not tag_name:
                self.telegram_client.send_message(chat_id, "어떤 태그를 볼지 못 정했어.")
            elif not notes:
                self.telegram_client.send_message(chat_id, f"'{tag_name}' 태그 메모를 못 찾았어.")
            else:
                self.telegram_client.send_message(chat_id, self._build_tag_notes_message(tag_name, notes))
            return

        if tool_name == "suggest_note_merge":
            note_count = self.note_manager.count_notes()
            if note_count < 2:
                self.note_manager.mark_processed(message_id)
                self.telegram_client.send_message(chat_id, "아직 메모가 2개 미만이라 병합 후보를 볼 수 없어.")
                return

            notes = self.note_manager.recent_notes_for_merge(limit=max(note_count, 2))
            suggestion = self.nim_provider.suggest_note_merge(
                query=analysis.tool_query or original_text,
                notes=notes,
            )
            self.note_manager.mark_processed(message_id)
            if suggestion is None:
                self.telegram_client.send_message(chat_id, "지금 바로 합칠 만한 메모는 못 찾았어.")
                return

            keep_note = self.note_manager.get_note(suggestion["keep_note_id"])
            merge_note = self.note_manager.get_note(suggestion["merge_note_id"])
            if keep_note is None or merge_note is None:
                self.telegram_client.send_message(chat_id, "병합 후보를 찾았는데 원본 메모를 다시 확인해야 해.")
                return

            self.note_manager.create_merge_proposal(
                chat_id=str(chat_id),
                keep_note_id=suggestion["keep_note_id"],
                merge_note_id=suggestion["merge_note_id"],
                reason=suggestion["reason"],
            )
            self.telegram_client.send_message(
                chat_id,
                self._build_merge_proposal_message(
                    keep_note=keep_note,
                    merge_note=merge_note,
                    reason=suggestion["reason"],
                ),
            )
            return

        if tool_name == "search_notes":
            query = analysis.tool_query or original_text
            self.process_note_search(message_id, chat_id, query)
            return

        self.note_manager.mark_processed(message_id)
        self.telegram_client.send_message(chat_id, "도구를 고르긴 했는데 실행 방식이 아직 연결되지 않았어.")

    def _store_message_or_ignore(self, message: StoredMessage) -> str | None:
        try:
            return self.note_manager.store_message(message)
        except DuplicateMessageError:
            logger.info(
                "Ignored duplicate Telegram message chat_id=%s telegram_message_id=%s",
                message.chat_id,
                message.telegram_message_id,
            )
            return None

    def _send_message_safely(self, chat_id: int | str, text: str, *, purpose: str) -> bool:
        try:
            sent = self.telegram_client.send_message(chat_id, text)
        except Exception:
            logger.warning(
                "Telegram send_message raised chat_id=%s purpose=%s",
                chat_id,
                purpose,
                exc_info=True,
            )
            return False
        if sent is False:
            logger.warning(
                "Telegram send_message failed chat_id=%s purpose=%s",
                chat_id,
                purpose,
            )
            return False
        logger.info("Sent Telegram message chat_id=%s purpose=%s", chat_id, purpose)
        return True

    def _send_result_message(
        self,
        message_id: str,
        chat_id: int | str,
        text: str,
        *,
        purpose: str,
    ) -> bool:
        sent = self._send_message_safely(chat_id, text, purpose=purpose)
        if not sent:
            self.note_manager.mark_reply_failed(message_id)
            logger.warning(
                "Marked MESSAGE reply_failed db_message_id=%s purpose=%s",
                message_id,
                purpose,
            )
        return sent

    def process_photo_message(
        self,
        message_id: str,
        chat_id: int | str,
        sender_id: int | str,
        telegram_message_id: int,
        photo: TelegramPhotoSize,
        caption: str | None,
    ) -> None:
        reply_token = _CURRENT_REPLY_MESSAGE_ID.set(message_id)
        saved_image = None
        try:
            saved_image = self.image_archive.save_telegram_photo(
                message_id=message_id,
                chat_id=chat_id,
                telegram_message_id=telegram_message_id,
                photo=photo,
            )
            logger.info(
                "Stored photo file db_message_id=%s image_id=%s local_path=%s",
                message_id,
                saved_image.image_id,
                saved_image.local_path,
            )

            analysis = self.nim_provider.analyze_image(saved_image.local_path, caption=caption)
            logger.info(
                "Finished image analysis db_message_id=%s category=%s is_note=%s needs_user_clarification=%s",
                message_id,
                analysis.category,
                analysis.is_note,
                analysis.needs_user_clarification,
            )
            self.note_manager.update_image_analysis(
                saved_image.image_id,
                ocr_text=analysis.ocr_text,
                summary=analysis.summary,
                image_type=analysis.category,
                confidence=analysis.confidence,
            )

            if analysis.needs_user_clarification or analysis.category == "unsure":
                self.note_manager.mark_needs_review(message_id)
                self.telegram_client.send_message(
                    chat_id,
                    self._build_photo_review_request_message(analysis.ocr_text),
                )
                return

            if analysis.is_note is False or analysis.category == "general_photo":
                self.note_manager.mark_processed(message_id)
                self.telegram_client.send_message(chat_id, "일반 사진으로 보관했어.")
                return

            existing_note = self.note_manager.get_note_by_message_id(message_id)
            if existing_note is not None:
                self.note_manager.mark_processed(message_id)
                self._remember_note_reference(
                    chat_id=str(chat_id),
                    sender_id=str(sender_id),
                    note_id=str(existing_note.get("id")),
                )
                self.note_manager.set_conversation_state(
                    chat_id=str(chat_id),
                    sender_id=str(sender_id),
                    key="last_image_note_id",
                    value={"note_id": str(existing_note.get("id"))},
                )
                self._send_result_message(
                    message_id,
                    chat_id,
                    "\uc774 \uc0ac\uc9c4\uc740 \uc774\ubbf8 \uba54\ubaa8\ub85c \uc800\uc7a5\ub418\uc5b4 \uc788\uc5b4.",
                    purpose="image_duplicate_note",
                )
                return

            if analysis.confidence < 0.55:
                analysis = analysis.model_copy(update={"title": "OCR \ud655\uc778 \ud544\uc694 \uba54\ubaa8"})

            source_text = (analysis.ocr_text or caption or analysis.summary).strip()
            saved_note = self.note_manager.store_analysis_and_note(
                message_id=message_id,
                provider_name="nvidia_nim_vision",
                model_name=self.nim_provider.vision_model,
                source_text=source_text,
                analysis=analysis,
            )
            self._remember_note_reference(
                chat_id=str(chat_id),
                sender_id=str(sender_id),
                note_id=saved_note.note_id,
            )
            self.note_manager.set_conversation_state(
                chat_id=str(chat_id),
                sender_id=str(sender_id),
                key="last_image_note_id",
                value={"note_id": saved_note.note_id},
            )
            self._send_result_message(
                message_id,
                chat_id,
                self._build_image_saved_message(
                    ocr_text=analysis.ocr_text,
                    summary=analysis.summary,
                    notion_status=saved_note.notion_status,
                ),
                purpose="image_note_saved",
            )
        except NIMProviderError as exc:
            logger.exception("Failed to process Telegram photo db_message_id=%s", message_id)
            if saved_image is not None:
                self.note_manager.mark_needs_review(message_id)
                self.telegram_client.send_message(
                    chat_id,
                    "사진은 저장했어. OCR이나 판별이 실패했어. 메모로 남길 거면 내용을 한 줄로 보내주고, 그냥 사진이면 '사진'이라고 답장해줘.",
                )
                logger.info("Requested manual clarification after image analysis failure db_message_id=%s error=%s", message_id, exc)
                return

            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                "사진은 받았지만 저장 중에 실패했어. 다시 시도해줘.",
            )
        except Exception:
            logger.exception("Failed to process Telegram photo db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                "사진은 받았지만 처리 중에 실패했어. 다시 시도해줘.",
            )
        finally:
            _CURRENT_REPLY_MESSAGE_ID.reset(reply_token)

    @staticmethod
    def _build_success_message(
        title: str,
        summary: str,
        action: str,
        notion_status: str = "disabled",
    ) -> str:
        prefix = "기존 메모에 덧붙였어." if action == "append" else "처리 완료."
        message = f"{prefix}\n\n요약: {summary}"
        if notion_status == "exported":
            message += "\nNotion: 저장함"
        elif notion_status == "failed":
            message += "\nNotion: 저장 실패"
        return message

    @staticmethod
    def _build_image_success_message(
        *,
        summary: str,
        notion_status: str,
    ) -> str:
        message = f"사진 메모로 저장했어.\n\n요약: {summary}"
        if notion_status == "exported":
            message += "\nNotion: 저장함"
        elif notion_status == "failed":
            message += "\nNotion: 저장 실패"
        return message

    @staticmethod
    def _build_photo_review_request_message(partial_ocr_text: str | None) -> str:
        message = (
            "사진은 저장했어. 그런데 이게 메모용 사진인지 일반 사진인지 확신이 안 가.\n"
            "메모로 남길 거면 내용을 한 줄로 보내주고, 그냥 사진이면 '사진'이라고 답장해줘."
        )
        if partial_ocr_text:
            message += f"\n\n읽은 글자 일부: {partial_ocr_text[:80]}"
        return message

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(text.strip().lower().split())

    @staticmethod
    def _is_note_search_request(text: str | None) -> bool:
        if not text:
            return False
        normalized = " ".join(text.strip().lower().split())
        has_scope = any(hint in normalized for hint in NOTE_SCOPE_HINTS)
        has_action = any(hint in normalized for hint in NOTE_SEARCH_ACTION_HINTS)
        return has_scope and has_action

    @staticmethod
    def _is_note_count_request(text: str | None) -> bool:
        if not text:
            return False
        normalized = " ".join(text.strip().lower().split())
        if any(
            hint in normalized
            for hint in ("\uac1c\uc218", "\uba87\uac1c", "\uba87 \uac1c", "\ucd1d \uba87")
        ):
            return "\uba54\ubaa8" in normalized or "\uc800\uc7a5\ub41c" in normalized
        if "메모" not in normalized:
            return False
        return any(hint in normalized for hint in NOTE_COUNT_HINTS)

    @staticmethod
    def _build_local_search_message(notes: list[dict]) -> str:
        lines = [f"관련 메모 {min(len(notes), 5)}개 찾았어."]
        for index, note in enumerate(notes[:5], start=1):
            title = note.get("title") or "제목 없음"
            summary = note.get("summary") or ""
            lines.append(f"{index}. {title}: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _build_recent_notes_message(notes: list[dict]) -> str:
        if not notes:
            return "저장된 메모가 아직 없어."
        lines = [f"최근 메모 {len(notes)}개야."]
        for index, note in enumerate(notes, start=1):
            title = note.get("title") or "제목 없음"
            summary = note.get("summary") or ""
            lines.append(f"{index}. {title}: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _build_tag_list_message(tags: list[str]) -> str:
        if not tags:
            return "등록된 태그가 아직 없어."
        preview = ", ".join(tags[:20])
        if len(tags) > 20:
            preview += ", ..."
        return f"등록된 태그 {len(tags)}개야.\n{preview}"

    @staticmethod
    def _build_tag_notes_message(tag_name: str, notes: list[dict]) -> str:
        lines = [f"'{tag_name}' 태그 메모 {len(notes)}개 찾았어."]
        for index, note in enumerate(notes, start=1):
            title = note.get("title") or "제목 없음"
            summary = note.get("summary") or ""
            lines.append(f"{index}. {title}: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _trim_note_for_agent(note: dict) -> dict:
        return {
            "id": note.get("id"),
            "title": note.get("title"),
            "summary": note.get("summary"),
            "tags": note.get("tags"),
            "body": str(note.get("body", ""))[:1200],
            "created_at": note.get("created_at"),
        }

    def _execute_agent_tool(self, tool_name: str, arguments: dict) -> dict:
        if tool_name == "count_notes":
            return {"count": self.note_manager.count_notes()}

        if tool_name == "recent_notes":
            limit = max(1, min(int(arguments.get("limit", 5)), 10))
            notes = self.note_manager.recent_notes(limit=limit)
            return {"notes": [self._trim_note_for_agent(note) for note in notes]}

        if tool_name == "list_tags":
            return {"tags": self.note_manager.list_tags()}

        if tool_name == "count_notes_by_tag":
            tag_name = str(arguments.get("tag_name", "")).strip()
            return {
                "tag_name": tag_name,
                "count": self.note_manager.count_notes_by_tag(tag_name),
            }

        if tool_name == "notes_by_tag":
            tag_name = str(arguments.get("tag_name", "")).strip()
            limit = max(1, min(int(arguments.get("limit", 5)), 10))
            notes = self.note_manager.notes_by_tag(tag_name, limit=limit) if tag_name else []
            return {
                "tag_name": tag_name,
                "notes": [self._trim_note_for_agent(note) for note in notes],
            }

        if tool_name == "search_notes":
            query = str(arguments.get("query", "")).strip()
            limit = max(1, min(int(arguments.get("limit", 5)), 10))
            notes = self.note_manager.search_notes(query, limit=limit) if query else []
            return {
                "query": query,
                "notes": [self._trim_note_for_agent(note) for note in notes],
            }

        if tool_name == "read_note":
            note_id = str(arguments.get("note_id", "")).strip()
            note = self.note_manager.get_note(note_id) if note_id else None
            return {"note": self._trim_note_for_agent(note) if note else None}

        raise ValueError(f"unsupported agent tool: {tool_name}")

    @classmethod
    def _sanitize_agent_response(cls, text: str, tool_history: list[dict]) -> str:
        normalized = text.replace("\r\n", "\n").strip()
        if not normalized:
            return cls._build_agent_fallback_message(tool_history)
        normalized = re.sub(r"^\s*#+\s*", "", normalized, flags=re.MULTILINE)
        normalized = normalized.replace("**", "").replace("`", "")
        normalized = re.sub(r"^\s*[-*]\s+", "", normalized, flags=re.MULTILINE)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        if cls._looks_like_markdown_table(normalized) or "|" in normalized:
            return cls._build_agent_fallback_message(tool_history)
        return normalized[:1200]

    @staticmethod
    def _build_agent_fallback_message(tool_history: list[dict]) -> str:
        if not tool_history:
            return "메모를 확인했지만 바로 답을 정리하지 못했어. 조금만 다르게 다시 물어봐줘."
        last_result = tool_history[-1].get("result", {})
        notes = last_result.get("notes")
        if isinstance(notes, list) and notes:
            lines = [f"관련 메모 {min(len(notes), 5)}개를 찾았어."]
            for index, note in enumerate(notes[:5], start=1):
                title = note.get("title") or "제목 없음"
                summary = note.get("summary") or ""
                lines.append(f"{index}. {title}: {summary}")
            return "\n".join(lines)
        note = last_result.get("note")
        if isinstance(note, dict):
            title = note.get("title") or "제목 없음"
            summary = note.get("summary") or ""
            return f"{title}\n\n요약: {summary}"
        return "메모를 확인했어. 질문을 조금 더 구체적으로 보내주면 더 정확히 답할 수 있어."

    @staticmethod
    def _should_retry_as_contextual_query(
        *,
        text: str,
        conversation_context: list[dict],
    ) -> bool:
        if not conversation_context:
            return False
        normalized = " ".join(text.strip().lower().split())
        if not normalized:
            return False
        followup_markers = (
            "그거",
            "그거 말고",
            "이거",
            "저거",
            "전체",
            "전부",
            "이어서",
            "계속",
            "말고",
            "뭐있지",
            "뭐 있지",
            "말이야",
        )
        return len(normalized) <= 40 and any(marker in normalized for marker in followup_markers)

    @staticmethod
    def _build_merge_proposal_message(
        *,
        keep_note: dict,
        merge_note: dict,
        reason: str,
    ) -> str:
        keep_title = keep_note.get("title") or "제목 없음"
        merge_title = merge_note.get("title") or "제목 없음"
        return (
            "합칠 만한 메모를 찾았어.\n\n"
            f"유지할 메모: {keep_title}\n"
            f"합칠 메모: {merge_title}\n"
            f"이유: {reason}\n\n"
            "원하면 '합쳐' 또는 '병합해'라고 답장해. 취소하려면 '취소'라고 보내."
        )

    @staticmethod
    def _build_merge_completed_message(note: dict) -> str:
        summary = str(note.get("summary") or "").strip() or "메모를 하나로 합쳤어."
        return f"병합 완료.\n\n요약: {summary}"

    @staticmethod
    def _extract_tag_from_text(text: str) -> str | None:
        patterns = (
            r"([^\s]+)\s*태그",
            r"태그\s*([^\s]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return None

    @classmethod
    def _classify_merge_reply(cls, text: str | None) -> str | None:
        if not text:
            return None
        normalized = cls._normalize_text(text)
        approve_phrases = (
            "합쳐",
            "합쳐줘",
            "병합",
            "병합해",
            "진행해",
            "승인",
            "좋아",
            "그래",
            "응",
            "ㅇㅇ",
            "yes",
        )
        cancel_phrases = (
            "취소",
            "하지마",
            "하지 마",
            "그만",
            "아니",
            "ㄴㄴ",
            "no",
        )
        if any(phrase in normalized for phrase in approve_phrases):
            return "approve"
        if any(phrase in normalized for phrase in cancel_phrases):
            return "cancel"
        return None

    @classmethod
    def _sanitize_search_message(cls, summary: str, notes: list[dict]) -> str:
        normalized = summary.replace("\r\n", "\n").strip()
        if not normalized:
            return cls._build_local_search_message(notes)

        if cls._looks_like_markdown_table(normalized) or len(normalized) > 900:
            return cls._build_local_search_message(notes)

        normalized = re.sub(r"^\s*#+\s*", "", normalized, flags=re.MULTILINE)
        normalized = normalized.replace("**", "").replace("`", "")
        normalized = re.sub(r"^\s*[-*]\s+", "", normalized, flags=re.MULTILINE)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        if "|" in normalized:
            return cls._build_local_search_message(notes)
        return normalized

    """
    @staticmethod
    def _build_image_saved_message(
        *,
        ocr_text: str | None,
        summary: str,
        notion_status: str,
    ) -> str:
        message = "?ъ쭊 硫붾え濡???ν뻽??"
        if ocr_text:
            message += f"\n\n?쎌? ?댁슜:\n{ocr_text}"
        message += f"\n\n?붿빟: {summary}"
        if notion_status == "exported":
            message += "\nNotion: ??ν븿"
        elif notion_status == "failed":
            message += "\nNotion: ????ㅽ뙣"
        return message

    @classmethod
    def _detect_fast_read_intent(cls, text: str | None) -> str | None:
        if not text:
            return None
        normalized = cls._normalize_text(text)
        has_reference = any(hint in normalized for hint in FAST_READ_REFERENCE_HINTS)
        has_read_content = any(hint in normalized for hint in FAST_READ_CONTENT_HINTS)
        has_read_verb = any(verb in normalized for verb in ("알려줘", "보여줘"))
        if has_reference and (has_read_content or has_read_verb):
            return "read_last_note"
        return None

    def _legacy_fast_read_last_note(self, *, message_id: str, chat_id: int | str) -> None:
        note = self.note_manager.get_last_note_for_chat(
            str(chat_id),
            prefer_image=True,
            within_minutes=30,
        )
        self.note_manager.mark_processed(message_id)

        if note is None:
            self.telegram_client.send_message(chat_id, "최근 30분 안에 저장한 메모를 찾지 못했어.")
            logger.info("Fast read found no recent note db_message_id=%s", message_id)
            return

        body = str(note.get("image_ocr_text") or note.get("body") or note.get("summary") or "").strip()
        title = str(note.get("title") or "").strip()
        summary = str(note.get("summary") or "").strip()
        source_content_type = str(note.get("source_content_type") or "").strip()

        lines = [
            "방금 OCR로 저장된 메모는 이렇게 저장돼 있어."
            if source_content_type == "photo"
            else "방금 저장된 메모는 이렇게 저장돼 있어."
        ]
        if title:
            lines.extend(("", f"[제목]\n{title}"))
        if body:
            lines.extend(("", f"[본문]\n{body}"))
        if summary:
            lines.extend(("", f"[요약]\n{summary}"))

        self.telegram_client.send_message(chat_id, "\n".join(lines))
        logger.info(
            "Served fast read for recent note db_message_id=%s source_content_type=%s",
            message_id,
            source_content_type or "unknown",
        )

    """
    @staticmethod
    def _build_image_saved_message(
        *,
        ocr_text: str | None,
        summary: str,
        notion_status: str,
    ) -> str:
        message = "\uc0ac\uc9c4 \uba54\ubaa8\ub85c \uc800\uc7a5\ud588\uc5b4."
        if ocr_text:
            message += f"\n\n\uc77d\uc740 \ub0b4\uc6a9:\n{ocr_text}"
        message += f"\n\n\uc694\uc57d: {summary}"
        message += "\n\n\uc218\uc815 \uc608\uc2dc: '\ud2c0\ub9b0 \uae00\uc790 -> \ubc14\ub978 \uae00\uc790 \uc218\uc815\ud574\uc918.'"
        if notion_status == "exported":
            message += "\nNotion: \uc804\uc1a1\ud568"
        elif notion_status == "failed":
            message += "\nNotion: \uc804\uc1a1 \uc2e4\ud328"
        return message

    @classmethod
    def _detect_fast_read_intent(cls, text: str | None) -> str | None:
        if not text:
            return None
        normalized = cls._normalize_text(text)
        if cls._list_command_mode(normalized) is not None:
            return None
        has_reference = any(
            re.search(rf"(?<![\w\uac00-\ud7a3]){re.escape(hint)}", normalized)
            for hint in FAST_READ_REFERENCE_HINTS
        )
        has_read_content = any(hint in normalized for hint in FAST_READ_CONTENT_HINTS)
        has_read_verb = any(
            verb in normalized
            for verb in ("\uc54c\ub824\uc918", "\ubcf4\uc5ec\uc918")
        )
        if has_read_content and has_read_verb:
            return "read_last_note"
        if has_reference and (has_read_content or has_read_verb):
            return "read_last_note"
        return None

    def _handle_fast_read_last_note(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        sender_id: str,
        text: str,
    ) -> None:
        resolved = self._resolve_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            text=text,
            prefer_image=True,
        )
        note = resolved.get("note")
        candidates = resolved.get("candidates") or []
        self.note_manager.mark_processed(message_id)

        if candidates:
            self._send_result_message(
                message_id,
                chat_id,
                self._build_reference_choice_message(
                    action_label="\uc870\ud68c",
                    notes=candidates,
                ),
                purpose="read_reference_choice",
            )
            return

        if not isinstance(note, dict):
            self._send_result_message(
                message_id,
                chat_id,
                "\ucd5c\uadfc 30\ubd84 \uc548\uc5d0 \uc800\uc7a5\ud55c \uba54\ubaa8\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc5b4.",
                purpose="read_missing",
            )
            logger.info("Fast read found no recent note db_message_id=%s", message_id)
            return

        self._remember_note_reference(
            chat_id=str(chat_id),
            sender_id=sender_id,
            note_id=str(note.get("id")),
        )
        body = str(note.get("image_ocr_text") or note.get("body") or note.get("summary") or "").strip()
        title = str(note.get("title") or "").strip()
        summary = str(note.get("summary") or "").strip()
        source_content_type = str(note.get("source_content_type") or "").strip()

        lines = [
            "\ubc29\uae08 OCR\ub85c \uc800\uc7a5\ub41c \uba54\ubaa8\ub294 \uc774\ub807\uac8c \uc800\uc7a5\ub3fc \uc788\uc5b4."
            if source_content_type == "photo"
            else "\ubc29\uae08 \uc800\uc7a5\ub41c \uba54\ubaa8\ub294 \uc774\ub807\uac8c \uc800\uc7a5\ub3fc \uc788\uc5b4."
        ]
        if title:
            lines.extend(("", "[\uc81c\ubaa9]\n" + title))
        if body:
            lines.extend(("", "[\ubcf8\ubb38]\n" + body))
        if summary:
            lines.extend(("", "[\uc694\uc57d]\n" + summary))
        self._send_result_message(message_id, chat_id, "\n".join(lines), purpose="read_note")
        logger.info(
            "Served fast read for note db_message_id=%s note_id=%s source_content_type=%s llm_called=false",
            message_id,
            note.get("id"),
            source_content_type or "unknown",
        )

    @staticmethod
    def _looks_like_markdown_table(text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines[:-1]):
            next_line = lines[index + 1]
            if line.count("|") >= 2 and re.fullmatch(r"[\|\-\:\s]+", next_line):
                return True
        return False


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
    image_archive: ImageArchive,
) -> UpdateRouter:
    config = RouterConfig(
        allowed_user_ids=parse_allowed_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS")),
    )
    return UpdateRouter(
        config=config,
        note_manager=note_manager,
        nim_provider=nim_provider,
        telegram_client=telegram_client,
        image_archive=image_archive,
    )
