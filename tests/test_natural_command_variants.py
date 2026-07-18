from __future__ import annotations

from pathlib import Path

import pytest

from app.models.schemas import RouteDecision

from tests.test_command_gate_regression import _insert_text_note, _post_text
from tests.test_webhook import FakeNIMProvider, FastPathForbiddenNIMProvider, build_client


def test_natural_add_resolves_selected_note_with_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="natural-add-selected",
        title="회의 메모",
        summary="회의 요약",
        body="기존 회의 기록",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_selected_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(
        client,
        message_id=2201,
        text="그 메모에 참고 링크 https://example.com/spec 덧붙여줘",
    )

    assert response.status_code == 200
    assert "https://example.com/spec" not in db.get_note_with_source(note_id)["body"]
    pending = db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action")
    assert pending["type"] == "add"
    assert pending["note_id"] == note_id
    assert pending["append_text"] == "참고 링크 https://example.com/spec"

    response = _post_text(client, message_id=2202, text="확인")

    assert response.status_code == 200
    assert "https://example.com/spec" in db.get_note_with_source(note_id)["body"]


def test_technical_statement_with_add_word_is_not_add_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    text = (
        "OCR 파이프라인은 IMAGE_FILE에 "
        "ocr_text 필드를 추가해 저장한다."
    )
    update_router = client.app.state.update_router

    assert update_router._detect_direct_command(text) is None

    response = _post_text(client, message_id=2203, text=text)

    assert response.status_code == 200
    assert db.count_notes() == 1
    note = db.recent_notes(limit=1)[0]
    assert note["body"] == text

class IgnoreRouteNIMProvider(FakeNIMProvider):
    def route_text(self, text: str, **kwargs):
        return RouteDecision(
            route="ignore",
            confidence=0.99,
            reason="test ignore route",
        )


@pytest.mark.parametrize(
    "prefix",
    [
        "\uc800\uc7a5\ud574\uc918:",
        "\uba54\ubaa8\ub85c \ub0a8\uaca8\uc918:",
        "\uae30\ub85d\ud574\uc918:",
    ],
)
def test_explicit_save_prefix_forces_ai_persistence(tmp_path: Path, monkeypatch, prefix: str) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, IgnoreRouteNIMProvider())
    body = "\uc751\ub2f5 \uc9c0\uc5f0\uc744 \uae30\ub85d\ud558\uace0 \ub2e4\uc74c \ud14c\uc2a4\ud2b8\uc5d0\uc11c \ube44\uad50\ud55c\ub2e4."

    response = _post_text(client, message_id=2300, text=prefix + " " + body)

    assert response.status_code == 200
    assert db.count_notes() == 1
    note = db.recent_notes(limit=1)[0]
    assert note["body"] == body
    assert len(db.fetch_all("AI_ANALYSIS")) == 1
