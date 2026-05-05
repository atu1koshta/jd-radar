"""LLM-canonicalized view of a resume.

The raw YAML on GitHub can use any keys the user prefers. This entity is the
*canonical* form the rest of the pipeline reads from: stable field names,
typed sub-models, derived signals (seniority, total years, search terms).

Cached on `Resume.interpreted`; invalidated whenever `Resume.body_hash`
changes. See `ports/resume_interpreter.py` and the LLM adapter for the
generation pass.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Free-form labels — the LLM picks descriptive strings ("backend",
# "security", "platform", ...). Suggested vocabularies live in the prompt;
# we don't enforce them here so a slightly off-label answer never crashes
# the pipeline.
SeniorityLevel = str
SkillCategory = str


class InterpretedSkill(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    category: str | None = None
    years: float | None = None
    proficiency: str | None = None


class InterpretedExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str
    title: str
    seniority: str | None = None
    start_year: int | None = None
    end_year: int | None = None  # None = currently held
    duration_years: float | None = None
    tech_stack: list[str] = Field(default_factory=list)
    summary: str | None = None


class InterpretedResume(BaseModel):
    """Canonical form consumed by scoring, drafting, search-query generation."""

    model_config = ConfigDict(extra="ignore")

    canonical_name: str | None = None
    headline: str | None = None
    summary: str = Field(
        description=(
            "2-3 sentence elevator pitch generated from the resume. Used in "
            "email drafting and as a Telegram alert blurb."
        )
    )

    total_experience_years: float = 0.0
    seniority_level: str = "mid"

    skills: list[InterpretedSkill] = Field(default_factory=list)
    experiences: list[InterpretedExperience] = Field(default_factory=list)

    domains: list[str] = Field(
        default_factory=list,
        description="Industry / vertical tags inferred from past roles (fintech, healthcare, ...).",
    )
    role_categories: list[str] = Field(
        default_factory=list,
        description="High-level role buckets (backend, fullstack, ml, devops, ...).",
    )
    search_query_terms: list[str] = Field(
        default_factory=list,
        description=(
            "5-10 portal-search keyword phrases. Used to seed Naukri/LinkedIn "
            "searches in Phase 2 without hard-coding queries."
        ),
    )

    # Provenance — cache invalidation key + audit trail.
    body_hash: str
    interpreted_at: datetime = Field(default_factory=datetime.utcnow)
    model_used: str | None = Field(
        default=None,
        description="Capability name of the LLM that produced this interpretation.",
    )
