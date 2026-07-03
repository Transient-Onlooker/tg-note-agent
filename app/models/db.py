from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.models.schemas import TextAnalysisResult


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class StoredMessage:
    id: str
    telegram_message_id: str
    chat_id: str
    sender_id: str
    raw_text: str
    content_type: str = "text"
    status: str = "received"


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS MESSAGE (
                    id TEXT PRIMARY KEY,
                    telegram_message_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS NOTE (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    body TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    notion_page_id TEXT,
                    notion_status TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES MESSAGE(id)
                );

                CREATE TABLE IF NOT EXISTS AI_ANALYSIS (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    category TEXT NOT NULL,
                    raw_response TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES MESSAGE(id)
                );
                """
            )
            self._ensure_note_column(conn, "notion_page_id", "TEXT")
            self._ensure_note_column(conn, "notion_status", "TEXT")
            conn.commit()

    @staticmethod
    def _ensure_note_column(
        conn: sqlite3.Connection,
        column_name: str,
        column_type: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(NOTE)").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE NOTE ADD COLUMN {column_name} {column_type}")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def insert_message(self, message: StoredMessage) -> str:
        created_at = utcnow_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO MESSAGE (
                    id,
                    telegram_message_id,
                    chat_id,
                    sender_id,
                    raw_text,
                    content_type,
                    status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.telegram_message_id,
                    message.chat_id,
                    message.sender_id,
                    message.raw_text,
                    message.content_type,
                    message.status,
                    created_at,
                ),
            )
            conn.commit()
        return message.id

    def find_message_by_telegram_ids(
        self,
        chat_id: str,
        telegram_message_id: str,
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM MESSAGE
                WHERE chat_id = ? AND telegram_message_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (chat_id, telegram_message_id),
            ).fetchone()
        return dict(row) if row else None

    def update_message_status(self, message_id: str, status: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE MESSAGE SET status = ? WHERE id = ?",
                (status, message_id),
            )
            conn.commit()

    def insert_analysis(
        self,
        message_id: str,
        provider: str,
        model: str,
        analysis: TextAnalysisResult,
    ) -> str:
        analysis_id = str(uuid.uuid4())
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO AI_ANALYSIS (
                    id,
                    message_id,
                    provider,
                    model,
                    category,
                    raw_response,
                    confidence,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    message_id,
                    provider,
                    model,
                    analysis.category,
                    analysis.raw_response,
                    analysis.confidence,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        return analysis_id

    def insert_note(self, message_id: str, analysis: TextAnalysisResult, body: str) -> str:
        note_id = str(uuid.uuid4())
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO NOTE (
                    id,
                    message_id,
                    title,
                    summary,
                    body,
                    tags,
                    confidence,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    message_id,
                    analysis.title,
                    analysis.summary,
                    body,
                    json.dumps(analysis.tags, ensure_ascii=False),
                    analysis.confidence,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        return note_id

    def update_note_notion_export(
        self,
        note_id: str,
        *,
        notion_page_id: str | None,
        notion_status: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE NOTE
                SET notion_page_id = ?, notion_status = ?
                WHERE id = ?
                """,
                (notion_page_id, notion_status, note_id),
            )
            conn.commit()

    def fetch_all(self, table: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(row) for row in rows]
