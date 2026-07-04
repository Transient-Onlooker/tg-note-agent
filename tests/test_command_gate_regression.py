from __future__ import annotations

import json
from pathlib import Path

from app.models.db import StoredMessage
from app.models.schemas import TextAnalysisResult
from tests.test_webhook import FastPathForbiddenNIMProvider, FakeNIMProvider, build_client


def _post_text(client, *, message_id: int, text: str):
    return client.post(
        "/webhook/telegram",
        json={
            "update_id": message_id,
            "message": {
                "message_id": message_id,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": text,
            },
        },
    )


def _insert_text_note(db, *, message_id: str, title: str, body: str, summary: str | None = None) -> str:
    db.insert_message(
        StoredMessage(
            id=message_id,
            telegram_message_id=message_id,
            chat_id="777",
            sender_id="123",
            raw_text=body,
        )
    )
    return db.insert_note(
        message_id,
        TextAnalysisResult(
            title=title,
            summary=summary or body[:40],
            tags=["테스트"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        body,
    )


def _insert_image_note(db, *, body: str) -> str:
    message = StoredMessage(
        id="photo-message",
        telegram_message_id="900",
        chat_id="777",
        sender_id="123",
        raw_text="",
        content_type="photo",
    )
    db.insert_message(message)
    image_id = db.insert_image_file(
        message_id=message.id,
        telegram_file_id="file-1",
        telegram_file_unique_id="uniq-1",
        local_path="note.jpg",
        mime_type="image/jpeg",
        file_size=100,
        width=100,
        height=100,
    )
    db.update_image_analysis(
        image_id,
        ocr_text=body,
        summary="OCR 요약",
        image_type="handwritten_note",
        confidence=0.8,
    )
    return db.insert_note(
        message.id,
        TextAnalysisResult(
            title="OCR 메모",
            summary="OCR 요약",
            tags=["OCR"],
            category="note",
            confidence=0.8,
            raw_response='{"ok": true}',
        ),
        body,
    )


def test_recent_list_and_numbered_selection_do_not_call_llm_or_save(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(db, message_id="source-1", title="이항분포 메모", body="X ~ B(n, p)")

    response = _post_text(client, message_id=1001, text="최근 저장된 항목들 모두 알려줘")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 1
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="last_list_results") == {
        "note_ids": [note_id],
    }

    response = _post_text(client, message_id=1002, text="1번 메모")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 1
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="last_selected_note_id") == {
        "note_id": note_id,
    }
    assert "선택" in telegram.messages[-1]["text"]


def test_full_list_command_has_priority_over_read_and_correction(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    first_note_id = _insert_text_note(db, message_id="list-1", title="첫 메모", body="첫 본문")
    second_note_id = _insert_text_note(db, message_id="list-2", title="둘째 메모", body="둘째 본문")
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_selected_note_id",
        value={"note_id": first_note_id},
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="pending_correction",
        value={"note_id": first_note_id, "old_text": "첫", "new_text": "새"},
    )

    response = _post_text(client, message_id=1003, text="전체 메모 목록 알려줘")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 2
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_correction") is None
    list_state = db.get_conversation_state(chat_id="777", sender_id="123", key="last_list_results")
    assert list_state == {"note_ids": [second_note_id, first_note_id]}
    assert "최근 저장된 항목" in telegram.messages[-1]["text"]
    assert "1. 둘째 메모" in telegram.messages[-1]["text"]
    assert "2. 첫 메모" in telegram.messages[-1]["text"]


def test_discourse_prefix_with_all_notes_stays_list_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    _insert_text_note(db, message_id="list-3", title="하나", body="본문 하나")
    _insert_text_note(db, message_id="list-4", title="둘", body="본문 둘")

    response = _post_text(client, message_id=1004, text="아니 그거 말고 모든 메모")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 2
    assert "최근 저장된 항목" in telegram.messages[-1]["text"]
    assert "수정했어" not in telegram.messages[-1]["text"]


def test_discourse_prefix_with_saved_notes_phrase_stays_list_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    _insert_text_note(db, message_id="list-5", title="하나", body="본문 하나")
    _insert_text_note(db, message_id="list-6", title="둘", body="본문 둘")

    response = _post_text(client, message_id=1005, text="아니 그거 말고 여태까지 저장된 메모들 말이야")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 2
    assert "최근 저장된 항목" in telegram.messages[-1]["text"]
    assert "수정" not in telegram.messages[-1]["text"]


def test_original_read_and_search_are_db_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="source-2",
        title="이항분포 정리",
        body="이항분포에서 p는 성공 확률이다.",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_artifact_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=1011, text="요약 말고 원문 전체 보여줘")
    assert response.status_code == 200
    assert "이항분포에서 p는 성공 확률이다." in telegram.messages[-1]["text"]
    assert len(db.fetch_all("NOTE")) == 1

    response = _post_text(client, message_id=1012, text="이항분포 관련 메모 알려줘")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 1
    state = db.get_conversation_state(chat_id="777", sender_id="123", key="last_search_results")
    assert state == {"note_ids": [note_id], "query": "이항분포"}


