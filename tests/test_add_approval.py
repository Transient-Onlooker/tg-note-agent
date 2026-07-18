from __future__ import annotations

from pathlib import Path

from tests.test_command_gate_regression import _insert_text_note, _post_text
from app.models.schemas import TextAnalysisResult
from tests.test_webhook import FakeNIMProvider, FastPathForbiddenNIMProvider, build_client


class SummaryRewriteNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="확률과 통계 보고서",
            summary="확률과 통계의 개념과 활용 사례를 간결하게 정리한 보고서 메모.",
            tags=["확률과 통계"],
            category="note",
            confidence=0.94,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


def test_slash_add_previews_then_appends_on_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="add-source-1",
        title="확률과 통계 보고서",
        summary="확률과 통계 개요",
        body="확률과 통계는 자료를 분석하는 학문이다.",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_list_results",
        value={"note_ids": [note_id]},
    )

    response = _post_text(client, message_id=2101, text="/add 1번 메모에 7월 10일까지 예시 문단 추가")

    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert "7월 10일까지" not in note["body"]
    assert "추가 미리보기" in telegram.messages[-1]["text"]
    pending = db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action")
    assert pending["type"] == "add"
    assert pending["append_text"] == "7월 10일까지 예시 문단"

    response = _post_text(client, message_id=2102, text="승인")

    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert "7월 10일까지 예시 문단" in note["body"]
    assert "추가: 7월 10일까지 예시 문단" in note["summary"]
    assert len(db.fetch_all("NOTE_REVISION")) == 1
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action") is None
    assert "기존 메모에 추가했어" in telegram.messages[-1]["text"]


def test_slash_add_can_use_selected_note_and_cancel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="add-source-2",
        title="대수 노트",
        summary="대수 개요",
        body="대수 기본 개념",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_selected_note_id",
        value={"note_id": note_id},
    )

    response = _post_text(client, message_id=2103, text="/add 삼각함수 참고 링크")

    assert response.status_code == 200
    assert "추가 미리보기" in telegram.messages[-1]["text"]

    response = _post_text(client, message_id=2104, text="취소")

    assert response.status_code == 200
    assert "삼각함수 참고 링크" not in db.get_note_with_source(note_id)["body"]
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action") is None


def test_slash_add_summary_rewrite_does_not_append_instruction(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, SummaryRewriteNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="add-source-3",
        title="확률과 통계 보고서",
        summary="7월 8일까지 보고서 작성. 확률과 통계는 불확실한 사건을 수치로 분석하",
        body="확률과 통계는 불확실한 사건을 수치로 분석하고 자료를 바탕으로 합리적인 결론을 내리는 데 도움을 주는 학문이다.",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_list_results",
        value={"note_ids": [note_id]},
    )

    response = _post_text(client, message_id=2105, text="/add 1번 메모에 요약 다시 써줘")

    assert response.status_code == 200
    assert "요약 재작성 미리보기" in telegram.messages[-1]["text"]
    pending = db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action")
    assert pending["type"] == "summary_rewrite"
    assert "요약 다시 써줘" not in pending["new_body"]

    response = _post_text(client, message_id=2106, text="승인")

    assert response.status_code == 200
    note = db.get_note_with_source(note_id)
    assert "요약 다시 써줘" not in note["body"]
    assert note["summary"] == "확률과 통계의 개념과 활용 사례를 간결하게 정리한 보고서 메모."
    assert "요약을 다시 썼어" in telegram.messages[-1]["text"]

def test_natural_add_uses_preview_and_approval_without_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, FastPathForbiddenNIMProvider())
    note_id = _insert_text_note(
        db,
        message_id="add-source-natural",
        title="주간 계획",
        summary="현재 주간 계획",
        body="화요일에 계획을 정리한다.",
    )
    db.set_conversation_state(
        chat_id="777",
        sender_id="123",
        key="last_list_results",
        value={"note_ids": [note_id]},
    )

    response = _post_text(
        client,
        message_id=2107,
        text="1번 메모에 수요일 회의 일정 추가해줘",
    )

    assert response.status_code == 200
    assert "수요일 회의 일정" not in db.get_note_with_source(note_id)["body"]
    pending = db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action")
    assert pending["type"] == "add"
    assert pending["append_text"] == "수요일 회의 일정"

    response = _post_text(client, message_id=2108, text="확인")

    assert response.status_code == 200
    assert "수요일 회의 일정" in db.get_note_with_source(note_id)["body"]
    assert len(db.fetch_all("NOTE_REVISION")) == 1
