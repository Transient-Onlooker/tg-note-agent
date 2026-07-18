from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.notion import NotionExportResult
from app.main import create_app
from app.models.db import Database, DuplicateMessageError, StoredMessage
from app.models.schemas import RouteDecision, TextAnalysisResult
from app.services.nim_provider import NvidiaNIMProvider


class FakeNIMProvider:
    model = "fake-text-model"
    text_model = "fake-text-model"
    router_model = "fake-router-model"
    vision_model = "fake-vision-model"

    def route_text(self, text: str, **kwargs):
        analysis = self.analyze_text(text, **kwargs)
        route = "create"
        if analysis.tool_name:
            route = "tool"
        elif analysis.action == "append":
            route = "append"
        elif analysis.action == "ignore" or analysis.is_note is False:
            route = "ignore"
        return RouteDecision(
            route=route,
            confidence=analysis.confidence,
            target_note_id=analysis.target_note_id,
            reason=analysis.summary,
            tool_name=analysis.tool_name,
            tool_query=analysis.tool_query,
            tool_tag=analysis.tool_tag,
            tool_limit=analysis.tool_limit,
        )

    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="액체연료 로켓 엔진의 재생냉각",
            summary="연소실 벽면 냉각 방식 조사 메모",
            tags=["rocket", "engine", "cooling"],
            category="note",
            confidence=0.93,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )

    def analyze_image(self, image_path: str, caption: str | None = None):
        return TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 지구과학 문제지를 제작하는 계획",
            tags=["지구과학", "시험", "문제지"],
            category="note",
            confidence=0.91,
            raw_response='{"ok": true}',
            ocr_text="지구과학 시험지 제작 기말고사 끝나고 하기",
            is_note=True,
            needs_user_clarification=False,
            action="create",
        )

    def summarize_note_search(self, *, query: str, notes: list[dict]):
        titles = ", ".join(note["title"] for note in notes)
        return f"관련 메모를 찾았어: {titles}"


class UnsureImageNIMProvider(FakeNIMProvider):
    def analyze_image(self, image_path: str, caption: str | None = None):
        return TextAnalysisResult(
            title="사진 판별 필요",
            summary="글씨를 충분히 읽지 못함",
            tags=[],
            category="unsure",
            confidence=0.2,
            raw_response='{"ok": true}',
            ocr_text="지구...",
            is_note=None,
            needs_user_clarification=True,
        )


class MarkdownTableSearchNIMProvider(FakeNIMProvider):
    def summarize_note_search(self, *, query: str, notes: list[dict]):
        return (
            "## 지구과학 관련 메모\n\n"
            "| 제목 | 요약 |\n"
            "|------|------|\n"
            "| **지구과학 문제지 제작 계획** | 기말고사 이후 제작 계획 |\n"
        )


class FailingNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        from app.services.nim_provider import NIMProviderError

        raise NIMProviderError("nim failed")


class FastPathForbiddenNIMProvider(FakeNIMProvider):
    def route_text(self, text: str, **kwargs):
        raise AssertionError("fast read path should not call route_text")

    def analyze_text(self, text: str, **kwargs):
        raise AssertionError("fast read path should not call analyze_text")

    def plan_agent_step(self, *, query: str, tool_history: list[dict], conversation_context=None):
        raise AssertionError("fast read path should not call plan_agent_step")


class IgnoreTextNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="오늘 날씨 어떄",
            summary="날씨를 묻는 일반 대화",
            tags=[],
            category="chat",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
        )


class AppendTextNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 제작 계획에 시험 범위 정리를 추가",
            tags=["지구과학", "시험", "문제지", "범위"],
            category="note",
            confidence=0.94,
            raw_response='{"ok": true}',
            is_note=True,
            action="append",
            target_note_id="existing-note-id",
        )


class CountToolNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="메모 개수 조회",
            summary="저장된 메모 개수를 조회",
            tags=[],
            category="query",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
            tool_name="count_notes",
        )


class RecentToolNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="최근 메모 조회",
            summary="최근 메모를 조회",
            tags=[],
            category="query",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
            tool_name="recent_notes",
            tool_limit=3,
        )


class TagListToolNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="태그 목록 조회",
            summary="태그 목록을 조회",
            tags=[],
            category="query",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
            tool_name="list_tags",
        )


class TagNotesToolNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="태그 메모 조회",
            summary="지구과학 태그 메모를 조회",
            tags=[],
            category="query",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
            tool_name="notes_by_tag",
            tool_tag="지구과학",
            tool_limit=3,
        )


class SearchToolNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="메모 검색",
            summary="메모 검색",
            tags=[],
            category="query",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
            tool_name="search_notes",
            tool_query=text,
        )


class MarkdownTableSearchToolNIMProvider(SearchToolNIMProvider):
    def summarize_note_search(self, *, query: str, notes: list[dict]):
        return (
            "## 지구과학 관련 메모\n\n"
            "| 제목 | 요약 |\n"
            "|------|------|\n"
            "| **지구과학 문제지 제작 계획** | 기말고사 이후 제작 계획 |\n"
        )


class MergeSuggestToolNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="메모 병합 후보 찾기",
            summary="합칠 만한 메모 후보를 찾음",
            tags=[],
            category="query",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
            tool_name="suggest_note_merge",
            tool_query=text,
        )

    def suggest_note_merge(self, *, query: str, notes: list[dict]):
        keep_note = next(note for note in notes if "제작 계획" in note["title"])
        merge_note = next(note for note in notes if "범위 정리" in note["title"])
        return {
            "keep_note_id": keep_note["id"],
            "merge_note_id": merge_note["id"],
            "reason": "둘 다 지구과학 시험지 작업 메모라서 합치는 편이 자연스러움",
        }

    def summarize_merged_note(self, *, keep_note: dict, merge_note: dict, existing_tags: list[str] | None = None):
        return TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="시험지 제작 계획과 범위 정리 메모를 하나로 합친 통합 메모",
            tags=["지구과학", "시험", "문제지", "범위"],
            category="note",
            confidence=0.96,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


class AgentFallbackNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="메모 유연 조회",
            summary="여러 메모를 읽어 답해야 하는 요청",
            tags=[],
            category="query",
            confidence=0.95,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
            tool_name="agent_fallback",
            tool_query=text,
        )

    def plan_agent_step(self, *, query: str, tool_history: list[dict], conversation_context=None):
        if not tool_history:
            return {
                "action": "tool",
                "tool_name": "search_notes",
                "arguments": {"query": "지구과학", "limit": 3},
                "response": "",
            }
        return {
            "action": "respond",
            "tool_name": None,
            "arguments": {},
            "response": "지구과학 관련 메모는 2개야. 문제지 제작 계획이랑 시험 범위 정리 메모가 있어.",
        }


