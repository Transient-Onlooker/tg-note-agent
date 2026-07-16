# tg-note-agent

Telegram webhook based personal note agent.

## Current scope

- Accept Telegram text updates through `/webhook/telegram`
- Save raw messages to SQLite
- Treat Telegram outbound replies as best-effort so `sendMessage` timeout does not cause webhook 500
- Run deterministic command gate before AI routing for read/search/recent/count/correction/delete/numbered references
- Support prototype slash commands for clear intent: `/new`, `/add`, `/list`, `/show`, `/raw`, `/fix`, `/delete`, `/dedupe`, `/next`, `/prev`, `/page`, and `/help`
- Require preview/approval before mutating or deleting existing notes
- Save `NOTE`, `AI_ANALYSIS`, `IMAGE_FILE`, `CONVERSATION_STATE`, and `NOTE_REVISION`
- Sync OCR correction into both `NOTE.body` and `IMAGE_FILE.ocr_text`
- Use NVIDIA NIM only after command-gate miss for save/append/tool/fallback workflows

## Command UX Direction

Slash commands are the current prototype interface, not the final product shape.

The long-term target is a natural-language router that converts user messages into the same internal command objects used by slash commands. For example, "5번 메모의 인민을 시민으로 바꿔줘" should become an internal `fix` action with target resolution, preview, and approval, even if the user did not type `/fix`.

Slash commands should remain as a debugging and fallback surface while the natural-language router is being developed. The important boundary is that AI may help classify intent and resolve arguments, but existing-note mutation and deletion must still pass deterministic validation and user approval.

## Known Risks

- OCR quality still depends on the selected vision model and image clarity.
- Notion sync is optional and still secondary to local SQLite storage.
- Long multi-step agent behavior is intentionally bounded; deterministic commands should stay in the command gate.

## Current Issues To Fix Next

- Note metadata generation can still accept poor model output. Add validation so unrelated AI titles/summaries are rejected instead of being saved as if they were valid summaries.
- Saved-note summaries must be actual AI summaries, not prefix truncation. If AI metadata fails, the bot should say that summary generation failed and store only the cleaned body.
- Natural-language router should be added after slash command behavior is stable. It should route ordinary Korean requests into internal actions such as `new`, `list`, `show`, `add`, `fix`, `delete`, and `dedupe` without requiring the user to type `/`.
- Add Telegram document/PDF intake after the image pipeline is stable. PDF handling should extract text, preserve file metadata, summarize long documents, and support follow-up commands against the saved document note.
- Continue with narrow, targeted tests for the changed command/provider paths instead of running the whole suite after every small change.

## Operator Checklist

Already checked on the current local setup:

- `.env` exists and required keys are populated: Telegram bot token, allowed user id, NIM API key/base URL/model settings, token cap, vision model, and SQLite path.
- `.gitignore` excludes `.env`, local SQLite files, generated logs, and image files.
- `testterminal.bat` is available for local bot-style testing without opening Telegram.

The test terminal also accepts local images and PDFs:

    /image "C:\\path\\photo.jpg" | optional caption
    /pdf "C:\\path\\document.pdf" | optional caption

`/image` runs the existing Telegram photo/OCR path using the local file. `/pdf` processes pages in order, keeps sufficient embedded text, and sends the largest directly encoded JPEG page image to vision OCR when text is missing. It then sends the merged page text through `/new` to test title and summary generation. Pages that require full PDF rasterization or non-JPEG image decoding are reported as OCR failures because native PDF/image extensions may be blocked by Windows application-control policy.

PDF test limits can be changed with `TESTTERMINAL_PDF_MAX_PAGES` (default `12`), `TESTTERMINAL_PDF_MAX_CHARS` (default `30000`), and `TESTTERMINAL_PDF_MIN_TEXT_CHARS` (default `20`).

Before live Telegram testing:

- Start the FastAPI server and ngrok tunnel.
- Restart the server after command-menu changes so Telegram command registration runs again.
- Keep VPN/Tailscale routing in a state where `api.telegram.org` resolves to a real Telegram IP, not `127.0.0.1`.

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
