from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.db import Database
from tests.test_webhook import FakeNIMProvider, FakeTelegramClient


class TimeoutTelegramClient(FakeTelegramClient):
    def send_message(self, chat_id: int | str, text: str) -> None:
        raise httpx.ConnectTimeout("telegram sendMessage timed out")


def build_timeout_client(tmp_path: Path) -> tuple[TestClient, Database]:
    db = Database(str(tmp_path / "app.sqlite"))
    db.initialize()
    telegram = TimeoutTelegramClient()
    app = create_app()
    app.state.database = db
    app.state.note_manager = __import__("app.services.note_manager", fromlist=["NoteManager"]).NoteManager(db)
    app.state.nim_provider = FakeNIMProvider()
    app.state.telegram_client = telegram
    app.state.notion_client = None
    app.state.image_archive = __import__("app.services.image_archive", fromlist=["ImageArchive"]).ImageArchive(
        image_root=str(tmp_path / "images"),
        telegram_client=telegram,
        db=db,
    )
    app.state.update_router = __import__("app.services.router", fromlist=["build_router"]).build_router(
        app.state.note_manager,
        app.state.nim_provider,
        app.state.telegram_client,
        app.state.image_archive,
    )
    return TestClient(app), db


def test_telegram_send_message_timeout_does_not_return_500(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db = build_timeout_client(tmp_path)

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 99,
            "message": {
                "message_id": 155,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "액체연료 로켓 엔진 냉각 방식 찾아보기",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    messages = db.fetch_all("MESSAGE")
    assert len(messages) == 1
    assert messages[0]["status"] == "reply_failed"
    assert len(db.fetch_all("NOTE")) == 1
