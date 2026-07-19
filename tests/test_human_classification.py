from __future__ import annotations

import json

from app.models.schemas import TextAnalysisResult
from app.services.list_capture import parse_note_list_items
from app.services.nim_provider import NvidiaNIMProvider
from app.services.router import UpdateRouter


def _router() -> UpdateRouter:
    return object.__new__(UpdateRouter)


def _provider() -> NvidiaNIMProvider:
    return NvidiaNIMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        router_model="router-model",
        text_model="text-model",
        max_tokens=900,
    )


def test_narrative_with_modify_word_is_not_a_correction_command() -> None:
    text = (
        "\uc624\ub298\uc740 \uc2dc\ud5d8 \ubb38\uc81c\uc9c0 \ubc29\ud5a5\uc744 \uc815\ub9ac\ud588\ub2e4.\n"
        "\ub2e8\uc6d0\ubcc4 \ubb38\uc81c \uc218\uc640 \ub09c\uc774\ub3c4\ub97c \uac80\ud1a0\ud560 \uc608\uc815\uc774\ub2e4.\n"
        "\uc8fc\ub9d0\uc5d0 \ub2e4\uc2dc \uc77d\uace0 \uc624\ub958\ub97c \uc218\uc815\ud55c\ub2e4."
    )

    router = _router()
    assert router._detect_direct_command(text) is None
    assert router._looks_like_meta_command(text) is False
    assert parse_note_list_items(text) == []


def test_explicit_correction_requests_still_use_command_gate() -> None:
    router = _router()

    numbered = router._detect_direct_command("1\ubc88 \uba54\ubaa8 \uc218\uc815")
    referenced = router._detect_direct_command("\uadf8 \uba54\ubaa8 \uc218\uc815\ud574\uc918")
    replacement = router._detect_direct_command(
        "9\uc6d4 \uccab\uac12\uc774 \uc544\ub2c8\ub77c \uc624\ub298 \ud560 \uac83\uc774\uc57c"
    )

    assert numbered is not None and numbered.name == "correct_last_note"
    assert referenced is not None and referenced.name == "correct_last_note"
    assert replacement is not None and replacement.name == "correct_last_note"


def test_modify_related_note_query_remains_search() -> None:
    command = _router()._detect_direct_command(
        "수정 관련 메모 찾아줘"
    )

    assert command is not None
    assert command.name == "search_notes"

def test_heuristic_fallback_keeps_short_human_todos_and_study_notes() -> None:
    provider = _provider()
    texts = (
        "\ub0b4\uc77c \ud559\uad50 \ub05d\ub098\uace0 \uc9c0\uad6c\uacfc\ud559 \ubb38\uc81c\uc9c0 \ub9cc\ub4e4\uae30",
        "\uc218\ud559 \uc2dc\ud5d8 18\ubc88 \ubb38\uc81c \uc624\ub958 \ud655\uc778",
        "\uccad\ucd98\ub3fc\uc9c0 \uc2dc\ub9ac\uc988 \ub9c8\uc9c0\ub9c9 \ud55c \ucc95\ud130 \uc77d\uae30",
        "\ud0dd\ubc30 \ubcf4\ub0b4\uae30",
        "\ubc30\uadf8\ud558\uae30",
    )

    assert [
        provider._build_heuristic_route_decision(
            text=text,
            candidate_notes=[],
            conversation_context=[],
        ).route
        for text in texts
    ] == ["create", "create", "create", "create", "create"]


def test_heuristic_fallback_still_ignores_casual_and_do_not_save_text() -> None:
    provider = _provider()

    hello = provider._build_heuristic_route_decision(
        text="\uc548\ub155",
        candidate_notes=[],
        conversation_context=[],
    )
    weather = provider._build_heuristic_route_decision(
        text="\uc624\ub298 \ub0a0\uc528 \uc5b4\ub54c",
        candidate_notes=[],
        conversation_context=[],
    )
    blocked = provider._build_heuristic_route_decision(
        text="\uc774\uac74 \uc800\uc7a5\ud558\uc9c0 \ub9c8",
        candidate_notes=[],
        conversation_context=[],
    )

    assert hello.route == "ignore"
    assert weather.route == "ignore"
    assert blocked.route == "ignore"
    assert blocked.confidence == 0.99


