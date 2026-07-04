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
                    deleted_at TEXT,
                    deleted_reason TEXT,
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
                    ocr_text TEXT,
                    summary TEXT,
                    image_type TEXT,
                    confidence REAL,
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

                CREATE TABLE IF NOT EXISTS CONVERSATION_STATE (
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(chat_id, sender_id, key)
                );

                CREATE TABLE IF NOT EXISTS NOTE_REVISION (
                    id TEXT PRIMARY KEY,
                    note_id TEXT NOT NULL,
                    previous_body TEXT NOT NULL,
                    new_body TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(note_id) REFERENCES NOTE(id)
                );
                """
            )
            self._ensure_note_column(conn, "notion_page_id", "TEXT")
            self._ensure_note_column(conn, "notion_status", "TEXT")
            self._ensure_note_column(conn, "deleted_at", "TEXT")
            self._ensure_note_column(conn, "deleted_reason", "TEXT")
            self._ensure_image_column(conn, "ocr_text", "TEXT")
            self._ensure_image_column(conn, "summary", "TEXT")
            self._ensure_image_column(conn, "image_type", "TEXT")
            self._ensure_image_column(conn, "confidence", "REAL")
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

    @staticmethod
    def _ensure_image_column(
        conn: sqlite3.Connection,
        column_name: str,
        column_type: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(IMAGE_FILE)").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE IMAGE_FILE ADD COLUMN {column_name} {column_type}")

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

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM MESSAGE WHERE id = ?",
                (message_id,),
            ).fetchone()
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
        terms = self._expand_search_terms(query)
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
                WHERE deleted_at IS NULL
                  AND ({' OR '.join(where_parts)})
                ORDER BY created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM NOTE WHERE id = ? AND deleted_at IS NULL",
                (note_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_note_any_status(self, note_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM NOTE WHERE id = ?",
                (note_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_note_by_message_id(self, message_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM NOTE
                WHERE message_id = ?
                  AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (message_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_note_with_source(self, note_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    NOTE.*,
                    MESSAGE.chat_id AS source_chat_id,
                    MESSAGE.sender_id AS source_sender_id,
                    MESSAGE.content_type AS source_content_type,
                    MESSAGE.created_at AS source_message_created_at,
                    IMAGE_FILE.id AS image_id,
                    IMAGE_FILE.ocr_text AS image_ocr_text,
                    IMAGE_FILE.summary AS image_summary,
                    IMAGE_FILE.image_type AS image_type,
                    IMAGE_FILE.confidence AS image_confidence
                FROM NOTE
                JOIN MESSAGE ON MESSAGE.id = NOTE.message_id
                LEFT JOIN IMAGE_FILE ON IMAGE_FILE.message_id = MESSAGE.id
                WHERE NOTE.id = ?
                  AND NOTE.deleted_at IS NULL
                LIMIT 1
                """,
                (note_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_last_note_for_chat(
        self,
        *,
        chat_id: str,
        prefer_image: bool = True,
        within_minutes: int = 30,
    ) -> dict[str, Any] | None:
        cutoff = (datetime.now(UTC) - timedelta(minutes=within_minutes)).isoformat()

        def fetch_for_content_type(content_type: str | None) -> dict[str, Any] | None:
            query = """
                SELECT
                    NOTE.*,
                    MESSAGE.chat_id AS source_chat_id,
                    MESSAGE.content_type AS source_content_type,
                    MESSAGE.created_at AS source_message_created_at,
                    IMAGE_FILE.ocr_text AS image_ocr_text,
                    IMAGE_FILE.summary AS image_summary,
                    IMAGE_FILE.image_type AS image_type,
                    IMAGE_FILE.confidence AS image_confidence
                FROM NOTE
                JOIN MESSAGE
                    ON MESSAGE.id = NOTE.message_id
                LEFT JOIN IMAGE_FILE
                    ON IMAGE_FILE.message_id = MESSAGE.id
                WHERE MESSAGE.chat_id = ?
                  AND MESSAGE.created_at >= ?
                  AND NOTE.deleted_at IS NULL
            """
            params: list[Any] = [chat_id, cutoff]
            if content_type is not None:
                query += " AND MESSAGE.content_type = ?"
                params.append(content_type)
            query += " ORDER BY NOTE.created_at DESC LIMIT 1"

            with self.connection() as conn:
                row = conn.execute(query, tuple(params)).fetchone()
            return dict(row) if row else None

        if prefer_image:
            image_note = fetch_for_content_type("photo")
            if image_note is not None:
                return image_note
        return fetch_for_content_type(None)

    def delete_note(self, note_id: str, *, reason: str | None = None) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE NOTE
                SET deleted_at = ?, deleted_reason = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (utcnow_iso(), reason, note_id),
            )
            conn.commit()

    def list_tags(self) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM TAG ORDER BY name COLLATE NOCASE ASC"
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def count_notes(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM NOTE WHERE deleted_at IS NULL"
            ).fetchone()
        return int(row["count"]) if row else 0

    def recent_notes(self, *, limit: int = 5) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM NOTE
                WHERE deleted_at IS NULL
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
                JOIN NOTE ON NOTE.id = NOTE_TAG.note_id
                WHERE TAG.normalized_name = ?
                  AND NOTE.deleted_at IS NULL
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
                  AND NOTE.deleted_at IS NULL
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
                    ocr_text,
                    summary,
                    image_type,
                    confidence,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    None,
                    None,
                    None,
                    None,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        return image_id

    def update_image_analysis(
        self,
        image_id: str,
        *,
        ocr_text: str | None,
        summary: str | None,
        image_type: str | None,
        confidence: float | None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE IMAGE_FILE
                SET ocr_text = ?, summary = ?, image_type = ?, confidence = ?
                WHERE id = ?
                """,
                (ocr_text, summary, image_type, confidence, image_id),
            )
            conn.commit()

    def set_conversation_state(
        self,
        *,
        chat_id: str,
        sender_id: str,
        key: str,
        value: Any,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO CONVERSATION_STATE (
                    chat_id,
                    sender_id,
                    key,
                    value_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, sender_id, key)
                DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    sender_id,
                    key,
                    json.dumps(value, ensure_ascii=False),
                    utcnow_iso(),
                ),
            )
            conn.commit()

    def get_conversation_state(
        self,
        *,
        chat_id: str,
        sender_id: str,
        key: str,
        max_age_minutes: int | None = 30,
    ) -> Any | None:
        query = """
            SELECT value_json, updated_at
            FROM CONVERSATION_STATE
            WHERE chat_id = ? AND sender_id = ? AND key = ?
        """
        params: list[Any] = [chat_id, sender_id, key]
        if max_age_minutes is not None:
            cutoff = (datetime.now(UTC) - timedelta(minutes=max_age_minutes)).isoformat()
            query += " AND updated_at >= ?"
            params.append(cutoff)
        query += " ORDER BY updated_at DESC LIMIT 1"

        with self.connection() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            return None

    def clear_conversation_state(
        self,
        *,
        chat_id: str,
        sender_id: str,
        key: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                DELETE FROM CONVERSATION_STATE
                WHERE chat_id = ? AND sender_id = ? AND key = ?
                """,
                (chat_id, sender_id, key),
            )
            conn.commit()

    def insert_note_revision(
        self,
        *,
        note_id: str,
        previous_body: str,
        new_body: str,
        reason: str | None = None,
    ) -> str:
        revision_id = str(uuid.uuid4())
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO NOTE_REVISION (
                    id,
                    note_id,
                    previous_body,
                    new_body,
                    reason,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    note_id,
                    previous_body,
                    new_body,
                    reason,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        return revision_id

    def replace_note_body(
        self,
        *,
        note_id: str,
        new_body: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        note = self.get_note(note_id)
        if note is None:
            return None

        previous_body = str(note.get("body") or "")
        self.insert_note_revision(
            note_id=note_id,
            previous_body=previous_body,
            new_body=new_body,
            reason=reason,
        )

        with self.connection() as conn:
            conn.execute(
                "UPDATE NOTE SET body = ? WHERE id = ?",
                (new_body, note_id),
            )
            conn.execute(
                """
                UPDATE IMAGE_FILE
                SET ocr_text = ?
                WHERE message_id = (
                    SELECT message_id
                    FROM NOTE
                    WHERE id = ?
                )
                """,
                (new_body, note_id),
            )
            conn.commit()

        return self.get_note_with_source(note_id)

    def fetch_all(self, table: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_tag_name(tag: str) -> str:
        return " ".join(tag.strip().lower().split())

    @staticmethod
    def _expand_search_terms(query: str) -> list[str]:
        raw_terms = [
            term
            for term in re.split(r"\s+", query.strip())
            if len(term) >= 1
        ]
        if not raw_terms:
            return []

        expanded: list[str] = []
        seen: set[str] = set()
        alias_map = {
            "september": ["9월", "9"],
            "9월": ["september", "9"],
            "todo": ["할일", "할 일"],
            "할일": ["todo", "할 일"],
            "할": ["todo"],
        }
        for term in raw_terms:
            normalized = term.strip().lower()
            if len(term) >= 2 and normalized not in seen:
                seen.add(normalized)
                expanded.append(term)
            for alias in alias_map.get(normalized, []):
                alias_key = alias.lower()
                if alias_key in seen:
                    continue
                seen.add(alias_key)
                expanded.append(alias)
        return expanded

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
