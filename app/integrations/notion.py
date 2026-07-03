from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class NotionExportResult:
    page_id: str
    url: str | None
    status: str = "exported"


class NotionClient:
    def __init__(
        self,
        api_key: str,
        *,
        database_id: str | None = None,
        parent_page_id: str | None = None,
        title_property: str = "Name",
        tags_property: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not database_id and not parent_page_id:
            raise ValueError("NotionClient requires database_id or parent_page_id")

        self.api_key = api_key
        self.database_id = database_id
        self.parent_page_id = parent_page_id
        self.title_property = title_property
        self.tags_property = tags_property
        self.timeout = timeout
        self.base_url = "https://api.notion.com/v1"

    def export_note(
        self,
        *,
        title: str,
        summary: str,
        body: str,
        tags: list[str],
    ) -> NotionExportResult:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }

        payload = {
            "parent": self._build_parent(),
            "properties": self._build_properties(title, tags),
            "children": self._build_children(summary, body, tags),
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/pages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        return NotionExportResult(
            page_id=data["id"],
            url=data.get("url"),
        )

    def _build_parent(self) -> dict[str, str]:
        if self.database_id:
            return {"database_id": self.database_id}
        return {"page_id": self.parent_page_id or ""}

    def _build_properties(self, title: str, tags: list[str]) -> dict:
        title_value = [{"text": {"content": title[:2000]}}]
        if self.database_id:
            properties: dict[str, object] = {
                self.title_property: {"title": title_value},
            }
            if self.tags_property and tags:
                properties[self.tags_property] = {
                    "multi_select": [{"name": tag[:100]} for tag in tags]
                }
            return properties
        return {"title": {"title": title_value}}

    @staticmethod
    def _build_children(summary: str, body: str, tags: list[str]) -> list[dict]:
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"요약: {summary}"}}
                    ]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"원문: {body}"}}
                    ]
                },
            },
        ]
        if tags:
            children.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": f"태그: {', '.join(tags)}"},
                            }
                        ]
                    },
                }
            )
        return children


def build_notion_client_from_env() -> NotionClient | None:
    import os

    api_key = os.getenv("NOTION_API_KEY", "").strip()
    database_id = os.getenv("NOTION_NOTES_DATABASE_ID", "").strip() or None
    parent_page_id = os.getenv("NOTION_PARENT_PAGE_ID", "").strip() or None

    if not api_key or (not database_id and not parent_page_id):
        return None

    return NotionClient(
        api_key=api_key,
        database_id=database_id,
        parent_page_id=parent_page_id,
        title_property=os.getenv("NOTION_TITLE_PROPERTY", "Name").strip() or "Name",
        tags_property=os.getenv("NOTION_TAGS_PROPERTY", "").strip() or None,
        timeout=float(os.getenv("NOTION_TIMEOUT_SECONDS", "15")),
    )
