"""LogOnlyEmailSender: never sends, always reports success."""

from __future__ import annotations

import pytest

from jobhunter.adapters.email_sender.log_only import LogOnlyEmailSender
from jobhunter.ports.email_sender import Email


@pytest.mark.asyncio
async def test_send_returns_success_with_log_only_backend() -> None:
    sender = LogOnlyEmailSender()
    result = await sender.send(
        Email(to="x@y.z", subject="hi", body="hello world")
    )
    assert result.sent is True
    assert result.backend == "log_only"
    assert result.message_id is not None
    assert result.message_id.startswith("log_only:")


@pytest.mark.asyncio
async def test_message_ids_are_unique_per_call() -> None:
    sender = LogOnlyEmailSender()
    a = await sender.send(Email(to="a", subject="s", body="b"))
    b = await sender.send(Email(to="a", subject="s", body="b"))
    assert a.message_id != b.message_id
