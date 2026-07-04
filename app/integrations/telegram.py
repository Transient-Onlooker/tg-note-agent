from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, bot_token: str, timeout: float = 10.0) -> None:
        self.bot_token = bot_token
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, chat_id: int | str, text: str) -> bool:
        timeout = httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": str(chat_id), "text": text},
                )
                response.raise_for_status()
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            logger.warning(
                "Failed to send Telegram message chat_id=%s error=%s",
                chat_id,
                exc,
            )
            return False
        return True

    def get_file_path(self, file_id: str) -> str:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/getFile",
                params={"file_id": file_id},
            )
            response.raise_for_status()
            data = response.json()

        return data["result"]["file_path"]

    def download_file(self, file_path: str) -> bytes:
        file_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(file_url)
            response.raise_for_status()
            return response.content