class ContextCaptureNIMProvider(FakeNIMProvider):
    def __init__(self) -> None:
        self.seen_context: list[dict] = []

    def analyze_text(self, text: str, **kwargs):
        self.seen_context = list(kwargs.get("conversation_context") or [])
        return TextAnalysisResult(
            title="연속 대화 메모",
            summary="이전 대화 맥락을 반영한 메모",
            tags=["context"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
            is_note=True,
            action="create",
        )


class ContextualIgnoreNIMProvider(FakeNIMProvider):
    def analyze_text(self, text: str, **kwargs):
        return TextAnalysisResult(
            title="후속 질의",
            summary="짧은 후속 질의",
            tags=[],
            category="chat",
            confidence=0.8,
            raw_response='{"ok": true}',
            is_note=False,
            action="ignore",
        )

    def plan_agent_step(self, *, query: str, tool_history: list[dict], conversation_context=None):
        if not tool_history:
            return {
                "action": "tool",
                "tool_name": "search_notes",
                "arguments": {"query": "지구과학", "limit": 5},
                "response": "",
            }
        return {
            "action": "respond",
            "tool_name": None,
            "arguments": {},
            "response": "전체로 보면 지구과학 관련 메모는 2개야.",
        }


class FakeTelegramClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send_message(self, chat_id: int | str, text: str) -> None:
        self.messages.append({"chat_id": str(chat_id), "text": text})

    def get_file_path(self, file_id: str) -> str:
        return f"photos/{file_id}.jpg"

    def download_file(self, file_path: str) -> bytes:
        return b"fake-image-bytes"


class TimeoutTelegramClient(FakeTelegramClient):
    def send_message(self, chat_id: int | str, text: str) -> None:
        raise httpx.ConnectTimeout("telegram sendMessage timed out")


class FakeNotionClient:
    def export_note(self, *, title: str, summary: str, body: str, tags: list[str]) -> NotionExportResult:
        return NotionExportResult(page_id="notion-page-1", url="https://notion.so/page")


class FailingNotionClient:
    def export_note(self, *, title: str, summary: str, body: str, tags: list[str]) -> NotionExportResult:
        raise RuntimeError("notion failed")


def test_nim_provider_uses_task_specific_timeouts(tmp_path: Path, monkeypatch) -> None:
    provider = NvidiaNIMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="text-model",
        vision_model="vision-model",
    )
    image_path = tmp_path / "note.jpg"
    image_path.write_bytes(b"fake-image")
    calls: list[dict] = []

    def fake_post_completion(payload, *, model_name: str, read_timeout: float | None = None):
        calls.append(
            {
                "model_name": model_name,
                "read_timeout": read_timeout,
            }
        )
        if model_name == "vision-model":
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "사진 메모",
                                    "summary": "사진 OCR",
                                    "tags": ["사진"],
                                    "category": "note",
                                    "confidence": 0.8,
                                    "ocr_text": "사진 글자",
                                    "is_note": True,
                                    "needs_user_clarification": False,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        if payload["messages"][0]["content"].startswith("You answer personal note search"):
            return {"choices": [{"message": {"content": "검색 결과 요약"}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title": "메모",
                                "summary": "저장 메모",
                                "tags": ["메모"],
                                "category": "note",
                                "confidence": 0.8,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_post_completion", fake_post_completion)

    provider.analyze_text("메모 저장")
    provider.summarize_note_search(
        query="내 메모중에 지구과학 뭐있더라",
        notes=[{"title": "지구과학", "summary": "요약", "body": "본문", "tags": "[]"}],
    )
    provider.analyze_image(str(image_path))

    assert calls == [
        {"model_name": "text-model", "read_timeout": 45.0},
        {"model_name": "text-model", "read_timeout": 12.0},
        {"model_name": "vision-model", "read_timeout": 120.0},
    ]


def test_nim_provider_clamps_excessive_max_tokens() -> None:
    provider = NvidiaNIMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="text-model",
        max_tokens=900000,
    )

    assert provider.max_tokens == 2000


def test_nim_provider_rejects_empty_choices(monkeypatch) -> None:
    provider = NvidiaNIMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="text-model",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "",
                "choices": [],
                "created": 0,
                "model": "",
                "object": "chat.completion",
            },
        )

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(Exception) as exc_info:
        provider._post_completion(
            {"model": "text-model", "messages": []},
            model_name="text-model",
        )

    assert "no choices" in str(exc_info.value)


def test_degraded_note_summary_does_not_masquerade_as_ai_summary() -> None:
    provider = NvidiaNIMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="text-model",
    )

    result = provider.build_fallback_note_analysis(
        "확률과 통계는 불확실한 사건을 수치로 분석하고 자료를 바탕으로 결론을 내리는 학문이다.",
    )

    assert result.summary == "AI 요약 생성에 실패했어. 원문을 확인해줘."
    assert result.confidence == 0.05


def test_nim_provider_preserves_temporal_info_in_metadata() -> None:
    provider = NvidiaNIMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="text-model",
    )

    data = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"title":"확률과 통계 보고서",'
                        '"summary":"불확실한 사건을 수치로 분석하는 학문에 대한 보고서를 작성한다.",'
                        '"tags":["확률과 통계"],"category":"note","confidence":0.9}'
                    )
                }
            }
        ]
    }

    result = provider._build_analysis_result(
        data,
        source_text="7월 9일까지 보고서 작성. 확률과 통계는 불확실한 사건을 수치로 분석한다.",
        fallback_category="note",
    )

    assert "7월 9일까지" in f"{result.title} {result.summary}"


def test_nim_provider_does_not_promote_past_narrative_time_to_deadline() -> None:
    provider = NvidiaNIMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="text-model",
    )
    data = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"title":"3시까지 - 친구 연락 문제",'
                        '"summary":"3시까지: 친구의 늦은 연락으로 신뢰 문제가 생겼다는 불만.",'
                        '"tags":["친구"],"category":"note","confidence":0.9}'
                    )
                }
            }
        ]
    }

    result = provider._build_analysis_result(
        data,
        source_text="어제 친구의 연락을 3시까지 기다렸는데 답이 없어서 속상했다.",
        fallback_category="note",
    )

    assert result.title == "친구 연락 문제"
    assert result.summary == "친구의 늦은 연락으로 신뢰 문제가 생겼다는 불만."


