from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.integrations.notion import NotionClient
from app.models.db import Database, StoredMessage
from app.models.schemas import TextAnalysisResult
from app.services.list_capture import parse_note_list_items

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SavedNoteResult:
    message_id: str
    note_id: str
    analysis_id: str
    action: str = "create"
    notion_page_id: str | None = None
    notion_status: str = "disabled"


class NoteManager:
    def __init__(self, db: Database, notion_client: NotionClient | None = None) -> None:
        self.db = db
        self.notion_client = notion_client

    def store_message(self, message: StoredMessage) -> str:
        return self.db.insert_message(message)

    def find_existing_message(
        self,
        chat_id: str,
        telegram_message_id: str,
    ) -> dict | None:
        return self.db.find_message_by_telegram_ids(chat_id, telegram_message_id)

    def get_message(self, message_id: str) -> dict | None:
        return self.db.get_message(message_id)

    def find_pending_photo_review(self, chat_id: str) -> dict | None:
        return self.db.find_latest_message(
            chat_id=chat_id,
            content_type="photo",
            status="needs_review",
        )

    def recent_chat_messages(
        self,
        chat_id: str,
        *,
        limit: int = 8,
        max_age_minutes: int = 30,
        exclude_message_id: str | None = None,
    ) -> list[dict]:
        return self.db.recent_chat_messages(
            chat_id=chat_id,
            limit=limit,
            max_age_minutes=max_age_minutes,
            exclude_message_id=exclude_message_id,
        )

    def search_notes(self, query: str, *, limit: int = 10) -> list[dict]:
        return self.db.search_notes(query, limit=limit)

    def list_tags(self) -> list[str]:
        return self.db.list_tags()

    def count_notes(self) -> int:
        return self.db.count_notes()

    def recent_notes(self, *, limit: int = 5) -> list[dict]:
        return self.db.recent_notes(limit=limit)

    def count_notes_by_tag(self, tag_name: str) -> int:
        return self.db.count_notes_by_tag(tag_name)

    def notes_by_tag(self, tag_name: str, *, limit: int = 5) -> list[dict]:
        return self.db.notes_by_tag(tag_name, limit=limit)

    def recent_notes_for_merge(self, *, limit: int = 20) -> list[dict]:
        return self.db.recent_notes_for_merge(limit=limit)

    def create_merge_proposal(
        self,
        *,
        chat_id: str,
        keep_note_id: str,
        merge_note_id: str,
        reason: str,
    ) -> str:
        return self.db.insert_merge_proposal(
            chat_id=chat_id,
            keep_note_id=keep_note_id,
            merge_note_id=merge_note_id,
            reason=reason,
        )

    def find_pending_merge_proposal(self, chat_id: str) -> dict | None:
        return self.db.find_pending_merge_proposal(chat_id)

    def update_merge_proposal_status(self, proposal_id: str, status: str) -> None:
        self.db.update_merge_proposal_status(proposal_id, status)

    def get_note(self, note_id: str) -> dict | None:
        return self.db.get_note(note_id)

    def get_note_with_source(self, note_id: str) -> dict | None:
        return self.db.get_note_with_source(note_id)

    def get_note_any_status(self, note_id: str) -> dict | None:
        return self.db.get_note_any_status(note_id)

    def get_note_by_message_id(self, message_id: str) -> dict | None:
        return self.db.get_note_by_message_id(message_id)

    def find_recent_note_by_body(
        self,
        *,
        chat_id: str,
        sender_id: str,
        body: str,
        within_minutes: int = 10,
    ) -> dict | None:
        return self.db.find_recent_note_by_body(
            chat_id=chat_id,
            sender_id=sender_id,
            body=body,
            within_minutes=within_minutes,
        )

    def find_duplicate_notes_by_body(
        self,
        *,
        chat_id: str,
        sender_id: str,
        limit: int = 200,
    ) -> list[dict]:
        return self.db.find_duplicate_notes_by_body(
            chat_id=chat_id,
            sender_id=sender_id,
            limit=limit,
        )

    def get_last_note_for_chat(
        self,
        chat_id: str,
        *,
        prefer_image: bool = True,
        within_minutes: int = 30,
    ) -> dict | None:
        return self.db.get_last_note_for_chat(
            chat_id=chat_id,
            prefer_image=prefer_image,
            within_minutes=within_minutes,
        )

    def update_image_analysis(
        self,
        image_id: str,
        *,
        ocr_text: str | None,
        summary: str | None,
        image_type: str | None,
        confidence: float | None,
    ) -> None:
        self.db.update_image_analysis(
            image_id,
            ocr_text=ocr_text,
            summary=summary,
            image_type=image_type,
            confidence=confidence,
        )

    def set_conversation_state(
        self,
        *,
        chat_id: str,
        sender_id: str,
        key: str,
        value,
    ) -> None:
        self.db.set_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key=key,
            value=value,
        )

    def get_conversation_state(
        self,
        *,
        chat_id: str,
        sender_id: str,
        key: str,
        max_age_minutes: int | None = 30,
    ):
        return self.db.get_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key=key,
            max_age_minutes=max_age_minutes,
        )

    def clear_conversation_state(
        self,
        *,
        chat_id: str,
        sender_id: str,
        key: str,
    ) -> None:
        self.db.clear_conversation_state(
            chat_id=chat_id,
            sender_id=sender_id,
            key=key,
        )

    def replace_note_body(
        self,
        *,
        note_id: str,
        new_body: str,
        reason: str | None = None,
    ) -> dict | None:
        updated_note = self.db.replace_note_body(
            note_id=note_id,
            new_body=new_body,
            reason=reason,
        )
        if updated_note is not None:
            self._sync_note_list_items(note_id, new_body)
        return updated_note

    def replace_note_text_fields(
        self,
        *,
        note_id: str,
        new_title: str,
        new_summary: str,
        new_body: str,
        reason: str | None = None,
    ) -> dict | None:
        updated_note = self.db.replace_note_text_fields(
            note_id=note_id,
            new_title=new_title,
            new_summary=new_summary,
            new_body=new_body,
            reason=reason,
        )
        if updated_note is not None:
            self._sync_note_list_items(note_id, new_body)
        return updated_note

    def delete_note(self, note_id: str, *, reason: str | None = None) -> None:
        self.db.delete_note(note_id, reason=reason)

    def get_note_list_items(self, note_id: str) -> list[dict]:
        return self.db.get_note_list_items(note_id)

    def sync_note_list_items(self, note_id: str, body: str) -> list[dict]:
        return self._sync_note_list_items(note_id, body)

    def store_analysis_and_note(
        self,
        message_id: str,
        provider_name: str,
        model_name: str,
        source_text: str,
        analysis: TextAnalysisResult,
        existing_note_id: str | None = None,
    ) -> SavedNoteResult:
        analysis_id = self.db.insert_analysis(message_id, provider_name, model_name, analysis)
        export_body = source_text
        export_tags = list(analysis.tags)
        action = "create"
        if existing_note_id:
            existing_note = self.db.get_note(existing_note_id)
            if existing_note is not None:
                merged_tags = self._merge_tags(existing_note.get("tags"), analysis.tags)
                merged_body = self._append_note_body(existing_note.get("body", ""), source_text)
                self.db.update_note(
                    existing_note_id,
                    title=analysis.title,
                    summary=analysis.summary,
                    body=merged_body,
                    tags=merged_tags,
                    confidence=analysis.confidence,
                )
                note_id = existing_note_id
                action = "append"
                export_body = merged_body
                export_tags = merged_tags
            else:
                note_id = self.db.insert_note(message_id, analysis, source_text)
        else:
            note_id = self.db.insert_note(message_id, analysis, source_text)
        self._sync_note_list_items(note_id, export_body)

        notion_page_id: str | None = None
        notion_status = "disabled"

        if self.notion_client is not None:
            try:
                notion_result = self.notion_client.export_note(
                    title=analysis.title,
                    summary=analysis.summary,
                    body=export_body,
                    tags=export_tags,
                )
                notion_page_id = notion_result.page_id
                notion_status = notion_result.status
                logger.info(
                    "Exported note to Notion note_id=%s notion_page_id=%s",
                    note_id,
                    notion_page_id,
                )
            except Exception:
                notion_status = "failed"
                logger.exception("Failed to export note to Notion note_id=%s", note_id)

            self.db.update_note_notion_export(
                note_id,
                notion_page_id=notion_page_id,
                notion_status=notion_status,
            )

        self.db.update_message_status(message_id, "processed")
        return SavedNoteResult(
            message_id=message_id,
            note_id=note_id,
            analysis_id=analysis_id,
            action=action,
            notion_page_id=notion_page_id,
            notion_status=notion_status,
        )

    def mark_ai_failed(self, message_id: str) -> None:
        self.db.update_message_status(message_id, "ai_failed")

    def mark_processed(self, message_id: str) -> None:
        self.db.update_message_status(message_id, "processed")

    def mark_needs_review(self, message_id: str) -> None:
        self.db.update_message_status(message_id, "needs_review")

    def mark_action_failed(self, message_id: str) -> None:
        self.db.update_message_status(message_id, "action_failed")

    def mark_reply_failed(self, message_id: str) -> None:
        self.db.update_message_status(message_id, "reply_failed")

    def merge_notes(
        self,
        *,
        keep_note_id: str,
        merge_note_id: str,
        merged_analysis: TextAnalysisResult | None = None,
    ) -> dict:
        keep_note = self.db.get_note(keep_note_id)
        merge_note = self.db.get_note(merge_note_id)
        if keep_note is None or merge_note is None:
            raise ValueError("merge target note not found")

        merged_tags = self._merge_tags(keep_note.get("tags"), json.loads(merge_note.get("tags", "[]")))
        merged_body = self._append_note_body(keep_note.get("body", ""), merge_note.get("body", ""))
        title = merged_analysis.title if merged_analysis is not None else str(keep_note.get("title", ""))
        summary = merged_analysis.summary if merged_analysis is not None else str(keep_note.get("summary", ""))
        confidence = (
            merged_analysis.confidence
            if merged_analysis is not None
            else float(keep_note.get("confidence", 0.0))
        )
        if merged_analysis is not None and merged_analysis.tags:
            merged_tags = self._merge_tags(json.dumps(merged_tags, ensure_ascii=False), merged_analysis.tags)

        self.db.update_note(
            keep_note_id,
            title=title,
            summary=summary,
            body=merged_body,
            tags=merged_tags,
            confidence=confidence,
        )
        self._sync_note_list_items(keep_note_id, merged_body)
        self.db.delete_note(merge_note_id)
        return self.db.get_note(keep_note_id) or keep_note

    def _sync_note_list_items(self, note_id: str, body: str) -> list[dict]:
        items = [item.to_record() for item in parse_note_list_items(body)]
        self.db.replace_note_list_items(note_id=note_id, items=items)
        return items

    @staticmethod
    def _append_note_body(existing_body: str, new_text: str) -> str:
        cleaned_existing = existing_body.strip()
        cleaned_new = new_text.strip()
        if not cleaned_existing:
            return cleaned_new
        if not cleaned_new:
            return cleaned_existing
        return f"{cleaned_existing}\n\n{cleaned_new}"

    @staticmethod
    def _merge_tags(existing_tags_raw, new_tags: list[str]) -> list[str]:
        try:
            existing_tags = json.loads(existing_tags_raw or "[]")
        except Exception:
            existing_tags = []

        merged: list[str] = []
        seen: set[str] = set()
        for tag in list(existing_tags) + list(new_tags):
            normalized = " ".join(str(tag).strip().lower().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(str(tag).strip())
        return merged
