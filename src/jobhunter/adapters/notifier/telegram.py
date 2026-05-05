"""Telegram NotificationChannel adapter.

Uses the bot HTTP API directly (httpx). Avoids the heavier
`python-telegram-bot` dependency since we only need send + a health probe.
Markdown V2 formatting requires escaping certain characters; we keep
messages plain to dodge that pitfall.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from loguru import logger

from jobhunter.ports.notifier import Notification, NotificationResult

if TYPE_CHECKING:
    from jobhunter.bootstrap.config import Settings

_BASE = "https://api.telegram.org"


class TelegramNotifier:
    name = "telegram"

    def __init__(self, *, token: str, chat_id: str, request_timeout_s: float = 15.0) -> None:
        if not token or not chat_id:
            raise ValueError(
                "TelegramNotifier requires both `token` and `chat_id`. "
                "Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env."
            )
        self._token = token
        self._chat_id = chat_id
        self._timeout = request_timeout_s

    @classmethod
    def from_settings(cls, settings: "Settings") -> "TelegramNotifier":
        if not settings.telegram_token or not settings.telegram_chat_id:
            raise ValueError(
                "TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in .env"
            )
        return cls(
            token=settings.telegram_token,
            chat_id=settings.telegram_chat_id,
        )

    async def send(self, msg: Notification) -> NotificationResult:
        text = self._render(msg)
        url = f"{_BASE}/bot{self._token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(
                    url,
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
        except httpx.HTTPError as e:
            logger.warning("telegram send failed: {}", e)
            return NotificationResult(
                delivered=False, channel=self.name, error=f"{type(e).__name__}: {e}"
            )

        if resp.status_code != 200:
            return NotificationResult(
                delivered=False,
                channel=self.name,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        body = resp.json()
        if not body.get("ok"):
            return NotificationResult(
                delivered=False,
                channel=self.name,
                error=body.get("description", "telegram returned ok=false"),
            )
        message_id = str(body.get("result", {}).get("message_id", ""))
        return NotificationResult(
            delivered=True, channel=self.name, message_id=message_id or None
        )

    async def health_check(self) -> bool:
        url = f"{_BASE}/bot{self._token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.get(url)
        except httpx.HTTPError as e:
            logger.warning("telegram health_check transport error: {}", e)
            return False
        return resp.status_code == 200 and bool(resp.json().get("ok"))

    @staticmethod
    def _render(msg: Notification) -> str:
        parts: list[str] = []
        if msg.title:
            parts.append(msg.title)
            parts.append("")
        parts.append(msg.body)
        if msg.metadata:
            parts.append("")
            for k, v in msg.metadata.items():
                parts.append(f"{k}: {v}")
        return "\n".join(parts)