def build_client(
    tmp_path: Path,
    nim_provider,
    notion_client=None,
) -> tuple[TestClient, Database, FakeTelegramClient]:
    db = Database(str(tmp_path / "app.sqlite"))
    db.initialize()
    telegram = FakeTelegramClient()
    app = create_app()
    app.state.database = db
    app.state.note_manager = __import__("app.services.note_manager", fromlist=["NoteManager"]).NoteManager(
        db,
        notion_client=notion_client,
    )
    app.state.nim_provider = nim_provider
    app.state.telegram_client = telegram
    app.state.notion_client = notion_client
    app.state.image_archive = __import__("app.services.image_archive", fromlist=["ImageArchive"]).ImageArchive(
        image_root=str(tmp_path / "images"),
        telegram_client=telegram,
        db=db,
    )
    app.state.update_router = __import__("app.services.router", fromlist=["build_router"]).build_router(
        app.state.note_manager,
        app.state.nim_provider,
        app.state.telegram_client,
        app.state.image_archive,
    )
    return TestClient(app), db, telegram


def test_allowed_user_text_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 55,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "액체연료 로켓 엔진 냉각 방식 찾아보기",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert len(db.fetch_all("MESSAGE")) == 1
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert json.loads(notes[0]["tags"]) == ["rocket", "engine", "cooling"]
    assert sorted(tag["name"] for tag in db.fetch_all("TAG")) == ["cooling", "engine", "rocket"]
    assert len(db.fetch_all("AI_ANALYSIS")) == 1
    assert [message["text"] for message in telegram.messages] == [
        "수신 완료.",
        "메모로 저장했어.\n\n요약: 연소실 벽면 냉각 방식 조사 메모",
    ]
    return
    assert [message["text"] for message in telegram.messages] == [
        "수신 완료.",
        "처리 완료.\n\n요약: 연소실 벽면 냉각 방식 조사 메모",
    ]


def test_duplicate_message_is_ignored_without_second_ack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 55,
            "chat": {"id": 777},
            "from": {"id": 123},
            "text": "duplicate",
        },
    }

    first_response = client.post("/webhook/telegram", json=payload)
    second_response = client.post("/webhook/telegram", json=payload)

    assert first_response.status_code == 200
    assert first_response.json()["status"] == "accepted"
    assert second_response.status_code == 200
    assert second_response.json() == {"status": "ignored", "detail": "duplicate_message"}
    assert len(db.fetch_all("MESSAGE")) == 1
    assert telegram.messages[0]["text"] == "수신 완료."
    assert [message["text"] for message in telegram.messages].count("수신 완료.") == 1


def test_non_note_text_is_not_saved_as_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, IgnoreTextNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 57,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "오늘 날씨 어떄",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert len(db.fetch_all("NOTE")) == 0
    assert len(db.fetch_all("AI_ANALYSIS")) == 0
    assert db.fetch_all("MESSAGE")[0]["status"] == "processed"
    assert telegram.messages[1]["text"].endswith("메모로 저장하진 않았어.")
    assert "실시간 날씨 조회 도구" in telegram.messages[1]["text"]


def test_greeting_reply_is_not_saved_but_answers_chat(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, IgnoreTextNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 58,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "안녕",
            },
        },
    )

    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 0
    assert "안녕" in telegram.messages[1]["text"]
    assert telegram.messages[1]["text"].endswith("메모로 저장하진 않았어.")


def test_identity_question_reply_is_not_over_explanatory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, IgnoreTextNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 59,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "넌 누구니",
            },
        },
    )

    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 0
    assert "노트 에이전트" in telegram.messages[1]["text"]
    assert "메모 명령이나 저장할 내용" not in telegram.messages[1]["text"]
    assert telegram.messages[1]["text"].endswith("메모로 저장하진 않았어.")


def test_generic_question_reply_is_minimal_when_not_handled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, IgnoreTextNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 60,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "9+9=?",
            },
        },
    )

    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 0
    assert telegram.messages[1]["text"] == "메모로 저장하진 않았어."


