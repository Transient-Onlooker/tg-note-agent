from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.integrations.notion import build_notion_client_from_env
from app.models.db import Database
from app.models.schemas import (
    TelegramChat,
    TelegramMessage,
    TelegramPhotoSize,
    TelegramUpdate,
    TelegramUser,
)
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


@dataclass(slots=True)
class PdfExtractionResult:
    text: str
    total_pages: int
    processed_pages: int
    ocr_pages: list[int]
    failed_pages: list[int]
    page_limit_reached: bool


class TerminalTelegramClient:
    def __init__(self) -> None:
        self._local_files: dict[str, Path] = {}

    def send_message(self, chat_id: int | str, text: str) -> bool:
        print()
        print("[bot]")
        print(text)
        print()
        return True

    def set_my_commands(self, *args, **kwargs) -> bool:
        return True

    def register_local_file(self, path: Path) -> tuple[str, str]:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"File not found: {resolved}")

        unique_id = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
        file_id = f"local-{unique_id}"
        self._local_files[file_id] = resolved
        return file_id, unique_id

    def get_file_path(self, file_id: str) -> str:
        path = self._local_files.get(file_id)
        if path is None:
            raise RuntimeError(f"Unknown local test file id: {file_id}")
        return str(path)

    def download_file(self, file_path: str) -> bytes:
        path = Path(file_path).resolve()
        if path not in self._local_files.values():
            raise RuntimeError(f"Unknown local test file path: {path}")
        return path.read_bytes()


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


def _parse_attachment_command(text: str) -> tuple[str, Path, str | None] | None:
    command, separator, argument = text.strip().partition(" ")
    normalized_command = command.lower()
    if normalized_command not in {"/image", "/pdf"}:
        return None
    if not separator or not argument.strip():
        raise ValueError(
            f"Usage: {normalized_command} <file path> | optional caption"
        )

    raw_path, has_caption, raw_caption = argument.partition("|")
    cleaned_path = raw_path.strip().strip('"')
    if not cleaned_path:
        raise ValueError(
            f"Usage: {normalized_command} <file path> | optional caption"
        )

    caption = raw_caption.strip() if has_caption and raw_caption.strip() else None
    return normalized_command, Path(cleaned_path).expanduser(), caption


def _build_photo_update(
    *,
    message_id: int,
    chat_id: int,
    user_id: int,
    path: Path,
    caption: str | None,
    telegram_client: TerminalTelegramClient,
) -> TelegramUpdate:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Image file not found: {resolved}")

    file_id, unique_id = telegram_client.register_local_file(resolved)
    return TelegramUpdate(
        update_id=message_id,
        message=TelegramMessage(
            message_id=message_id,
            date=int(time.time()),
            chat=TelegramChat(id=chat_id),
            from_user=TelegramUser(id=user_id),
            caption=caption,
            photo=[
                TelegramPhotoSize(
                    file_id=file_id,
                    file_unique_id=unique_id,
                    width=0,
                    height=0,
                    file_size=resolved.stat().st_size,
                )
            ],
        ),
    )


def _resolve_pdf_object(value):
    if value is None:
        return None
    get_object = getattr(value, "get_object", None)
    return get_object() if callable(get_object) else value


def _raw_pdf_image_candidates(owner, *, depth: int = 0) -> list[tuple[str, bytes]]:
    if depth > 4:
        return []

    resources = _resolve_pdf_object(owner.get("/Resources"))
    if not resources:
        return []
    xobjects = _resolve_pdf_object(resources.get("/XObject"))
    if not xobjects:
        return []

    candidates: list[tuple[str, bytes]] = []
    for reference in xobjects.values():
        xobject = _resolve_pdf_object(reference)
        if not xobject:
            continue

        subtype = str(xobject.get("/Subtype") or "")
        if subtype == "/Form":
            candidates.extend(
                _raw_pdf_image_candidates(xobject, depth=depth + 1)
            )
            continue
        if subtype != "/Image":
            continue

        filter_value = _resolve_pdf_object(xobject.get("/Filter"))
        filters = (
            {str(item) for item in filter_value}
            if isinstance(filter_value, (list, tuple))
            else {str(filter_value)}
        )
        if "/DCTDecode" not in filters:
            continue

        try:
            data = xobject.get_data()
        except Exception:
            continue
        if data:
            candidates.append((".jpg", data))
    return candidates


