"""NotificationChannel port.

Vendor-neutral push contract. Adapters: Telegram (v1), Slack / Discord /
webhook later. Channels carry their own creds; the port stays small.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Notification(BaseModel):
    """Vendor-neutral message envelope.

    `kind` lets channels route different categories ("alert", "halt",
    "draft_ready") to different formats / topics later.
    """

    kind: str = "alert"
    title: str | None = None
    body: str
    metadata: dict[str, str] = Field(default_factory=dict)


class NotificationResult(BaseModel):
    delivered: bool
    channel: str
    message_id: str | None = None
    error: str | None = None


@runtime_checkable
class NotificationChannel(Protocol):
    name: str

    async def send(self, msg: Notification) -> NotificationResult: ...

    async def health_check(self) -> bool:
        """Cheap probe — used by `cli alert-test` to verify creds before a
        real run goes out and fails silently."""
        ...