def test_note_count_query_returns_direct_answer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, CountToolNIMProvider())
    source_message = StoredMessage(
        id="source-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="기말고사 끝나고 지구과학 문제지 제작",
    )
    db.insert_message(source_message)
    db.insert_note(
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
            "update_id": 2,
            "message": {
                "message_id": 59,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "지금까지 저장된 메모 개수 몇개지",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert db.fetch_all("MESSAGE")[1]["content_type"] == "text"
    assert db.fetch_all("MESSAGE")[1]["status"] == "processed"
    assert telegram.messages[0]["text"] == "수신 완료."
    assert telegram.messages[1]["text"] == "지금 저장된 메모는 1개야."
    assert len(db.fetch_all("NOTE")) == 1
    assert len(db.fetch_all("AI_ANALYSIS")) == 0


def test_recent_notes_tool_returns_recent_summaries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, RecentToolNIMProvider())
    for index in range(3):
        message = StoredMessage(
            id=f"source-{index}",
            telegram_message_id=str(10 + index),
            chat_id="777",
            sender_id="123",
            raw_text=f"메모 {index}",
        )
        db.insert_message(message)
        db.insert_note(
            message.id,
            TextAnalysisResult(
                title=f"메모 제목 {index}",
                summary=f"메모 요약 {index}",
                tags=["테스트"],
                category="note",
                confidence=0.9,
                raw_response='{"ok": true}',
            ),
            message.raw_text,
        )

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 3,
            "message": {
                "message_id": 60,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "최근 메모 보여줘",
            },
        },
    )

    assert response.status_code == 200
    assert telegram.messages[1]["text"].startswith("최근 저장된 항목 3개야.")
    assert "메모 제목 2" in telegram.messages[1]["text"]


def test_tag_list_tool_returns_registered_tags(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, TagListToolNIMProvider())
    message = StoredMessage(
        id="source",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 메모",
    )
    db.insert_message(message)
    db.insert_note(
        message.id,
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 계획",
            tags=["지구과학", "시험"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        message.raw_text,
    )

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 4,
            "message": {
                "message_id": 61,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "태그 목록 보여줘",
            },
        },
    )

    assert response.status_code == 200
    assert "등록된 태그 2개야." in telegram.messages[1]["text"]
    assert "지구과학" in telegram.messages[1]["text"]
    assert "시험" in telegram.messages[1]["text"]


def test_tag_notes_tool_returns_notes_for_tag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, TagNotesToolNIMProvider())
    message = StoredMessage(
        id="source",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 메모",
    )
    db.insert_message(message)
    db.insert_note(
        message.id,
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 계획",
            tags=["지구과학", "시험"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        message.raw_text,
    )

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 5,
            "message": {
                "message_id": 62,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "지구과학 태그 메모 보여줘",
            },
        },
    )

    assert response.status_code == 200
    assert telegram.messages[1]["text"].startswith("'지구과학' 태그 메모 1개 찾았어.")


def test_note_search_query_searches_existing_notes_without_creating_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, SearchToolNIMProvider())
    source_message = StoredMessage(
        id="source-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 시험지 제작 기말고사 끝나고 하기",
    )
    db.insert_message(source_message)
    db.insert_note(
        source_message.id,
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 지구과학 문제지를 제작하는 계획",
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
            "update_id": 2,
            "message": {
                "message_id": 56,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "내 메모중에 지구과학 관련된거 뭐있더라",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    messages = db.fetch_all("MESSAGE")
    assert len(messages) == 2
    assert messages[1]["content_type"] == "text"
    assert messages[1]["status"] == "processed"
    assert len(db.fetch_all("NOTE")) == 1
    assert telegram.messages[0]["text"] == "수신 완료."
    assert telegram.messages[1]["text"] == (
        "관련 메모 1개를 찾았어.\n"
        "1. 지구과학 문제지 제작 계획\n"
        "기말고사 이후 지구과학 문제지를 제작하는 계획"
    )


def test_note_search_markdown_table_is_converted_to_plain_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, MarkdownTableSearchToolNIMProvider())
    source_message = StoredMessage(
        id="source-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 시험지 제작 기말고사 끝나고 하기",
    )
    db.insert_message(source_message)
    db.insert_note(
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
            "update_id": 2,
            "message": {
                "message_id": 56,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "내 메모중에 지구과학 관련된거 뭐있더라",
            },
        },
    )

    assert response.status_code == 200
    assert "|" not in telegram.messages[1]["text"]
    assert telegram.messages[1]["text"].startswith("관련 메모 1개를 찾았어.")


