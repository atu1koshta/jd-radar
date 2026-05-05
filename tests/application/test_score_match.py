"""score_match orchestrates LLM rubric + pure math.

LLM is fully mocked; tests prove the use case correctly stitches the rubric
into a Match without ever inventing a final number itself.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from jobhunter.application.score_match import ScoringRubric, score_match
from jobhunter.core.entities import (
    Decision,
    InterpretedResume,
    InterpretedSkill,
    Resume,
    canonical_body_hash,
)
from jobhunter.core.scoring.confidence import (
    W_EXPERIENCE,
    W_SKILL,
    W_TITLE,
)
from jobhunter.ports.llm import LLMCapabilities, Prompt

T = TypeVar("T", bound=BaseModel)


class StubLLM:
    """Deterministic LLM that returns whatever rubric we hand it."""

    def __init__(self, rubric: ScoringRubric) -> None:
        self._rubric = rubric
        self.last_prompt: Prompt | None = None
        self.capabilities = LLMCapabilities(
            name="stub", supports_native_tools=True, supports_json_schema=True
        )

    async def complete(self, prompt: Prompt) -> Any:
        raise AssertionError("score_match must use structured(), not complete()")

    async def structured(self, prompt: Prompt, schema: type[T]) -> T:
        assert schema is ScoringRubric
        self.last_prompt = prompt
        return self._rubric  # type: ignore[return-value]

    async def tool_call(self, prompt: Prompt, tools: list) -> Any:
        raise AssertionError("score_match must not call tool_call()")

    async def stream(self, prompt: Prompt):  # pragma: no cover
        if False:
            yield ""


def _interpreted(years: float = 8.0) -> InterpretedResume:
    return InterpretedResume(
        canonical_name="Atul",
        headline="Software architect",
        summary="Backend engineer with 8y building Python microservices on AWS.",
        total_experience_years=years,
        seniority_level="senior",
        skills=[
            InterpretedSkill(name="Python", category="language", years=8),
            InterpretedSkill(name="FastAPI", category="framework"),
            InterpretedSkill(name="PostgreSQL", category="database"),
        ],
        experiences=[],
        domains=["fintech"],
        role_categories=["backend"],
        search_query_terms=["python backend", "fastapi postgres"],
        body_hash="abc",
    )


def _resume(years: float = 8.0) -> Resume:
    body = {"name": "Atul", "skills": ["Python", "FastAPI"]}
    return Resume(
        body=body,
        body_hash=canonical_body_hash(body),
        interpreted=_interpreted(years=years),
    )


async def test_high_confidence_yields_alert_decision() -> None:
    llm = StubLLM(
        ScoringRubric(
            skill_overlap=0.9, title_match=0.9, years_required=5, reasoning="strong fit"
        )
    )
    match, rubric = await score_match(
        resume=_resume(years=8),
        jd_text="Senior Python backend, 5+ yrs",
        llm=llm,
        risk_tolerance=0.3,
        job_id="job:1",
    )

    expected_conf = W_SKILL * 0.9 + W_TITLE * 0.9 + W_EXPERIENCE * 1.0
    assert abs(match.confidence - expected_conf) < 1e-9
    assert abs(match.risk - (1.0 - expected_conf)) < 1e-9
    assert match.decision is Decision.ALERT
    assert match.job_id == "job:1"
    assert rubric is llm._rubric  # type: ignore[attr-defined]
    assert match.breakdown["reasoning"] == "strong fit"


async def test_partial_skill_match_lands_on_alert() -> None:
    llm = StubLLM(
        ScoringRubric(
            skill_overlap=0.5, title_match=0.5, years_required=5, reasoning="ok"
        )
    )
    match, _ = await score_match(
        resume=_resume(years=8),
        jd_text="...",
        llm=llm,
        risk_tolerance=0.3,
        job_id="job:2",
    )
    assert match.decision is Decision.ALERT


async def test_low_skill_overlap_skips() -> None:
    llm = StubLLM(
        ScoringRubric(
            skill_overlap=0.1, title_match=0.1, years_required=99, reasoning="bad fit"
        )
    )
    match, _ = await score_match(
        resume=_resume(years=8),
        jd_text="...",
        llm=llm,
        risk_tolerance=0.3,
        job_id="job:3",
    )
    assert match.decision is Decision.SKIP


async def test_unknown_years_required_uses_neutral_experience_fit() -> None:
    llm = StubLLM(
        ScoringRubric(
            skill_overlap=0.8, title_match=0.8, years_required=None, reasoning="..."
        )
    )
    match, _ = await score_match(
        resume=_resume(years=8),
        jd_text="...",
        llm=llm,
        risk_tolerance=0.3,
        job_id="job:4",
    )
    expected_conf = W_SKILL * 0.8 + W_TITLE * 0.8 + W_EXPERIENCE * 0.5
    assert abs(match.confidence - expected_conf) < 1e-9


async def test_anti_bot_signal_is_propagated_into_risk() -> None:
    llm = StubLLM(
        ScoringRubric(
            skill_overlap=0.9, title_match=0.9, years_required=5, reasoning="..."
        )
    )
    match, _ = await score_match(
        resume=_resume(years=8),
        jd_text="...",
        llm=llm,
        risk_tolerance=0.3,
        job_id="job:5",
        portal_anti_bot_score=0.5,
    )
    assert match.risk > 0.5


async def test_prompt_uses_interpreted_skills_not_raw_yaml() -> None:
    llm = StubLLM(
        ScoringRubric(
            skill_overlap=0.5, title_match=0.5, years_required=3, reasoning="..."
        )
    )
    await score_match(
        resume=_resume(years=8),
        jd_text="JD: Python backend with PostgreSQL",
        llm=llm,
        risk_tolerance=0.3,
        job_id="job:6",
    )
    assert llm.last_prompt is not None
    user = llm.last_prompt.user or ""
    # Skills come from the InterpretedResume, not the raw body.
    assert "Python" in user
    assert "FastAPI" in user
    assert "PostgreSQL" in user
    # Seniority and search categories surface in the prompt summary.
    assert "senior" in user.lower()
    assert "PostgreSQL" in user


async def test_missing_interpretation_raises_resume_error() -> None:
    """Loader contract: `interpreted` must be populated before scoring runs.

    Hitting `score_match` with `interpreted=None` is a programmer error
    (skipped the loader, mocked it badly, ...) and must surface loudly
    rather than silently scoring against unprocessed YAML.
    """
    import pytest

    from jobhunter.core.errors import ResumeError

    llm = StubLLM(
        ScoringRubric(
            skill_overlap=0.4, title_match=0.5, years_required=3, reasoning="..."
        )
    )
    body = {"name": "Atul", "skills": ["Rust", "Tokio"]}
    resume = Resume(body=body, body_hash=canonical_body_hash(body), interpreted=None)

    with pytest.raises(ResumeError):
        await score_match(
            resume=resume,
            jd_text="...",
            llm=llm,
            risk_tolerance=0.3,
            job_id="job:7",
        )
