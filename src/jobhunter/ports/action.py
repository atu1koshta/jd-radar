"""Action port — the v1 extensibility hinge.

Each action declares its dependencies via `__init__` so adding a new action
type (auto-apply, calendar invite, Notion export, Slack DM) is purely
additive: drop a new adapter file, register an entry point, list it in
`ENABLED_ACTIONS`. Nothing in `core/` or `application/` changes.

`ActionContext` is the bag of ports an action can pull from; it's a
`dict[str, Any]` keyed by port name rather than a fat object so each
action's constructor reveals exactly what it touches.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from jobhunter.core.entities import Job, Match


class ActionContext(BaseModel):
    """Per-execution carrier handed to every action.

    `ports` maps a port name (e.g. "notifier", "email_sender", "llm",
    "match_repo", "draft_repo") to a concrete adapter instance. Action
    constructors pull the entries they need; nothing else.
    """

    model_config = {"arbitrary_types_allowed": True}

    job: Job
    match: Match
    ports: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class ActionOutcome(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class ActionResult(BaseModel):
    name: str
    outcome: ActionOutcome
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=datetime.utcnow)


@runtime_checkable
class Action(Protocol):
    name: str

    async def is_applicable(self, ctx: ActionContext) -> bool:
        """Cheap predicate the orchestrator checks before `execute`. Lets an
        action opt out of matches it doesn't care about (e.g. ApplyAction
        skipping cold-outreach decisions)."""
        ...

    async def execute(self, ctx: ActionContext) -> ActionResult: ...