def test_agent_fallback_query_can_read_notes_and_answer(tmp_path: Path, monkeypatch) -> None:
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
            "update_id": 21,
            "message": {
                "message_id": 68,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "내 메모 중에 지구과학 관련된 거 뭐 있었지",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert db.fetch_all("MESSAGE")[-1]["status"] == "processed"
    assert len(db.fetch_all("NOTE")) == 2
    assert "관련 메모 2개를 찾았어." in telegram.messages[-1]["text"]
    assert "지구과학 시험 범위 정리" in telegram.messages[-1]["text"]
    assert "지구과학 문제지 제작 계획" in telegram.messages[-1]["text"]


def test_recent_chat_context_is_passed_to_analysis(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    provider = ContextCaptureNIMProvider()
    client, db, _telegram = build_client(tmp_path, provider)

    previous_message = StoredMessage(
        id="previous-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="아까 얘기한 지구과학 시험지 건 이어서",
    )
    db.insert_message(previous_message)
    with db.connection() as conn:
        conn.execute(
            "UPDATE MESSAGE SET created_at = ? WHERE id = ?",
            ((datetime.now(UTC) - timedelta(minutes=5)).isoformat(), previous_message.id),
        )
        conn.commit()

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 22,
            "message": {
                "message_id": 69,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "그거 계속하자",
            },
        },
    )

    assert response.status_code == 200
    assert len(provider.seen_context) == 1
    assert provider.seen_context[0]["raw_text"] == "아까 얘기한 지구과학 시험지 건 이어서"


def test_chat_context_older_than_30_minutes_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    provider = ContextCaptureNIMProvider()
    client, db, _telegram = build_client(tmp_path, provider)

    old_message = StoredMessage(
        id="old-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="오래된 대화",
    )
    db.insert_message(old_message)
    with db.connection() as conn:
        conn.execute(
            "UPDATE MESSAGE SET created_at = ? WHERE id = ?",
            ((datetime.now(UTC) - timedelta(minutes=31)).isoformat(), old_message.id),
        )
        conn.commit()

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 23,
            "message": {
                "message_id": 70,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "이어서 할까",
            },
        },
    )

    assert response.status_code == 200
    assert provider.seen_context == []


