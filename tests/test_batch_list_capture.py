from __future__ import annotations

from pathlib import Path

from app.models.schemas import RouteDecision, TextAnalysisResult
from app.services.list_capture import extract_explicit_batch_split, parse_note_list_items
from app.services.note_manager import NoteManager
from tests.test_command_gate_regression import _insert_text_note, _post_text
from tests.test_webhook import FakeNIMProvider, build_client


BATCH_TEXT = (
    "\ub2e4\uc774\uc18c \uc548\uacbd\ub2e6\uc774\n"
    "\ub2e4\uc774\uc18c \ud0dd\ubc30\ubc15\uc2a4(\ubf41\ubf41\uc774)\n"
    "\uc7a0\uc2e4 \ud53c\uc790\n"
    "\uc7a0\uc2e4 \uc548\uacbd\ud14c \uad50\uccb4\n"
    "\uc7a0\uc2e4 \uad50\ubcf4\ubb38\uace0 - \ub2f4\ubc30\uace0\uc591\uc774 1\ud3b8\n\n"
    "\uccad\ucd98\ub3fc\uc9c0 \uc2dc\ub9ac\uc988 \ub9c8\uc9c0\ub9c9 \ud55c\ucc95\ud130 \uc77d\uae30\n"
    "\ubc30\uadf8\ud558\uae30\n\n"
    "\ud0dd\ubc30 \ubd99\uc774\uae30 \uae00\ub77c\uc2a4\n"
    "\ubbf8\uc801\ubd84 \uc219\uc81c \ud558\uae30"
)