def _extract_pdf_pages(
    path: Path,
    *,
    nim_provider: NvidiaNIMProvider,
    caption: str | None,
    max_pages: int,
    min_embedded_chars: int,
) -> PdfExtractionResult:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"PDF file not found: {resolved}")
    if resolved.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {resolved.name}")
    if max_pages < 1:
        raise ValueError("TESTTERMINAL_PDF_MAX_PAGES must be at least 1")

    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PDF dependencies are missing. Run: "
            ".venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(str(resolved))
        total_pages = len(reader.pages)
    except Exception as exc:
        raise RuntimeError(f"Could not open PDF: {exc}") from exc

    processed_pages = min(total_pages, max_pages)
    page_sections: list[str] = []
    ocr_pages: list[int] = []
    failed_pages: list[int] = []

    with tempfile.TemporaryDirectory(prefix="tg-note-agent-pdf-") as temp_dir:
        temp_root = Path(temp_dir)
        for page_index in range(processed_pages):
            page_number = page_index + 1
            page = reader.pages[page_index]
            try:
                embedded_text = (page.extract_text() or "").strip()
            except Exception:
                embedded_text = ""

            compact_text = "".join(embedded_text.split())
            if len(compact_text) >= min_embedded_chars:
                page_sections.append(
                    f"[Page {page_number} | embedded text]\n{embedded_text}"
                )
                continue

            images = _raw_pdf_image_candidates(page)
            if not images:
                failed_pages.append(page_number)
                continue

            suffix, image_data = max(images, key=lambda item: len(item[1]))
            image_path = temp_root / f"page-{page_number}{suffix}"
            image_path.write_bytes(image_data)
            context = (
                f"PDF {resolved.name}, page {page_number}. "
                "Extract all readable text."
            )
            if caption:
                context = f"{caption}\n{context}"

            try:
                analysis = nim_provider.analyze_image(
                    str(image_path),
                    caption=context,
                )
            except Exception:
                failed_pages.append(page_number)
                continue
            ocr_text = (analysis.ocr_text or "").strip()
            if not ocr_text:
                failed_pages.append(page_number)
                continue

            ocr_pages.append(page_number)
            page_sections.append(
                f"[Page {page_number} | vision OCR]\n{ocr_text}"
            )

    return PdfExtractionResult(
        text="\n\n".join(page_sections),
        total_pages=total_pages,
        processed_pages=processed_pages,
        ocr_pages=ocr_pages,
        failed_pages=failed_pages,
        page_limit_reached=processed_pages < total_pages,
    )


def _build_pdf_create_text(
    *,
    path: Path,
    extraction: PdfExtractionResult,
    caption: str | None,
    max_chars: int,
) -> tuple[str, bool]:
    if max_chars < 1:
        raise ValueError("TESTTERMINAL_PDF_MAX_CHARS must be at least 1")

    document_text = extraction.text.strip()
    was_truncated = len(document_text) > max_chars
    if was_truncated:
        document_text = document_text[:max_chars].rstrip()

    header = (
        f"[PDF: {path.name} | processed "
        f"{extraction.processed_pages}/{extraction.total_pages} pages]"
    )
    source_text = "\n\n".join(
        part for part in (caption, header, document_text) if part
    )
    return f"/new {source_text}", was_truncated


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
    print('Image: /image "C:\\path\\photo.jpg" | optional caption')
    print('PDF:   /pdf "C:\\path\\document.pdf" | optional caption')
    print("PDF pages use embedded text first and vision OCR when text is missing.")
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
        try:
            attachment = _parse_attachment_command(text)
        except ValueError as exc:
            print(f"[system] {exc}")
            continue

        if attachment is not None:
            command, path, caption = attachment
            try:
                if command == "/image":
                    update = _build_photo_update(
                        message_id=message_id,
                        chat_id=chat_id,
                        user_id=user_id,
                        path=path,
                        caption=caption,
                        telegram_client=telegram_client,
                    )
                else:
                    extraction = _extract_pdf_pages(
                        path,
                        nim_provider=nim_provider,
                        caption=caption,
                        max_pages=int(
                            os.getenv("TESTTERMINAL_PDF_MAX_PAGES", "12")
                        ),
                        min_embedded_chars=int(
                            os.getenv("TESTTERMINAL_PDF_MIN_TEXT_CHARS", "20")
                        ),
                    )
                    if not extraction.text:
                        print(
                            "[system] No text could be extracted from the PDF. "
                            f"OCR failed pages: {extraction.failed_pages or 'all'}"
                        )
                        continue

                    pdf_text, was_truncated = _build_pdf_create_text(
                        path=path,
                        extraction=extraction,
                        caption=caption,
                        max_chars=int(
                            os.getenv("TESTTERMINAL_PDF_MAX_CHARS", "30000")
                        ),
                    )
                    print(
                        "[system] PDF test mode: "
                        f"{extraction.processed_pages}/{extraction.total_pages} pages, "
                        f"vision OCR pages={extraction.ocr_pages or 'none'}"
                    )
                    if extraction.failed_pages:
                        print(f"[system] OCR failed pages: {extraction.failed_pages}")
                    if extraction.page_limit_reached:
                        print(
                            "[system] Page limit reached. Change "
                            "TESTTERMINAL_PDF_MAX_PAGES to process more pages."
                        )
                    if was_truncated:
                        print(
                            "[system] Text limit reached. Change "
                            "TESTTERMINAL_PDF_MAX_CHARS to include more text."
                        )
                    update = _build_update(message_id, chat_id, user_id, pdf_text)

                result = update_router.handle_update(
                    update,
                    background_tasks=None,
                )
            except (RuntimeError, ValueError, OSError) as exc:
                print(f"[system] {exc}")
                continue

            if result.status not in {"processed", "accepted"}:
                print(f"[system] {result.status}: {result.detail}")
            continue

        result = update_router.handle_update(
            _build_update(message_id, chat_id, user_id, text),
            background_tasks=None,
        )
        if result.status not in {"processed", "accepted"}:
            print(f"[system] {result.status}: {result.detail}")


if __name__ == "__main__":
    raise SystemExit(main())
