from __future__ import annotations

from pathlib import Path

from tests.test_command_gate_regression import _insert_text_note, _post_text
from tests.test_webhook import FastPathForbiddenNIMProvider, build_client


def test_slash_list_paginates_with_next_prev_and_page(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    for index in range(12):
        _insert_text_note(
            db,
            message_id=f"page-{index}",
            title=f"메모 {index + 1}",
            body=f"본문 {index + 1}",
        )

    response = _post_text(client, message_id=2001, text="/list")

    assert response.status_code == 200
    assert "최근 저장된 항목 12개야." in telegram.messages[-1]["text"]
    assert "(1 / 2) 페이지" in telegram.messages[-1]["text"]
    assert "/next" in telegram.messages[-1]["text"]
    state = db.get_conversation_state(chat_id="777", sender_id="123", key="last_list_results")
    assert len(state["note_ids"]) == 10

    response = _post_text(client, message_id=2002, text="/next")

    assert response.status_code == 200
    assert "(2 / 2) 페이지" in telegram.messages[-1]["text"]
    state = db.get_conversation_state(chat_id="777", sender_id="123", key="last_list_results")
    assert len(state["note_ids"]) == 2

    response = _post_text(client, message_id=2003, text="/prev")

    assert response.status_code == 200
    assert "(1 / 2) 페이지" in telegram.messages[-1]["text"]

    response = _post_text(client, message_id=2004, text="/page 2")

    assert response.status_code == 200
    assert "(2 / 2) 페이지" in telegram.messages[-1]["text"]
