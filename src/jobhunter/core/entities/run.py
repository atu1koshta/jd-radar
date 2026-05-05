"""Run — one invocation of the pipeline.

Persists per-invocation observability: counters, status, timing. Used by
`cli stats` (future) and as the audit trail when a run halts on captcha
or LLM error.

Per-job state (FETCHED / SCORED / FAILED / ...) is intentionally NOT
modelled here. Adding a `JobRunState` aggregate makes sense once we
introduce a durable queue (v2). With the in-process `asyncio.Queue` v1
the process dying loses the queue contents anyway, so per-job recovery
is moot.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    HALTED = "halted"  # captcha / manual halt
    FAILED = "failed"


class RunCounters(BaseModel):
    jobs_seen: int = 0
    jobs_scored: int = 0
    jobs_failed: int = 0
    matches_drafted: int = 0
    matches_alerted: int = 0
    matches_skipped: int = 0
    actions_succeeded: int = 0
    actions_failed: int = 0


class Run(BaseModel):
    id: str
    portal: str
    query: str
    workers: int = 1
    status: RunStatus = RunStatus.RUNNING
    counters: RunCounters = Field(default_factory=RunCounters)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    error: str | None = None
