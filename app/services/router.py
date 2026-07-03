from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass

from fastapi import BackgroundTasks

from app.integrations.telegram import TelegramClient
from app.models.db import DuplicateMessageError, StoredMessage
from app.models.schemas import TelegramMessage, TelegramPhotoSize, TelegramUpdate, WebhookResult
from app.services.image_archive import ImageArchive
from app.services.nim_provider import NIMProviderError, NvidiaNIMProvider
from app.services.note_manager import NoteManager

logger = logging.getLogger(__name__)

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
        image_archive: ImageArchive,
    ) -> None:
        self.config = config
        self.note_manager = note_manager
        self.nim_provider = nim_provider
        self.telegram_client = telegram_client
        self.image_archive = image_archive

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
        self.telegram_client.send_message(message.chat.id, "수신 완료.")
        logger.info("Sent receive acknowledgement chat_id=%s", message.chat.id)

        if background_tasks is not None:
            background_tasks.add_task(
                self.process_message,
                message_id,
                message.chat.id,
                message.text or "",
            )
            return WebhookResult(status="accepted")

        self.process_message(message_id, message.chat.id, message.text or "")
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
        self.telegram_client.send_message(message.chat.id, "사진 수신 완료.")
        logger.info("Sent photo receive acknowledgement chat_id=%s", message.chat.id)

        if background_tasks is not None:
            background_tasks.add_task(
                self.process_photo_message,
                message_id,
                message.chat.id,
                message.message_id,
                largest_photo,
                message.caption,
            )
            return WebhookResult(status="accepted")

        self.process_photo_message(
            message_id,
            message.chat.id,
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
            )
            return WebhookResult(status="accepted", detail="photo_review_retry")

        self.process_message(pending_photo_review["id"], message.chat.id, reply_text)
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

    def process_message(self, message_id: str, chat_id: int | str, text: str) -> None:
        try:
            logger.info(
                "Starting text route db_message_id=%s router_model=%s",
                message_id,
                self.nim_provider.router_model,
            )
            existing_tags = self.note_manager.list_tags()
            candidate_notes = self.note_manager.search_notes(text, limit=5)
            conversation_context = self.note_manager.recent_chat_messages(
                str(chat_id),
                limit=8,
                max_age_minutes=30,
                exclude_message_id=message_id,
            )
            route = self.nim_provider.route_text(
                text,
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

            if route.tool_name:
                self._execute_tool_request(
                    message_id=message_id,
                    chat_id=chat_id,
                    original_text=text,
                    analysis=route,
                )
                return

            if (
                route.route == "ignore"
            ) and not self._should_retry_as_contextual_query(
                text=text,
                conversation_context=conversation_context,
            ):
                self.note_manager.mark_processed(message_id)
                self.telegram_client.send_message(
                    chat_id,
                    "메모로 저장하진 않았어.",
                )
                logger.info("Ignored non-note text db_message_id=%s", message_id)
                return

            if (
                route.route == "ignore"
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
            action = "append" if route.route == "append" else "create"
            if action == "append" and route.target_note_id:
                existing_note = self.note_manager.get_note(route.target_note_id)
                if existing_note is not None:
                    existing_note_id = route.target_note_id

            try:
                analysis = self.nim_provider.analyze_text(
                    text,
                    existing_tags=existing_tags,
                    candidate_notes=candidate_notes,
                    conversation_context=conversation_context,
                    action=action,
                    target_note_id=existing_note_id,
                )
            except NIMProviderError:
                logger.exception(
                    "Failed to generate note metadata; saving degraded note db_message_id=%s",
                    message_id,
                )
                analysis = self.nim_provider.build_fallback_note_analysis(
                    text,
                    action=action,
                    target_note_id=existing_note_id,
                )

            saved_note = self.note_manager.store_analysis_and_note(
                message_id=message_id,
                provider_name="nvidia_nim",
                model_name=self.nim_provider.text_model,
                source_text=text,
                analysis=analysis,
                existing_note_id=existing_note_id,
            )
            logger.info("Stored note and AI analysis db_message_id=%s", message_id)
            response_text = self._build_success_message(
                analysis.summary,
                saved_note.action,
                saved_note.notion_status,
            )
            self.telegram_client.send_message(chat_id, response_text)
            logger.info("Sent text completion message chat_id=%s db_message_id=%s", chat_id, message_id)
        except NIMProviderError as exc:
            logger.exception("Failed to process Telegram text db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                f"AI 분석이 너무 오래 걸리거나 실패했어. ({exc})",
            )
        except Exception:
            logger.exception("Failed to process Telegram text db_message_id=%s", message_id)
            self.note_manager.mark_ai_failed(message_id)
            self.telegram_client.send_message(
                chat_id,
                "메시지는 저장했지만 AI 분석에는 실패했어. 나중에 다시 시도해줘.",
            )

    def process_note_search(self, message_id: str, chat_id: int | str, query: str) -> None:
        try:
            logger.info("Starting note search db_message_id=%s query=%r", message_id, query)
            notes = self.note_manager.search_notes(query, limit=10)
            if not notes:
                self.note_manager.mark_processed(message_id)
                self.telegram_client.send_message(
                    chat_id,
                    "관련 메모를 못 찾았어.",
                )
                return

            try:
                summary = self.nim_provider.summarize_note_search(query=query, notes=notes)
            except NIMProviderError:
                logger.exception("Failed to summarize note search db_message_id=%s", message_id)
                summary = self._build_local_search_message(notes)

            summary = self._sanitize_search_message(summary, notes)
            self.note_manager.mark_processed(message_id)
            self.telegram_client.send_message(chat_id, summary)
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

    def process_photo_message(
        self,
        message_id: str,
        chat_id: int | str,
        telegram_message_id: int,
        photo: TelegramPhotoSize,
        caption: str | None,
    ) -> None:
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

            source_text = (analysis.ocr_text or caption or analysis.summary).strip()
            saved_note = self.note_manager.store_analysis_and_note(
                message_id=message_id,
                provider_name="nvidia_nim_vision",
                model_name=self.nim_provider.vision_model,
                source_text=source_text,
                analysis=analysis,
            )
            self.telegram_client.send_message(
                chat_id,
                self._build_image_success_message(
                    summary=analysis.summary,
                    notion_status=saved_note.notion_status,
                ),
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

    @staticmethod
    def _build_success_message(
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