def test_delete_request_and_confirm_are_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(db, message_id="source-3", title="삭제 대상", body="삭제 테스트")
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_selected_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=1021, text="그 메모 삭제해줘")
    assert response.status_code == 200
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_delete_note_id") == {
        "note_id": note_id,
    }
    assert "삭제" in telegram.messages[-1]["text"]

    response = _post_text(client, message_id=1022, text="삭제 확인")
    assert response.status_code == 200
    assert db.get_note(note_id) is None
    assert db.get_note_any_status(note_id)["deleted_at"] is not None

    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="pending_delete_note_id",
        value={"note_id": note_id},
    )
    response = _post_text(client, message_id=1023, text="삭제 확인")
    assert response.status_code == 200
    assert "이미 삭제" in telegram.messages[-1]["text"]

    response = _post_text(client, message_id=1024, text="삭제 확인")
    assert response.status_code == 200
    assert "삭제 대기" in telegram.messages[-1]["text"]


def test_ocr_correction_updates_note_and_image_without_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_image_note(db, body="9 눈썰맺?\nX ~ B(n, p)")
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_image_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=1031, text="9 눈썰맺? 이 아니라 오늘 할 것이야. 수정해줘")
    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert note["body"] == "오늘 할 것\nX ~ B(n, p)"
    assert note["image_ocr_text"] == "오늘 할 것\nX ~ B(n, p)"
    assert len(db.fetch_all("NOTE_REVISION")) == 1
    assert "변경 전" in telegram.messages[-1]["text"]
    assert "변경 후" in telegram.messages[-1]["text"]


def test_correction_without_explicit_fix_word_and_technical_ocr_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_image_note(db, body="9월 첫값\n정수기 청소")
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_image_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=1041, text="9월 첫값이 아니라, 오늘 할것 이야.")
    assert response.status_code == 200
    assert db.get_note_with_source(note_id)["image_ocr_text"] == "오늘 할것\n정수기 청소"
    assert len(db.fetch_all("NOTE")) == 1

    tech_client, tech_db, tech_telegram = build_client(tmp_path / "tech", FastPathForbiddenNIMProvider())
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    response = _post_text(
        tech_client,
        message_id=1042,
        text="OCR 파이프라인은 IMAGE_FILE에 ocr_text, summary, image_type, confidence를 따로 저장한다.",
    )
    assert response.status_code == 200
    assert len(tech_db.fetch_all("NOTE")) == 1
    tech_note = tech_db.fetch_all("NOTE")[0]
    assert tech_note["title"] == "OCR 파이프라인 저장 구조"
    assert tech_note["summary"] == "OCR 파이프라인은 IMAGE_FILE에 ocr_text, summary, image_type, confidence를 따로 저장한다."
    assert tech_note["body"] == "OCR 파이프라인은 IMAGE_FILE에 ocr_text, summary, image_type, confidence를 따로 저장한다."
    assert set(["ocr", "image_file", "pipeline"]).issubset(set(json.loads(tech_note["tags"])))
    assert tech_telegram.messages[-1]["text"].startswith("메모로 저장했어.")


def test_ocr_word_with_read_verb_is_command_not_note_save(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_image_note(db, body="OCR 원문 내용")
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_image_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=1051, text="OCR 원문 보여줘")
    assert response.status_code == 200
    assert "OCR 원문 내용" in telegram.messages[-1]["text"]
    assert len(db.fetch_all("NOTE")) == 1
    assert db.fetch_all("MESSAGE")[-1]["status"] == "processed"
