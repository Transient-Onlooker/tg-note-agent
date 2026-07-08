# tg-note-agent

Telegram webhook based personal note agent.

## Current scope

- Accept Telegram text updates through `/webhook/telegram`
- Save raw messages to SQLite
- Treat Telegram outbound replies as best-effort so `sendMessage` timeout does not cause webhook 500
- Run deterministic command gate before AI routing for read/search/recent/count/correction/delete/numbered references
- Save `NOTE`, `AI_ANALYSIS`, `IMAGE_FILE`, `CONVERSATION_STATE`, and `NOTE_REVISION`
- Sync OCR correction into both `NOTE.body` and `IMAGE_FILE.ocr_text`
- Use NVIDIA NIM only after command-gate miss for save/append/tool/fallback workflows

## Known Risks

- OCR quality still depends on the selected vision model and image clarity.
- Notion sync is optional and still secondary to local SQLite storage.
- Long multi-step agent behavior is intentionally bounded; deterministic commands should stay in the command gate.

## Current Issues To Fix Next

- `/fix` currently recognizes the command but does not execute edits yet. Implement candidate selection, change preview, and explicit approval before mutating an existing note.
- Note metadata generation can still accept poor model output. Add validation so unrelated AI titles/summaries are rejected instead of being saved as if they were valid summaries.
- Saved-note summaries must be actual AI summaries, not prefix truncation. If AI metadata fails, the bot should say that summary generation failed and store only the cleaned body.
- Continue with narrow, targeted tests for the changed command/provider paths instead of running the whole suite after every small change.

## Architecture

Current implementation and next target diagrams:

- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/diagrams/README.md`

## Environment

Copy `.env.example` to `.env` and set:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `NIM_API_KEY`
- `NIM_BASE_URL`
- `NIM_TEXT_MODEL` (`z-ai/glm-5.2` current local default; `minimaxai/minimax-m2.7` is also supported)
- `NIM_TIMEOUT_SECONDS`
- `NIM_MAX_TOKENS` (`900` recommended output cap for metadata JSON; this is not the input context limit)
- `NIM_VISION_MODEL`
- `NOTION_API_KEY`
- `NOTION_NOTES_DATABASE_ID` or `NOTION_PARENT_PAGE_ID`
- `SQLITE_PATH`

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Test

```bash
pytest
```
