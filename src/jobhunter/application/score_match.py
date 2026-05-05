"""Use case: score a job description against the active resume.

Inputs come from the canonical `InterpretedResume` cached on the Resume
row. The LLM never re-reads the raw YAML during scoring — that's done once
upstream by `ResumeInterpreter`. This keeps every job's scoring prompt
small, consistent, and cheap.

Pipeline:
- LLM returns three [0, 1] sub-scores via `ScoringRubric` (strict schema).
- Pure math in `core/scoring/` combines them into confidence + risk.
- `decide()` buckets the result for the Action subsystem.
"""

from __future__ import annotations

import textwrap
import uuid

from pydantic import BaseModel, Field

from jobhunter.core.entities import InterpretedResume, Match, Resume
from jobhunter.core.errors import ResumeError
from jobhunter.core.scoring.confidence import (
    ConfidenceComponents,
    compute_confidence,
    experience_fit_from_years,
)
from jobhunter.core.scoring.risk_gate import compute_risk, decide
from jobhunter.ports.llm import LLMProvider, Prompt


class ScoringRubric(BaseModel):
    """LLM output schema. Three sub-scores + free-text reasoning."""

    skill_overlap: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of resume's strong skills that the JD also requires.",
    )
    title_match: float = Field(
        ge=0.0,
        le=1.0,
        description="How well the resume's seniority/role matches the JD title.",
    )
    years_required: float | None = Field(
        default=None,
        description="Years of experience the JD demands, or null if unspecified.",
    )
    reasoning: str = Field(description="One short paragraph defending the sub-scores.")


def _resume_summary_for_prompt(resume: Resume) -> str:
    """Render the canonical resume view for the scoring prompt.

    Always sources from `resume.interpreted`. The `ResumeLoader` contract
    guarantees this field is populated before the use case ever runs; a
    missing interpretation is an upstream bug, not a normal state to
    silently paper over.
    """
    if resume.interpreted is None:
        raise ResumeError(
            "resume.interpreted is None — loader contract violated. "
            "Run `jobhunter load-resume --refresh` and retry."
        )
    return _render_interpreted(resume.interpreted)


def _render_interpreted(r: InterpretedResume) -> str:
    skills_by_category: dict[str, list[str]] = {}
    for s in r.skills:
        bucket = s.category or "other"
        label = f"{s.name}" + (f" ({s.years:g}y)" if s.years else "")
        skills_by_category.setdefault(bucket, []).append(label)
    skills_lines = [
        f"  - {cat}: {', '.join(items)}"
        for cat, items in sorted(skills_by_category.items())
    ] or ["  - (none parsed)"]

    exp_lines = []
    for e in r.experiences:
        span = ""
        if e.start_year:
            span = f" ({e.start_year}–{e.end_year or 'present'})"
        sen = f" [{e.seniority}]" if e.seniority else ""
        exp_lines.append(
            f"  - {e.title} @ {e.company}{sen}{span}"
            + (f" — {', '.join(e.tech_stack)}" if e.tech_stack else "")
        )
    if not exp_lines:
        exp_lines = ["  - (none parsed)"]

    return textwrap.dedent(
        f"""\
        Name: {r.canonical_name or '(none)'}
        Headline: {r.headline or '(none)'}
        Seniority: {r.seniority_level}
        Total experience years: {r.total_experience_years:.1f}
        Domains: {', '.join(r.domains) or '(none)'}
        Role categories: {', '.join(r.role_categories) or '(none)'}
        Summary: {r.summary}

        Skills:
        {chr(10).join(skills_lines)}

        Experiences:
        {chr(10).join(exp_lines)}
        """
    ).strip()


def _build_prompt(resume: Resume, jd_text: str) -> Prompt:
    system = textwrap.dedent(
        """\
        You are a strict skill-matching evaluator for a job-hunting assistant.

        You will receive a candidate resume summary and a job description (JD).
        Output three numeric sub-scores in the closed interval [0, 1]:

        - skill_overlap: fraction of the candidate's strong skills the JD also
          values, weighted by importance to the JD. Synonyms count
          (FastAPI ~ Django ~ Flask). Hard mismatches do not count.
        - title_match: how well the candidate's seniority and role align with
          the JD's title and seniority bracket.
        - years_required: integer or float years of experience the JD asks for,
          or null if not stated.

        Then write one short paragraph of reasoning. Return ONLY the JSON.
        Do not invent extra fields; do not output the final confidence score.
        """
    ).strip()
    user = (
        f"=== RESUME ===\n{_resume_summary_for_prompt(resume)}\n\n"
        f"=== JOB DESCRIPTION ===\n{jd_text.strip()}"
    )
    return Prompt(system=system, user=user, temperature=0.1)


async def score_match(
    *,
    resume: Resume,
    jd_text: str,
    llm: LLMProvider,
    risk_tolerance: float,
    job_id: str,
    portal_anti_bot_score: float = 0.0,
) -> tuple[Match, ScoringRubric]:
    """Score a JD vs the active resume and produce a `Match` + raw rubric."""
    rubric = await llm.structured(_build_prompt(resume, jd_text), ScoringRubric)

    years_held = (
        resume.interpreted.total_experience_years if resume.interpreted else None
    )
    fit = experience_fit_from_years(
        years_held=years_held,
        years_required=rubric.years_required,
    )
    confidence, breakdown = compute_confidence(
        ConfidenceComponents(
            skill_overlap=rubric.skill_overlap,
            title_match=rubric.title_match,
            experience_fit=fit,
        )
    )
    risk = compute_risk(
        confidence=confidence, portal_anti_bot_score=portal_anti_bot_score
    )
    decision = decide(confidence=confidence, risk=risk, risk_tolerance=risk_tolerance)

    breakdown["years_required"] = rubric.years_required
    breakdown["years_held"] = years_held
    breakdown["risk"] = risk
    breakdown["risk_tolerance"] = risk_tolerance
    breakdown["reasoning"] = rubric.reasoning

    match = Match(
        id=f"match:{uuid.uuid4().hex[:12]}",
        job_id=job_id,
        resume_id=resume.id,
        confidence=confidence,
        risk=risk,
        decision=decision,
        breakdown=breakdown,
    )
    return match, rubric