class BatchNIMProvider(FakeNIMProvider):
    def __init__(self, *, route: str = "create") -> None:
        self.route = route
        self.route_calls = 0
        self.analysis_calls = 0

    def route_text(self, text: str, **kwargs):
        self.route_calls += 1
        return RouteDecision(
            route=self.route,
            confidence=0.9,
            reason="batch test",
        )

    def analyze_text(self, text: str, **kwargs):
        self.analysis_calls += 1
        return TextAnalysisResult(
            title="\uc678\ucd9c\uacfc \ud560 \uc77c \ubb36\uc74c",
            summary="\uc678\ucd9c, \ub3c5\uc11c, \uac8c\uc784, \uc219\uc81c \ud56d\ubaa9\uc744 \uc815\ub9ac\ud55c \uba54\ubaa8.",
            tags=["todo", "shopping"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


def test_parser_extracts_user_batch_without_changing_raw_text() -> None:
    items = parse_note_list_items(BATCH_TEXT)

    assert len(items) == 9
    assert items[0].body == "\ub2e4\uc774\uc18c \uc548\uacbd\ub2e6\uc774"
    assert items[5].section_label == "\ubb36\uc74c 2"
    assert items[-1].position == 9
    assert BATCH_TEXT.endswith(items[-1].body)


def test_parser_does_not_split_wrapped_prose() -> None:
    paragraph = (
        "\uc624\ub298\uc740 \uc9c0\uad6c\uacfc\ud559 \ubb38\uc81c\uc9c0\ub97c \ub9cc\ub4e4\uae30 \uc704\ud55c \uc804\uccb4 \ubc29\ud5a5\uc744 \uc815\ub9ac\ud588\ub2e4.\n"
        "\uba3c\uc800 \ub2e8\uc6d0\ubcc4 \ubb38\uc81c \uc218\ub97c \uc815\ud558\uace0 \ub09c\uc774\ub3c4 \ubc30\ubd84\ub3c4 \ud568\uaed8 \uac80\ud1a0\ud560 \uc608\uc815\uc774\ub2e4.\n"
        "\uc644\uc131\ub41c \ubb38\uc81c\uc9c0\ub294 \uc8fc\ub9d0\uc5d0 \ub2e4\uc2dc \uc77d\uc73c\uba74\uc11c \uc624\ub958\ub97c \uc218\uc815\ud55c\ub2e4."
    )

    assert parse_note_list_items(paragraph) == []


def test_blank_group_does_not_inherit_previous_heading() -> None:
    text = (
        "장보기:\n"
        "- 안경닦이\n"
        "- 택배박스\n\n"
        "배그하기\n"
        "미적분 숙제"
    )

    items = parse_note_list_items(text)

    assert [item.section_label for item in items] == [
        "장보기",
        "장보기",
        "묶음 2",
        "묶음 2",
    ]

def test_parser_preserves_checkbox_completion() -> None:
    text = (
        "\ud560 \uc77c:\n"
        "- [ ] \uc815\uc218\uae30 \uccad\uc18c\n"
        "- [x] \ud0dd\ubc30 \ubcf4\ub0b4\uae30\n"
        "- \ubbf8\uc801\ubd84 \uc219\uc81c"
    )

    items = parse_note_list_items(text)

    assert [item.is_completed for item in items] == [False, True, False]
    assert all(item.section_label == "\ud560 \uc77c" for item in items)


def test_multiline_batch_forces_one_note_even_when_router_ignores(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    provider = BatchNIMProvider(route="ignore")
    client, db, telegram = build_client(tmp_path, provider)

    response = _post_text(client, message_id=3001, text=BATCH_TEXT)

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert notes[0]["body"] == BATCH_TEXT
    assert len(db.get_note_list_items(notes[0]["id"])) == 9
    assert provider.route_calls == 1
    assert provider.analysis_calls == 1
    assert "\ubb36\uc74c \uba54\ubaa8\ub85c \uc778\uc2dd\ud55c \ud56d\ubaa9: 9\uac1c" in telegram.messages[-1]["text"]


def test_batch_route_tool_cannot_bypass_note_persistence(tmp_path: Path, monkeypatch) -> None:
    class ToolRouteProvider(BatchNIMProvider):
        def route_text(self, text: str, **kwargs):
            self.route_calls += 1
            return RouteDecision(
                route="tool",
                confidence=0.9,
                reason="incorrect tool route",
                tool_name="count_notes",
            )

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    provider = ToolRouteProvider()
    client, db, _telegram = build_client(tmp_path, provider)

    response = _post_text(client, message_id=3006, text=BATCH_TEXT)

    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 1
    assert len(db.fetch_all("NOTE_LIST_ITEM")) == 9
    assert provider.route_calls == 1
    assert provider.analysis_calls == 1

def test_explicit_batch_split_previews_then_creates_notes_after_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    provider = BatchNIMProvider()
    client, db, telegram = build_client(tmp_path, provider)
    request_text = "\uac01\uac01 \uc800\uc7a5\ud574\uc918:\n" + BATCH_TEXT

    response = _post_text(client, message_id=3002, text=request_text)

    assert response.status_code == 200
    assert db.fetch_all("NOTE") == []
    pending = db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action")
    assert pending["type"] == "batch_split"
    assert len(pending["items"]) == 9
    assert provider.route_calls == 0
    assert provider.analysis_calls == 0
    assert "9\uac1c\uc758 \uba54\ubaa8\ub85c \ub098\ub220 \uc800\uc7a5\ud560\uae4c" in telegram.messages[-1]["text"]

    response = _post_text(client, message_id=3003, text="\uc2b9\uc778")

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 9
    assert provider.route_calls == 0
    assert provider.analysis_calls == 1
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action") is None
    assert "\ucd1d 9\uac1c\uc758 \uba54\ubaa8" in telegram.messages[-1]["text"]


def test_explicit_batch_split_cancel_creates_no_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, _telegram = build_client(tmp_path, BatchNIMProvider())

    _post_text(client, message_id=3004, text="\ub098\ub220 \uc800\uc7a5\ud574\uc918:\n" + BATCH_TEXT)
    response = _post_text(client, message_id=3005, text="\ucde8\uc18c")

    assert response.status_code == 200
    assert db.fetch_all("NOTE") == []
    assert db.get_conversation_state(chat_id="777", sender_id="123", key="pending_action") is None


def test_list_items_resync_after_correction_and_merge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    _client, db, _telegram = build_client(tmp_path, BatchNIMProvider())
    manager = NoteManager(db)
    first_id = _insert_text_note(
        db,
        message_id="batch-source-1",
        title="\uc8fc\ub9d0 \ud560 \uc77c",
        summary="\uc8fc\ub9d0 \ud560 \uc77c",
        body="- \uc548\uacbd\ud14c \uad50\uccb4\n- \ubbf8\uc801\ubd84 \uc219\uc81c",
    )
    second_id = _insert_text_note(
        db,
        message_id="batch-source-2",
        title="\ucd94\uac00 \ud560 \uc77c",
        summary="\ucd94\uac00 \ud560 \uc77c",
        body="- \ud0dd\ubc30 \ubcf4\ub0b4\uae30\n- \ubc30\uadf8\ud558\uae30",
    )
    manager.sync_note_list_items(first_id, db.get_note(first_id)["body"])
    manager.sync_note_list_items(second_id, db.get_note(second_id)["body"])

    manager.replace_note_text_fields(
        note_id=first_id,
        new_title="\uc8fc\ub9d0 \ud560 \uc77c",
        new_summary="\uc8fc\ub9d0 \ud560 \uc77c",
        new_body="- \uc548\uacbd\ud14c \uad50\uccb4\n- \ud655\ub960\uacfc \ud1b5\uacc4 \uc219\uc81c",
        reason="test",
    )
    assert [item["body"] for item in manager.get_note_list_items(first_id)] == [
        "\uc548\uacbd\ud14c \uad50\uccb4",
        "\ud655\ub960\uacfc \ud1b5\uacc4 \uc219\uc81c",
    ]

    manager.merge_notes(keep_note_id=first_id, merge_note_id=second_id)

    assert len(manager.get_note_list_items(first_id)) == 4
    assert manager.get_note_list_items(second_id) == []
    assert len(db.fetch_all("NOTE_REVISION")) == 1


def test_explicit_split_extractor_requires_a_split_command() -> None:
    assert extract_explicit_batch_split("\uac01\uac01 \uc800\uc7a5\ud574\uc918:\n" + BATCH_TEXT) == BATCH_TEXT
    assert extract_explicit_batch_split(BATCH_TEXT) is None
