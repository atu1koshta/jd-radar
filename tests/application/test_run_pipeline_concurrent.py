"""run_pipeline: producer + N-consumer behaviour against fully-mocked deps.

Verifies:
- All search-produced jobs reach the consumers (no jobs lost).
- N workers run concurrently (in-flight count > 1 at some moment).
- Counters reflect every job's decision bucket.
- Per-job exceptions don't poison sibling workers.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, TypeVar

import pytest
from pydantic import BaseModel, HttpUrl

from jobhunter.application.run_pipeline import run_pipeline
from jobhunter.core.entities import (
    Decision,
    InterpretedResume,
    Job,
    JobQuery,
    Resume,
    RunStatus,
    canonical_body_hash,
)
from jobhunter.ports.action import ActionContext, ActionOutcome, ActionResult
from jobhunter.ports.llm import LLMCapabilities, Prompt

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _StubLLM:
    """Returns a fixed rubric per call. Used by score_match."""

    def __init__(self) -> None:
        self.capabilities = LLMCapabilities(
            name="stub", supports_native_tools=True, supports_json_schema=True
        )
        self.calls = 0

    async def complete(self, prompt: Prompt) -> Any:
        raise AssertionError("unused")

    async def structured(self, prompt: Prompt, schema: type[T]) -> T:
        # Mock the score_match rubric. Skip via low scores.
        from jobhunter.application.score_match import ScoringRubric

        self.calls += 1
        if schema is ScoringRubric:
            return ScoringRubric(  # type: ignore[return-value]
                skill_overlap=0.95,
                title_match=0.95,
                years_required=3,
                reasoning="strong fit",
            )
        raise AssertionError(f"unexpected schema {schema}")

    async def tool_call(self, prompt: Prompt, tools: list) -> Any:
        raise AssertionError("unused")

    async def stream(self, prompt: Prompt):  # pragma: no cover
        if False:
            yield ""


class _StubLoader:
    def __init__(self, resume: Resume) -> None:
        self._resume = resume

    async def load(self, *, force_refresh: bool = False) -> Resume:
        return self._resume


class _Portal:
    """Fake portal that yields N jobs and records concurrency on fetch_jd."""

    def __init__(self, *, n_jobs: int, fetch_delay_s: float = 0.05) -> None:
        self._n = n_jobs
        self._fetch_delay = fetch_delay_s
        self._in_flight = 0
        self.peak_in_flight = 0
        self.fetched_ids: list[str] = []
        self.closed = False

    async def search(self, query: JobQuery, *, limit: int) -> AsyncIterator[Job]:
        for i in range(min(self._n, limit)):
            yield Job(
                id=f"naukri:{i}",
                portal="naukri",
                external_id=str(i),
                url=HttpUrl(f"https://www.naukri.com/job-{i}"),
                title=f"Engineer {i}",
                company=f"Co {i}",
            )

    async def fetch_jd(self, job: Job) -> Job:
        self._in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            await asyncio.sleep(self._fetch_delay)
            self.fetched_ids.append(job.id)
            return job.model_copy(
                update={
                    "jd_raw": f"JD body for {job.id}",
                    "jd_content_hash": "h" * 12,
                }
            )
        finally:
            self._in_flight -= 1

    async def close(self) -> None:
        self.closed = True


class _AlwaysAlertAction:
    name = "alert"

    async def is_applicable(self, ctx: ActionContext) -> bool:
        return True

    async def execute(self, ctx: ActionContext) -> ActionResult:
        return ActionResult(name=self.name, outcome=ActionOutcome.SUCCESS)


class _BoomAction:
    # Use "alert" so decide_actions selects it under DRAFT/ALERT — the
    # hardcoded mapping in decide_actions filters by v1 action names.
    name = "alert"

    async def is_applicable(self, ctx: ActionContext) -> bool:
        return True

    async def execute(self, ctx: ActionContext) -> ActionResult:
        raise RuntimeError("kaboom")


# ---------------------------------------------------------------------------
# Test container
# ---------------------------------------------------------------------------


def _resume() -> Resume:
    body = {"name": "Test"}
    return Resume(
        body=body,
        body_hash=canonical_body_hash(body),
        interpreted=InterpretedResume(
            summary="Backend dev with 8 years.",
            total_experience_years=8.0,
            seniority_level="senior",
            body_hash=canonical_body_hash(body),
        ),
    )


class _Container:
    """Just enough of `Container` to satisfy run_pipeline."""

    def __init__(
        self,
        *,
        portal: _Portal,
        actions: list[Any],
        workers: int,
    ) -> None:
        self.portal_obj = portal
        self._actions = actions

        from jobhunter.bootstrap.config import Settings

        self.settings = Settings(
            pipeline_workers=workers,
            pipeline_queue_size=4,
            risk_tolerance=0.3,
            enabled_actions=["alert"],
        )
        self.llm = _StubLLM()
        self.resume_loader = _StubLoader(_resume())
        self.notifier = None
        self.email_sender = None
        self.match_repo = None
        self.draft_repo = None
        self.action_record_repo = None
        self.run_repo = None

    def build_portal(self, name: str):
        return self.portal_obj

    def build_action(self, name: str):
        return next(a for a in self._actions if a.name == name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_jobs_drained_and_status_done_with_n_workers() -> None:
    portal = _Portal(n_jobs=5, fetch_delay_s=0.02)
    container = _Container(portal=portal, actions=[_AlwaysAlertAction()], workers=2)

    report = await run_pipeline(
        container=container,  # type: ignore[arg-type]
        portal_name="naukri",
        query=JobQuery(keywords="x"),
        limit=5,
    )

    assert report.status is RunStatus.DONE
    assert report.counters.jobs_seen == 5
    assert report.counters.jobs_scored == 5
    assert report.counters.matches_drafted == 5  # rubric 0.95/0.95 → DRAFT
    assert report.counters.actions_succeeded >= 5
    assert portal.closed is True
    assert sorted(portal.fetched_ids) == [f"naukri:{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_concurrent_workers_actually_overlap() -> None:
    portal = _Portal(n_jobs=4, fetch_delay_s=0.1)
    container = _Container(portal=portal, actions=[_AlwaysAlertAction()], workers=3)

    await run_pipeline(
        container=container,  # type: ignore[arg-type]
        portal_name="naukri",
        query=JobQuery(keywords="x"),
        limit=4,
    )
    # With 3 workers and a 100ms fetch_jd, peak in-flight must exceed 1.
    assert portal.peak_in_flight >= 2


@pytest.mark.asyncio
async def test_single_worker_runs_jobs_serially() -> None:
    portal = _Portal(n_jobs=3, fetch_delay_s=0.02)
    container = _Container(portal=portal, actions=[_AlwaysAlertAction()], workers=1)

    await run_pipeline(
        container=container,  # type: ignore[arg-type]
        portal_name="naukri",
        query=JobQuery(keywords="x"),
        limit=3,
    )
    assert portal.peak_in_flight == 1


@pytest.mark.asyncio
async def test_one_failing_action_does_not_kill_other_jobs() -> None:
    portal = _Portal(n_jobs=4, fetch_delay_s=0.01)
    boom = _BoomAction()
    container = _Container(portal=portal, actions=[boom], workers=2)
    # `enabled_actions=["alert"]` already matches the BoomAction's name,
    # so decide_actions will pick it up.

    report = await run_pipeline(
        container=container,  # type: ignore[arg-type]
        portal_name="naukri",
        query=JobQuery(keywords="x"),
        limit=4,
    )

    # Every job still produces a Match; only the action records the failure.
    assert report.status is RunStatus.DONE
    assert report.counters.jobs_scored == 4
    assert report.counters.actions_failed == 4
    assert report.counters.actions_succeeded == 0
