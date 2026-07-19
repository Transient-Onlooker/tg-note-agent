from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.models.schemas import RouteDecision, TextAnalysisResult


class NIMProviderError(RuntimeError):
    pass


class NvidiaNIMProvider:
    ROUTER_TIMEOUT_SECONDS = 12.0
    NOTE_SAVE_TIMEOUT_SECONDS = 45.0
    NOTE_SEARCH_TIMEOUT_SECONDS = 600.0
    MERGE_SUGGEST_TIMEOUT_SECONDS = 120.0
    IMAGE_ANALYSIS_TIMEOUT_SECONDS = 120.0

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str | None = None,
        *,
        router_model: str | None = None,
        text_model: str | None = None,
        router_timeout_seconds: float = 12.0,
        text_timeout_seconds: float = 45.0,
        timeout: float = 30.0,
        max_tokens: int = 220,
        vision_model: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        resolved_model = text_model or model or "z-ai/glm-5.2"
        self.router_model = router_model or resolved_model
        self.text_model = resolved_model
        self.model = self.text_model
        self.vision_model = vision_model or self.text_model
        self.timeout = timeout
        self.router_timeout_seconds = router_timeout_seconds
        self.text_timeout_seconds = text_timeout_seconds
        self.max_tokens = max(128, min(max_tokens, 2000))

    def route_text(
        self,
        text: str,
        *,
        candidate_notes: list[dict[str, Any]] | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> RouteDecision:
        candidate_notes = candidate_notes or []
        conversation_context = conversation_context or []
        compact_candidates = [
            {
                "note_id": note.get("id", ""),
                "title": note.get("title", ""),
                "summary": note.get("summary", ""),
                "body_excerpt": str(note.get("body", ""))[:180],
            }
            for note in candidate_notes[:5]
        ]
        compact_context = [
            {
                "text": str(item.get("raw_text", ""))[:180],
                "content_type": item.get("content_type", ""),
                "created_at": item.get("created_at", ""),
            }
            for item in conversation_context[-8:]
        ]
        payload = {
            "model": self.router_model,
            "temperature": 0.0,
            "max_tokens": min(self.max_tokens, 800),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a fast router for a Telegram personal assistant. "
                        "Return strict JSON with keys route, confidence, target_note_id, reason, tool_name, tool_query, tool_tag, tool_limit. "
                        "route must be one of create, append, ignore, tool. "
                        "Use route=tool only when the user is clearly asking about existing notes or tags. "
                        "Development logs, personal records, technical decisions, study notes, TODOs, ideas, settings, architecture descriptions, and implementation details should default to create or append unless the user explicitly says not to save. "
                        "Short follow-up queries that depend on recent chat context should prefer route=tool with tool_name=agent_fallback, not ignore. "
                        "Use append only when the text clearly belongs to one of the candidate notes. "
                        "If append is chosen, target_note_id must be one of the candidate note_id values. "
                        "For tools, available tool_name values are count_notes, recent_notes, list_tags, count_notes_by_tag, notes_by_tag, search_notes, suggest_note_merge, agent_fallback. "
                        "A short task-like phrase ending in 확인, 준비, 만들기, or 하기 is a note to create unless it explicitly asks about existing notes. "
                        "Keep reason very short, like 'development log' or 'note query'."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Text:\n{text}\n\n"
                        f"Recent same-chat context within 30 minutes:\n{json.dumps(compact_context, ensure_ascii=False)}\n\n"
                        f"Candidate notes for append:\n{json.dumps(compact_candidates, ensure_ascii=False)}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            data = self._post_completion(
                payload,
                model_name=self.router_model,
                read_timeout=self.router_timeout_seconds,
            )
            parsed = self._parse_json_message(data, error_prefix="Failed to parse router response")
            route = str(parsed.get("route", "")).strip().lower() or "ignore"
            if route not in {"create", "append", "ignore", "tool"}:
                route = "ignore"
            tool_name = str(parsed.get("tool_name", "")).strip() or None
            if tool_name not in {
                None,
                "count_notes",
                "recent_notes",
                "list_tags",
                "count_notes_by_tag",
                "notes_by_tag",
                "search_notes",
                "suggest_note_merge",
                "agent_fallback",
            }:
                tool_name = None
            target_value = parsed.get("target_note_id")
            target_note_id = str(target_value).strip() or None if target_value is not None else None
            confidence = self._coerce_confidence(parsed.get("confidence"))
            reason_value = parsed.get("reason")
            reason = str(reason_value).strip() if reason_value is not None else ""
            tool_query_value = parsed.get("tool_query")
            tool_query = str(tool_query_value).strip() or None if tool_query_value is not None else None
            tool_tag_value = parsed.get("tool_tag")
            tool_tag = str(tool_tag_value).strip() or None if tool_tag_value is not None else None
            try:
                tool_limit = int(parsed.get("tool_limit")) if parsed.get("tool_limit") is not None else None
            except (TypeError, ValueError):
                tool_limit = None
            if tool_limit is not None:
                tool_limit = max(1, min(tool_limit, 10))

            normalized_text = " ".join(text.strip().lower().split())
            query_markers = (
                "알려줘",
                "보여줘",
                "찾아줘",
                "검색",
                "몇 개",
                "몇개",
                "뭐 있",
                "뭐있",
                "원문",
                "목록",
            )
            weather_markers = ("날씨", "기온", "비 와", "비와", "눈 와", "눈와")
            asks_supported_query = any(marker in normalized_text for marker in query_markers)
            asks_weather = any(marker in normalized_text for marker in weather_markers)
            asks_general_question = self._looks_like_general_question(normalized_text)
            continuation_statement = re.match(
                r"^(?:그리고|추가로|또|이어서|거기에)(?:\s|$)",
                normalized_text,
            ) is not None
            explicitly_blocks_save = (
                "저장하지 마" in normalized_text
                or "저장하지말" in normalized_text
            )
            if asks_weather:
                route = "ignore"
                tool_name = None
                tool_query = None
                reason = reason or "unsupported weather query"
            elif asks_general_question and not explicitly_blocks_save:
                route = "tool"
                tool_name = "agent_fallback"
                tool_query = text
                reason = "general question"
            elif (
                route in {"ignore", "tool"}
                and self._looks_like_note_worthy_text(text)
                and not asks_supported_query
                and not explicitly_blocks_save
            ):
                route = "create"
                tool_name = None
                tool_query = None
                reason = "note-like statement"

            if (
                route in {"create", "append"}
                and continuation_statement
                and conversation_context
                and len(candidate_notes) == 1
            ):
                route = "append"
                target_note_id = str(candidate_notes[0].get("id", "")).strip() or None

            valid_candidate_ids = {
                str(note.get("id", "")).strip()
                for note in candidate_notes
                if str(note.get("id", "")).strip()
            }
            if route == "append":
                if target_note_id not in valid_candidate_ids:
                    target_note_id = self._guess_append_target(candidate_notes, normalized_text)
                if target_note_id is None:
                    route = "create"
            else:
                target_note_id = None

            return RouteDecision(
                route=route,
                confidence=confidence,
                target_note_id=target_note_id,
                reason=reason,
                tool_name=tool_name,
                tool_query=tool_query,
                tool_tag=tool_tag,
                tool_limit=tool_limit,
            )
        except NIMProviderError:
            return self._build_heuristic_route_decision(
                text=text,
                candidate_notes=candidate_notes,
                conversation_context=conversation_context,
            )

    def analyze_text(
        self,
        text: str,
        *,
        existing_tags: list[str] | None = None,
        candidate_notes: list[dict[str, Any]] | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
        action: str = "create",
        target_note_id: str | None = None,
    ) -> TextAnalysisResult:
        existing_tags = existing_tags or []
        candidate_notes = candidate_notes or []
        conversation_context = conversation_context or []
        compact_candidates = [
            {
                "note_id": note.get("id", ""),
                "title": note.get("title", ""),
                "summary": note.get("summary", ""),
                "tags": note.get("tags", ""),
                "body_excerpt": str(note.get("body", ""))[:240],
            }
            for note in candidate_notes[:5]
        ]
        compact_context = [
            {
                "text": str(item.get("raw_text", ""))[:240],
                "content_type": item.get("content_type", ""),
                "created_at": item.get("created_at", ""),
            }
            for item in conversation_context[-8:]
        ]
        payload = {
            "model": self.text_model,
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You create structured note metadata for a Telegram personal knowledge assistant. "
                        "Return strict JSON with keys title, summary, tags, category, confidence. "
                        "Do not include markdown or extra text. "
                        "title and summary must be written in Korean. "
                        "Never use Chinese characters or Hanja in title, summary, or tags; use Korean or English words instead. "
                        "summary must always be Korean, even if the source text mixes English and Korean. "
                        "Preserve the user's stated meaning; do not reinterpret test sentences as tasks, bug reports, or requirements unless the user explicitly asks to create a task. "
                        "If the source text is already a short single-sentence note, the summary may closely mirror the source text. "
                        "If the source text is longer than one sentence, summary must be an abstractive 1-2 sentence Korean summary, not a copied prefix or truncated excerpt. "
                        "For long source text, compress the central meaning and omit examples unless they are essential. "
                        "Preserve dates and times only when they clearly describe a future plan, appointment, task, or deadline. "
                        "Narrative times from past events, such as '3시까지 기다렸다', are context and must not be promoted or prepended as deadlines. "
                        "Never infer a deadline from the Korean particle '까지' alone. "
                        "tags must be an array of short strings. "
                        "confidence must be a number between 0 and 1. "
                        "Prefer reusing existing tags when they already match the content. "
                        "Assume the note will be saved. "
                        "Use the recent same-chat context to resolve short follow-up sentences. "
                        "Keep the title concise and the summary informative."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Generate note metadata for the following Telegram text and respond only in JSON.\n\n"
                        f"Text:\n{text}\n\n"
                        f"Recent same-chat context within 30 minutes:\n{json.dumps(compact_context, ensure_ascii=False)}\n\n"
                        f"Existing tags:\n{json.dumps(existing_tags, ensure_ascii=False)}\n\n"
                        f"Candidate notes for possible append:\n{json.dumps(compact_candidates, ensure_ascii=False)}\n\n"
                        f"Save action:\n{action}\n"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        data = self._post_completion(
            payload,
            model_name=self.text_model,
            read_timeout=self.text_timeout_seconds,
        )
        result = self._build_analysis_result(
            data,
            source_text=text,
            fallback_category="note",
        )
        self._remove_narrative_time_prefix(result, source_text=text)
        result.action = action
        result.target_note_id = target_note_id
        result.is_note = True
        result.tool_name = None
        result.tool_query = None
        result.tool_tag = None
        result.tool_limit = None
        return result

    @staticmethod
    def _remove_narrative_time_prefix(
        result: TextAnalysisResult,
        *,
        source_text: str,
    ) -> None:
        time_match = re.search(
            r"(?P<period>오전|오후)?\s*(?P<hour>\d{1,2})\s*시"
            r"(?:\s*(?P<minute>\d{1,2})\s*분)?\s*까지",
            source_text,
        )
        if time_match is None:
            return

        source_window = source_text[
            max(0, time_match.start() - 20):time_match.end() + 100
        ]
        narrative_markers = (
            "기다렸",
            "기다린",
            "연락이 없",
            "연락이 늦",
            "약속이 취소",
            "취소되어",
            "취소돼",
        )
        if not any(marker in source_window for marker in narrative_markers):
            return

        period = time_match.group("period")
        minute = time_match.group("minute")
        period_pattern = rf"{re.escape(period)}\s*" if period else r"(?:오전|오후)?\s*"
        minute_pattern = rf"\s*{minute}\s*분" if minute else ""
        prefix_pattern = re.compile(
            rf"^\s*{period_pattern}{time_match.group('hour')}\s*시"
            rf"{minute_pattern}\s*까지\s*(?:[-:：]\s*)?"
        )
        result.title = prefix_pattern.sub("", result.title).strip() or result.title
        result.summary = prefix_pattern.sub("", result.summary).strip() or result.summary

    def analyze_image(self, image_path: str, caption: str | None = None) -> TextAnalysisResult:
        image_data_url = self._build_data_url(image_path)
        caption_text = caption.strip() if caption else ""
        payload = {
            "model": self.vision_model,
            "temperature": 0.1,
            "max_tokens": max(self.max_tokens, 320),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You analyze Telegram images for a personal note assistant. "
                        "Return strict JSON with keys "
                        "title, summary, tags, category, confidence, ocr_text, is_note, needs_user_clarification. "
                        "Use category values note, general_photo, or unsure. "
                        "Extract image text as faithfully as possible into ocr_text before summarizing it. "
                        "Do not translate Korean into English. "
                        "title and summary must be written in Korean. "
                        "Never use Chinese characters or Hanja in title, summary, or tags; use Korean or English words instead. "
                        "Set is_note true only when the image is clearly a note, document, whiteboard, screenshot, or text-heavy memo. "
                        "Set needs_user_clarification true when text is unreadable or intent is ambiguous. "
                        "ocr_text should contain the original readable text from the image, or an empty string if none is readable. "
                        "If any part is hard to read, preserve what is readable and mark uncertain fragments briefly. "
                        "Do not include markdown or extra text."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Classify this Telegram image.\n"
                                f"Caption: {caption_text or '(none)'}\n"
                                "Decide whether it should become a note or stay as a general photo."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
        }
        data = self._post_completion(
            payload,
            model_name=self.vision_model,
            read_timeout=self.IMAGE_ANALYSIS_TIMEOUT_SECONDS,
        )
        source_text = caption_text or Path(image_path).name
        return self._build_analysis_result(
            data,
            source_text=source_text,
            fallback_category="unsure",
        )

    def summarize_note_search(
        self,
        *,
        query: str,
        notes: list[dict[str, Any]],
    ) -> str:
        compact_notes = [
            {
                "title": note.get("title", ""),
                "summary": note.get("summary", ""),
                "body": note.get("body", ""),
                "tags": note.get("tags", ""),
                "created_at": note.get("created_at", ""),
            }
            for note in notes
        ]
        payload = {
            "model": self.router_model,
            "temperature": 0.2,
            "max_tokens": min(self.max_tokens, 1200),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You answer personal note search requests in Korean. "
                        "Use only the provided notes. "
                        "Be concise and include the most relevant note titles. "
                        "Return plain text only in Korean. "
                        "Never use Chinese characters or Hanja; rewrite them in Korean or English. "
                        "Do not use markdown tables, headings, bullets with asterisks, or code fences."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Search query:\n{query}\n\n"
                        "Matching notes as JSON:\n"
                        f"{json.dumps(compact_notes, ensure_ascii=False)}"
                    ),
                },
            ],
        }
        data = self._post_completion(
            payload,
            model_name=self.router_model,
            read_timeout=self.router_timeout_seconds,
        )
        return self._extract_message_text(data)

    def plan_agent_step(
        self,
        *,
        query: str,
        tool_history: list[dict[str, Any]],
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        conversation_context = conversation_context or []
        compact_context = [
            {
                "text": str(item.get("raw_text", ""))[:240],
                "content_type": item.get("content_type", ""),
                "created_at": item.get("created_at", ""),
            }
            for item in conversation_context[-8:]
        ]
        payload = {
            "model": self.router_model,
            "temperature": 0.1,
            "max_tokens": min(max(self.max_tokens, 320), 1200),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a read-focused note assistant for flexible Telegram queries. "
                        "You can inspect notes before answering. "
                        "Return strict JSON with keys action, tool_name, arguments, response. "
                        "action must be either tool or respond. "
                        "When action=tool, response must be empty. "
                        "When action=respond, tool_name must be null and arguments must be an empty object. "
                        "Available tool_name values are count_notes, recent_notes, list_tags, count_notes_by_tag, notes_by_tag, search_notes, read_note. "
                        "Use only one tool per step. "
                        "Prefer broad search first, then read_note only for the most relevant notes. "
                        "Recent conversation context is from the same chat and only covers the last 30 minutes. "
                        "Use it when the query depends on earlier user messages. "
                        "Respond in Korean plain text without markdown tables."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User query:\n{query}\n\n"
                        "Recent same-chat context within 30 minutes:\n"
                        f"{json.dumps(compact_context, ensure_ascii=False)}\n\n"
                        "Previous tool history as JSON:\n"
                        f"{json.dumps(tool_history, ensure_ascii=False)}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        data = self._post_completion(
            payload,
            model_name=self.router_model,
            read_timeout=self.router_timeout_seconds,
        )
        return self._parse_json_message(data, error_prefix="Failed to parse agent step")

    def suggest_note_merge(
        self,
        *,
        query: str,
        notes: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        compact_notes = [
            {
                "note_id": note.get("id", ""),
                "title": note.get("title", ""),
                "summary": note.get("summary", ""),
                "tags": note.get("tags", ""),
                "body_excerpt": str(note.get("body", ""))[:400],
            }
            for note in notes
        ]
        payload = {
            "model": self.router_model,
            "temperature": 0.1,
            "max_tokens": min(max(self.max_tokens, 400), 1200),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You review personal notes and decide whether two notes should be merged. "
                        "Return strict JSON with keys should_merge, keep_note_id, merge_note_id, reason. "
                        "Use should_merge=true only when the two notes are clearly about the same work item, plan, or topic and merging would reduce duplication. "
                        "If there is no strong merge candidate, return should_merge=false with empty keep_note_id and merge_note_id. "
                        "Prefer keeping the broader or older note as keep_note_id. "
                        "Do not include markdown or extra text."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{query}\n\n"
                        "Candidate notes as JSON:\n"
                        f"{json.dumps(compact_notes, ensure_ascii=False)}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        data = self._post_completion(
            payload,
            model_name=self.router_model,
            read_timeout=self.router_timeout_seconds,
        )
        content = self._extract_message_text(data)
        stripped = self._strip_code_fences(content)
        candidate = self._extract_json_candidate(stripped) or stripped
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise NIMProviderError(
                f"Failed to parse merge suggestion response: {json.dumps(data, ensure_ascii=False)[:500]}"
            ) from exc

        should_merge = self._coerce_bool(parsed.get("should_merge"), default=False)
        keep_note_id = str(parsed.get("keep_note_id", "")).strip()
        merge_note_id = str(parsed.get("merge_note_id", "")).strip()
        reason = str(parsed.get("reason", "")).strip()
        if not should_merge or not keep_note_id or not merge_note_id or keep_note_id == merge_note_id:
            return None
        return {
            "keep_note_id": keep_note_id,
            "merge_note_id": merge_note_id,
            "reason": reason or "두 메모가 같은 주제를 다루고 있어.",
        }

    def summarize_merged_note(
        self,
        *,
        keep_note: dict[str, Any],
        merge_note: dict[str, Any],
        existing_tags: list[str] | None = None,
    ) -> TextAnalysisResult:
        existing_tags = existing_tags or []
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": min(max(self.max_tokens, 320), 1200),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You merge two personal notes into one clean note summary. "
                        "Return strict JSON with keys title, summary, tags, category, confidence. "
                        "title and summary must be written in Korean. "
                        "Never use Chinese characters or Hanja in title, summary, or tags; use Korean or English words instead. "
                        "tags must be an array of short strings. "
                        "Prefer reusing existing tags when they fit. "
                        "Set category to note. "
                        "Do not include markdown or extra text."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Existing tags:\n{json.dumps(existing_tags, ensure_ascii=False)}\n\n"
                        "Keep note:\n"
                        f"{json.dumps(keep_note, ensure_ascii=False)}\n\n"
                        "Merge note:\n"
                        f"{json.dumps(merge_note, ensure_ascii=False)}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        data = self._post_completion(
            payload,
            model_name=self.model,
            read_timeout=self.NOTE_SAVE_TIMEOUT_SECONDS,
        )
        source_text = f'{keep_note.get("body", "")}\n\n{merge_note.get("body", "")}'.strip()
        return self._build_analysis_result(
            data,
            source_text=source_text,
            fallback_category="note",
        )

    def build_fallback_note_analysis(
        self,
        text: str,
        *,
        action: str = "create",
        target_note_id: str | None = None,
    ) -> TextAnalysisResult:
        result = self._build_degraded_result(
            source_text=text,
            fallback_category="note",
            raw_response='{"fallback": true}',
        )
        result.action = action
        result.target_note_id = target_note_id
        result.is_note = True
        return result

    def _build_heuristic_route_decision(
        self,
        *,
        text: str,
        candidate_notes: list[dict[str, Any]],
        conversation_context: list[dict[str, Any]],
    ) -> RouteDecision:
        normalized = " ".join(text.strip().lower().split())
        if not normalized:
            return RouteDecision(route="ignore", confidence=0.1, reason="empty")
        if "저장하지 마" in text or "저장하지말" in normalized:
            return RouteDecision(route="ignore", confidence=0.99, reason="explicit do not save")
        if self._looks_like_general_question(normalized):
            return RouteDecision(
                route="tool",
                confidence=0.7,
                reason="general question fallback",
                tool_name="agent_fallback",
                tool_query=text,
            )
        if self._looks_like_contextual_followup(normalized, conversation_context):
            return RouteDecision(
                route="tool",
                confidence=0.7,
                reason="context follow-up",
                tool_name="agent_fallback",
                tool_query=text,
            )
        if self._looks_like_note_worthy_text(text):
            target_note_id = self._guess_append_target(candidate_notes, normalized)
            if target_note_id is None and len(candidate_notes) == 1 and re.match(
                r"^(?:그리고|추가로|또|이어서|거기에)(?:\s|$)",
                normalized,
            ):
                target_note_id = str(candidate_notes[0].get("id", "")).strip() or None
            return RouteDecision(
                route="append" if target_note_id else "create",
                confidence=0.65,
                target_note_id=target_note_id,
                reason="heuristic note",
            )
        return RouteDecision(route="ignore", confidence=0.4, reason="heuristic ignore")

    def _post_completion(
        self,
        payload: dict[str, Any],
        *,
        model_name: str,
        read_timeout: float | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(
            connect=10.0,
            read=read_timeout or self.timeout,
            write=30.0,
            pool=30.0,
        )
        started_at = time.perf_counter()

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if response.status_code >= 400:
                    raise NIMProviderError(
                        f"NIM API error {response.status_code}: {response.text[:500]}"
                    )
                data = response.json()
                choices = data.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise NIMProviderError(
                        f"NIM returned no choices for model={model_name}: {json.dumps(data, ensure_ascii=False)[:500]}"
                    )
                return data
        except httpx.ReadTimeout as exc:
            elapsed = time.perf_counter() - started_at
            raise NIMProviderError(
                f"NIM read timeout after {elapsed:.1f}s for model={model_name}"
            ) from exc
        except httpx.HTTPError as exc:
            raise NIMProviderError(
                f"NIM HTTP error for model={model_name}: {exc}"
            ) from exc

    def _build_analysis_result(
        self,
        data: dict[str, Any],
        *,
        source_text: str,
        fallback_category: str,
    ) -> TextAnalysisResult:
        raw_response = json.dumps(data, ensure_ascii=False)
        try:
            content = self._extract_message_text(data)
            parsed = self._parse_content(content, source_text=source_text)
        except Exception as exc:
            if fallback_category == "note":
                return self._build_degraded_result(
                    source_text=source_text,
                    fallback_category=fallback_category,
                    raw_response=raw_response,
                )
            raise NIMProviderError(
                f"Failed to parse NIM response: {raw_response[:500]}"
            ) from exc

        title = str(parsed.get("title", "")).strip() or self._fallback_title(source_text)
        summary = str(parsed.get("summary", "")).strip() or self._fallback_summary(source_text)
        title, summary = self._preserve_important_temporal_info(source_text, title, summary)
        tags = self._normalize_tags(parsed.get("tags"))
        category = str(parsed.get("category", fallback_category)).strip() or fallback_category
        confidence = self._coerce_confidence(parsed.get("confidence"))
        ocr_text = str(parsed.get("ocr_text", "")).strip() or None
        is_note = self._coerce_optional_bool(parsed.get("is_note"))
        needs_user_clarification = self._coerce_bool(
            parsed.get("needs_user_clarification"),
            default=False,
        )

        if is_note is None:
            if category == "note":
                is_note = True
            elif category == "general_photo":
                is_note = False
            elif ocr_text:
                is_note = True

        if category == "unsure" and ocr_text and is_note is True:
            category = "note"

        action = str(parsed.get("action", "create")).strip().lower() or "create"
        if action not in {"create", "append", "ignore"}:
            action = "create"
        target_note_id = str(parsed.get("target_note_id", "")).strip() or None
        tool_name = str(parsed.get("tool_name", "")).strip() or None
        if tool_name not in {
            None,
            "count_notes",
            "recent_notes",
            "list_tags",
            "count_notes_by_tag",
            "notes_by_tag",
            "search_notes",
            "suggest_note_merge",
            "agent_fallback",
        }:
            tool_name = None
        tool_query = str(parsed.get("tool_query", "")).strip() or None
        tool_tag = str(parsed.get("tool_tag", "")).strip() or None
        try:
            tool_limit = int(parsed.get("tool_limit")) if parsed.get("tool_limit") is not None else None
        except (TypeError, ValueError):
            tool_limit = None
        if tool_limit is not None:
            tool_limit = max(1, min(tool_limit, 10))

        return TextAnalysisResult(
            title=title,
            summary=summary,
            tags=tags,
            category=category,
            confidence=confidence,
            raw_response=raw_response,
            ocr_text=ocr_text,
            is_note=is_note,
            needs_user_clarification=needs_user_clarification,
            action=action,
            target_note_id=target_note_id,
            tool_name=tool_name,
            tool_query=tool_query,
            tool_tag=tool_tag,
            tool_limit=tool_limit,
        )

    @staticmethod
    def _extract_message_text(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise KeyError("choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise KeyError("message")

        content = message.get("content")
        normalized = NvidiaNIMProvider._normalize_message_content(content)
        if normalized:
            return normalized

        for field in ("output_text", "text", "reasoning_content"):
            value = message.get(field)
            normalized = NvidiaNIMProvider._normalize_message_content(value)
            if normalized:
                return normalized

        raise KeyError("content")

    @classmethod
    def _parse_json_message(cls, data: dict[str, Any], *, error_prefix: str) -> dict[str, Any]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise NIMProviderError(f"{error_prefix}: response had no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise NIMProviderError(f"{error_prefix}: response had no message")

        fallback_objects: list[dict[str, Any]] = []
        for field in ("content", "output_text", "text", "reasoning_content"):
            content = cls._normalize_message_content(message.get(field))
            if not content:
                continue
            stripped = cls._strip_code_fences(content)
            candidate = cls._extract_json_candidate(stripped) or stripped
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if "route" in parsed or "action" in parsed:
                return parsed
            if any(str(key).strip() and value not in (None, "", [], {}) for key, value in parsed.items()):
                fallback_objects.append(parsed)

        if fallback_objects:
            return fallback_objects[0]
        raise NIMProviderError(
            f"{error_prefix}: {json.dumps(data, ensure_ascii=False)[:500]}"
        )
    @staticmethod
    def _normalize_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    stripped = item.strip()
                    if stripped:
                        parts.append(stripped)
                    continue

                if not isinstance(item, dict):
                    continue

                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    parts.append(text_value.strip())
                    continue

                if item.get("type") == "output_text":
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())

            return "\n".join(parts).strip()

        return ""

    def _build_degraded_result(
        self,
        *,
        source_text: str,
        fallback_category: str,
        raw_response: str,
    ) -> TextAnalysisResult:
        cleaned = " ".join(source_text.split()).strip()
        summary = "AI 요약 생성에 실패했어. 원문을 확인해줘." if cleaned else self._fallback_summary(source_text)
        return TextAnalysisResult(
            title=self._fallback_title(source_text),
            summary=summary,
            tags=[],
            category=fallback_category,
            confidence=0.05,
            raw_response=raw_response,
            is_note=True,
        )

    @classmethod
    def _parse_content(cls, content: str, *, source_text: str) -> dict[str, Any]:
        stripped = cls._strip_code_fences(content)
        candidates = [stripped]
        extracted = cls._extract_json_candidate(stripped)
        if extracted and extracted != stripped:
            candidates.insert(0, extracted)

        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        partial = cls._parse_partial_json_like(stripped)
        if partial:
            partial.setdefault("title", cls._fallback_title(source_text))
            partial.setdefault("summary", cls._fallback_summary(source_text))
            partial.setdefault("category", "note")
            partial.setdefault("confidence", 0.35)
            return partial

        raise json.JSONDecodeError("Unable to parse model content", stripped, 0)

    @staticmethod
    def _strip_code_fences(content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            ).strip()
        return stripped

    @staticmethod
    def _extract_json_candidate(content: str) -> str | None:
        start = content.find("{")
        if start < 0:
            return None

        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(content)):
            char = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return content[start : index + 1]

        return content[start:]

    @classmethod
    def _parse_partial_json_like(cls, content: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        string_fields = ("title", "summary", "category", "ocr_text")
        for field in string_fields:
            value = cls._extract_partial_string_field(content, field)
            if value is not None:
                result[field] = value

        tags_match = re.search(r'"tags"\s*:\s*\[(.*?)\]', content, flags=re.DOTALL)
        if tags_match:
            tags = re.findall(r'"([^"]+)"', tags_match.group(1))
            result["tags"] = tags

        confidence_match = re.search(r'"confidence"\s*:\s*([0-9.]+)', content)
        if confidence_match:
            result["confidence"] = confidence_match.group(1)

        bool_fields = ("is_note", "needs_user_clarification")
        for field in bool_fields:
            bool_match = re.search(rf'"{field}"\s*:\s*(true|false)', content, flags=re.IGNORECASE)
            if bool_match:
                result[field] = bool_match.group(1).lower() == "true"

        return result

    @staticmethod
    def _extract_partial_string_field(content: str, field: str) -> str | None:
        marker = f'"{field}"'
        start = content.find(marker)
        if start < 0:
            return None

        colon = content.find(":", start + len(marker))
        if colon < 0:
            return None

        quote = content.find('"', colon + 1)
        if quote < 0:
            return None

        index = quote + 1
        chars: list[str] = []
        escaped = False
        while index < len(content):
            char = content[index]
            if escaped:
                chars.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                break
            else:
                chars.append(char)
            index += 1

        value = "".join(chars).strip()
        return value or None

    @staticmethod
    def _looks_like_note_worthy_text(text: str) -> bool:
        normalized = " ".join(text.strip().lower().split())
        if len(normalized) < 4:
            return False
        note_keywords = (
            "로그",
            "개발",
            "기록",
            "공부",
            "학습",
            "todo",
            "to do",
            "할 일",
            "아이디어",
            "설정",
            "아키텍처",
            "엔드포인트",
            "웹훅",
            "fastapi",
            "api",
            "구현",
            "구조",
            "결정",
            "계획",
            "메모",
        )
        if any(keyword in normalized for keyword in note_keywords):
            return True
        note_endings = (
            "했다",
            "하기로 했다",
            "받는다",
            "쓴다",
            "정리",
            "정했다",
            "붙인다",
            "남기기로 했다",
            "하기",
            "만들기",
            "확인",
            "준비",
            "구매",
            "읽기",
            "보내기",
            "시작하기",
            "공부하기",
            "제출하기",
            "예약",
            "알아보기",
        )
        return any(ending in normalized for ending in note_endings)

    @staticmethod
    def _looks_like_general_question(normalized: str) -> bool:
        if any(
            marker in normalized
            for marker in ("알려줘", "보여줘", "찾아줘", "검색", "뭐 있", "뭐있", "원문", "목록")
        ):
            return False
        if any(marker in normalized for marker in ("날씨", "기온", "비 와", "비와", "눈 와", "눈와")):
            return False
        question_body = normalized.rstrip("?？").strip()
        question_words = ("뭐", "무엇", "어떻게", "어때", "왜", "언제", "어디", "누구", "몇")
        return (
            any(marker in question_body for marker in question_words)
            and question_body.endswith(("까", "지", "어때", "인가", "맞아"))
        )

    @staticmethod
    def _looks_like_contextual_followup(normalized: str, conversation_context: list[dict[str, Any]]) -> bool:
        if not conversation_context:
            return False
        followup_markers = (
            "그거",
            "이거",
            "저거",
            "전체",
            "전부",
            "이어서",
            "계속",
            "말고",
            "뭐있지",
            "뭐 있지",
            "말이야",
        )
        return len(normalized) <= 40 and any(marker in normalized for marker in followup_markers)

    @staticmethod
    def _guess_append_target(candidate_notes: list[dict[str, Any]], normalized_text: str) -> str | None:
        if not candidate_notes:
            return None
        for note in candidate_notes:
            haystack = " ".join(
                [
                    str(note.get("title", "")).lower(),
                    str(note.get("summary", "")).lower(),
                    str(note.get("body", "")).lower()[:300],
                ]
            )
            terms = [term for term in re.split(r"\s+", normalized_text) if len(term) >= 2]
            if sum(1 for term in terms if term in haystack) >= 2:
                note_id = str(note.get("id", "")).strip()
                if note_id:
                    return note_id
        return None

    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(tag).strip() for tag in value if str(tag).strip()]

    @staticmethod
    def _coerce_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return min(1.0, max(0.0, confidence))

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1"}:
                return True
            if lowered in {"false", "no", "0"}:
                return False
        return default

    @classmethod
    def _coerce_optional_bool(cls, value: Any) -> bool | None:
        if value is None:
            return None
        return cls._coerce_bool(value, default=False)

    @classmethod
    def _preserve_important_temporal_info(
        cls,
        source_text: str,
        title: str,
        summary: str,
    ) -> tuple[str, str]:
        temporal_items = cls._extract_temporal_items(source_text)
        if not temporal_items:
            return title, summary

        actionable_items = [
            item for item in temporal_items if cls._is_actionable_temporal_item(source_text, item)
        ]
        for item in temporal_items:
            if item not in actionable_items:
                title = cls._remove_temporal_prefix(title, item)
                summary = cls._remove_temporal_prefix(summary, item)

        if not actionable_items:
            return title, summary

        combined = f"{title} {summary}"
        missing = [item for item in actionable_items if item not in combined]
        if not missing:
            return title, summary

        prefix = " / ".join(missing[:3])
        if title and all(item not in title for item in missing[:1]) and len(title) <= 54:
            title = f"{prefix} - {title}"[:80]
        if summary:
            summary = f"{prefix}: {summary}"
        else:
            summary = prefix
        return title, summary

    @staticmethod
    def _remove_temporal_prefix(value: str, item: str) -> str:
        cleaned = re.sub(
            rf"^\s*{re.escape(item)}\s*(?:[-–—:：/]\s*)?",
            "",
            value,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned or value

    @staticmethod
    def _is_actionable_temporal_item(source_text: str, item: str) -> bool:
        lowered = source_text.lower()
        index = lowered.find(item.lower())
        if index < 0:
            return False

        before = lowered[max(0, index - 32):index]
        after = lowered[index + len(item):index + len(item) + 64]
        nearby = f"{before} {item.lower()} {after}"
        explicit_schedule_hints = ("마감", "기한", "데드라인", "deadline", "일정", "예약")
        if any(hint in nearby for hint in explicit_schedule_hints):
            return True

        past_context_hints = ("어제", "지난", "예전에", "전에")
        past_action_hints = ("기다렸", "했었", "했는데", "했지만", "이었다", "였는데", "취소했", "못했")
        if any(hint in before for hint in past_context_hints):
            return False
        if any(hint in after[:40] for hint in past_action_hints):
            return False

        future_action_hints = (
            "작성",
            "제출",
            "완료",
            "시작",
            "하기",
            "해야",
            "하자",
            "할 것",
            "제작",
            "예약",
            "방문",
            "출발",
            "회의",
            "수업",
            "숙제",
            "공부",
            "읽기",
            "보내기",
        )
        return any(hint in after for hint in future_action_hints)

    @staticmethod
    def _extract_temporal_items(source_text: str) -> list[str]:
        patterns = (
            r"\b\d{4}[-./년]\s*\d{1,2}[-./월]\s*\d{1,2}\s*(?:일)?\b",
            r"\b\d{1,2}\s*월\s*\d{1,2}\s*일(?:까지|까지는|까지로|에|부터|까지)?",
            r"\b\d{1,2}\s*/\s*\d{1,2}(?:까지|에|부터)?",
            r"\b\d{1,2}\s*시\s*(?:\d{1,2}\s*분)?(?:까지|부터|에)?",
            r"\b(?:오전|오후)\s*\d{1,2}\s*시\s*(?:\d{1,2}\s*분)?",
            r"\b(?:오늘|내일|모레|이번\s*주|다음\s*주|이번\s*달|다음\s*달|월요일|화요일|수요일|목요일|금요일|토요일|일요일)(?:까지|까지는|에|부터)?",
            r"\b(?:마감|기한|데드라인|deadline)\s*[:：]?\s*[^\s,.;!?]{1,24}",
        )
        items: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, source_text, flags=re.IGNORECASE):
                item = " ".join(match.group(0).split()).strip(" ,.;!?")
                if item and item not in items:
                    items.append(item)
        return items[:5]

    @staticmethod
    def _fallback_title(source_text: str) -> str:
        cleaned = " ".join(source_text.split()).strip()
        return cleaned[:40] or "제목 미확인"

    @staticmethod
    def _fallback_summary(source_text: str) -> str:
        cleaned = " ".join(source_text.split()).strip()
        return cleaned[:120] or "요약을 생성하지 못했어."

    @staticmethod
    def _build_data_url(image_path: str) -> str:
        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
