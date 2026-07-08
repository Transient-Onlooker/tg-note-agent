from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.telegram_webhook import router as telegram_router
from app.integrations.notion import build_notion_client_from_env
from app.integrations.telegram import TelegramClient
from app.models.db import Database
from app.services.image_archive import ImageArchive
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

    notion_client = build_notion_client_from_env()
    note_manager = NoteManager(database, notion_client=notion_client)
    nim_provider = NvidiaNIMProvider(
        api_key=get_required_env("NIM_API_KEY", "test-key"),
        base_url=get_required_env("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        router_model=get_required_env("NIM_ROUTER_MODEL", "nvidia/nemotron-3-nano-30b-a3b"),
        text_model=get_required_env("NIM_TEXT_MODEL", "z-ai/glm-5.2"),
        vision_model=os.getenv("NIM_VISION_MODEL", "").strip() or None,
        router_timeout_seconds=float(os.getenv("NIM_ROUTER_TIMEOUT_SECONDS", "12")),
        text_timeout_seconds=float(os.getenv("NIM_TEXT_TIMEOUT_SECONDS", "45")),
        timeout=float(os.getenv("NIM_TIMEOUT_SECONDS", "30")),
        max_tokens=int(os.getenv("NIM_MAX_TOKENS", "900000")),
    )
    telegram_client = TelegramClient(
        bot_token=get_required_env("TELEGRAM_BOT_TOKEN", "test-token")
    )
    image_archive = ImageArchive(
        image_root=os.getenv("IMAGE_ROOT", "./images"),
        telegram_client=telegram_client,
        db=database,
    )

    app.state.database = database
    app.state.note_manager = note_manager
    app.state.notion_client = notion_client
    app.state.nim_provider = nim_provider
    app.state.telegram_client = telegram_client
    app.state.image_archive = image_archive
    app.state.update_router = build_router(
        note_manager,
        nim_provider,
        telegram_client,
        image_archive,
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="tg-note-agent", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(telegram_router)
    return app


app = create_app()
