from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.telegram_webhook import router as telegram_router
from app.integrations.telegram import TelegramClient
from app.models.db import Database
from app.services.nim_provider import NvidiaNIMProvider
from app.services.note_manager import NoteManager
from app.services.router import build_router


def get_required_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    sqlite_path = get_required_env("SQLITE_PATH", "./data/app.sqlite")
    database = Database(sqlite_path)
    database.initialize()

    note_manager = NoteManager(database)
    nim_provider = NvidiaNIMProvider(
        api_key=get_required_env("NIM_API_KEY", "test-key"),
        base_url=get_required_env("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        model=get_required_env("NIM_TEXT_MODEL", "meta/llama-3.1-70b-instruct"),
        timeout=float(os.getenv("NIM_TIMEOUT_SECONDS", "180")),
        max_tokens=int(os.getenv("NIM_MAX_TOKENS", "220")),
    )
    telegram_client = TelegramClient(
        bot_token=get_required_env("TELEGRAM_BOT_TOKEN", "test-token")
    )

    app.state.database = database
    app.state.note_manager = note_manager
    app.state.nim_provider = nim_provider
    app.state.telegram_client = telegram_client
    app.state.update_router = build_router(note_manager, nim_provider, telegram_client)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="tg-note-agent", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(telegram_router)
    return app


app = create_app()
