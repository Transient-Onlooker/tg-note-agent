from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


class DuplicateMessageError(RuntimeError):
    pass


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

                CREATE TABLE IF NOT EXISTS IMAGE_FILE (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    telegram_file_id TEXT NOT NULL,
                    telegram_file_unique_id TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    file_size INTEGER,
                    width INTEGER,
                    height INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES MESSAGE(id)
                );

                CREATE TABLE IF NOT EXISTS TELEGRAM_MESSAGE_DEDUPE (
                    chat_id TEXT NOT NULL,
                    telegram_message_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(chat_id, telegram_message_id)
                );

                CREATE TABLE IF NOT EXISTS TAG (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS NOTE_TAG (
                    note_id TEXT NOT NULL,
                    tag_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(note_id, tag_id),
                    FOREIGN KEY(note_id) REFERENCES NOTE(id),
                    FOREIGN KEY(tag_id) REFERENCES TAG(id)
                );

                CREATE TABLE IF NOT EXISTS MERGE_PROPOSAL (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    keep_note_id TEXT NOT NULL,
                    merge_note_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_note_column(conn, "notion_page_id", "TEXT")
            self._ensure_note_column(conn, "notion_status", "TEXT")
            self._backfill_tag_registry(conn)
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
            try:
                conn.execute(
                    """
                    INSERT INTO TELEGRAM_MESSAGE_DEDUPE (
                        chat_id,
                        telegram_message_id,
                        message_id,
                        created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        message.chat_id,
                        message.telegram_message_id,
                        message.id,
                        created_at,
                    ),
                )
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
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise DuplicateMessageError(
                    f"Duplicate Telegram message chat_id={message.chat_id} "
                    f"telegram_message_id={message.telegram_message_id}"
                ) from exc
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

    def find_latest_message(
        self,
        *,
        chat_id: str,
        content_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        query = """
            SELECT *
            FROM MESSAGE
            WHERE chat_id = ?
        """
        params: list[Any] = [chat_id]

        if content_type is not None:
            query += " AND content_type = ?"
            params.append(content_type)
        if status is not None:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT 1"
        with self.connection() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return dict(row) if row else None

    def recent_chat_messages(
        self,
        *,
        chat_id: str,
        limit: int = 8,
        max_age_minutes: int = 30,
        exclude_message_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(minutes=max_age_minutes)).isoformat()
        query = """
            SELECT id, chat_id, sender_id, raw_text, content_type, status, created_at
            FROM MESSAGE
            WHERE chat_id = ?
              AND created_at >= ?
              AND TRIM(raw_text) != ''
        """
        params: list[Any] = [chat_id, cutoff]
        if exclude_message_id is not None:
            query += " AND id != ?"
            params.append(exclude_message_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        ordered = [dict(row) for row in rows]
        ordered.reverse()
        return ordered

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
                    notion_page_id,
                    notion_status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    message_id,
                    analysis.title,
                    analysis.summary,
                    body,
                    json.dumps(analysis.tags, ensure_ascii=False),
                    analysis.confidence,
                    None,
                    "disabled",
                    utcnow_iso(),
                ),
            )
            self._replace_note_tags(conn, note_id, analysis.tags)
            conn.commit()
        return note_id

    def update_note(
        self,
        note_id: str,
        *,
        title: str,
        summary: str,
        body: str,
        tags: list[str],
        confidence: float,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE NOTE
                SET title = ?, summary = ?, body = ?, tags = ?, confidence = ?
                WHERE id = ?
                """,
                (
                    title,
                    summary,
                    body,
                    json.dumps(tags, ensure_ascii=False),
                    confidence,
                    note_id,
                ),
            )
            self._replace_note_tags(conn, note_id, tags)
            conn.commit()

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

    def search_notes(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        terms = [
            term
            for term in re.split(r"\s+", query.strip())
            if len(term) >= 2
        ]
        if not terms:
            return []

        where_parts = []
        params: list[Any] = []
        for term in terms:
            pattern = f"%{term}%"
            where_parts.append(
                """
                (
                    title LIKE ?
                    OR summary LIKE ?
                    OR body LIKE ?
                    OR tags LIKE ?
                )
                """
            )
            params.extend([pattern, pattern, pattern, pattern])

        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM NOTE
                WHERE {' OR '.join(where_parts)}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM NOTE WHERE id = ?",
                (note_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_note(self, note_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM NOTE_TAG WHERE note_id = ?", (note_id,))
            conn.execute("DELETE FROM NOTE WHERE id = ?", (note_id,))
            conn.execute("DELETE FROM TAG WHERE id NOT IN (SELECT DISTINCT tag_id FROM NOTE_TAG)")
            conn.commit()

    def list_tags(self) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM TAG ORDER BY name COLLATE NOCASE ASC"
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def count_notes(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM NOTE").fetchone()
        return int(row["count"]) if row else 0

    def recent_notes(self, *, limit: int = 5) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM NOTE
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_notes_for_merge(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.recent_notes(limit=limit)

    def count_notes_by_tag(self, tag_name: str) -> int:
        normalized = self._normalize_tag_name(tag_name)
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM NOTE_TAG
                JOIN TAG ON TAG.id = NOTE_TAG.tag_id
                WHERE TAG.normalized_name = ?
                """,
                (normalized,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def notes_by_tag(self, tag_name: str, *, limit: int = 5) -> list[dict[str, Any]]:
        normalized = self._normalize_tag_name(tag_name)
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT NOTE.*
                FROM NOTE
                JOIN NOTE_TAG ON NOTE_TAG.note_id = NOTE.id
                JOIN TAG ON TAG.id = NOTE_TAG.tag_id
                WHERE TAG.normalized_name = ?
                ORDER BY NOTE.created_at DESC
                LIMIT ?
                """,
                (normalized, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def insert_merge_proposal(
        self,
        *,
        chat_id: str,
        keep_note_id: str,
        merge_note_id: str,
        reason: str,
    ) -> str:
        proposal_id = str(uuid.uuid4())
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO MERGE_PROPOSAL (
                    id, chat_id, keep_note_id, merge_note_id, reason, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    chat_id,
                    keep_note_id,
                    merge_note_id,
                    reason,
                    "proposed",
                    utcnow_iso(),
                ),
            )
            conn.commit()
        return proposal_id

    def find_pending_merge_proposal(self, chat_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM MERGE_PROPOSAL
                WHERE chat_id = ? AND status = 'proposed'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_merge_proposal_status(self, proposal_id: str, status: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE MERGE_PROPOSAL SET status = ? WHERE id = ?",
                (status, proposal_id),
            )
            conn.commit()

    def insert_image_file(
        self,
        *,
        message_id: str,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        local_path: str,
        mime_type: str,
        file_size: int | None,
        width: int,
        height: int,
    ) -> str:
        image_id = str(uuid.uuid4())
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO IMAGE_FILE (
                    id,
                    message_id,
                    telegram_file_id,
                    telegram_file_unique_id,
                    local_path,
                    mime_type,
                    file_size,
                    width,
                    height,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    message_id,
                    telegram_file_id,
                    telegram_file_unique_id,
                    local_path,
                    mime_type,
                    file_size,
                    width,
                    height,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        return image_id

    def fetch_all(self, table: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_tag_name(tag: str) -> str:
        return " ".join(tag.strip().lower().split())

    def _replace_note_tags(
        self,
        conn: sqlite3.Connection,
        note_id: str,
        tags: list[str],
    ) -> None:
        conn.execute("DELETE FROM NOTE_TAG WHERE note_id = ?", (note_id,))
        for tag in tags:
            normalized = self._normalize_tag_name(tag)
            if not normalized:
                continue
            existing = conn.execute(
                "SELECT id, name FROM TAG WHERE normalized_name = ?",
                (normalized,),
            ).fetchone()
            if existing is None:
                tag_id = str(uuid.uuid4())
                display_name = tag.strip()
                conn.execute(
                    """
                    INSERT INTO TAG (id, name, normalized_name, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (tag_id, display_name, normalized, utcnow_iso()),
                )
            else:
                tag_id = str(existing["id"])
            conn.execute(
                """
                INSERT OR IGNORE INTO NOTE_TAG (note_id, tag_id, created_at)
                VALUES (?, ?, ?)
                """,
                (note_id, tag_id, utcnow_iso()),
            )

    def _backfill_tag_registry(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT id, tags FROM NOTE").fetchall()
        for row in rows:
            try:
                tags = json.loads(row["tags"] or "[]")
            except json.JSONDecodeError:
                tags = []
            if isinstance(tags, list):
                self._replace_note_tags(conn, str(row["id"]), [str(tag) for tag in tags])
