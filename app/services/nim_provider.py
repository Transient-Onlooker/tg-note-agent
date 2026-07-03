from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.models.schemas import TextAnalysisResult


class NIMProviderError(RuntimeError):
    pass


class NvidiaNIMProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 30.0,
        max_tokens: int = 160,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def analyze_text(self, text: str) -> TextAnalysisResult:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You analyze personal notes. "
                        "Return strict JSON with keys title, summary, tags, category, confidence. "
                        "tags must be an array of short strings. confidence must be a number between 0 and 1."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Analyze the following Telegram note text and respond only in JSON.\n\n"
                        f"Text:\n{text}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(connect=10.0, read=self.timeout, write=30.0, pool=30.0)
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
        except httpx.ReadTimeout as exc:
            elapsed = time.perf_counter() - started_at
            raise NIMProviderError(
                f"NIM read timeout after {elapsed:.1f}s for model={self.model}"
            ) from exc
        except httpx.HTTPError as exc:
            raise NIMProviderError(
                f"NIM HTTP error for model={self.model}: {exc}"
            ) from exc

        try:
            content = data["choices"][0]["message"]["content"]
            parsed = self._parse_content(content)
        except Exception as exc:
            raise NIMProviderError(
                f"Failed to parse NIM response: {json.dumps(data, ensure_ascii=False)[:500]}"
            ) from exc
        return TextAnalysisResult(
            title=parsed["title"].strip(),
            summary=parsed["summary"].strip(),
            tags=[str(tag).strip() for tag in parsed.get("tags", []) if str(tag).strip()],
            category=str(parsed.get("category", "note")).strip() or "note",
            confidence=float(parsed.get("confidence", 0.0)),
            raw_response=json.dumps(data, ensure_ascii=False),
        )

    @staticmethod
    def _parse_content(content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            ).strip()
        return json.loads(stripped)
