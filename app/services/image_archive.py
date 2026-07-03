from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from app.integrations.telegram import TelegramClient
from app.models.db import Database
from app.models.schemas import TelegramPhotoSize


@dataclass(slots=True)
class SavedImageResult:
    image_id: str
    local_path: str


class ImageArchive:
    def __init__(
        self,
        image_root: str,
        telegram_client: TelegramClient,
        db: Database,
    ) -> None:
        self.image_root = Path(image_root)
        self.telegram_client = telegram_client
        self.db = db

    def save_telegram_photo(
        self,
        *,
        message_id: str,
        chat_id: int | str,
        telegram_message_id: int,
        photo: TelegramPhotoSize,
    ) -> SavedImageResult:
        archive_dir = self.image_root / "archive" / str(chat_id)
        archive_dir.mkdir(parents=True, exist_ok=True)

        remote_file_path = self.telegram_client.get_file_path(photo.file_id)
        suffix = Path(remote_file_path).suffix or ".jpg"
        local_file_name = f"{telegram_message_id}_{photo.file_unique_id}{suffix}"
        local_path = archive_dir / local_file_name
        content = self.telegram_client.download_file(remote_file_path)
        local_path.write_bytes(content)

        mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        image_id = self.db.insert_image_file(
            message_id=message_id,
            telegram_file_id=photo.file_id,
            telegram_file_unique_id=photo.file_unique_id,
            local_path=str(local_path),
            mime_type=mime_type,
            file_size=photo.file_size,
            width=photo.width,
            height=photo.height,
        )
        return SavedImageResult(image_id=image_id, local_path=str(local_path))
