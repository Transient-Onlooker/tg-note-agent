from __future__ import annotations

import logging
from dataclasses import dataclass

from app.integrations.notion import NotionClient
from app.models.db import Database, StoredMessage
from app.models.schemas import TextAnalysisResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SavedNoteResult:
    message_id: str
    note_id: str
    analysis_id: str
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

    def store_analysis_and_note(
        self,
        message_id: str,
        provider_name: str,
        model_name: str,
        source_text: str,
        analysis: TextAnalysisResult,
    ) -> SavedNoteResult:
        analysis_id = self.db.insert_analysis(message_id, provider_name, model_name, analysis)
        note_id = self.db.insert_note(message_id, analysis, source_text)
        notion_page_id: str | None = None
        notion_status = "disabled"

        if self.notion_client is not None:
            try:
                notion_result = self.notion_client.export_note(
                    title=analysis.title,
                    summary=analysis.summary,
                    body=source_text,
                    tags=analysis.tags,
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
            notion_page_id=notion_page_id,
            notion_status=notion_status,
        )

    def mark_ai_failed(self, message_id: str) -> None:
        self.db.update_message_status(message_id, "ai_failed")
