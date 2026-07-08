# Telegram Command Design

This document defines the target command UX for the Telegram-first note agent.

## Principle

Slash commands fix the user intent. The text after the command remains flexible and can be interpreted by deterministic parsing, DB search, or AI-assisted argument resolution.

Do not let AI directly decide whether a message is an app command or note content when the command is explicit. AI may help resolve targets or summarize content, but state-changing actions must pass deterministic validation and, when destructive or mutating existing notes, user confirmation.

## Command Set

| Command | Intent | Examples | Target behavior |
| --- | --- | --- | --- |
| `/new` | Create a new note | `/new 오늘 회의에서 v1 테스트를 먼저 하기로 함` | Always create a new NOTE from the remaining text. AI may generate title, summary, tags. |
| `/add` | Append to an existing note | `/add 5번 메모에 후속 작업 추가`, `/add 대수 보고서 관련 메모에 참고 링크 추가` | Resolve target note, show append preview, require approval before mutating. |
| `/list` | List notes | `/list`, `/list 대수`, `/list 태그:수학` | Return paginated note list. Store `last_list_results`. |
| `/show` | Show note summary/detail | `/show 5번 메모`, `/show 대수 보고서 관련 메모` | Resolve target, show title/summary/body excerpt. Store `last_selected_note_id`. |
| `/raw` | Show original body/OCR | `/raw 5번`, `/raw 방금 메모` | Resolve target and return full `NOTE.body` or `IMAGE_FILE.ocr_text`. |
| `/delete` | Delete note(s) | `/delete 5번 메모`, `/delete 대수 관련 메모` | Resolve candidates, require approval, then soft delete. Never delete immediately on ambiguous text. |
| `/fix` | Modify existing note text | `/fix 5번 메모의 대수를 확률과 통계로 수정` | Resolve note and edit intent, show before/after preview, require approval. |
| `/dedupe` | Remove duplicate notes | `/dedupe`, `/dedupe 대수 관련 메모` | Find exact or scoped duplicates, show groups, require approval before soft delete. |
| `/help` | Show usage | `/help`, `/help fix` | Return command examples and safety rules. |

## Pagination

`/list` and broad `/show` results should paginate when the result set is larger than 10 items.

- Default page size: 10.
- Follow-up commands: `/next`, `/prev`, `/page 3`.
- Store page state in `CONVERSATION_STATE` with the query, result ids, page size, and current page.
- Page numbers must refer to the current result set only. If the result state is stale or missing, ask the user to run `/list` or `/show` again.

## Approval Rules

Creating a new note with `/new` does not need approval because it is additive and reversible by delete.

Any command that mutates an existing note or deletes data requires approval:

- `/add`: approve append preview.
- `/fix`: approve before/after diff.
- `/delete`: approve target list.
- `/dedupe`: approve duplicate groups and which notes will be kept/deleted.

Approvals should be explicit and tied to pending state:

- `승인`, `확인`, `진행`, `yes` execute the pending action.
- `취소`, `cancel` clears the pending action.
- Pending action state must include command type, target note ids, preview text, and creation time.

## Ambiguous Commands

Commands with broad or fuzzy targets must not execute directly.

Example: `/dedupe 대수 관련 메모`

Expected flow:

1. Search notes related to `대수`.
2. Detect duplicate groups only inside that scoped set.
3. Show candidate groups and the keep/delete plan.
4. Wait for approval.

If target resolution returns multiple unrelated candidates, ask the user to choose a number or narrow the query. Do not fall back to saving the command as a note.

## Telegram Bot Command Menu

Register the command names through Telegram `setMyCommands` or BotFather `/setcommands` so `/` opens Telegram's command suggestion menu.

Telegram only autocompletes command names and descriptions. It does not autocomplete command arguments such as `5번 메모` or `대수 관련 메모`.

Suggested command descriptions:

```text
new - 새 메모 생성
add - 기존 메모에 내용 추가
list - 메모 목록 보기
show - 메모 조회
raw - 원문/OCR 보기
delete - 메모 삭제 요청
fix - 메모 내용 수정 요청
dedupe - 중복 메모 정리
help - 사용법 보기
```
