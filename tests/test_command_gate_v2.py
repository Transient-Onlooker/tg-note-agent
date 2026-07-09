from __future__ import annotations

from pathlib import Path

from app.models.db import StoredMessage
from app.models.schemas import TextAnalysisResult
from tests.test_webhook import (
    AgentFallbackNIMProvider,
    FastPathForbiddenNIMProvider,
    FakeNIMProvider,
    MergeSuggestToolNIMProvider,
    build_client,
)


def test_regression_search_command_does_not_call_llm_or_save_router(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())

    source_message = StoredMessage(
        id="source-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 시험지 제작 기말고사 끝나고 하기",
    )
    db.insert_message(source_message)
    note_id = db.insert_note(
        source_message.id,
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 지구과학 시험 문제지 제작 계획",
            tags=["지구과학", "시험"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        source_message.raw_text,
    )

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 56,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "내 메모중에 지구과학 관련된거 뭐있더라",
            },
        },
    )

    assert response.status_code == 200
    assert db.fetch_all("MESSAGE")[-1]["status"] == "processed"
    assert len(db.fetch_all("NOTE")) == 1
    assert telegram.messages[-1] == {
        "chat_id": "777",
        "text": "관련 메모 1개를 찾았어.\n1. 지구과학 문제지 제작 계획\n기말고사 이후 지구과학 시험 문제지 제작 계획",
    }
    state = db.get_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_search_results",
    )
    assert state == {"note_ids": [note_id], "query": "지구과학 관련된거"}


def test_regression_correction_command_updates_existing_note_without_llm_or_create(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())

    source_message = StoredMessage(
        id="photo-message",
        telegram_message_id="20",
        chat_id="777",
        sender_id="123",
        raw_text="",
        content_type="photo",
    )
    db.insert_message(source_message)
    image_id = db.insert_image_file(
        message_id=source_message.id,
        telegram_file_id="file-1",
        telegram_file_unique_id="uniq-1",
        local_path=str(tmp_path / "images" / "note.jpg"),
        mime_type="image/jpeg",
        file_size=100,
        width=100,
        height=100,
    )
    db.update_image_analysis(
        image_id,
        ocr_text="9 눈썰맺?\n정수기 청소하기",
        summary="OCR 요약",
        image_type="note",
        confidence=0.7,
    )
    note_id = db.insert_note(
        source_message.id,
        TextAnalysisResult(
            title="OCR 메모",
            summary="OCR 요약",
            tags=["ocr"],
            category="note",
            confidence=0.8,
            raw_response='{"ok": true}',
        ),
        "9 눈썰맺?\n정수기 청소하기",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_artifact_note_id",
        value={"note_id": note_id},
    )

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 2,
            "message": {
                "message_id": 57,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "9 눈썰맺? -> 오늘 할 것 수정해줘.",
            },
        },
    )

    assert response.status_code == 200
    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 3,
            "message": {
                "message_id": 58,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "승인",
            },
        },
    )
    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert note is not None
    assert note["body"] == "오늘 할 것\n정수기 청소하기"
    assert note["image_ocr_text"] == "오늘 할 것\n정수기 청소하기"
    revisions = db.fetch_all("NOTE_REVISION")
    assert len(revisions) == 1
    assert "수정했어" in telegram.messages[-1]["text"]
    assert "변경" in telegram.messages[-1]["text"]