def test_short_followup_query_retries_as_contextual_agent_query(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, ContextualIgnoreNIMProvider())

    previous_message = StoredMessage(
        id="previous-message",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="내 메모 중에 지구과학 관련된 거 뭐 있었지",
    )
    db.insert_message(previous_message)
    db.insert_note(
        previous_message.id,
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 지구과학 문제지 제작",
            tags=["지구과학"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        "지구과학 문제지 제작 계획",
    )
    second_message = StoredMessage(
        id="second-message",
        telegram_message_id="11",
        chat_id="777",
        sender_id="123",
        raw_text="지구과학 시험 범위 정리",
    )
    db.insert_message(second_message)
    db.insert_note(
        second_message.id,
        TextAnalysisResult(
            title="지구과학 시험 범위 정리",
            summary="시험 범위와 출제 포인트 정리",
            tags=["지구과학"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        "지구과학 시험 범위 정리",
    )

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 24,
            "message": {
                "message_id": 71,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "그거 말고 뭐있지? 전체 말이야",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert db.fetch_all("MESSAGE")[-1]["status"] == "processed"
    assert telegram.messages[-1]["text"] == "전체로 보면 지구과학 관련 메모는 2개야."


def test_merge_suggestion_tool_creates_pending_proposal(tmp_path: Path, monkeypatch) -> None:
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

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 6,
            "message": {
                "message_id": 63,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "내가 저장한 메모 중에 합칠만한 거 있냐",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    proposals = db.fetch_all("MERGE_PROPOSAL")
    assert len(proposals) == 1
    assert proposals[0]["status"] == "proposed"
    assert len(db.fetch_all("NOTE")) == 2
    assert "합칠 만한 메모" in telegram.messages[1]["text"]


def test_merge_proposal_approval_merges_and_deletes_note(tmp_path: Path, monkeypatch) -> None:
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


def test_merge_proposal_cancel_keeps_notes_unchanged(tmp_path: Path, monkeypatch) -> None:
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
            "update_id": 9,
            "message": {
                "message_id": 66,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "합칠만한 거 봐줘",
            },
        },
    )
    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 10,
            "message": {
                "message_id": 67,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "취소",
            },
        },
    )

    assert response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 2
    assert db.fetch_all("MERGE_PROPOSAL")[0]["status"] == "canceled"
    assert "취소" in telegram.messages[-1]["text"]


def test_similar_note_is_appended_instead_of_creating_new_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, AppendTextNIMProvider())
    source_message = StoredMessage(
        id="existing-message-id",
        telegram_message_id="10",
        chat_id="777",
        sender_id="123",
        raw_text="기말고사 끝나고 지구과학 문제지 제작",
    )
    db.insert_message(source_message)
    db.insert_note(
        "existing-message-id",
        TextAnalysisResult(
            title="지구과학 문제지 제작 계획",
            summary="기말고사 이후 지구과학 시험 문제지 제작 계획",
            tags=["지구과학", "시험", "문제지"],
            category="note",
            confidence=0.9,
            raw_response='{"ok": true}',
        ),
        source_message.raw_text,
    )
    existing_note = db.fetch_all("NOTE")[0]
    assert existing_note["id"]
    monkeypatch.setattr(AppendTextNIMProvider, "analyze_text", lambda self, text, **kwargs: TextAnalysisResult(
        title="지구과학 문제지 제작 계획",
        summary="기말고사 이후 제작 계획에 시험 범위 정리를 추가",
        tags=["지구과학", "시험", "문제지", "범위"],
        category="note",
        confidence=0.94,
        raw_response='{"ok": true}',
        is_note=True,
        action="append",
        target_note_id=existing_note["id"],
    ))

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 58,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "시험 범위도 같이 정리하기",
            },
        },
    )

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert "시험 범위도 같이 정리하기" in notes[0]["body"]
    assert "기말고사 끝나고 지구과학 문제지 제작" in notes[0]["body"]
    assert "범위" in json.loads(notes[0]["tags"])
    assert telegram.messages[1]["text"] == "기존 메모에 덧붙였어.\n\n요약: 기말고사 이후 제작 계획에 시험 범위 정리를 추가"


def test_db_rejects_duplicate_message_insert(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "app.sqlite"))
    db.initialize()
    message = StoredMessage(
        id="first",
        telegram_message_id="55",
        chat_id="777",
        sender_id="123",
        raw_text="same",
    )
    duplicate = StoredMessage(
        id="second",
        telegram_message_id="55",
        chat_id="777",
        sender_id="123",
        raw_text="same",
    )

    db.insert_message(message)

    try:
        db.insert_message(duplicate)
    except DuplicateMessageError:
        pass
    else:
        raise AssertionError("duplicate insert should fail")

    assert len(db.fetch_all("MESSAGE")) == 1


def test_photo_flow_runs_ocr_and_creates_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 2,
            "message": {
                "message_id": 99,
                "chat": {"id": 777},
                "from": {"id": 123},
                "caption": "photo note",
                "photo": [
                    {
                        "file_id": "small-file",
                        "file_unique_id": "unique-small",
                        "width": 100,
                        "height": 100,
                        "file_size": 1000,
                    },
                    {
                        "file_id": "large-file",
                        "file_unique_id": "unique-large",
                        "width": 1000,
                        "height": 800,
                        "file_size": 5000,
                    },
                ],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert db.fetch_all("MESSAGE")[0]["status"] == "processed"
    image_files = db.fetch_all("IMAGE_FILE")
    assert image_files[0]["ocr_text"] == "지구과학 시험지 제작 기말고사 끝나고 하기"
    assert image_files[0]["summary"] == "기말고사 이후 지구과학 문제지를 제작하는 계획"
    assert image_files[0]["image_type"] == "note"
    assert len(db.fetch_all("IMAGE_FILE")) == 1
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert notes[0]["body"] == "지구과학 시험지 제작 기말고사 끝나고 하기"
    assert telegram.messages[0]["text"] == "사진 수신 완료."
    assert "사진 메모로 저장했어." in telegram.messages[1]["text"]
    assert "요약: 기말고사 이후 지구과학 문제지를 제작하는 계획" in telegram.messages[1]["text"]


def test_photo_success_message_includes_ocr_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 21,
            "message": {
                "message_id": 109,
                "chat": {"id": 777},
                "from": {"id": 123},
                "photo": [
                    {
                        "file_id": "large-file",
                        "file_unique_id": "unique-large-2",
                        "width": 1200,
                        "height": 900,
                        "file_size": 6000,
                    },
                ],
            },
        },
    )

    assert response.status_code == 200
    assert "읽은 내용:" in telegram.messages[1]["text"]
    assert "지구과학 시험지 제작 기말고사 끝나고 하기" in telegram.messages[1]["text"]


