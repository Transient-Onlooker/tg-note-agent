from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.integrations.notion import build_notion_client_from_env
from app.models.db import Database
from app.models.schemas import TelegramChat, TelegramMessage, TelegramUpdate, TelegramUser
from app.services.image_archive import ImageArchive
from app.services.nim_provider import NvidiaNIMProvider
from app.services.note_manager import NoteManager
from app.services.router import build_router, parse_allowed_user_ids


def _configure_console() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except OSError:
                pass


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value


def _required_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class TerminalTelegramClient:
    def send_message(self, chat_id: int | str, text: str) -> bool:
        print()
        print("[bot]")
        print(text)
        print()
        return True

    def set_my_commands(self, *args, **kwargs) -> bool:
        return True

    def get_file_path(self, file_id: str) -> str:
        raise RuntimeError("testterminal does not support Telegram file downloads")

    def download_file(self, file_path: str) -> bytes:
        raise RuntimeError("testterminal does not support Telegram file downloads")


def _build_update(message_id: int, chat_id: int, user_id: int, text: str) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=message_id,
        message=TelegramMessage(
            message_id=message_id,
            date=int(time.time()),
            chat=TelegramChat(id=chat_id),
            from_user=TelegramUser(id=user_id),
            text=text,
        ),
    )


def main() -> int:
    _configure_console()
    os.chdir(ROOT)
    _load_dotenv(ROOT / ".env")

    allowed_user_ids = parse_allowed_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS"))
    if not allowed_user_ids:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS is empty. Fill .env first.")

    user_id = int(os.getenv("TESTTERMINAL_USER_ID", str(next(iter(allowed_user_ids)))))
    chat_id = int(os.getenv("TESTTERMINAL_CHAT_ID", str(user_id)))

    database = Database(_required_env("SQLITE_PATH", "./data/app.sqlite"))
    database.initialize()

    notion_client = build_notion_client_from_env()
    note_manager = NoteManager(database, notion_client=notion_client)
    nim_provider = NvidiaNIMProvider(
        api_key=_required_env("NIM_API_KEY", "test-key"),
        base_url=_required_env("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        router_model=_required_env("NIM_ROUTER_MODEL", "nvidia/nemotron-3-nano-30b-a3b"),
        text_model=_required_env("NIM_TEXT_MODEL", "z-ai/glm-5.2"),
        vision_model=os.getenv("NIM_VISION_MODEL", "").strip() or None,
        router_timeout_seconds=float(os.getenv("NIM_ROUTER_TIMEOUT_SECONDS", "12")),
        text_timeout_seconds=float(os.getenv("NIM_TEXT_TIMEOUT_SECONDS", "120")),
        timeout=float(os.getenv("NIM_TIMEOUT_SECONDS", "30")),
        max_tokens=int(os.getenv("NIM_MAX_TOKENS", "900")),
    )
    telegram_client = TerminalTelegramClient()
    image_archive = ImageArchive(
        image_root=os.getenv("IMAGE_ROOT", "./images"),
        telegram_client=telegram_client,
        db=database,
    )
    update_router = build_router(note_manager, nim_provider, telegram_client, image_archive)

    print("tg-note-agent test terminal")
    print(f"user_id={user_id} chat_id={chat_id}")
    print("Type a message exactly like Telegram. Type /exit or Ctrl+C to quit.")
    print()

    message_id = int(time.time())
    while True:
        try:
            text = input("[you] ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not text:
            continue
        if text.lower() in {"/exit", "exit", "quit", "/quit"}:
            return 0

        message_id += 1
        result = update_router.handle_update(
            _build_update(message_id, chat_id, user_id, text),
            background_tasks=None,
        )
        if result.status not in {"processed", "accepted"}:
            print(f"[system] {result.status}: {result.detail}")


if __name__ == "__main__":
    raise SystemExit(main())
