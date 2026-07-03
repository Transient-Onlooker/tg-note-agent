from __future__ import annotations

from dataclasses import dataclass

from app.models.db import Database, StoredMessage
from app.models.schemas import TextAnalysisResult


@dataclass(slots=True)
class SavedNoteResult:
    message_id: str
    note_id: str
    analysis_id: str


class NoteManager:
    def __init__(self, db: Database) -> None:
        self.db = db

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
        self.db.update_message_status(message_id, "processed")
        return SavedNoteResult(
            message_id=message_id,
            note_id=note_id,
            analysis_id=analysis_id,
        )

    def mark_ai_failed(self, message_id: str) -> None:
        self.db.update_message_status(message_id, "ai_failed")