def test_single_candidate_continuation_uses_append_fallback() -> None:
    provider = _provider()
    candidate = {
        "id": "earth-note",
        "title": "\uc9c0\uad6c\uacfc\ud559 \ubb38\uc81c\uc9c0 \uc81c\uc791",
        "summary": "\ubb38\uc81c\uc9c0 \uc81c\uc791 \uacc4\ud68d",
        "body": "\uc9c0\uad6c\uacfc\ud559 \ubb38\uc81c\uc9c0\ub97c \ub9cc\ub4e0\ub2e4.",
    }

    decision = provider._build_heuristic_route_decision(
        text="\uadf8\ub9ac\uace0 \uc2dc\ud5d8 \ubc94\uc704\ub3c4 \uac19\uc774 \uc815\ub9ac",
        candidate_notes=[candidate],
        conversation_context=[],
    )

    assert decision.route == "append"
    assert decision.target_note_id == "earth-note"


def test_router_recovers_task_like_text_from_wrong_search_tool(monkeypatch) -> None:
    provider = _provider()

    def fake_post(payload, *, model_name: str, read_timeout: float):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "route": "tool",
                                "confidence": 0.9,
                                "target_note_id": None,
                                "reason": "note query",
                                "tool_name": "search_notes",
                                "tool_query": "수학 시험 18번",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post)

    result = provider.route_text(
        "수학 시험 18번 문제 오류 확인"
    )

    assert result.route == "create"
    assert result.tool_name is None
    assert result.target_note_id is None


def test_router_does_not_send_weather_to_unsupported_agent_tool(monkeypatch) -> None:
    provider = _provider()

    def fake_post(payload, *, model_name: str, read_timeout: float):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "route": "tool",
                                "confidence": 0.9,
                                "target_note_id": None,
                                "reason": "weather query",
                                "tool_name": "agent_fallback",
                                "tool_query": "오늘 날씨 어때",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post)

    result = provider.route_text("오늘 날씨 어때")

    assert result.route == "ignore"
    assert result.tool_name is None


def test_create_route_discards_model_placeholder_target(monkeypatch) -> None:
    provider = _provider()

    def fake_post(payload, *, model_name: str, read_timeout: float):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "route": "create",
                                "confidence": 0.9,
                                "target_note_id": "new_note",
                                "reason": "personal record",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post)

    result = provider.route_text(
        "오늘 친구랑 진로 이야기를 했다."
    )

    assert result.route == "create"
    assert result.target_note_id is None

def test_contextual_continuation_normalizes_create_to_append(monkeypatch) -> None:
    provider = _provider()
    candidate = {
        "id": "earth-note",
        "title": "지구과학 문제지 제작",
        "summary": "문제지 제작 계획",
        "body": "지구과학 문제지를 만든다.",
    }

    def fake_post(payload, *, model_name: str, read_timeout: float):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "route": "create",
                                "confidence": 0.9,
                                "target_note_id": None,
                                "reason": "note-like statement",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post)

    result = provider.route_text(
        "그리고 시험 범위도 같이 정리",
        candidate_notes=[candidate],
        conversation_context=[{"raw_text": "지구과학 메모"}],
    )

    assert result.route == "append"
    assert result.target_note_id == "earth-note"


def test_last_artifact_is_included_as_append_candidate() -> None:
    class FakeNoteManager:
        def get_conversation_state(self, **kwargs):
            return {"note_id": "recent-note"}

        def get_note(self, note_id: str):
            assert note_id == "recent-note"
            return {"id": note_id, "title": "방금 저장한 메모"}

    router = _router()
    router.note_manager = FakeNoteManager()

    candidates = router._include_last_artifact_candidate(
        chat_id="777",
        sender_id="123",
        candidate_notes=[],
        limit=5,
    )

    assert [note["id"] for note in candidates] == ["recent-note"]

def test_router_recovers_structured_json_from_reasoning_content(monkeypatch) -> None:
    provider = _provider()

    def fake_post(payload, *, model_name: str, read_timeout: float):
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"": ""}',
                        "reasoning_content": (
                            "The text is a new task, so use create.\n"
                            '{"route":"create","confidence":0.95,'
                            '"target_note_id":null,"reason":"new task",'
                            '"tool_name":null,"tool_query":null,'
                            '"tool_tag":null,"tool_limit":null}'
                        ),
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post)

    result = provider.route_text(
        "내일 학교 끝나고 지구과학 문제지 만들기"
    )

    assert result.route == "create"
    assert result.confidence == 0.95
    assert result.target_note_id is None
    assert result.reason == "new task"

