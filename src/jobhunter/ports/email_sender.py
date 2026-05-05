"""EmailSender port.

v1 ships only `LogOnlyEmailSender` (prints to logs, never sends). SMTP /
SendGrid / Gmail-API adapters can land later; the port shape stays.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Email(BaseModel):
    to: str
    subject: str
    body: str
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    reply_to: str | None = None


class SendResult(BaseModel):
    sent: bool
    backend: str
    message_id: str | None = None
    error: str | None = None


@runtime_checkable
class EmailSender(Protocol):
    name: str

    async def send(self, email: Email) -> SendResult: ...
