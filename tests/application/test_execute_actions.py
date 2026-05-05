"""execute_actions: iterate, persist ActionRecord, isolate failures."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import HttpUrl

from jobhunter.application.execute_actions import execute_actions
from jobhunter.core.entities import (
    ActionRecord,
    ActionStatus,
    Decision,
    Job,
    Match,
)
from jobhunter.ports.action import ActionContext, ActionOutcome, ActionResult


class _MemRepo:
    def __init__(self) -> None:
        self.rows: list[ActionRecord] = []

    async def get(self, id: str) -> ActionRecord | None:
        return next((r for r in self.rows if r.id == id), None)

    async def list(self, **f: Any) -> list[ActionRecord]:
        return list(self.rows)

    async def save(self, e: ActionRecord) -> ActionRecord:
        self.rows.append(e)
        return e

    async def delete(self, id: str) -> None:
        self.rows = [r for r in self.rows if r.id != id]


class _OkAction:
    name = "ok"

    async def is_applicable(self, ctx) -> bool:
        return True

    async def execute(self, ctx) -> ActionResult:
        return ActionResult(name=self.name, outcome=ActionOutcome.SUCCESS, payload={"k": "v"})


class _NotApplicableAction:
    name = "skipper"

    async def is_applicable(self, ctx) -> bool:
        return False

    async def execute(self, ctx) -> ActionResult:  # pragma: no cover
        raise AssertionError("must not run")


class _BoomAction:
    name = "boom"

    async def is_applicable(self, ctx) -> bool:
        return True

    async def execute(self, ctx) -> ActionResult:
        raise RuntimeError("kapow")


def _ctx() -> ActionContext:
    job = Job(
        id="naukri:1",
        portal="naukri",
        external_id="1",
        url=HttpUrl("https://www.naukri.com/x"),
        title="t",
        company="c",
    )
    match = Match(
        id="m1",
        job_id=job.id,
        confidence=0.5,
        risk=0.5,
        decision=Decision.ALERT,
    )
    return ActionContext(job=job, match=match, ports={})


@pytest.mark.asyncio
async def test_runs_actions_in_order_and_persists_each() -> None:
    repo = _MemRepo()
    results = await execute_actions(
        ctx=_ctx(), actions=[_OkAction(), _NotApplicableAction()], record_repo=repo
    )
    assert [r.outcome for r in results] == [ActionOutcome.SUCCESS, ActionOutcome.SKIPPED]
    statuses = {r.action_name: r.status for r in repo.rows}
    assert statuses == {"ok": ActionStatus.SUCCESS, "skipper": ActionStatus.SKIPPED}


@pytest.mark.asyncio
async def test_failing_action_is_isolated_and_recorded() -> None:
    repo = _MemRepo()
    results = await execute_actions(
        ctx=_ctx(), actions=[_BoomAction(), _OkAction()], record_repo=repo
    )
    assert results[0].outcome is ActionOutcome.FAILED
    assert "kapow" in (results[0].message or "")
    assert results[1].outcome is ActionOutcome.SUCCESS
    assert {r.action_name: r.status for r in repo.rows} == {
        "boom": ActionStatus.FAILED,
        "ok": ActionStatus.SUCCESS,
    }


@pytest.mark.asyncio
async def test_no_repo_means_no_persistence_but_still_returns_results() -> None:
    results = await execute_actions(
        ctx=_ctx(), actions=[_OkAction()], record_repo=None
    )
    assert len(results) == 1
    assert results[0].outcome is ActionOutcome.SUCCESS
