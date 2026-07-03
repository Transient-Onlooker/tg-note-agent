from __future__ import annotations

import httpx


class TelegramClient:
    def __init__(self, bot_token: str, timeout: float = 10.0) -> None:
        self.bot_token = bot_token
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, chat_id: int | str, text: str) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": str(chat_id), "text": text},
            )
            response.raise_for_status()
