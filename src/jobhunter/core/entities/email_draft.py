"""EmailDraft — a personalized cold-outreach email kept for human review.

Drafts are never auto-sent. They reach SMTP only after the user explicitly
runs `jobhunter send-draft <id>`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EmailDraftStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    SENT = "sent"
    DISCARDED = "discarded"


class EmailDraft(BaseModel):
    id: str
    job_id: str
    to: str
    subject: str
    body: str
    status: EmailDraftStatus = EmailDraftStatus.PENDING_REVIEW

    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: datetime | None = None
