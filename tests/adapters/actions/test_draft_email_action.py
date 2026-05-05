"""DraftEmailAction: LLM-fills payload, persists EmailDraft pending review."""

from __future__ import annotations

from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, HttpUrl

from jobhunter.adapters.actions.draft_email import DraftEmailAction, _DraftPayload
from jobhunter.core.entities import (
    Decision,
    EmailDraft,
    EmailDraftStatus,
    InterpretedResume,
    Job,
    Match,
    Resume,
    canonical_body_hash,
)
from jobhunter.ports.action import ActionContext, ActionOutcome
from jobhunter.ports.llm import LLMCapabilities, Prompt

T = TypeVar("T", bound=BaseModel)


class _StubLLM:
    def __init__(self, payload: _DraftPayload) -> None:
        self._payload = payload
        self.last_prompt: Prompt | None = None
        self.capabilities = LLMCapabilities(
            name="stub", supports_native_tools=True, supports_json_schema=True
        )

    async def complete(self, prompt: Prompt) -> Any:
        raise AssertionError("must use structured()")

    async def structured(self, prompt: Prompt, schema: type[T]) -> T:
        assert schema is _DraftPayload
        self.last_prompt = prompt
        return self._payload  # type: ignore[return-value]

    async def tool_call(self, prompt: Prompt, tools: list) -> Any:  # noqa: ARG002
        raise AssertionError("unused")

    async def stream(self, prompt: Prompt):  # pragma: no cover
        if False:
            yield ""


class _MemRepo:
    def __init__(self) -> None:
        self.rows: list[EmailDraft] = []

    async def get(self, id: str) -> EmailDraft | None:
        return next((r for r in self.rows if r.id == id), None)

    async def list(self, **f: Any) -> list[EmailDraft]:
        return list(self.rows)

    async def save(self, e: EmailDraft) -> EmailDraft:
        self.rows = [r for r in self.rows if r.id != e.id]
        self.rows.append(e)
        return e

    async def delete(self, id: str) -> None:
        self.rows = [r for r in self.rows if r.id != id]


def _resume() -> Resume:
    body = {"name": "Atul"}
    return Resume(
        body=body,
        body_hash=canonical_body_hash(body),
        interpreted=InterpretedResume(
            summary="Backend engineer with 8y FastAPI/Postgres/AWS.",
            total_experience_years=8.0,
            seniority_level="senior",
            skills=[],
            body_hash=canonical_body_hash(body),
        ),
    )


def _ctx(*, decision: Decision, ports: dict[str, Any]) -> ActionContext:
    job = Job(
        id="naukri:1",
        portal="naukri",
        external_id="1",
        url=HttpUrl("https://www.naukri.com/x"),
        title="Senior Backend Engineer",
        company="Acme",
        jd_raw="Hiring senior python backend engineer with FastAPI + PostgreSQL on AWS.",
    )
    match = Match(
        id="m1",
        job_id=job.id,
        confidence=0.9,
        risk=0.1,
        decision=decision,
    )
    return ActionContext(job=job, match=match, ports=ports)


@pytest.mark.asyncio
async def test_only_applicable_for_draft_decisions() -> None:
    a = DraftEmailAction()
    assert await a.is_applicable(_ctx(decision=Decision.DRAFT, ports={})) is True
    assert await a.is_applicable(_ctx(decision=Decision.ALERT, ports={})) is False
    assert await a.is_applicable(_ctx(decision=Decision.SKIP, ports={})) is False


@pytest.mark.asyncio
async def test_execute_persists_draft_and_never_marks_it_sent() -> None:
    llm = _StubLLM(
        _DraftPayload(
            subject="Quick intro re: Backend role at Acme",
            body="Hi team,\n\nI noticed your stack on FastAPI + Postgres ...",
            suggested_to=None,
        )
    )
    repo = _MemRepo()
    ctx = _ctx(
        decision=Decision.DRAFT,
        ports={"llm": llm, "draft_repo": repo, "resume": _resume()},
    )

    result = await DraftEmailAction().execute(ctx)

    assert result.outcome is ActionOutcome.SUCCESS
    assert len(repo.rows) == 1
    saved = repo.rows[0]
    assert saved.status is EmailDraftStatus.PENDING_REVIEW
    assert saved.job_id == ctx.job.id
    assert "Acme" in saved.subject
    assert saved.sent_at is None


@pytest.mark.asyncio
async def test_missing_llm_port_yields_failed_result() -> None:
    result = await DraftEmailAction().execute(
        _ctx(decision=Decision.DRAFT, ports={"draft_repo": _MemRepo()})
    )
    assert result.outcome is ActionOutcome.FAILED
    assert "llm" in (result.message or "")


@pytest.mark.asyncio
async def test_missing_draft_repo_yields_failed_result() -> None:
    llm = _StubLLM(
        _DraftPayload(subject="s", body="b", suggested_to=None)
    )
    result = await DraftEmailAction().execute(
        _ctx(decision=Decision.DRAFT, ports={"llm": llm})
    )
    assert result.outcome is ActionOutcome.FAILED
    assert "draft_repo" in (result.message or "")


@pytest.mark.asyncio
async def test_prompt_carries_jd_and_resume_summary() -> None:
    llm = _StubLLM(_DraftPayload(subject="s", body="b", suggested_to=None))
    ctx = _ctx(
        decision=Decision.DRAFT,
        ports={"llm": llm, "draft_repo": _MemRepo(), "resume": _resume()},
    )
    await DraftEmailAction().execute(ctx)

    user = (llm.last_prompt.user or "")  # type: ignore[union-attr]
    assert "Acme" in user
    assert "FastAPI" in user
    assert "Backend engineer with 8y" in user