def test_fast_read_last_note_returns_saved_ocr_without_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())

    photo_response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 22,
            "message": {
                "message_id": 110,
                "chat": {"id": 777},
                "from": {"id": 123},
                "photo": [
                    {
                        "file_id": "large-file",
                        "file_unique_id": "unique-large-3",
                        "width": 1000,
                        "height": 800,
                        "file_size": 5000,
                    },
                ],
            },
        },
    )

    assert photo_response.status_code == 200
    assert len(db.fetch_all("NOTE")) == 1
    assert len(db.fetch_all("AI_ANALYSIS")) == 1

    fast_provider = FastPathForbiddenNIMProvider()
    client.app.state.nim_provider = fast_provider
    client.app.state.update_router.nim_provider = fast_provider

    query_response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 23,
            "message": {
                "message_id": 111,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "저 메모의 전체 내용 알려줘.",
            },
        },
    )

    assert query_response.status_code == 200
    assert query_response.json()["status"] == "accepted"
    assert len(db.fetch_all("NOTE")) == 1
    assert len(db.fetch_all("AI_ANALYSIS")) == 1
    assert db.fetch_all("MESSAGE")[-1]["status"] == "processed"
    assert telegram.messages[2]["text"] == "수신 완료."
    assert "방금 OCR로 저장된 메모" in telegram.messages[3]["text"]
    assert "[본문]" in telegram.messages[3]["text"]
    assert "지구과학 시험지 제작 기말고사 끝나고 하기" in telegram.messages[3]["text"]


def test_unclear_photo_asks_for_clarification(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, UnsureImageNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 2,
            "message": {
                "message_id": 99,
                "chat": {"id": 777},
                "from": {"id": 123},
                "photo": [
                    {
                        "file_id": "large-file",
                        "file_unique_id": "unique-large",
                        "width": 1000,
                        "height": 800,
                        "file_size": 5000,
                    },
                ],
            },
        },
    )

    assert response.status_code == 200
    assert db.fetch_all("MESSAGE")[0]["status"] == "needs_review"
    assert telegram.messages[0]["text"] == "사진 수신 완료."
    assert "메모용 사진인지 일반 사진인지" in telegram.messages[1]["text"]


def test_unauthorized_user_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 55,
                "chat": {"id": 777},
                "from": {"id": 999},
                "text": "unauthorized",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert db.fetch_all("MESSAGE") == []
    assert telegram.messages == []


def test_ai_failure_marks_message_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FailingNIMProvider())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 55,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "will fail",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    messages = db.fetch_all("MESSAGE")
    assert len(messages) == 1
    assert messages[0]["status"] == "ai_failed"
    assert telegram.messages[0]["text"] == "수신 완료."
    assert "AI 분석이 너무 오래 걸리거나 실패" in telegram.messages[1]["text"]


def test_notion_export_marks_note_and_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider(), FakeNotionClient())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 77,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "notion export",
            },
        },
    )

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert notes[0]["notion_page_id"] == "notion-page-1"
    assert notes[0]["notion_status"] == "exported"
    assert telegram.messages[1]["text"].endswith("Notion: 저장함")


def test_notion_export_failure_keeps_local_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    client, db, telegram = build_client(tmp_path, FakeNIMProvider(), FailingNotionClient())

    response = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 88,
                "chat": {"id": 777},
                "from": {"id": 123},
                "text": "notion fail",
            },
        },
    )

    assert response.status_code == 200
    notes = db.fetch_all("NOTE")
    assert len(notes) == 1
    assert notes[0]["notion_page_id"] is None
    assert notes[0]["notion_status"] == "failed"
    assert telegram.messages[1]["text"].endswith("Notion: 저장 실패")
