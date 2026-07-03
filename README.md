# tg-note-agent

Telegram webhook based personal note agent.

## v1 scope

- Accept Telegram text updates through `/webhook/telegram`
- Save raw messages to SQLite
- Analyze note text with NVIDIA NIM
- Save `NOTE` and `AI_ANALYSIS`
- Reply to Telegram with a short summary

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
- `NIM_TEXT_MODEL`
- `NIM_TIMEOUT_SECONDS`
- `NIM_MAX_TOKENS`
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
