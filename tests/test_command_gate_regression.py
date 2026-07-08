from __future__ import annotations

import json
from pathlib import Path

from app.models.db import StoredMessage
from app.models.schemas import RouteDecision, TextAnalysisResult
from tests.test_webhook import FastPathForbiddenNIMProvider, FakeNIMProvider, build_client


class EchoTextNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title=text[:40] or "제목 없음",
            summary=text,
            tags=["테스트"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


class ExplicitSaveIgnoredNIMProvider(EchoTextNIMProvider):
    def route_text(self, text: str, **kwargs):
        return RouteDecision(
            route="ignore",
            confidence=0.95,
            reason="simulated router miss",
        )

    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="ignored",
            summary="ignored",
            tags=[],
            category="chat",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
        )


class RewritingSummaryNIMProvider(EchoTextNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="저장 명령어 접두사 버그 수정",
            summary="저장 명령어가 제목, 요약, 본문에 남지 않도록 수정해야 한다. 테스트 케이스에서 확인됨.",
            tags=["버그"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


class DroppingAnchorNIMProvider(EchoTextNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="\uc800\uc7a5 \uba85\ub839\uc5b4 \ubbf8\ub0a8\uae40 \ubc84\uadf8 \uc218\uc815",
            summary="\uc800\uc7a5 \uba85\ub839\uc5b4\ub294 \uc81c\ubaa9\uacfc \uc694\uc57d\uacfc \ubcf8\ubb38\uc5d0 \ub0a8\uc73c\uba74 \uc548 \ub41c\ub2e4.",
            tags=["bug"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


class PrefixInMetadataNIMProvider(EchoTextNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="\uba54\ubaa8\ub85c \uc800\uc7a5\ud574\uc918: \uc800\uc7a5 \uba85\ub839\uc5b4 \ubbf8\ub0a8\uae40",
            summary="\uc800\uc7a5\ud574\uc918: \uc81c\ubaa9\uacfc \uc694\uc57d\uc5d0 \uc800\uc7a5 \uba85\ub839\uc5b4\uac00 \ub0a8\uc9c0 \uc54a\ub3c4\ub85d \ud655\uc778",
            tags=["bug"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


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


def test_numbered_detail_request_reads_list_item_instead_of_saving(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    for index in range(1, 6):
        _insert_text_note(
            db,
            message_id=f"detail-source-{index}",
            title=f"{index}번 제목",
            body=f"{index}번 본문 상세 내용",
            summary=f"{index}번 요약",
        )

    response = _post_text(client, message_id=1006, text="최근 저장된 항목들 모두 알려줘")
    assert response.status_code == 200

    response = _post_text(client, message_id=1007, text="5번 좀더 알려줘")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 5
    assert "1번 본문 상세 내용" in telegram.messages[-1]["text"]

    response = _post_text(client, message_id=1008, text="5번 메모 좀더 알려달라고")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 5
    assert "1번 본문 상세 내용" in telegram.messages[-1]["text"]


def test_slash_new_creates_note_with_ai_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, EchoTextNIMProvider())

    response = _post_text(client, message_id=1009, text="/new SLASH_NEW_0708. 새 메모 생성 테스트")

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert notes[0]["body"] == "SLASH_NEW_0708. 새 메모 생성 테스트"
    assert "메모로 저장했어" in telegram.messages[-1]["text"]


def test_slash_list_show_delete_are_command_gate_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    older_note_id = _insert_text_note(db, message_id="slash-1", title="대수 보고서", body="대수 본문")
    newer_note_id = _insert_text_note(db, message_id="slash-2", title="확률 보고서", body="확률 본문")

    response = _post_text(client, message_id=1010, text="/list")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 2
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="last_list_results") == {
        "note_ids": [newer_note_id, older_note_id],
    }

    response = _post_text(client, message_id=1013, text="/show 2번 메모")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 2
    assert "대수 본문" in telegram.messages[-1]["text"]

    response = _post_text(client, message_id=1014, text="/delete 2번 메모")
    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 2
    assert "삭제할까" in telegram.messages[-1]["text"]
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_delete_note_id") == {
        "note_id": older_note_id,
    }


def test_slash_mutating_commands_do_not_save_before_approval_flow_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    _insert_text_note(db, message_id="slash-3", title="대수 메모", body="대수 본문")

    for offset, text in enumerate(
        (
            "/add 1번 메모에 후속 내용 추가",
            "/fix 1번 메모의 대수를 확률과 통계로 수정",
            "/dedupe 대수 관련 메모",
        ),
        start=1,
    ):
        response = _post_text(client, message_id=1020 + offset, text=text)
        assert response.status_code == 200
        assert len(db.fetch_all("NOTE")) == 1
        assert "메모로 저장하지도 않았어" in telegram.messages[-1]["text"]


def test_unknown_slash_command_is_not_saved_as_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())

    response = _post_text(client, message_id=1025, text="/unknown 이건 저장되면 안 된다")

    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 0
    assert "메모로 저장하진 않았어" in telegram.messages[-1]["text"]


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


def test_duplicate_delete_request_does_not_save_command_as_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    duplicate_body = "DUPLICATE_DELETE_BODY_0708. exact duplicate"
    _insert_text_note(db, message_id="duplicate-source-1", title="old duplicate", body=duplicate_body)
    _insert_text_note(db, message_id="duplicate-source-2", title="new duplicate", body=duplicate_body)
    _insert_text_note(db, message_id="unique-source", title="unique", body="unique body")

    response = _post_text(
        client,
        message_id=1025,
        text="\uc911\ubcf5\ub41c \uba54\ubaa8 \uc0ad\uc81c\ud574\uc918 \uc9c0\uae08 \uba54\ubaa8\ub4e4\uc911\uc5d0.",
    )

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    active_notes = [note for note in notes if note["deleted_at"] is None]
    deleted_notes = [note for note in notes if note["deleted_at"] is not None]
    assert len(notes) == 3
    assert len(active_notes) == 2
    assert len(deleted_notes) == 1
    assert deleted_notes[0]["body"] == duplicate_body
    assert not any(note["body"].startswith("\uc911\ubcf5\ub41c \uba54\ubaa8") for note in notes)
    assert "\uc911\ubcf5 \uba54\ubaa8 1\uac1c" in telegram.messages[-1]["text"]


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


def test_explicit_save_prefix_is_removed_before_note_storage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, EchoTextNIMProvider())

    response = _post_text(
        client,
        message_id=1052,
        text="메모로 저장해줘: tg-note-agent v1 테스트. 텍스트 저장 전처리 확인.",
    )

    assert response.status_code == 200
    note = db.fetch_all("NOTE")[0]
    assert note["title"].startswith("tg-note-agent v1 테스트")
    assert note["summary"] == "tg-note-agent v1 테스트. 텍스트 저장 전처리 확인."
    assert note["body"] == "tg-note-agent v1 테스트. 텍스트 저장 전처리 확인."


def test_explicit_save_is_stored_even_when_router_returns_ignore(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, ExplicitSaveIgnoredNIMProvider())

    response = _post_text(
        client,
        message_id=1054,
        text="메모로 저장해줘: PREFIX_FIX_0708_A. 저장 명령어는 남으면 안 된다.",
    )

    assert response.status_code == 200
    note = db.fetch_all("NOTE")[0]
    assert note["title"].startswith("PREFIX_FIX_0708_A")
    assert note["summary"] == "PREFIX_FIX_0708_A. 저장 명령어는 남으면 안 된다."
    assert note["body"] == "PREFIX_FIX_0708_A. 저장 명령어는 남으면 안 된다."
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="last_selected_note_id") == {
        "note_id": note["id"],
    }


def test_explicit_save_allows_ai_title_and_summary_without_leading_anchor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, RewritingSummaryNIMProvider())

    response = _post_text(
        client,
        message_id=1057,
        text="메모로 저장해줘: USER_PREFIX_FIX_0708. 저장 명령어는 제목과 요약과 본문에 남으면 안 된다.",
    )

    assert response.status_code == 200
    note = db.fetch_all("NOTE")[0]
    assert note["title"] == "저장 명령어 접두사 버그 수정"
    assert note["summary"] == "저장 명령어가 제목, 요약, 본문에 남지 않도록 수정해야 한다. 테스트 케이스에서 확인됨."
    assert note["body"] == "USER_PREFIX_FIX_0708. 저장 명령어는 제목과 요약과 본문에 남으면 안 된다."


