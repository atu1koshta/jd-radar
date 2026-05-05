"""Telegram notifier: rendering + HTTP behaviour against a mock transport."""

from __future__ import annotations

import httpx
import pytest

from jobhunter.adapters.notifier.telegram import TelegramNotifier
from jobhunter.ports.notifier import Notification


def _notifier(transport: httpx.MockTransport) -> TelegramNotifier:
    notif = TelegramNotifier(token="t-tok", chat_id="42")
    # The adapter builds a fresh httpx.AsyncClient per call. Patch the
    # public send-method to use our transport for the duration of a test.
    return notif


def test_constructor_rejects_missing_creds() -> None:
    with pytest.raises(ValueError):
        TelegramNotifier(token="", chat_id="42")
    with pytest.raises(ValueError):
        TelegramNotifier(token="abc", chat_id="")


@pytest.mark.asyncio
async def test_send_posts_chat_id_and_text(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = req.content.decode("utf-8")
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 7}},
        )

    transport = httpx.MockTransport(handler)

    # Replace httpx.AsyncClient so the adapter uses our transport.
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(transport=transport)

    monkeypatch.setattr(
        "jobhunter.adapters.notifier.telegram.httpx.AsyncClient", _factory
    )

    notifier = TelegramNotifier(token="t-tok", chat_id="42")
    result = await notifier.send(Notification(title="hi", body="world"))

    assert result.delivered is True
    assert result.channel == "telegram"
    assert result.message_id == "7"
    assert "/bott-tok/sendMessage" in str(captured["url"])
    assert "hi" in captured["body"]
    assert "world" in captured["body"]
    assert "42" in captured["body"]


@pytest.mark.asyncio
async def test_send_returns_failure_on_non_200(monkeypatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(transport=transport)

    monkeypatch.setattr(
        "jobhunter.adapters.notifier.telegram.httpx.AsyncClient", _factory
    )

    notifier = TelegramNotifier(token="x", chat_id="1")
    result = await notifier.send(Notification(body="hello"))
    assert result.delivered is False
    assert "401" in (result.error or "")