def test_router_uses_bounded_but_non_truncating_output_limit(monkeypatch) -> None:
    provider = _provider()
    captured: dict = {}

    def fake_post(payload, *, model_name: str, read_timeout: float):
        captured.update(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "route": "create",
                                "confidence": 0.9,
                                "target_note_id": None,
                                "reason": "todo",
                                "tool_name": None,
                                "tool_query": None,
                                "tool_tag": None,
                                "tool_limit": None,
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post)

    result = provider.route_text("\ub0b4\uc77c \ubb38\uc81c\uc9c0 \ub9cc\ub4e4\uae30")

    assert result.route == "create"
    assert captured["max_tokens"] == 800

def test_natural_reference_and_search_variants_use_command_gate() -> None:
    router = _router()
    cases = {
        "방금 거 원문 보여줘": "read_last_note",
        "방금 것 전체 내용 알려줘": "read_last_note",
        "1번 내용 보여줘": "read_last_note",
        "아까 거 삭제해줘": "delete_request",
        "혹시 이항분포 메모 좀 찾아줄래?": "search_notes",
    }

    for text, expected_name in cases.items():
        command = router._detect_direct_command(text)
        assert command is not None, text
        assert command.name == expected_name, text


def test_declarative_choice_sentences_do_not_become_corrections() -> None:
    router = _router()
    texts = (
        "오늘은 커피 말고 차를 마셨다.",
        "커피 말고 차 사기",
        "저장 구조는 JSON이 아니라 SQLite로 설계했다.",
    )

    for text in texts:
        assert router._extract_correction_intent(text) is None, text
        assert router._detect_direct_command(text) is None, text
        assert router._looks_like_meta_command(text) is False, text

    assert router._looks_like_technical_note_statement(texts[-1]) is True


def test_implicit_compact_corrections_are_still_detected() -> None:
    router = _router()

    for text in (
        "9월 첫값 말고 오늘 할 것",
        "9월 첫값이 아니라 오늘 할 것이야",
        "9월 첫값 -> 오늘 할 것",
    ):
        command = router._detect_direct_command(text)
        assert command is not None, text
        assert command.name == "correct_last_note", text

def test_past_waiting_time_is_not_promoted_as_generated_deadline() -> None:
    result = TextAnalysisResult(
        title="3시까지 - 친구 약속 불발",
        summary="3시까지: 친구 연락을 기다렸지만 약속이 취소되어 속상했다.",
        tags=["약속"],
        category="note",
        confidence=0.9,
        raw_response="{}",
    )

    _provider()._remove_narrative_time_prefix(
        result,
        source_text="어제 친구랑 3시까지 만나기로 했는데 연락이 없어서 오래 기다렸다.",
    )

    assert result.title == "친구 약속 불발"
    assert result.summary == "친구 연락을 기다렸지만 약속이 취소되어 속상했다."


def test_future_deadline_prefix_is_preserved() -> None:
    result = TextAnalysisResult(
        title="오후 3시까지 과제 제출",
        summary="내일 오후 3시까지 미적분 과제를 제출해야 한다.",
        tags=["과제"],
        category="note",
        confidence=0.9,
        raw_response="{}",
    )

    _provider()._remove_narrative_time_prefix(
        result,
        source_text="내일 오후 3시까지 미적분 과제 제출하기",
    )

    assert result.title == "오후 3시까지 과제 제출"
    assert result.summary == "내일 오후 3시까지 미적분 과제를 제출해야 한다."

def test_general_question_is_not_normalized_to_note_create(monkeypatch) -> None:
    provider = _provider()

    def fake_post(payload, *, model_name: str, read_timeout: float):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "route": "create",
                                "confidence": 0.9,
                                "target_note_id": None,
                                "reason": "dinner plan",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post)

    result = provider.route_text("내일 뭐 먹을까?")

    assert result.route == "tool"
    assert result.tool_name == "agent_fallback"
    assert result.tool_query == "내일 뭐 먹을까?"

    fallback = provider._build_heuristic_route_decision(
        text="내일 뭐 먹을까?",
        candidate_notes=[],
        conversation_context=[],
    )
    assert fallback.route == "tool"
    assert fallback.tool_name == "agent_fallback"