def test_explicit_save_uses_ai_metadata_even_when_anchor_is_dropped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, DroppingAnchorNIMProvider())
    body = "USER_PREFIX_FIX_0708. \uc800\uc7a5 \uba85\ub839\uc5b4\ub294 \uc81c\ubaa9\uacfc \uc694\uc57d\uacfc \ubcf8\ubb38\uc5d0 \ub0a8\uc73c\uba74 \uc548 \ub41c\ub2e4."

    response = _post_text(
        client,
        message_id=1060,
        text="\uba54\ubaa8\ub85c \uc800\uc7a5\ud574\uc918: " + body,
    )

    assert response.status_code == 200
    note = db.fetch_all("NOTE")[0]
    assert note["title"] == "\uc800\uc7a5 \uba85\ub839\uc5b4 \ubbf8\ub0a8\uae40 \ubc84\uadf8 \uc218\uc815"
    assert note["summary"] == "\uc800\uc7a5 \uba85\ub839\uc5b4\ub294 \uc81c\ubaa9\uacfc \uc694\uc57d\uacfc \ubcf8\ubb38\uc5d0 \ub0a8\uc73c\uba74 \uc548 \ub41c\ub2e4."
    assert note["body"] == body


def test_explicit_save_strips_save_prefix_from_ai_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, PrefixInMetadataNIMProvider())
    body = "PREFIX_METADATA_0708. \uc800\uc7a5 \uba85\ub839\uc5b4\ub294 metadata\uc5d0 \ub0a8\uc73c\uba74 \uc548 \ub41c\ub2e4."

    response = _post_text(
        client,
        message_id=1061,
        text="\uba54\ubaa8\ub85c \uc800\uc7a5\ud574\uc918: " + body,
    )

    assert response.status_code == 200
    note = db.fetch_all("NOTE")[0]
    assert note["title"] == "\uc800\uc7a5 \uba85\ub839\uc5b4 \ubbf8\ub0a8\uae40"
    assert note["summary"] == "\uc81c\ubaa9\uacfc \uc694\uc57d\uc5d0 \uc800\uc7a5 \uba85\ub839\uc5b4\uac00 \ub0a8\uc9c0 \uc54a\ub3c4\ub85d \ud655\uc778"
    assert note["body"] == body


