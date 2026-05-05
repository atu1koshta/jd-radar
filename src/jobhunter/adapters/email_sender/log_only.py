"""LogOnlyEmailSender — never sends, just logs.

v1 default. The pipeline persists `EmailDraft(status=pending_review)` and
the user runs `jobhunter send-draft <id>` to "send" — which calls this
adapter, prints the body to logs, and flips the row to `sent`. Real SMTP /
SendGrid adapters can replace this later by registering a different
entry point under `jobhunter.email_senders`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from jobhunter.ports.email_sender import Email, SendResult

if TYPE_CHECKING:
    from jobhunter.bootstrap.config import Settings


class LogOnlyEmailSender:
    name = "log_only"

    def __init__(self) -> None:
        self._sent_count = 0

    @classmethod
    def from_settings(cls, _settings: "Settings") -> "LogOnlyEmailSender":
        return cls()

    async def send(self, email: Email) -> SendResult:
        self._sent_count += 1
        logger.info("=" * 70)
        logger.info("EMAIL (log_only — not actually sent)")
        logger.info("To:      {}", email.to)
        if email.cc:
            logger.info("Cc:      {}", ", ".join(email.cc))
        if email.bcc:
            logger.info("Bcc:     {}", ", ".join(email.bcc))
        if email.reply_to:
            logger.info("ReplyTo: {}", email.reply_to)
        logger.info("Subject: {}", email.subject)
        logger.info("-" * 70)
        for line in email.body.splitlines():
            logger.info("  {}", line)
        logger.info("=" * 70)
        return SendResult(
            sent=True,
            backend=self.name,
            message_id=f"log_only:{self._sent_count}",
        )
