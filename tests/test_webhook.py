from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.integrations.notion import NotionExportResult
from app.models.db import Database


class FakeNIMProvider:
    model = "fake-model"

    def analyze_text(self, text: str):
        from app.models.schemas import TextAnalysisResult

        return TextAnalysisResult(
            title="액체연료 로켓 엔진의 재생냉각",
            summary="연소실 벽면 냉각 방식 조사 메모",
            tags=["rocket", "engine", "cooling"],
            category="note",
            confidence=0.93,
            raw_response='{"ok": true}',
        )


class FailingNIMProvider:
    model = "fake-model"

    def analyze_text(self, text: str):
        raise RuntimeError("nim failed")


class FakeTelegramClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send_message(self, chat_id: int | str, text: str) -> None:
        self.messages.append({"chat_id": str(chat_id), "text": text})


class FakeNotionClient:
    def export_note(self, *, title: str, summary: str, body: str, tags: list[str]) -> NotionExportResult:
        return NotionExportResult(page_id="notion-page-1", url="https://notion.so/page")


class FailingNotionClient:
    def export_note(self, *, title: str, summary: str, body: str, tags: list[str]) -> NotionExportResult:
        raise RuntimeError("notion failed")


def build_client(
    tmp_path: Path,
    nim_provider,
    notion_client=None,
) -> tuple[TestClient, Database, FakeTelegramClient]:
    db = Database(str(tmp_path / "app.sqlite"))
    db.initialize()
    telegram = FakeTelegramClient()
    app = create_app()
    app.state.database = db
    app.state.note_manager = __import__("app.services.note_manager", fromlist=["NoteManager"]).NoteManager(
        db,
        notion_client=notion_client,
    )
    app.state.nim_provider = nim_provider
    app.state.telegram_client = telegram
    app.state.notion_client = notion_client
    app.state.update_router = __import__("app.services.router", fromlist=["build_router"]).build_router(
        app.state.note_manager,
        app.state.nim_provider,
        app.state.telegram_client,
    )
    return TestClient(app), db, telegram


def test_allowed_user_text_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("NIM_API_KEY", "test-key")
    monkeypatch.setenv("NIM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("NIM_TEXT_MODEL", "test-model")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "ignored.sqlite"))
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 55,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "액체연료 로켓 엔진 냉각 방식 찾아보기",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"

    messages = db.fetch_all("MESSAGE")
    notes = db.fetch_all("NOTE")
    analyses = db.fetch_all("AI_ANALYSIS")
    assert len(messages) == 1
    assert messages[0]["status"] == "processed"
    assert len(notes) == 1
    assert json.loads(notes[0]["tags"]) == ["rocket", "engine", "cooling"]
    assert len(analyses) == 1
    assert len(telegram.messages) == 2
    assert telegram.messages[0]["text"] == "수신 완료."
    assert "저장했어." in telegram.messages[1]["text"]


def test_unauthorized_user_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 55,
                "chat": {"id": 777},
                "from": {"id": 999},
                "text": "unauthorized",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert db.fetch_all("MESSAGE") == []
    assert telegram.messages == []


def test_ai_failure_marks_message_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FailingNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 55,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "will fail",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    messages = db.fetch_all("MESSAGE")
    assert len(messages) == 1
    assert messages[0]["status"] == "ai_failed"
    assert len(telegram.messages) == 2
    assert telegram.messages[0]["text"] == "수신 완료."
    assert "AI 분석에는 실패" in telegram.messages[1]["text"]


def test_duplicate_message_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 55,
            "chat": {"id": 777},
            "from": {"id": 123},
            "text": "duplicate",
        },
    }

    first_response = client.post("/webhook/telegram", json=payload)
    second_response = client.post("/webhook/telegram", json=payload)

    assert first_response.status_code == 200
    assert first_response.json()["status"] == "accepted"
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "ignored"
    assert second_response.json()["detail"] == "duplicate_message"
    assert len(db.fetch_all("MESSAGE")) == 1
    assert len(telegram.messages) == 2


def test_notion_export_marks_note_and_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider(), FakeNotionClient())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 77,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "notion export",
            },
        },
    )

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert notes[0]["notion_page_id"] == "notion-page-1"
    assert notes[0]["notion_status"] == "exported"
    assert "Notion: 저장함" in telegram.messages[1]["text"]


def test_notion_export_failure_keeps_local_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider(), FailingNotionClient())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 88,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "notion fail",
            },
        },
    )

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert notes[0]["notion_page_id"] is None
    assert notes[0]["notion_status"] == "failed"
    assert "Notion: 저장 실패" in telegram.messages[1]["text"]
