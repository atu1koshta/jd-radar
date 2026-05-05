"""ActionRecord — durable trace of one Action.execute() call."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ActionStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class ActionRecord(BaseModel):
    id: str
    match_id: str
    action_name: str
    status: ActionStatus = ActionStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    executed_at: datetime = Field(default_factory=datetime.utcnow)