def test_regression_delete_command_soft_deletes_without_llm_or_create(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())

    source_message = StoredMessage(
        id="source-message",
        telegram_message_id="30",
        chat_id="777",
        sender_id="123",
        raw_text="개발 로그 정리",
    )
    db.insert_message(source_message)
    note_id = db.insert_note(
        source_message.id,
        TextAnalysisResult(
            title="개발 로그 정리",
            summary="에이전트 개발 로그",
            tags=["개발", "로그"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        source_message.raw_text,
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_artifact_note_id",
        value={"note_id": note_id},
    )

    first = client.post(
        "/webhook/telegram",
        json={
            "update_id": 3,
            "message": {
                "message_id": 58,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "그 메모 지워줘",
            },
        },
    )
    second = client.post(
        "/webhook/telegram",
        json={
            "update_id": 4,
            "message": {
                "message_id": 59,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "삭제 확인",
            },
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "삭제할까?" in telegram.messages[1]["text"]
    assert "삭제 확인" in telegram.messages[1]["text"]
    assert telegram.messages[-1]["text"] == "메모를 삭제했어.\n\n제목: 개발 로그 정리"
    assert db.count_notes() == 0
    note_rows = db.fetch_all("NOTE")
    assert len(note_rows) == 1
    assert note_rows[0]["deleted_at"] is not None
    pending_state = db.get_conversation_state(
        chat_id="777",
        sender_id="123",
        key="pending_delete_note_id",
    )
    assert pending_state is None


def test_explicit_save_response_keeps_summary_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, _db, telegram = build_client(tmp_path, FakeNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 5,
            "message": {
                "message_id": 60,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "오늘부터 개인 AI 에이전트 개발 로그를 텔레그램에 남기기로 했다.",
            },
        },
    )

    assert response.status_code == 200
    assert telegram.messages[-1]["text"].startswith("메모로 저장했어.\n\n요약:")
    assert "제목:" not in telegram.messages[-1]["text"]


def test_regression_note_query_returns_plain_text_summary_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, AgentFallbackNIMProvider())

    first_message = StoredMessage(
        id="note-1-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 문제지 제작 계획",
    )
    second_message = StoredMessage(
        id="note-2-message",
        telegram_message_id="11",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 시험 범위 정리",
    )
    db.insert_message(first_message)
    db.insert_note(
        first_message.id,
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 지구과학 문제지 제작",
            tags=["지구과학", "시험"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        first_message.raw_text,
    )
    db.insert_message(second_message)
    db.insert_note(
        second_message.id,
        TextAnalysisResult(
            title="지구과학 시험 범위 정리",
            summary="시험 범위와 출제 포인트 정리",
            tags=["지구과학", "범위"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        second_message.raw_text,
    )

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 6,
            "message": {
                "message_id": 61,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "내 메모 중에 지구과학 관련된 거 뭐 있었지",
            },
        },
    )

    assert response.status_code == 200
    assert telegram.messages[-1]["text"] == (
        "관련 메모 2개를 찾았어.\n"
        "1. 지구과학 시험 범위 정리\n"
        "시험 범위와 출제 포인트 정리\n"
        "2. 지구과학 문제지 제작 계획\n"
        "기말고사 이후 지구과학 문제지 제작"
    )


def test_regression_merge_approval_soft_deletes_merged_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, MergeSuggestToolNIMProvider())

    keep_message = StoredMessage(
        id="keep-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 시험지 제작 계획",
    )
    merge_message = StoredMessage(
        id="merge-message",
        telegram_message_id="11",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 시험 범위 정리",
    )
    db.insert_message(keep_message)
    db.insert_note(
        keep_message.id,
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 문제지 제작 계획",
            tags=["지구과학", "시험", "문제지"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        keep_message.raw_text,
    )
    db.insert_message(merge_message)
    db.insert_note(
        merge_message.id,
        TextAnalysisResult(
            title="지구과학 시험 범위 정리",
            summary="시험 범위와 출제 포인트 정리",
            tags=["지구과학", "시험", "범위"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        merge_message.raw_text,
    )

    client.post(
        "/webhook/telegram",
        json={
            "update_id": 7,
            "message": {
                "message_id": 64,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "메모로 저장한 것 중에 합칠 만한 거 있냐",
            },
        },
    )
    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 8,
            "message": {
                "message_id": 65,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "합쳐",
            },
        },
    )

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    active_notes = [note for note in notes if note["deleted_at"] is None]
    deleted_notes = [note for note in notes if note["deleted_at"] is not None]
    assert len(active_notes) == 1
    assert len(deleted_notes) == 1
    assert "지구과학 시험지 제작 계획" in active_notes[0]["body"]
    assert "지구과학 시험 범위 정리" in active_notes[0]["body"]
    assert db.fetch_all("MERGE_PROPOSAL")[0]["status"] == "approved"
    assert "병합 완료" in telegram.messages[-1]["text"]