def test_duplicate_explicit_save_body_is_not_saved_twice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, EchoTextNIMProvider())
    text = "메모로 저장해줘: DUPLICATE_BODY_0708. 같은 본문은 반복 저장하지 않는다."

    first = _post_text(client, message_id=1058, text=text)
    second = _post_text(client, message_id=1059, text=text)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(db.fetch_all("NOTE")) == 1
    assert "새로 추가하진 않았어" in telegram.messages[-1]["text"]


def test_correction_after_new_save_targets_new_note_not_stale_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, EchoTextNIMProvider())
    stale_note_id = _insert_text_note(
        db,
        message_id="stale-source",
        title="오래된 메모",
        summary="오래된 요약",
        body="오래된 본문",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_selected_note_id",
        value={"note_id": stale_note_id},
    )

    response = _post_text(
        client,
        message_id=1055,
        text="메모로 저장해줘: NEW_SELECTION_TOKEN. 새 저장 메모가 수정 대상이어야 한다.",
    )
    assert response.status_code == 200
    selected_state = db.get_conversation_state(chat_id="777", sender_id="123", key="last_selected_note_id")
    assert isinstance(selected_state, dict)
    new_note_id = selected_state["note_id"]
    assert new_note_id != stale_note_id

    response = _post_text(client, message_id=1056, text="NEW_SELECTION_TOKEN. 를 삭제해")

    assert response.status_code == 200
    assert db.get_note_with_source(stale_note_id)["body"] == "오래된 본문"
    updated_new_note = db.get_note_with_source(new_note_id)
    assert updated_new_note["body"] == "새 저장 메모가 수정 대상이어야 한다."


def test_delete_phrase_correction_updates_title_summary_and_body(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="source-4",
        title="메모로 저장해줘: 개발 로그",
        summary="메모로 저장해줘: 오늘 작업 요약",
        body="메모로 저장해줘: tg-note-agent v1 테스트 본문",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_selected_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=1053, text="메모로 저장해줘: 를 삭제해")

    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert note["title"] == "개발 로그"
    assert note["summary"] == "오늘 작업 요약"
    assert note["body"] == "tg-note-agent v1 테스트 본문"
