from __future__ import annotations

import json
import re
from pathlib import Path

from app.models.schemas import TextAnalysisResult
from app.services.router import UpdateRouter
from tests.test_webhook import FakeNIMProvider, build_client


HAN_IDEOGRAPH_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]"
)


class HanjaMetadataNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="친구 약속违约에 대한 불만",
            summary="친구가 약속을 반복违约해서 신뢰 문제가 생겼다는 불만.",
            tags=["친구", "违约", "trust"],
            category="note",
            confidence=0.93,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


def test_ai_generated_note_metadata_and_reply_remove_hanja(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "200")
    monkeypatch.setattr("app.services.router.BackgroundTasks.add_task", lambda _self, fn, *args: fn(*args))
    client, db, telegram = build_client(tmp_path, HanjaMetadataNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 9101,
            "message": {
                "message_id": 9101,
                "date": 1,
                "chat": {"id": 100},
                "from": {"id": 200},
                "text": "/new 친구와 한 약속이 반복해서 지켜지지 않아 속상하다.",
            },
        },
    )

    assert response.status_code == 200
    note = db.recent_notes(limit=1)[0]
    assert HAN_IDEOGRAPH_RE.search(note["title"]) is None
    assert HAN_IDEOGRAPH_RE.search(note["summary"]) is None
    assert all(HAN_IDEOGRAPH_RE.search(tag) is None for tag in json.loads(note["tags"]))
    assert note["body"] == "친구와 한 약속이 반복해서 지켜지지 않아 속상하다."
    assert HAN_IDEOGRAPH_RE.search(telegram.messages[-1]["text"]) is None


def test_hanja_only_generated_fields_use_safe_korean_fallbacks() -> None:
    analysis = TextAnalysisResult(
        title="违约",
        summary="违约",
        tags=["违约"],
        category="note",
        confidence=0.5,
        raw_response="{}",
    )

    assert analysis.title == "메모"
    assert analysis.summary == "원문을 저장한 메모."
    assert analysis.tags == []


def test_ai_generated_search_and_agent_answers_remove_hanja() -> None:
    search_reply = UpdateRouter._sanitize_search_message(
        "관련 메모에서 약속 \u8fdd\u7ea6 내용을 찾았어.",
        [],
    )
    agent_reply = UpdateRouter._sanitize_agent_response(
        "친구가 약속을 반복 \u8fdd\u7ea6했다는 내용이야.",
        [],
    )

    assert HAN_IDEOGRAPH_RE.search(search_reply) is None
    assert HAN_IDEOGRAPH_RE.search(agent_reply) is None
    assert "관련 메모" in search_reply
    assert "친구가 약속을 반복" in agent_reply
