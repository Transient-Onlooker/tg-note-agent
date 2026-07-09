from __future__ import annotations

from pathlib import Path

from tests.test_command_gate_regression import (
    _insert_text_note,
    _post_text,
)
from tests.test_webhook import FastPathForbiddenNIMProvider, build_client


def test_slash_fix_previews_then_applies_on_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="fix-source-1",
        title="미국 독립선언문 번역",
        summary="정부의 정당한 권력은 인민의 동의에서 나온다.",
        body="정부의 정당한 권력은 인민의 동의에서 나온다. 인민은 정부를 개혁할 권리가 있다.",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_list_results",
        value={"note_ids": [note_id]},
    )

    response = _post_text(client, message_id=1060, text="/fix 1번 메모의 인민을 시민으로 수정")

    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert "인민" in note["body"]
    assert "수정 미리보기" in telegram.messages[-1]["text"]
    pending = db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action")
    assert pending["type"] == "fix"
    assert pending["old_text"] == "인민"
    assert pending["new_text"] == "시민"

    response = _post_text(client, message_id=1061, text="승인")

    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert "인민" not in note["body"]
    assert "시민" in note["body"]
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action") is None
    assert len(db.fetch_all("NOTE_REVISION")) == 1
    assert "수정했어" in telegram.messages[-1]["text"]


def test_natural_fix_previews_and_can_cancel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="fix-source-2",
        title="용어 정리",
        summary="인민이라는 표현을 포함한다.",
        body="인민이라는 표현을 포함한다.",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_selected_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=1062, text="인민을 시민으로 수정해줘")

    assert response.status_code == 200
    assert "수정 미리보기" in telegram.messages[-1]["text"]
    assert "인민" in db.get_note_with_source(note_id)["body"]

    response = _post_text(client, message_id=1063, text="취소")

    assert response.status_code == 200
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action") is None
    assert "인민" in db.get_note_with_source(note_id)["body"]
    assert "취소" in telegram.messages[-1]["text"]
