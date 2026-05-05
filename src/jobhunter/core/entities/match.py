"""Match — the result of scoring one Job against the active Resume."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Decision(StrEnum):
    SKIP = "SKIP"
    ALERT = "ALERT"


class Match(BaseModel):
    id: str
    job_id: str
    resume_id: str = "resume:current"

    confidence: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    decision: Decision

    breakdown: dict[str, Any] = Field(
        default_factory=dict,
        description="skill_overlap / title_match / experience_fit components",
    )

    scored_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("confidence", "risk", mode="before")
    @classmethod
    def _clip01(cls, v: float) -> float:
        # Run before Field(ge=0, le=1) so out-of-range scores from a noisy
        # source are silently clamped instead of raising ValidationError.
        if isinstance(v, (int, float)):
            return max(0.0, min(1.0, float(v)))
        return v
