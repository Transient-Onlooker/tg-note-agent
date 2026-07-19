from __future__ import annotations

import re
from dataclasses import asdict, dataclass


_CHECKBOX_RE = re.compile(r"^\s*(?:[-*]\s*)?\[(?P<mark>[ xX])\]\s*(?P<body>.+?)\s*$")
_BULLET_RE = re.compile(
    r"^\s*(?P<mark>[-*•▪◦]|☐|☑|✓|✔|\d+[.)])\s*(?P<body>.+?)\s*$"
)
_HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s+)?(?P<body>[^:：]{1,40})[:：]\s*$")


@dataclass(frozen=True, slots=True)
class NoteListItemDraft:
    section_label: str | None
    body: str
    position: int
    is_completed: bool = False

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def extract_explicit_batch_split(text: str) -> str | None:
    """Return the note body only when the user explicitly asks to split it."""
    stripped = text.strip()
    prefix_patterns = (
        r"^(?:각각|각각의\s*메모로|여러\s*메모로)\s*"
        r"(?:나눠\s*)?저장해(?:줘|주세요|주라)?\s*[:,-]?\s*",
        r"^(?:나눠|분리해)\s*저장해(?:줘|주세요|주라)?\s*[:,-]?\s*",
    )
    for pattern in prefix_patterns:
        match = re.match(pattern, stripped, flags=re.IGNORECASE)
        if match:
            body = stripped[match.end() :].strip()
            return body or None

    suffix = re.search(
        r"\s*(?:각각|여러\s*메모로)\s*(?:나눠\s*)?"
        r"저장해(?:줘|주세요|주라)?[.!?]?\s*$",
        stripped,
        flags=re.IGNORECASE,
    )
    if suffix:
        body = stripped[: suffix.start()].strip()
        return body or None
    return None


def parse_note_list_items(text: str) -> list[NoteListItemDraft]:
    """Conservatively derive list items while leaving NOTE.body untouched."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or "\n" not in normalized:
        return []

    raw_lines = normalized.split("\n")
    non_empty = [line.strip() for line in raw_lines if line.strip()]
    if len(non_empty) < 2:
        return []

    bullet_count = sum(_is_list_marker(line) for line in non_empty)
    heading_count = sum(_HEADING_RE.match(line) is not None for line in non_empty)
    blank_group_count = _count_non_empty_groups(raw_lines)
    candidate_lines = [line for line in non_empty if _HEADING_RE.match(line) is None]
    if len(candidate_lines) < 2:
        return []

    short_count = sum(len(_strip_marker(line)[0]) <= 100 for line in candidate_lines)
    sentence_count = sum(
        _strip_marker(line)[0].rstrip().endswith((".", "?", "!", "다.", "요."))
        for line in candidate_lines
    )
    explicit_list = bullet_count >= 2 or heading_count >= 1
    implicit_list = (
        len(candidate_lines) >= 3
        and short_count / len(candidate_lines) >= 0.8
        and sentence_count / len(candidate_lines) < 0.5
        and (blank_group_count >= 2 or len(candidate_lines) >= 4)
    )
    if not explicit_list and not implicit_list:
        return []

    items: list[NoteListItemDraft] = []
    current_heading: str | None = None
    group_number = 1
    seen_content_in_group = False
    has_multiple_groups = blank_group_count >= 2

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            if seen_content_in_group:
                group_number += 1
                seen_content_in_group = False
                current_heading = None
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            current_heading = heading.group("body").strip()
            seen_content_in_group = False
            continue

        body, completed = _strip_marker(line)
        if not body:
            continue
        section_label = current_heading
        if section_label is None and has_multiple_groups:
            section_label = f"묶음 {group_number}"
        items.append(
            NoteListItemDraft(
                section_label=section_label,
                body=body,
                position=len(items) + 1,
                is_completed=completed,
            )
        )
        seen_content_in_group = True

    return items if len(items) >= 2 else []


def _count_non_empty_groups(lines: list[str]) -> int:
    groups = 0
    in_group = False
    for line in lines:
        if line.strip():
            if not in_group:
                groups += 1
                in_group = True
        else:
            in_group = False
    return groups


def _is_list_marker(line: str) -> bool:
    return _CHECKBOX_RE.match(line) is not None or _BULLET_RE.match(line) is not None


def _strip_marker(line: str) -> tuple[str, bool]:
    checkbox = _CHECKBOX_RE.match(line)
    if checkbox:
        return checkbox.group("body").strip(), checkbox.group("mark").lower() == "x"
    bullet = _BULLET_RE.match(line)
    if bullet:
        completed = bullet.group("mark") in {"☑", "✓", "✔"}
        return bullet.group("body").strip(), completed
    return line.strip(), False
